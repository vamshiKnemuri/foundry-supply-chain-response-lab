"""Foundry REST boundary. Deliberately not wired into local execution."""

import json
from urllib.parse import quote
from urllib.request import Request, urlopen

from .engine import LabError, PolicyError


class FoundryClient:
    def __init__(self, base_url: str, ontology: str, token: str, allow_write: bool = False):
        if not base_url.startswith("https://"):
            raise PolicyError("Foundry base URL must use HTTPS")
        self.base_url = base_url.rstrip("/")
        self.ontology = quote(ontology, safe="")
        self.token = token
        self.allow_write = allow_write

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base_url + path, data=data, method=method,
                          headers={"Authorization": "Bearer " + self.token,
                                   "Content-Type": "application/json"})
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def get_object(self, object_type: str, primary_key: str) -> dict:
        return self._request("GET", f"/api/v2/ontologies/{self.ontology}/objects/"
                             f"{quote(object_type, safe='')}/{quote(primary_key, safe='')}")

    def search_objects(self, object_type: str, where: dict, page_size: int = 100) -> dict:
        if not 1 <= page_size <= 1000:
            raise PolicyError("Invalid page size")
        return self._request("POST", f"/api/v2/ontologies/{self.ontology}/objects/"
                             f"{quote(object_type, safe='')}/search", {"where": where, "pageSize": page_size})

    def apply_action(self, action_type: str, parameters: dict) -> dict:
        if not self.allow_write:
            raise PolicyError("Foundry writes are disabled; explicit deployment configuration is required")
        result = self._request("POST", f"/api/v2/ontologies/{self.ontology}/actions/"
                               f"{quote(action_type, safe='')}/apply", {"parameters": parameters})
        if result.get("validation", {}).get("result") != "VALID":
            raise LabError("Foundry action did not return VALID validation")
        return result
