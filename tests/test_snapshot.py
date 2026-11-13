import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ecobox.snapshot import (INDEX_FILE, append_snapshot, decode_line, encode_line,
                             extend_index, read_snapshots)

FIXTURE = Path(__file__).parent / "fixtures" / "connector_status.json"


def row(connector_id, availability=1, charging_state=3, maintenance=False):
    return {"connectorId": connector_id, "availability": availability,
            "connectorChargingState": charging_state, "isUnderMaintenance": maintenance,
            "chargePointId": "cp", "connectorOrder": 1}


def at(hour, minute, day=13):
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


class IndexTest(unittest.TestCase):
    def test_new_ids_are_appended_and_old_positions_stay(self):
        index = extend_index([], [row("b"), row("a")])
        self.assertEqual(index, ["a", "b"])
        self.assertEqual(extend_index(index, [row("0"), row("b")]), ["a", "b", "0"])


class LineTest(unittest.TestCase):
    def test_round_trip_keeps_the_fields_normalisation_needs(self):
        rows = [row("a", 2, 2), row("b", None, 1, True)]
        ts, decoded = decode_line(encode_line(at(14, 35), ["a", "b"], rows), ["a", "b"])
        self.assertEqual(ts, at(14, 35))
        self.assertEqual(decoded, [
            {"connectorId": "a", "availability": 2, "connectorChargingState": 2,
             "isUnderMaintenance": False},
            {"connectorId": "b", "availability": None, "connectorChargingState": 1,
             "isUnderMaintenance": True}])

    def test_connector_missing_from_the_response_is_null_and_skipped_on_decode(self):
        line = encode_line(at(9, 0), ["a", "b"], [row("b")])
        self.assertEqual(json.loads(line)["r"], [None, [1, 3, 0]])
        self.assertEqual([r["connectorId"] for r in decode_line(line, ["a", "b"])[1]], ["b"])

    def test_line_written_before_the_index_grew_still_decodes(self):
        line = encode_line(at(9, 0), ["a"], [row("a")])
        self.assertEqual(len(decode_line(line, ["a", "later"])[1]), 1)


class AppendTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name) / "snapshots"

    def test_polls_land_in_utc_day_files_and_read_back_in_order(self):
        append_snapshot(self.directory, at(23, 58), [row("a", 2, 2)])
        append_snapshot(self.directory, at(0, 3, day=14), [row("a"), row("b")])
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),
                         ["2026-11-13.jsonl", "2026-11-14.jsonl", INDEX_FILE])
        polls = list(read_snapshots(self.directory))
        self.assertEqual([ts for ts, _ in polls], [at(23, 58), at(0, 3, day=14)])
        self.assertEqual([len(rows) for _, rows in polls], [1, 2])
        self.assertEqual(polls[0][1][0]["availability"], 2)

    def test_real_response_round_trips_every_connector(self):
        rows = json.loads(FIXTURE.read_text())["result"]
        append_snapshot(self.directory, at(12, 0), rows)
        (_, decoded), = read_snapshots(self.directory)
        expected = {r["connectorId"]: (r["availability"], r["connectorChargingState"],
                                       r["isUnderMaintenance"]) for r in rows}
        self.assertEqual(len(decoded), 168)
        self.assertEqual({r["connectorId"]: (r["availability"], r["connectorChargingState"],
                                             r["isUnderMaintenance"]) for r in decoded}, expected)


if __name__ == "__main__":
    unittest.main()
