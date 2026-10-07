from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from response_lab.engine import ConflictError, LabError, Ledger, PolicyError, PolicyGate, ResponseLab
from response_lab.foundry import FoundryClient
from response_lab.model import OpenAIRanker
from response_lab.server import create_server

ASSETS = Path(__file__).resolve().parents[1] / "src/response_lab/assets"


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = json.loads((ASSETS / "data/scenarios.json").read_text())
        self.path = Path(self.tmp.name) / "data.json"
        self.path.write_text(json.dumps(self.data))
        self.db = Path(self.tmp.name) / "ledger.db"
        self.lab = ResponseLab(self.path, self.db)

    def test_budget_eligible_route_beats_faster_over_budget_route(self):
        self.lab.ontology.data["routes"][0]["cost_usd"] = 12000
        result = self.lab.investigate("DIS-001")
        self.assertEqual(result["proposal"]["route_id"], "RTE-PC")
        self.assertTrue(result["gate"]["allowed_for_review"])

    def test_investigation_reads_remaining_inventory_after_execution(self):
        self.lab.ontology.data["shipments"].append({**self.data["shipments"][0], "id": "SHP-NEW"})
        self.lab.ontology.data["disruptions"].append({**self.data["disruptions"][0], "id": "DIS-NEW", "shipment_id": "SHP-NEW"})
        record = self.lab.submit(self.lab.investigate("DIS-001"))
        self.lab.ledger.decide(record["id"], "Reviewer", True)
        self.lab.ledger.execute(record["id"])
        self.assertEqual(self.lab.investigate("DIS-NEW")["proposal"]["inventory_id"], "LOT-402")
        with self.assertRaises(ConflictError):
            self.lab.investigate("DIS-001")

    def test_policy_recomputes_cost_and_arrival(self):
        from response_lab.engine import DeterministicPlanner, ReadTools
        proposal = DeterministicPlanner().propose("DIS-003", ReadTools(self.lab.ontology))
        forged = replace(proposal, incremental_cost_usd=1, days_saved=50)
        self.assertFalse(PolicyGate().evaluate(forged, self.lab.ontology)["allowed_for_review"])

    def test_two_ledger_instances_cannot_overspend_inventory(self):
        result = self.lab.investigate("DIS-001")
        records = [self.lab.submit(result), self.lab.submit(result)]
        for record in records:
            self.lab.ledger.decide(record["id"], "Reviewer", True)
        other = Ledger(self.db, self.lab.ontology)
        barrier = threading.Barrier(2)
        def execute(pair):
            ledger, record = pair
            barrier.wait()
            try:
                ledger.execute(record["id"])
                return "executed"
            except ConflictError:
                return "conflict"
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(execute, zip([self.lab.ledger, other], records)))
        self.assertCountEqual(outcomes, ["executed", "conflict"])
        self.assertTrue(self.lab.ledger.audit_valid())

    def test_rejected_proposal_cannot_execute(self):
        record = self.lab.submit(self.lab.investigate("DIS-001"))
        self.lab.ledger.decide(record["id"], "Reviewer", False)
        with self.assertRaises(PolicyError):
            self.lab.ledger.execute(record["id"])

    def test_audit_detects_modified_payload(self):
        self.lab.submit(self.lab.investigate("DIS-001"))
        with self.lab.ledger._db() as db:
            db.execute("UPDATE audit SET payload='{}' WHERE seq=1")
        self.assertFalse(self.lab.ledger.audit_valid())

    def test_model_invalid_json_shape_fails_closed(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "[]"}]}]}).encode()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-only"}), patch("response_lab.model.urlopen", return_value=response):
            with self.assertRaises(PolicyError):
                OpenAIRanker().choose([{"inventory_id": "LOT", "route_id": "RTE"}])

    def test_foundry_shapes_and_invalid_action_result(self):
        client = FoundryClient("https://example.palantirfoundry.com", "space/name", "test-only", allow_write=True)
        with patch.object(client, "_request", return_value={}) as request:
            client.get_object("InventoryLot", "LOT/1")
            self.assertIn("space%2Fname/objects/InventoryLot/LOT%2F1", request.call_args.args[1])
            with self.assertRaises(LabError):
                client.apply_action("reserve", {"quantity": 1})


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lab = ResponseLab(ASSETS / "data/scenarios.json", Path(self.tmp.name) / "ledger.db")
        self.server = create_server(self.lab, ASSETS / "web", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()

    def test_http_workflow_and_static_assets(self):
        for path in ["/", "/style.css", "/app.js", "/api/health"]:
            self.assertEqual(self.request("GET", path)[0], 200)
        headers = {"Content-Type": "application/json"}
        status, raw = self.request("POST", "/api/investigate", '{"disruption_id":"DIS-001"}', headers)
        self.assertEqual(status, 200)
        status, raw = self.request("POST", "/api/proposals", raw, headers)
        self.assertEqual(status, 200)
        record = json.loads(raw)
        path = "/api/proposals/" + record["id"]
        self.assertEqual(self.request("POST", path + "/execute", "{}", headers)[0], 400)
        self.assertEqual(self.request("POST", path + "/decision", '{"approver":"Reviewer","approve":true}', headers)[0], 200)
        self.assertEqual(self.request("POST", path + "/execute", "{}", headers)[0], 200)

    def test_bad_requests_and_cross_origin_are_rejected(self):
        for body in ["null", "[]", "{", '{"proposal":null}']:
            self.assertEqual(self.request("POST", "/api/proposals", body, {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/investigate", '{}', {"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request("GET", "/api/health", headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("GET", "/api/health", headers={"Host": "example.com"})[0], 403)
        self.assertEqual(self.request("GET", "/../pyproject.toml")[0], 404)


if __name__ == "__main__":
    unittest.main()
