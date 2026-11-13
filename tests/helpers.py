"""Fixtures, fakes and a network guard shared by the tests."""

import json
import socket
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecobox.models import (ChargePoint, ConnectorStatus, parse_station_list,  # noqa: E402
                           parse_status)
from ecobox.recorder import Recorder  # noqa: E402
from ecobox.scope import ScopeFilter  # noqa: E402
from ecobox.storage import Storage  # noqa: E402


def _refuse_network(*_args, **_kwargs):
    raise RuntimeError("tests must not touch the network")


# Any accidental real connection fails loudly instead of reaching an outside host.
socket.socket.connect = _refuse_network
socket.socket.connect_ex = _refuse_network
socket.create_connection = _refuse_network

FIXTURES = ROOT / "tests" / "fixtures"
POLL_S = 180
GAP_S = POLL_S * 2.5


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_stations() -> list[ChargePoint]:
    return parse_station_list(load_fixture("station_list.json"))


def fixture_statuses() -> list[ConnectorStatus]:
    return parse_status(load_fixture("connector_status.json"))


HACETTEPE = ScopeFilter(("hacettepe",))

# Raw availability codes per normalised state, for scripting payloads.
AVAILABILITY = {"available": 1, "charging": 2, "unavailable": 3, "faulted": 4, "offline": 5,
                "maintenance": 6}


def connector_id(display_name: str, order: int = 1) -> str:
    for cp in fixture_stations():
        if cp.display_name == display_name:
            for c in cp.connectors:
                if c.connector_order == order:
                    return c.id
    raise KeyError(display_name)


EGO = [connector_id(f"Hacettepe Ego - {n} Ac") for n in range(1, 5)]
F_BLOK_2 = connector_id("Hacettepe - F Blok 2")


def scripted_statuses(states: dict[str, str], default: str | None = None) -> list[ConnectorStatus]:
    """Fixture status rows with the given connectors forced to a state.

    `default` forces every other row too; None keeps the fixture's own values.
    """
    rows = []
    for row in fixture_statuses():
        state = states.get(row.connector_id, default)
        if state is None:
            rows.append(row)
            continue
        rows.append(ConnectorStatus(row.charge_point_id, row.connector_id, row.connector_order,
                                    AVAILABILITY[state], None, None, False))
    return rows


class FakeClock:
    def __init__(self, start: datetime):
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeApi:
    def __init__(self):
        self.stations = fixture_stations()
        self.statuses = fixture_statuses()
        self.fail_status = False
        self.fail_stations = False
        self.status_calls = 0
        self.station_calls = 0

    def fetch_stations(self) -> list[ChargePoint]:
        self.station_calls += 1
        if self.fail_stations:
            raise ConnectionError("stations down")
        return self.stations

    def fetch_status(self) -> list[ConnectorStatus]:
        self.status_calls += 1
        if self.fail_status:
            raise TimeoutError("status timed out")
        return self.statuses


class FakeTelegram:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.updates: list[list[dict]] = []
        self.fail_send = False

    def get_updates(self, offset, timeout_s):
        return self.updates.pop(0) if self.updates else []

    def send_message(self, chat_id: int, text: str) -> None:
        if self.fail_send:
            raise ConnectionError("telegram down")
        self.sent.append((chat_id, text))


def message(chat_id: int, text: str, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


class World:
    """Storage + recorder over the fixture catalog, driven by a fake clock."""

    def __init__(self, start: datetime = datetime(2026, 11, 9, 4, 0, tzinfo=UTC)):
        self.clock = FakeClock(start)
        self.api = FakeApi()
        self.storage = Storage(":memory:")
        self.recorder = Recorder(self.api, self.storage, HACETTEPE, self.clock, GAP_S)
        self.recorder.refresh_catalog()

    def poll(self, states: dict[str, str] | None = None, default: str | None = None):
        if states is not None or default is not None:
            self.api.statuses = scripted_statuses(states or {}, default)
        return self.recorder.poll_once()

    def run(self, until: datetime, script, step_s: int = POLL_S) -> None:
        """Poll every step_s from now until `until` (exclusive); script(ts) -> states dict."""
        while self.clock.now() < until:
            self.poll(script(self.clock.now()), default="offline")
            self.clock.advance(step_s)


class FakeSlack:
    """In-memory Slack: one private channel's history plus a log of every call."""

    CHANNEL = "G0ECOBOX"
    BOT_ID = "B0BOT"

    def __init__(self):
        self.calls: list[tuple] = []
        self.history_messages: list[dict] = []
        self.posted: list[tuple[str, str]] = []
        self.taken_names: set[str] = set()
        self.fail_next: dict[str, Exception] = {}
        self._ts = 1762992000_000000  # microseconds; Slack ts is "seconds.micros"

    def next_ts(self) -> str:
        self._ts += 1_000_000
        return f"{self._ts // 1_000_000}.{self._ts % 1_000_000:06d}"

    def _enter(self, method: str, *args) -> None:
        self.calls.append((method, *args))
        if method in self.fail_next:
            raise self.fail_next.pop(method)

    def create_private_channel(self, name: str) -> str:
        from ecobox.slack import SlackError
        self._enter("create", name)
        if name in self.taken_names:
            raise SlackError("conversations.create", "name_taken")
        self.history_messages.append({"type": "message", "subtype": "channel_join",
                                      "user": "UBOT", "ts": self.next_ts(), "text": "joined"})
        return self.CHANNEL

    def invite(self, channel_id: str, user_id: str) -> None:
        self._enter("invite", channel_id, user_id)
        self.history_messages.append({"type": "message", "subtype": "channel_join",
                                      "user": user_id, "ts": self.next_ts(), "text": "joined"})

    def history(self, channel_id: str, oldest_ts: str) -> list[dict]:
        from ecobox.slack import ts_key
        self._enter("history", channel_id, oldest_ts)
        return [m for m in self.history_messages if ts_key(m["ts"]) > ts_key(oldest_ts)]

    def post_message(self, channel_id: str, text: str) -> str:
        self._enter("post", channel_id)
        ts = self.next_ts()
        self.history_messages.append({"type": "message", "bot_id": self.BOT_ID, "user": "UBOT",
                                      "ts": ts, "text": text})
        self.posted.append((channel_id, text))
        return ts

    def say(self, user: str, text: str, **extra) -> str:
        ts = self.next_ts()
        self.history_messages.append({"type": "message", "user": user, "ts": ts, "text": text,
                                      **extra})
        return ts

    def methods(self) -> list[str]:
        return [call[0] for call in self.calls]
