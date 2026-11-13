import unittest
from datetime import UTC, datetime, timedelta

import helpers  # noqa: F401  (path setup + network guard)

from ecobox.records import Gap, Transition
from ecobox.sessions import derive_sessions

T0 = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def tr(minutes, prev, new, uncertain=False, cid="a"):
    return Transition(cid, at(minutes), prev, new, None if prev is None else at(minutes - 3),
                      uncertain)


class SessionTest(unittest.TestCase):
    def test_open_close_and_clean(self):
        sessions = derive_sessions([
            tr(0, None, "available", True),
            tr(30, "available", "charging"),
            tr(150, "charging", "available"),
            tr(200, "available", "charging"),
        ], [])
        self.assertEqual(len(sessions), 2)
        first, second = sessions
        self.assertTrue(first.is_clean)
        self.assertEqual(first.duration, timedelta(minutes=120))
        self.assertTrue(second.is_open)
        self.assertFalse(second.is_clean)

    def test_charging_to_faulted_closes_session(self):
        sessions = derive_sessions([tr(0, "available", "charging"),
                                    tr(40, "charging", "faulted")], [])
        self.assertEqual(sessions[0].duration, timedelta(minutes=40))

    def test_uncertain_ends_and_inner_gaps_make_session_unclean(self):
        sessions = derive_sessions([
            tr(0, None, "charging", True),                  # first seen already charging
            tr(60, "charging", "available"),
            tr(100, "available", "charging"),
            tr(200, "charging", "available", True),         # unplug inside a gap
            tr(300, "available", "charging"),
            tr(400, "charging", "available"),               # certain, but a gap sits inside
            tr(500, "available", "charging", cid="b"),
            tr(560, "charging", "available", cid="b"),      # other connector, gap not its own
        ], [Gap("a", at(320), at(360))])
        by_start = {(s.connector_id, s.start): s for s in sessions}
        self.assertTrue(by_start[("a", at(0))].start_uncertain)
        self.assertTrue(by_start[("a", at(100))].end_uncertain)
        self.assertTrue(by_start[("a", at(300))].contains_gap)
        self.assertTrue(by_start[("b", at(500))].is_clean)
        self.assertEqual(sum(s.is_clean for s in sessions), 1)


if __name__ == "__main__":
    unittest.main()
