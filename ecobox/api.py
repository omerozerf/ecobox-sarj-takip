"""Client for the public (undocumented) Ecobox map API."""

import json
import urllib.request
from typing import Any, Protocol

from ecobox.models import ChargePoint, ConnectorStatus, parse_station_list

RawStatusRow = dict[str, Any]

BASE_URL = "https://map-api.echargo.com"
STATUS_PATH = "/api/app/connector/status?includeNonPublic=true"
LIST_PATH = "/api/app/list?includeNonPublic=true"
LIST_BODY = {"pageNumber": 1, "count": 10000, "direction": 0}

# Without these the API answers with an empty body.
REQUEST_HEADERS = {
    "Origin": "http://harita.ecoboxsarj.com",
    "Referer": "http://harita.ecoboxsarj.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; charger-status-logger/1.0)",
}


class EcoboxApiError(RuntimeError):
    pass


class EcoboxApi(Protocol):
    def fetch_stations(self) -> list[ChargePoint]: ...

    def fetch_status(self) -> list[ConnectorStatus]: ...


class HttpEcoboxApi:
    def __init__(self, timeout_s: float, base_url: str = BASE_URL):
        self._timeout_s = timeout_s
        self._base_url = base_url

    def fetch_stations(self) -> list[ChargePoint]:
        return parse_station_list(self._request("POST", LIST_PATH, LIST_BODY))

    def fetch_status(self) -> list[ConnectorStatus]:
        return [ConnectorStatus.from_api(row) for row in self.fetch_status_rows()]

    def fetch_status_rows(self) -> list[RawStatusRow]:
        """The status rows exactly as the API sent them, for collectors that store raw fields."""
        return self._request("GET", STATUS_PATH, None)["result"]

    def _request(self, method: str, path: str, body: dict | None) -> dict[str, Any]:
        headers = dict(REQUEST_HEADERS)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self._base_url + path, data=data,
                                         headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
            raw = response.read()
        if not raw.strip():
            raise EcoboxApiError(f"{method} {path}: empty body (headers rejected?)")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise EcoboxApiError(f"{method} {path}: unexpected payload type {type(payload).__name__}")
        if payload.get("result") is None:
            raise EcoboxApiError(f"{method} {path}: no result (message={payload.get('message')!r})")
        return payload
