"""Evidence-first response engine with deterministic write gates.

The local store intentionally mirrors an Ontology: objects have stable primary
keys, links are traversed explicitly, and writes happen through named actions.
No production system is contacted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Protocol


class LabError(Exception):
    pass


class NoFeasiblePlan(LabError):
    pass


class PolicyError(LabError):
    pass


class ConflictError(LabError):
    pass


@dataclass(frozen=True)
class Evidence:
    object_type: str
    object_id: str
    field: str
    value: Any
    source: str


@dataclass(frozen=True)
class Proposal:
    disruption_id: str
    shipment_id: str
    inventory_id: str
    route_id: str
    quantity: int
    estimated_arrival_day: int
    days_saved: int
    incremental_cost_usd: int
    rationale: str
    evidence: tuple[Evidence, ...]

    def public(self) -> dict[str, Any]:
        return {**asdict(self), "evidence": [asdict(e) for e in self.evidence]}


class OntologyReader(Protocol):
    def disruption(self, object_id: str) -> dict[str, Any]: ...
    def shipment(self, object_id: str) -> dict[str, Any]: ...
    def inventory_for(self, sku: str) -> list[dict[str, Any]]: ...
    def routes_from(self, facility_id: str) -> list[dict[str, Any]]: ...
    def supplier(self, object_id: str) -> dict[str, Any]: ...
    def policy(self) -> dict[str, Any]: ...
    def disruptions(self) -> list[dict[str, Any]]: ...


class LocalOntology:
    """Synthetic read model. User-authored notes are data, never instructions."""

    def __init__(self, path: Path):
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def _one(self, kind: str, object_id: str) -> dict[str, Any]:
        for item in self.data[kind]:
            if item["id"] == object_id:
                return dict(item)
        raise LabError(f"Unknown {kind.rstrip('s')}: {object_id}")

    def disruption(self, object_id: str) -> dict[str, Any]:
        return self._one("disruptions", object_id)

    def shipment(self, object_id: str) -> dict[str, Any]:
        return self._one("shipments", object_id)

    def inventory_for(self, sku: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self.data["inventory"] if x["sku"] == sku]

    def routes_from(self, facility_id: str) -> list[dict[str, Any]]:
        return [dict(x) for x in self.data["routes"] if x["facility_id"] == facility_id]

    def supplier(self, object_id: str) -> dict[str, Any]:
        return self._one("suppliers", object_id)

    def policy(self) -> dict[str, Any]:
        return dict(self.data["policy"])

    def disruptions(self) -> list[dict[str, Any]]:
        return [dict(x) for x in self.data["disruptions"]]


class ReadTools:
    """Read-only allowlist exposed to a planner; no write tool is available."""

    NAMES = ("get_disruption", "get_shipment", "get_inventory", "get_routes", "get_supplier", "get_policy")

    def __init__(self, ontology: OntologyReader):
        self.ontology = ontology
        self.calls: list[dict[str, Any]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.NAMES:
            raise PolicyError(f"Tool is not allowlisted: {name}")
        if not isinstance(arguments, dict) or any(not isinstance(v, str) for v in arguments.values()):
            raise PolicyError("Tool arguments must be strings")
        expected = {
            "get_disruption": "id", "get_shipment": "id", "get_inventory": "sku",
            "get_routes": "facility_id", "get_supplier": "id", "get_policy": None,
        }[name]
        if set(arguments) != ({expected} if expected else set()):
            raise PolicyError(f"Invalid arguments for {name}")
        if any(len(v) > 120 for v in arguments.values()):
            raise PolicyError("Tool argument too long")
        result = {
            "get_disruption": lambda: self.ontology.disruption(arguments["id"]),
            "get_shipment": lambda: self.ontology.shipment(arguments["id"]),
            "get_inventory": lambda: self.ontology.inventory_for(arguments["sku"]),
            "get_routes": lambda: self.ontology.routes_from(arguments["facility_id"]),
            "get_supplier": lambda: self.ontology.supplier(arguments["id"]),
            "get_policy": lambda: self.ontology.policy(),
        }[name]()
        self.calls.append({"name": name, "arguments": arguments, "result": result})
        return result


def _evidence(kind: str, obj: dict[str, Any], *fields: str) -> tuple[Evidence, ...]:
    return tuple(Evidence(kind, obj["id"], field, obj[field], f"ontology://{kind}/{obj['id']}#{field}") for field in fields)


class DeterministicPlanner:
    """Fully runnable offline planner. Model adapters can replace candidate ranking."""

    def __init__(self, ranker: Any = None):
        self.ranker = ranker

    def propose(self, disruption_id: str, tools: ReadTools) -> Proposal:
        disruption = tools.call("get_disruption", {"id": disruption_id})
        shipment = tools.call("get_shipment", {"id": disruption["shipment_id"]})
        supplier = tools.call("get_supplier", {"id": shipment["supplier_id"]})
        inventory = tools.call("get_inventory", {"sku": shipment["sku"]})
        policy = tools.call("get_policy", {})
        candidates: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
        for lot in inventory:
            if lot["available_units"] < shipment["units"] or lot["facility_id"] == shipment["origin_facility_id"]:
                continue
            for route in tools.call("get_routes", {"facility_id": lot["facility_id"]}):
                if route["destination_id"] != shipment["destination_id"] or not route["enabled"]:
                    continue
                arrival = max(lot["ready_day"], disruption["reported_day"]) + route["transit_days"]
                cost = route["cost_usd"] + lot["handling_cost_usd"] * shipment["units"]
                if arrival < shipment["delayed_arrival_day"]:
                    candidates.append((arrival, cost, lot, route))
        if not candidates:
            raise NoFeasiblePlan("No feasible alternate inventory and route found")
        eligible = [x for x in candidates if x[1] <= policy["max_incremental_cost_usd"]
                    and shipment["delayed_arrival_day"] - x[0] >= policy["min_days_saved"]]
        # Keep a blocked explanation when nothing passes policy, but prefer a
        # valid alternative over a faster over-budget route.
        candidates = eligible or candidates
        if self.ranker:
            options = [{"inventory_id": lot["id"], "route_id": route["id"],
                        "arrival_day": arrival, "incremental_cost_usd": cost}
                       for arrival, cost, lot, route in candidates]
            choice = self.ranker.choose(options)
            if not isinstance(choice, dict) or set(choice) != {"inventory_id", "route_id"}:
                raise PolicyError("Model returned an invalid candidate selection")
            selected = next((item for item in candidates if item[2]["id"] == choice["inventory_id"]
                             and item[3]["id"] == choice["route_id"]), None)
            if selected is None:
                raise PolicyError("Model selected an unverified candidate")
            arrival, cost, lot, route = selected
        else:
            arrival, cost, lot, route = min(candidates, key=lambda x: (x[0], x[1], x[2]["id"]))
        saved = shipment["delayed_arrival_day"] - arrival
        evidence = (
            *_evidence("Disruption", disruption, "severity", "reported_day", "summary"),
            *_evidence("Shipment", shipment, "sku", "units", "delayed_arrival_day", "destination_id"),
            *_evidence("Supplier", supplier, "name", "risk_tier"),
            *_evidence("InventoryLot", lot, "available_units", "ready_day", "facility_id", "handling_cost_usd"),
            *_evidence("Route", route, "transit_days", "cost_usd", "enabled"),
        )
        return Proposal(disruption_id, shipment["id"], lot["id"], route["id"], shipment["units"],
                        arrival, saved, cost, "Reserve alternate inventory and dispatch via the selected verified route.", evidence)


class PolicyGate:
    def evaluate(self, proposal: Proposal, ontology: OntologyReader) -> dict[str, Any]:
        policy = ontology.policy()
        disruption = ontology.disruption(proposal.disruption_id)
        shipment = ontology.shipment(proposal.shipment_id)
        lot = next((x for x in ontology.inventory_for(shipment["sku"]) if x["id"] == proposal.inventory_id), None)
        route = next((x for x in ontology.routes_from(lot["facility_id"]) if x["id"] == proposal.route_id), None) if lot else None
        arrival = max(lot["ready_day"], disruption["reported_day"]) + route["transit_days"] if lot and route else None
        cost = route["cost_usd"] + lot["handling_cost_usd"] * shipment["units"] if lot and route else None
        checks = {
            "allowed_action": proposal.rationale == "Reserve alternate inventory and dispatch via the selected verified route.",
            "shipment_link_matches": disruption["shipment_id"] == proposal.shipment_id,
            "quantity_matches_shipment": type(proposal.quantity) is int and proposal.quantity > 0 and proposal.quantity == shipment["units"],
            "inventory_available": bool(lot and lot["available_units"] >= proposal.quantity),
            "route_enabled": bool(route and route["enabled"] and route["destination_id"] == shipment["destination_id"]),
            "alternate_origin": bool(lot and lot["facility_id"] != shipment["origin_facility_id"]),
            "calculations_match": (type(proposal.incremental_cost_usd) is int and proposal.incremental_cost_usd == cost
                                   and type(proposal.estimated_arrival_day) is int and proposal.estimated_arrival_day == arrival
                                   and type(proposal.days_saved) is int and arrival is not None
                                   and proposal.days_saved == shipment["delayed_arrival_day"] - arrival),
            "cost_within_limit": proposal.incremental_cost_usd <= policy["max_incremental_cost_usd"],
            "days_saved": proposal.days_saved >= policy["min_days_saved"],
            "human_approval_required": policy.get("approval_required") is True,
            "local_simulation_only": policy.get("mode") == "local_simulation_only",
        }
        return {"allowed_for_review": all(checks.values()), "checks": checks, "policy": policy}


class Ledger:
    """SQLite approval and simulated writeback ledger with idempotent execution."""

    def __init__(self, path: Path, ontology: LocalOntology):
        self.path = path
        self.ontology = ontology
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS proposals (
                  id TEXT PRIMARY KEY, disruption_id TEXT NOT NULL, body TEXT NOT NULL,
                  status TEXT NOT NULL, approver TEXT, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS inventory_balance (
                  id TEXT PRIMARY KEY, available_units INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS executed_shipments (
                  shipment_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL,
                  event TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
            """)
            for lot in ontology.data["inventory"]:
                db.execute("INSERT OR IGNORE INTO inventory_balance VALUES (?, ?)", (lot["id"], lot["available_units"]))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _audit(self, db: sqlite3.Connection, event: str, payload: dict[str, Any]) -> None:
        previous = db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = previous["hash"] if previous else "0" * 64
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        timestamp = int(time.time())
        digest = sha256(f"{prev_hash}|{timestamp}|{event}|{body}".encode()).hexdigest()
        db.execute("INSERT INTO audit (at,event,payload,prev_hash,hash) VALUES (?,?,?,?,?)",
                   (timestamp, event, body, prev_hash, digest))

    def create(self, proposal: Proposal, gate: dict[str, Any]) -> dict[str, Any]:
        if not PolicyGate().evaluate(proposal, self.ontology)["allowed_for_review"]:
            raise PolicyError("Proposal failed deterministic policy checks")
        proposal_id = str(uuid.uuid4())
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM executed_shipments WHERE shipment_id=?", (proposal.shipment_id,)).fetchone():
                raise ConflictError("This shipment has already been resolved")
            balance = db.execute("SELECT available_units FROM inventory_balance WHERE id=?", (proposal.inventory_id,)).fetchone()
            if not balance or balance["available_units"] < proposal.quantity:
                raise ConflictError("Inventory changed since investigation; investigate again")
            db.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?)",
                       (proposal_id, proposal.disruption_id, json.dumps(proposal.public()), "pending", None, int(time.time())))
            self._audit(db, "proposal_created", {"proposal_id": proposal_id, "disruption_id": proposal.disruption_id})
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise LabError("Unknown proposal")
        return {"id": row["id"], "status": row["status"], "approver": row["approver"],
                "created_at": row["created_at"], "proposal": json.loads(row["body"])}

    def decide(self, proposal_id: str, approver: str, approve: bool) -> dict[str, Any]:
        if not isinstance(approver, str) or type(approve) is not bool:
            raise PolicyError("Approver must be text and approve must be a boolean")
        approver = approver.strip()
        if not (2 <= len(approver) <= 80):
            raise PolicyError("Approver name must be 2–80 characters")
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise LabError("Unknown proposal")
            if row["status"] != "pending":
                raise ConflictError("Proposal is no longer pending")
            status = "approved" if approve else "rejected"
            db.execute("UPDATE proposals SET status=?, approver=? WHERE id=?", (status, approver, proposal_id))
            self._audit(db, "proposal_decided", {"proposal_id": proposal_id, "status": status, "approver": approver})
        return self.get(proposal_id)

    def execute(self, proposal_id: str) -> dict[str, Any]:
        """Only updates the local simulation. A production Foundry action is never called here."""
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise LabError("Unknown proposal")
            if row["status"] == "executed":
                return self.get(proposal_id)
            if row["status"] != "approved":
                raise PolicyError("Human approval is required before execution")
            proposal = json.loads(row["body"])
            fresh = Proposal(**{**proposal, "evidence": tuple(Evidence(**x) for x in proposal["evidence"])})
            if db.execute("SELECT 1 FROM executed_shipments WHERE shipment_id=?", (fresh.shipment_id,)).fetchone():
                raise ConflictError("This shipment has already been resolved by another proposal")
            if not PolicyGate().evaluate(fresh, self.ontology)["allowed_for_review"]:
                raise PolicyError("Proposal no longer passes policy")
            balance = db.execute("SELECT available_units FROM inventory_balance WHERE id=?", (fresh.inventory_id,)).fetchone()
            if not balance or balance["available_units"] < fresh.quantity:
                raise ConflictError("Inventory changed since approval")
            db.execute("UPDATE inventory_balance SET available_units=available_units-? WHERE id=?",
                       (fresh.quantity, fresh.inventory_id))
            db.execute("UPDATE proposals SET status='executed' WHERE id=?", (proposal_id,))
            db.execute("INSERT INTO executed_shipments VALUES (?, ?)", (fresh.shipment_id, proposal_id))
            self._audit(db, "simulated_action_executed", {"proposal_id": proposal_id,
                         "inventory_id": fresh.inventory_id, "quantity": fresh.quantity})
        return self.get(proposal_id)

    def audit(self) -> list[dict[str, Any]]:
        with self._db() as db:
            return [dict(x) for x in db.execute("SELECT * FROM audit ORDER BY seq")]

    def current_ontology(self) -> LocalOntology:
        snapshot = LocalOntology.__new__(LocalOntology)
        snapshot.data = deepcopy(self.ontology.data)
        with self._db() as db:
            balances = {row["id"]: row["available_units"] for row in db.execute("SELECT * FROM inventory_balance")}
        for lot in snapshot.data["inventory"]:
            lot["available_units"] = balances[lot["id"]]
        return snapshot

    def resolved_shipments(self) -> set[str]:
        with self._db() as db:
            return {row["shipment_id"] for row in db.execute("SELECT shipment_id FROM executed_shipments")}

    def audit_valid(self) -> bool:
        previous = "0" * 64
        for row in self.audit():
            expected = sha256(f"{previous}|{row['at']}|{row['event']}|{row['payload']}".encode()).hexdigest()
            if row["prev_hash"] != previous or row["hash"] != expected:
                return False
            previous = row["hash"]
        return True


class ResponseLab:
    def __init__(self, data_path: Path, db_path: Path, ranker: Any = None):
        self.ontology = LocalOntology(data_path)
        self.ledger = Ledger(db_path, self.ontology)
        self.ranker = ranker

    def investigate(self, disruption_id: str) -> dict[str, Any]:
        ontology = self.ledger.current_ontology()
        if ontology.disruption(disruption_id)["shipment_id"] in self.ledger.resolved_shipments():
            raise ConflictError("This shipment has already been resolved in the local simulation")
        tools = ReadTools(ontology)
        proposal = DeterministicPlanner(self.ranker).propose(disruption_id, tools)
        gate = PolicyGate().evaluate(proposal, ontology)
        return {"proposal": proposal.public(), "gate": gate,
                "trace": [{"tool": x["name"], "arguments": x["arguments"]} for x in tools.calls]}

    def submit(self, investigation: dict[str, Any]) -> dict[str, Any]:
        raw = investigation["proposal"]
        if not isinstance(raw, dict):
            raise PolicyError("Proposal must be a JSON object")
        # Never trust quantities, savings, or citations sent back by a browser.
        class FixedRanker:
            def choose(self, options: list[dict[str, Any]]) -> dict[str, str]:
                return {"inventory_id": raw["inventory_id"], "route_id": raw["route_id"]}

        ontology = self.ledger.current_ontology()
        canonical = DeterministicPlanner(FixedRanker()).propose(raw["disruption_id"], ReadTools(ontology))
        if raw != canonical.public():
            raise PolicyError("Proposal differs from verified ontology evidence")
        return self.ledger.create(canonical, PolicyGate().evaluate(canonical, self.ontology))
