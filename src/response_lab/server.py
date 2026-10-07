"""Local-only API and static dashboard, using the Python standard library."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from .engine import ConflictError, LabError, PolicyError, ResponseLab


def create_server(lab: ResponseLab, web_root: Path, port: int = 8765) -> ThreadingHTTPServer:
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
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                raise PolicyError("Content-Type must be application/json")
            if self.headers.get("Transfer-Encoding"):
                raise PolicyError("Transfer-Encoding is unsupported")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 100_000:
                raise PolicyError("Request body must be 1–100000 bytes")
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise PolicyError("Incomplete request body")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise PolicyError("JSON object required")
            return value

        def _local(self) -> bool:
            actual_port = self.server.server_port
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin", "")
            return host in (f"127.0.0.1:{actual_port}", f"localhost:{actual_port}") and (not origin or origin in
                    (f"http://127.0.0.1:{actual_port}", f"http://localhost:{actual_port}"))

        def _run(self, fn) -> None:
            if not self._local():
                self._reply(403, {"error": "Local requests only"})
                return
            try:
                self._reply(200, fn())
            except ConflictError as error:
                self._reply(409, {"error": str(error)})
            except (PolicyError, LabError, ValueError, KeyError, TypeError, AttributeError) as error:
                self._reply(400, {"error": str(error)})
            except TimeoutError:
                self._reply(408, {"error": "Request body timed out"})

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/disruptions":
                self._run(lambda: {"disruptions": [{**d, "resolved": d["shipment_id"] in lab.ledger.resolved_shipments()}
                                                  for d in lab.ontology.disruptions()], "shipments": lab.ontology.data["shipments"]})
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
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
            else:
                self._reply(404, {"error": "Not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            def dispatch() -> dict:
                body = self._json()
                if path == "/api/investigate":
                    if not isinstance(body.get("disruption_id"), str):
                        raise PolicyError("disruption_id must be text")
                    return lab.investigate(body["disruption_id"])
                if path == "/api/proposals":
                    return lab.submit(body)
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "proposals"]:
                    if parts[3] == "decision":
                        if type(body.get("approve")) is not bool:
                            raise PolicyError("approve must be a boolean")
                        return lab.ledger.decide(parts[2], body.get("approver", ""), body["approve"])
                    if parts[3] == "execute":
                        return lab.ledger.execute(parts[2])
                raise LabError("Unknown API route")
            self._run(dispatch)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(lab: ResponseLab, web_root: Path, port: int = 8765) -> None:
    server = create_server(lab, web_root, port)
    print(f"Response Lab: http://127.0.0.1:{server.server_port}  (local simulation only)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
