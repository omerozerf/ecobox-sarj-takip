-- The database schema as first deployed (before the Slack front-end), kept verbatim so the
-- migration test opens a database exactly like the live one.
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

CREATE TABLE IF NOT EXISTS subscriptions (
    id            INTEGER PRIMARY KEY,
    chat_id       INTEGER NOT NULL,
    location_term TEXT,
    created_utc   TEXT NOT NULL
);
