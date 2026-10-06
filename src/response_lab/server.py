"""Local-only API and static dashboard, using the Python standard library."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from .engine import ConflictError, LabError, PolicyError, ResponseLab


def serve(lab: ResponseLab, web_root: Path, port: int = 8765) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict, content_type: str = "application/json") -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 100_000:
                raise PolicyError("Request body must be 1–100000 bytes")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise PolicyError("JSON object required")
            return value

        def _local(self) -> bool:
            host = self.headers.get("Host", "").split(":")[0]
            origin = self.headers.get("Origin", "")
            return host in ("127.0.0.1", "localhost") and (not origin or origin in
                    (f"http://127.0.0.1:{port}", f"http://localhost:{port}"))

        def _run(self, fn) -> None:
            if not self._local():
                self._reply(403, {"error": "Local requests only"})
                return
            try:
                self._reply(200, fn())
            except ConflictError as error:
                self._reply(409, {"error": str(error)})
            except (PolicyError, LabError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                self._reply(400, {"error": str(error)})

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/disruptions":
                self._run(lambda: {"disruptions": lab.ontology.disruptions(), "shipments": lab.ontology.data["shipments"]})
            elif path == "/api/audit":
                self._run(lambda: {"events": lab.ledger.audit(), "chain_valid": lab.ledger.audit_valid()})
            elif path == "/api/health":
                self._run(lambda: {"status": "ok", "mode": "local_simulation_only"})
            elif path in ("/", "/index.html", "/style.css", "/app.js") and self._local():
                name = "index.html" if path == "/" else path[1:]
                types = {"index.html": "text/html", "style.css": "text/css", "app.js": "text/javascript"}
                data = (web_root / name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", types[name] + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
            else:
                self._reply(404, {"error": "Not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            def dispatch() -> dict:
                body = self._json()
                if path == "/api/investigate":
                    return lab.investigate(str(body["disruption_id"]))
                if path == "/api/proposals":
                    return lab.submit(body)
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "proposals"]:
                    if parts[3] == "decision":
                        if type(body.get("approve")) is not bool:
                            raise PolicyError("approve must be a boolean")
                        return lab.ledger.decide(parts[2], str(body.get("approver", "")), body["approve"])
                    if parts[3] == "execute":
                        return lab.ledger.execute(parts[2])
                raise LabError("Unknown API route")
            self._run(dispatch)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Response Lab: http://127.0.0.1:{port}  (local simulation only)")
    try:
        server.serve_forever()
    finally:
        server.server_close()

