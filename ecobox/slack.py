"""Minimal Slack Web API client (HTTPS polling, no websocket) over urllib.

Slack answers HTTP 200 with `{"ok": false, "error": "..."}` on failure and HTTP 429 with a
`Retry-After` header when rate limited; both become exceptions here. The bot token travels
only in the Authorization header and never appears in an exception message.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any, Protocol

API_BASE = "https://slack.com/api"
HISTORY_PAGE_SIZE = 200
DEFAULT_RETRY_AFTER_S = 30.0

# Errors that retrying cannot fix: the transport stops with a log line instead of backing off.
FATAL_ERRORS = frozenset({
    "invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired",
    "missing_scope", "not_allowed_token_type", "no_permission", "name_taken", "invalid_name",
    "channel_not_found", "is_archived", "user_not_found", "cant_invite_self",
})


class SlackError(RuntimeError):
    def __init__(self, method: str, error: str):
        super().__init__(f"{method}: {error}")
        self.method = method
        self.error = error

    @property
    def fatal(self) -> bool:
        return self.error in FATAL_ERRORS


class SlackRateLimited(SlackError):
    def __init__(self, method: str, retry_after_s: float):
        super().__init__(method, f"rate limited, retry after {retry_after_s:g}s")
        self.retry_after_s = retry_after_s


class SlackClient(Protocol):
    def create_private_channel(self, name: str) -> str:
        """Returns the new channel's id."""

    def invite(self, channel_id: str, user_id: str) -> None: ...

    def history(self, channel_id: str, oldest_ts: str) -> list[dict[str, Any]]:
        """Messages strictly newer than `oldest_ts`, oldest first."""

    def post_message(self, channel_id: str, text: str) -> str:
        """Posts plain text; returns the message ts."""

    def upload_file(self, channel_id: str, data: bytes, filename: str, title: str) -> None:
        """Shares a file in the channel (needs the files:write scope)."""


def ts_key(ts: str) -> Decimal:
    """Slack message timestamps ("1700000000.000100") compared exactly."""
    return Decimal(ts)


def escape(text: str) -> str:
    """Slack treats &, < and > as control characters in message text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


class HttpSlackClient:
    def __init__(self, token: str, http_timeout_s: float = 20.0):
        self._token = token
        self._http_timeout_s = http_timeout_s

    def create_private_channel(self, name: str) -> str:
        return self._call("conversations.create", {"name": name, "is_private": "true"}
                          )["channel"]["id"]

    def invite(self, channel_id: str, user_id: str) -> None:
        self._call("conversations.invite", {"channel": channel_id, "users": user_id})

    def history(self, channel_id: str, oldest_ts: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params = {"channel": channel_id, "oldest": oldest_ts, "inclusive": "false",
                      "limit": str(HISTORY_PAGE_SIZE)}
            if cursor:
                params["cursor"] = cursor
            payload = self._call("conversations.history", params)
            messages.extend(payload.get("messages", []))
            cursor = payload.get("response_metadata", {}).get("next_cursor", "")
            if not payload.get("has_more") or not cursor:
                break
        return sorted(messages, key=lambda m: ts_key(m["ts"]))

    def post_message(self, channel_id: str, text: str) -> str:
        return self._call("chat.postMessage", {
            "channel": channel_id, "text": escape(text), "mrkdwn": "false",
            "unfurl_links": "false", "unfurl_media": "false"})["ts"]

    def upload_file(self, channel_id: str, data: bytes, filename: str, title: str) -> None:
        """Slack's external upload flow: ask for an upload URL, send the bytes there, then
        complete the upload into the channel. files.upload is retired for new apps."""
        target = self._call("files.getUploadURLExternal",
                            {"filename": filename, "length": str(len(data))})
        self._send_bytes(target["upload_url"], data)
        self._call("files.completeUploadExternal", {
            "files": json.dumps([{"id": target["file_id"], "title": title}]),
            "channel_id": channel_id})

    def _send_bytes(self, upload_url: str, data: bytes) -> None:
        """The upload URL is pre-signed by Slack; the bot token is not sent there."""
        request = urllib.request.Request(upload_url, data=data, method="POST",
                                         headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(request, timeout=self._http_timeout_s) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            raise SlackError("files.upload", f"HTTP {exc.code}") from None
        except Exception as exc:
            raise SlackError("files.upload", f"{type(exc).__name__}: "
                                             f"{str(exc).replace(self._token, '***')}") from None

    def _call(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{API_BASE}/{method}", data=urllib.parse.urlencode(params).encode(),
            headers={"Authorization": f"Bearer {self._token}",
                     "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._http_timeout_s) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            exc.close()
            if exc.code == 429:
                raise SlackRateLimited(method, _seconds(retry_after)) from None
            raise SlackError(method, f"HTTP {exc.code}") from None
        except Exception as exc:  # re-raised without anything that could carry the token
            raise SlackError(method, f"{type(exc).__name__}: "
                                     f"{str(exc).replace(self._token, '***')}") from None
        if not payload.get("ok"):
            error = str(payload.get("error", "not ok"))
            if error == "ratelimited":
                raise SlackRateLimited(method, DEFAULT_RETRY_AFTER_S)
            raise SlackError(method, error)
        return payload


def _seconds(retry_after: str | None) -> float:
    try:
        return max(1.0, float(retry_after))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_S
