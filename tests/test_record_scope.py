import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from helpers import (EGO, EVERYTHING, FIXTURES, HACETTEPE_TERMS, POLL_S, FakeTelegram, World,
                     display_scope, make_commands, message)

from ecobox.bot import TelegramBot, TelegramOutbox
from ecobox.commands import TELEGRAM, Conversation
from ecobox.config import Config
from ecobox.models import ConnectorStatus, parse_status
from ecobox.notifier import SubscriptionNotifier
from ecobox.storage import Storage

OWNER = 1111
NOW = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)


def other_site_connector(world: World) -> str:
    """An AC connector outside Hacettepe."""
    return next(e.connector_id for e in world.storage.catalog()
                if e.is_ac and "hacettepe" not in e.location_name.casefold()
                and "hacettpe" not in e.location_name.casefold())


class SchemaV2MigrationTest(unittest.TestCase):
    """The live database today: schema_v2 (Slack era), with rows in every table."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "live.sqlite3"
        conn = sqlite3.connect(self.path)
        conn.executescript((FIXTURES / "schema_v2.sql").read_text(encoding="utf-8"))
        conn.executescript("""
            INSERT INTO poll_log(ts_utc, kind, ok, rows_total, rows_in_scope, duration_ms)
                VALUES ('2026-11-13T08:00:00Z', 'status', 1, 168, 14, 90);
            INSERT INTO charge_points VALUES ('cp1', 'Hacettepe Ego - 1 Ac', 'l1',
                'Hacettepe Teknokent Ego', NULL, NULL, NULL, 1, '2026-11-13T08:00:00Z',
                '2026-11-13T08:00:00Z', '{}');
            INSERT INTO connectors VALUES ('c1', 'cp1', 1, 1, 22, 0, NULL, NULL,
                '2026-11-13T08:00:00Z', '2026-11-13T08:00:00Z');
            INSERT INTO samples VALUES (1, 'c1', '2026-11-13T08:00:00Z', 'charging', 2, 2,
                'charging', 0);
            INSERT INTO transitions(connector_id, ts_utc, prev_state, new_state, prev_ts_utc,
                uncertain) VALUES ('c1', '2026-11-13T08:00:00Z', NULL, 'charging', NULL, 1);
            INSERT INTO subscriptions(chat_id, location_term, created_utc, transport)
                VALUES ('G0ECOBOX', 'ego', '2026-11-13T08:01:00Z', 'slack');
            INSERT INTO service_state VALUES ('slack.channel_id', 'G0ECOBOX',
                '2026-11-13T08:00:00Z');
        """)
        conn.commit()
        conn.close()

    def dump(self) -> dict[str, list[tuple]]:
        conn = sqlite3.connect(self.path)
        try:
            tables = ["poll_log", "charge_points", "connectors", "samples", "transitions",
                      "subscriptions", "service_state"]
            return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
        finally:
            conn.close()

    def columns(self, table: str) -> list[str]:
        conn = sqlite3.connect(self.path)
        try:
            return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        finally:
            conn.close()

    def test_migration_keeps_every_row_and_is_idempotent(self):
        before = self.dump()
        Storage(self.path).close()
        after = self.dump()
        for table, rows in before.items():
            # old columns keep their values; new columns are appended as NULL
            self.assertEqual([row[:len(rows[0])] for row in after[table]], rows, table)
        self.assertEqual(self.columns("samples")[-1], "extra_json")
        self.assertEqual(self.columns("connectors")[-3:],
                         ["socket_code", "reservation_status", "nearby_reservations"])
        self.assertEqual(self.columns("charge_points")[-2:],
                         ["is_device_in_working_hours", "supports_auto_charge"])
        self.assertIn("connector_id", self.columns("catalog_snapshots"))

        Storage(self.path).close()  # a second open changes nothing
        self.assertEqual(self.dump(), after)
        self.assertEqual(self.columns("samples").count("extra_json"), 1)

    def test_migrated_database_keeps_working(self):
        storage = Storage(self.path)
        self.addCleanup(storage.close)
        self.assertEqual(storage.latest_samples()["c1"][1], "charging")
        self.assertEqual(storage.get_state("slack.channel_id"), "G0ECOBOX")
        self.assertEqual([s.transport for s in storage.subscriptions()], ["slack"])

    def test_schema_v1_still_migrates(self):
        path = self.path.with_name("v1.sqlite3")
        conn = sqlite3.connect(path)
        conn.executescript((FIXTURES / "schema_v1.sql").read_text(encoding="utf-8"))
        conn.close()
        Storage(path).close()
        conn = sqlite3.connect(path)
        try:
            names = [r[1] for r in conn.execute("PRAGMA table_info(subscriptions)")]
        finally:
            conn.close()
        self.assertIn("transport", names)


class RecordScopeTest(unittest.TestCase):
    def setUp(self):
        self.world = World(NOW, record=EVERYTHING)
        self.addCleanup(self.world.storage.close)

    def test_every_site_is_recorded(self):
        self.assertEqual(len(self.world.storage.catalog()), 168)
        result = self.world.poll()
        self.assertEqual(result.sample_count, 168)
        self.assertEqual(result.missing_connectors, set())

    def test_display_scope_keeps_the_bot_on_hacettepe(self):
        self.world.poll()
        commands = make_commands(self.world)
        chat = Conversation(TELEGRAM, OWNER, "/")
        status = commands.reply("/durum", chat).text
        self.assertTrue(status.startswith("🟢 1/12 AC priz boş"))
        self.assertIn("⚡ DC: 1/2 boş", status)
        self.assertNotIn("Hacettepe", status)  # short names are computed over Hacettepe only
        self.assertIn("Ego 4", status)
        stats = commands.reply("/istatistik", chat).text
        self.assertIn("Dayanak: 1 okuma", stats)
        unknown = commands.reply("/haberver kızılay", chat).text
        self.assertIn("eşleşen AC priz yok", unknown)
        self.assertNotIn("Akıncı", unknown)  # only display-scope locations are offered

    def test_subscriptions_fire_only_for_display_scope_sockets(self):
        self.world.poll()
        telegram = FakeTelegram()
        bot = TelegramBot(telegram, make_commands(self.world), OWNER)
        notifier = SubscriptionNotifier(self.world.storage, display_scope(self.world),
                                        {TELEGRAM: TelegramOutbox(telegram)})
        self.world.recorder.add_listener(notifier.on_transitions)
        elsewhere = other_site_connector(self.world)
        self.world.clock.advance(POLL_S)
        self.world.poll({elsewhere: "charging"})
        bot.handle_update(message(OWNER, "/haberver"))
        telegram.sent.clear()

        self.world.clock.advance(POLL_S)
        self.world.poll({elsewhere: "available"})  # another site frees up: silence
        self.assertEqual(telegram.sent, [])
        self.world.clock.advance(POLL_S)
        self.world.poll({elsewhere: "available", EGO[0]: "available"})
        self.assertEqual([text for _, text in telegram.sent],
                         ["🟢 Boşaldı: Ego 1\nTekrar haber için: /haberver"])

    def test_analysis_reaches_outside_the_display_scope(self):
        self.world.poll()
        text = make_commands(self.world).reply("analiz akıncı plaza",
                                               Conversation(TELEGRAM, OWNER, "/")).text
        self.assertTrue(text.startswith("🔎 Akıncı plaza · "), text)


class ExtraFieldsTest(unittest.TestCase):
    def test_reservations_and_charge_point_flags_are_snapshotted(self):
        world = World(NOW)
        self.addCleanup(world.storage.close)
        cp = next(cp for cp in world.api.stations if cp.connectors[0].id == EGO[0])
        reserved = replace(cp.connectors[0], reservation_status=json.dumps({"status": 1}),
                           nearby_reservations=json.dumps([{"id": "r1"}]))
        world.api.stations[world.api.stations.index(cp)] = replace(cp, connectors=(reserved,))
        world.clock.advance(3600)
        world.recorder.refresh_catalog()
        [first, second] = world.storage.catalog_snapshots(EGO[0])
        self.assertIsNone(first["reservation_status"])
        self.assertIsNone(first["nearby_reservations"])  # an empty list is stored as NULL
        self.assertEqual(json.loads(second["reservation_status"]), {"status": 1})
        self.assertEqual(json.loads(second["nearby_reservations"]), [{"id": "r1"}])
        self.assertEqual((second["is_device_in_working_hours"], second["supports_auto_charge"],
                          second["is_public"]), (1, 0, int(cp.is_public)))

    def test_api_parses_reservation_fields(self):
        from helpers import load_fixture
        from ecobox.models import parse_station_list
        payload = load_fixture("station_list.json")
        raw = payload["result"]["items"][0]["connectors"][0]
        raw["connectorReservationStatus"] = {"state": "reserved"}
        raw["nearByConnectorReservations"] = []
        connector = parse_station_list(payload)[0].connectors[0]
        self.assertEqual(json.loads(connector.reservation_status), {"state": "reserved"})
        self.assertIsNone(connector.nearby_reservations)

    def test_unknown_status_fields_are_kept_verbatim(self):
        raw = {"chargePointId": "cp", "connectorId": "c", "availability": 1,
               "reservationState": 2}
        [status] = parse_status({"result": [raw]})
        self.assertEqual(json.loads(status.extra_json), {"reservationState": 2})
        known_only = ConnectorStatus.from_api({"chargePointId": "cp", "connectorId": "c"})
        self.assertIsNone(known_only.extra_json)

        world = World(NOW)
        self.addCleanup(world.storage.close)
        world.api.statuses = [replace(s, extra_json=status.extra_json) if s.connector_id == EGO[0]
                              else s for s in world.api.statuses]
        world.poll()
        stored = world.storage._conn.execute(
            "SELECT extra_json FROM samples WHERE connector_id=?", (EGO[0],)).fetchone()[0]
        self.assertEqual(json.loads(stored), {"reservationState": 2})


class ScopeConfigTest(unittest.TestCase):
    def test_record_everything_show_hacettepe_by_default(self):
        config = Config.load(Path("/nonexistent/.env"), environ={})
        self.assertEqual(config.record_scope_terms, ())
        self.assertEqual(config.scope_terms, HACETTEPE_TERMS)

    def test_record_scope_can_be_narrowed(self):
        config = Config.load(Path("/nonexistent"), {"ECOBOX_RECORD_SCOPE": "hacettepe, ego"})
        self.assertEqual(config.record_scope_terms, ("hacettepe", "ego"))

    def test_digest_settings(self):
        config = Config.load(Path("/nonexistent"), {})
        self.assertEqual((config.digest_weekday, config.digest_hour, config.digest_min_days),
                         (0, 9, 3.0))
        config = Config.load(Path("/nonexistent"), {"DIGEST_WEEKDAY": "7", "DIGEST_HOUR": "20",
                                                    "DIGEST_MIN_DAYS": "5"})
        self.assertEqual((config.digest_weekday, config.digest_hour, config.digest_min_days),
                         (6, 20, 5.0))
        self.assertIsNone(Config.load(Path("/nonexistent"),
                                      {"DIGEST_WEEKDAY": "0"}).digest_weekday)
        with self.assertRaises(ValueError):
            Config.load(Path("/nonexistent"), {"DIGEST_WEEKDAY": "8"})


if __name__ == "__main__":
    unittest.main()
