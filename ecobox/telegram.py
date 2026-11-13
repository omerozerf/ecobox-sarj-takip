"""Minimal Telegram Bot API client (long polling) over urllib."""

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramClient(Protocol):
    def get_updates(self, offset: int | None, timeout_s: int) -> list[dict[str, Any]]: ...

    def send_message(self, chat_id: int, text: str) -> None: ...


class HttpTelegramClient:
    def __init__(self, token: str, http_timeout_s: float = 20.0):
        self._token = token
        self._http_timeout_s = http_timeout_s

    def get_updates(self, offset: int | None, timeout_s: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout_s, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params, timeout_s + self._http_timeout_s)

    def send_message(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text,
                                   "disable_web_page_preview": True}, self._http_timeout_s)

    def _call(self, method: str, params: dict[str, Any], timeout_s: float) -> Any:
        request = urllib.request.Request(
            f"{API_BASE}/bot{self._token}/{method}", data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            exc.close()
            raise TelegramError(f"{method}: HTTP {exc.code}") from None
        except Exception as exc:  # re-raised without the URL, which contains the token
            raise TelegramError(f"{method}: {type(exc).__name__}: "
                                f"{str(exc).replace(self._token, '***')}") from None
        if not payload.get("ok"):
            raise TelegramError(f"{method}: {payload.get('description', 'not ok')}")
        return payload["result"]
