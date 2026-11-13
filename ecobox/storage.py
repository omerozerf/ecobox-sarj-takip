"""SQLite storage. All timestamps are UTC ISO strings (see clock.to_db)."""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ecobox.clock import from_db, to_db
from ecobox.models import ChargePoint, ConnectorStatus, Tariff
from ecobox.records import (CatalogEntry, CurrentStatus, Gap, Sample, Subscription,
                            Transition)

SCHEMA = """
CREATE TABLE IF NOT EXISTS poll_log (
    id            INTEGER PRIMARY KEY,
    ts_utc        TEXT    NOT NULL,
    kind          TEXT    NOT NULL,          -- 'status' | 'stations'
    ok            INTEGER NOT NULL,
    error         TEXT,
    rows_total    INTEGER,
    rows_in_scope INTEGER,
    duration_ms   INTEGER
);
CREATE INDEX IF NOT EXISTS poll_log_kind_ts ON poll_log(kind, ts_utc);

CREATE TABLE IF NOT EXISTS charge_points (
    id             TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    location_id    TEXT NOT NULL,
    location_name  TEXT NOT NULL,
    address        TEXT,
    latitude       REAL,
    longitude      REAL,
    is_public      INTEGER NOT NULL,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc  TEXT NOT NULL,
    raw_json       TEXT NOT NULL             -- latest list item, connectors included
);

CREATE TABLE IF NOT EXISTS connectors (
    id                 TEXT PRIMARY KEY,
    charge_point_id    TEXT NOT NULL REFERENCES charge_points(id),
    connector_order    INTEGER NOT NULL,
    type               INTEGER NOT NULL,     -- 1 = AC, 2 = DC
    power_kw           REAL NOT NULL,
    has_barrier        INTEGER NOT NULL,
    maintenance_title  TEXT,
    maintenance_end_at TEXT,
    first_seen_utc     TEXT NOT NULL,
    last_seen_utc      TEXT NOT NULL
);

-- One row per distinct tariff seen on a connector; tariff_id NULL means the API gave none.
CREATE TABLE IF NOT EXISTS tariff_history (
    id             INTEGER PRIMARY KEY,
    connector_id   TEXT NOT NULL REFERENCES connectors(id),
    tariff_id      TEXT,
    energy_price   REAL,
    service_price  REAL,
    initial_price  REAL,
    currency       TEXT,
    vat            REAL,
    effective_date TEXT,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tariff_history_conn ON tariff_history(connector_id, id);

CREATE TABLE IF NOT EXISTS samples (
    poll_id              INTEGER NOT NULL REFERENCES poll_log(id),
    connector_id         TEXT    NOT NULL REFERENCES connectors(id),
    ts_utc               TEXT    NOT NULL,
    state                TEXT    NOT NULL,   -- normalised
    availability         INTEGER,            -- raw fields from the status endpoint
    charging_state       INTEGER,
    charging_state_name  TEXT,
    is_under_maintenance INTEGER,
    PRIMARY KEY (connector_id, ts_utc)
);
CREATE INDEX IF NOT EXISTS samples_poll ON samples(poll_id);

CREATE TABLE IF NOT EXISTS transitions (
    id           INTEGER PRIMARY KEY,
    connector_id TEXT    NOT NULL REFERENCES connectors(id),
    ts_utc       TEXT    NOT NULL,           -- first sample showing new_state
    prev_state   TEXT,                       -- NULL: first observation of the connector
    new_state    TEXT    NOT NULL,
    prev_ts_utc  TEXT,                       -- last sample showing prev_state
    uncertain    INTEGER NOT NULL            -- 1: true change time unknown
);
CREATE INDEX IF NOT EXISTS transitions_conn_ts ON transitions(connector_id, ts_utc);

CREATE TABLE IF NOT EXISTS gaps (
    id           INTEGER PRIMARY KEY,
    connector_id TEXT NOT NULL REFERENCES connectors(id),
    from_ts_utc  TEXT NOT NULL,
    to_ts_utc    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS gaps_conn ON gaps(connector_id, from_ts_utc);

-- chat_id holds the transport's conversation id: an integer Telegram chat id, or a Slack
-- channel id stored as TEXT (SQLite keeps non-numeric text as text in an INTEGER column).
CREATE TABLE IF NOT EXISTS subscriptions (
    id            INTEGER PRIMARY KEY,
    chat_id       INTEGER NOT NULL,
    location_term TEXT,
    created_utc   TEXT NOT NULL,
    transport     TEXT NOT NULL DEFAULT 'telegram'
);

-- Small persistent key/value state of the front-ends (e.g. the Slack channel id and the
-- newest processed message ts), so a restart neither redoes a bootstrap nor replays messages.
CREATE TABLE IF NOT EXISTS service_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_utc TEXT NOT NULL
);
"""

# Additive migrations for databases created by an older SCHEMA: (table, column, definition).
# Each runs only when the column is missing, so opening a database is idempotent.
ADDED_COLUMNS = (
    ("subscriptions", "transport", "TEXT NOT NULL DEFAULT 'telegram'"),
)


def migrate(conn: sqlite3.Connection) -> None:
    """Bring any earlier schema up to date without touching existing rows."""
    conn.executescript(SCHEMA)
    for table, column, definition in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@dataclass(frozen=True)
class SampleWrite:
    status: ConnectorStatus
    state: str


class Storage:
    """Thread-safe facade over one SQLite connection."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            migrate(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- poll log -------------------------------------------------------------------

    def log_failure(self, ts: datetime, kind: str, error: str, duration_ms: int) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO poll_log(ts_utc, kind, ok, error, duration_ms) VALUES (?,?,0,?,?)",
                (to_db(ts), kind, error, duration_ms))
            return cur.lastrowid

    def ok_status_poll_times(self) -> list[datetime]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc FROM poll_log WHERE kind='status' AND ok=1 ORDER BY ts_utc").fetchall()
        return [from_db(r[0]) for r in rows]

    def poll_counts(self) -> tuple[int, int]:
        """(ok, failed) status polls."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(ok),0), COALESCE(SUM(1-ok),0) FROM poll_log "
                "WHERE kind='status'").fetchone()
        return int(row[0]), int(row[1])

    # ----- catalog --------------------------------------------------------------------

    def upsert_catalog(self, charge_points: list[ChargePoint], ts: datetime,
                       rows_total: int, duration_ms: int) -> None:
        now = to_db(ts)
        with self._lock, self._conn:
            for cp in charge_points:
                self._conn.execute(
                    """INSERT INTO charge_points(id, display_name, location_id, location_name,
                           address, latitude, longitude, is_public, first_seen_utc,
                           last_seen_utc, raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,
                           location_id=excluded.location_id, location_name=excluded.location_name,
                           address=excluded.address, latitude=excluded.latitude,
                           longitude=excluded.longitude, is_public=excluded.is_public,
                           last_seen_utc=excluded.last_seen_utc, raw_json=excluded.raw_json""",
                    (cp.id, cp.display_name, cp.location_id, cp.location_name, cp.address,
                     cp.latitude, cp.longitude, int(cp.is_public), now, now,
                     json.dumps(cp.raw, ensure_ascii=False)))
                for c in cp.connectors:
                    self._conn.execute(
                        """INSERT INTO connectors(id, charge_point_id, connector_order, type,
                               power_kw, has_barrier, maintenance_title, maintenance_end_at,
                               first_seen_utc, last_seen_utc)
                           VALUES (?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET charge_point_id=excluded.charge_point_id,
                               connector_order=excluded.connector_order, type=excluded.type,
                               power_kw=excluded.power_kw, has_barrier=excluded.has_barrier,
                               maintenance_title=excluded.maintenance_title,
                               maintenance_end_at=excluded.maintenance_end_at,
                               last_seen_utc=excluded.last_seen_utc""",
                        (c.id, cp.id, c.connector_order, c.type, c.power_kw, int(c.has_barrier),
                         c.maintenance_title, c.maintenance_end_at, now, now))
                    self._record_tariff(c.id, c.tariff, now)
            self._conn.execute(
                "INSERT INTO poll_log(ts_utc, kind, ok, rows_total, rows_in_scope, duration_ms) "
                "VALUES (?, 'stations', 1, ?, ?, ?)",
                (now, rows_total, sum(len(cp.connectors) for cp in charge_points), duration_ms))

    def _record_tariff(self, connector_id: str, tariff: Tariff | None, now: str) -> None:
        key = (None,) * 6 if tariff is None else (
            tariff.tariff_id, tariff.energy_price, tariff.service_price, tariff.initial_price,
            tariff.currency, tariff.vat)
        latest = self._conn.execute(
            "SELECT id, tariff_id, energy_price, service_price, initial_price, currency, vat "
            "FROM tariff_history WHERE connector_id=? ORDER BY id DESC LIMIT 1",
            (connector_id,)).fetchone()
        if latest is not None and tuple(latest[1:]) == key:
            self._conn.execute("UPDATE tariff_history SET last_seen_utc=? WHERE id=?",
                               (now, latest[0]))
            return
        effective = None if tariff is None else tariff.effective_date
        self._conn.execute(
            "INSERT INTO tariff_history(connector_id, tariff_id, energy_price, service_price, "
            "initial_price, currency, vat, effective_date, first_seen_utc, last_seen_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", (connector_id, *key, effective, now, now))

    def has_catalog(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM connectors LIMIT 1").fetchone() is not None

    def catalog(self) -> list[CatalogEntry]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT c.id, c.charge_point_id, cp.display_name, cp.location_name,
                          c.connector_order, c.type, c.power_kw
                   FROM connectors c JOIN charge_points cp ON cp.id = c.charge_point_id
                   ORDER BY cp.location_name, cp.display_name, c.connector_order""").fetchall()
        return [CatalogEntry(*r) for r in rows]

    def current_tariff(self, connector_id: str) -> Tariff | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tariff_id, energy_price, service_price, initial_price, currency, vat, "
                "effective_date FROM tariff_history WHERE connector_id=? ORDER BY id DESC LIMIT 1",
                (connector_id,)).fetchone()
        if row is None or row[0] is None:
            return None
        return Tariff(*row)

    def tariff_history(self, connector_id: str) -> list[tuple[Tariff | None, datetime, datetime]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tariff_id, energy_price, service_price, initial_price, currency, vat, "
                "effective_date, first_seen_utc, last_seen_utc FROM tariff_history "
                "WHERE connector_id=? ORDER BY id", (connector_id,)).fetchall()
        return [(None if r[0] is None else Tariff(*r[:7]), from_db(r[7]), from_db(r[8]))
                for r in rows]

    # ----- status samples -------------------------------------------------------------

    def latest_samples(self) -> dict[str, tuple[datetime, str]]:
        """connector_id -> (ts, state) of its newest sample."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT s.connector_id, s.ts_utc, s.state FROM samples s
                   JOIN (SELECT connector_id, MAX(ts_utc) AS ts FROM samples
                         GROUP BY connector_id) m
                     ON m.connector_id = s.connector_id AND m.ts = s.ts_utc""").fetchall()
        return {r[0]: (from_db(r[1]), r[2]) for r in rows}

    def record_status_poll(self, ts: datetime, rows_total: int, duration_ms: int,
                           samples: list[SampleWrite], transitions: list[Transition],
                           gaps: list[Gap]) -> int:
        now = to_db(ts)
        with self._lock, self._conn:
            poll_id = self._conn.execute(
                "INSERT INTO poll_log(ts_utc, kind, ok, rows_total, rows_in_scope, duration_ms) "
                "VALUES (?, 'status', 1, ?, ?, ?)",
                (now, rows_total, len(samples), duration_ms)).lastrowid
            self._conn.executemany(
                "INSERT INTO samples(poll_id, connector_id, ts_utc, state, availability, "
                "charging_state, charging_state_name, is_under_maintenance) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [(poll_id, s.status.connector_id, now, s.state, s.status.availability,
                  s.status.charging_state, s.status.charging_state_name,
                  None if s.status.is_under_maintenance is None
                  else int(s.status.is_under_maintenance)) for s in samples])
            self._conn.executemany(
                "INSERT INTO transitions(connector_id, ts_utc, prev_state, new_state, "
                "prev_ts_utc, uncertain) VALUES (?,?,?,?,?,?)",
                [(t.connector_id, to_db(t.ts), t.prev_state, t.new_state,
                  None if t.prev_ts is None else to_db(t.prev_ts), int(t.uncertain))
                 for t in transitions])
            self._conn.executemany(
                "INSERT INTO gaps(connector_id, from_ts_utc, to_ts_utc) VALUES (?,?,?)",
                [(g.connector_id, to_db(g.from_ts), to_db(g.to_ts)) for g in gaps])
        return poll_id

    def samples(self, connector_ids: set[str]) -> list[Sample]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT poll_id, connector_id, ts_utc, state FROM samples "
                "ORDER BY ts_utc, connector_id").fetchall()
        return [Sample(r[0], r[1], from_db(r[2]), r[3]) for r in rows if r[1] in connector_ids]

    def transitions(self, connector_ids: set[str]) -> list[Transition]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT connector_id, ts_utc, prev_state, new_state, prev_ts_utc, uncertain "
                "FROM transitions ORDER BY connector_id, ts_utc, id").fetchall()
        return [Transition(r[0], from_db(r[1]), r[2], r[3],
                           None if r[4] is None else from_db(r[4]), bool(r[5]))
                for r in rows if r[0] in connector_ids]

    def gaps(self, connector_ids: set[str]) -> list[Gap]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT connector_id, from_ts_utc, to_ts_utc FROM gaps ORDER BY from_ts_utc"
            ).fetchall()
        return [Gap(r[0], from_db(r[1]), from_db(r[2])) for r in rows if r[0] in connector_ids]

    def current_status(self) -> list[CurrentStatus]:
        latest = self.latest_samples()
        result = []
        for entry in self.catalog():
            if entry.connector_id not in latest:
                continue
            sampled_at, state = latest[entry.connector_id]
            with self._lock:
                row = self._conn.execute(
                    "SELECT ts_utc, uncertain FROM transitions WHERE connector_id=? "
                    "ORDER BY ts_utc DESC, id DESC LIMIT 1", (entry.connector_id,)).fetchone()
            result.append(CurrentStatus(
                entry=entry, state=state, sampled_at=sampled_at,
                since=None if row is None else from_db(row[0]),
                since_uncertain=True if row is None else bool(row[1]),
                tariff=self.current_tariff(entry.connector_id)))
        return result

    # ----- subscriptions --------------------------------------------------------------

    def add_subscription(self, transport: str, chat_id: int | str, location_term: str | None,
                         ts: datetime) -> int:
        """Replace this conversation's subscription; other transports keep theirs."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM subscriptions WHERE transport=? AND chat_id=?",
                               (transport, chat_id))
            return self._conn.execute(
                "INSERT INTO subscriptions(transport, chat_id, location_term, created_utc) "
                "VALUES (?,?,?,?)", (transport, chat_id, location_term, to_db(ts))).lastrowid

    def remove_subscriptions(self, transport: str, chat_id: int | str) -> int:
        with self._lock, self._conn:
            return self._conn.execute(
                "DELETE FROM subscriptions WHERE transport=? AND chat_id=?",
                (transport, chat_id)).rowcount

    def remove_subscription(self, subscription_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM subscriptions WHERE id=?", (subscription_id,))

    def subscriptions(self) -> list[Subscription]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, transport, chat_id, location_term, created_utc FROM subscriptions "
                "ORDER BY id").fetchall()
        return [Subscription(r[0], r[1], r[2], r[3], from_db(r[4])) for r in rows]

    # ----- front-end state ------------------------------------------------------------

    def get_state(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM service_state WHERE key=?",
                                     (key,)).fetchone()
        return None if row is None else row[0]

    def set_state(self, key: str, value: str, ts: datetime) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO service_state(key, value, updated_utc) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_utc=excluded.updated_utc", (key, value, to_db(ts)))
