import unittest
from datetime import UTC, date, datetime, timedelta

from helpers import EGO, F_BLOK_2, GAP_S, POLL_S, World

from ecobox.clock import local_bucket, to_local
from ecobox.records import Transition
from ecobox.stats import PollView, Selection, Statistics, time_distribution, wait_until_free

MONDAY_0700_LOCAL = datetime(2026, 11, 9, 4, 0, tzinfo=UTC)
A, B, C, D = EGO


def local_parts(ts: datetime) -> tuple[int, int]:
    local = to_local(ts)
    return (local.date() - date(2026, 11, 9)).days, local.hour * 60 + local.minute


def three_mornings(ts: datetime) -> dict[str, str]:
    """Known history, polled 07:00-12:00 local on Mon/Tue/Wed (Mac asleep otherwise).

    A charges 08:00-10:00 every day; B 09:00-09:30 on Mon and Tue; C plugs in Mon 11:00 and
    unplugs Tue 07:30 (session spans the overnight gap); D is found charging at Tue 07:00 after
    the gap (true plug-in time unknown) and unplugs at 08:00.
    """
    day, minute = local_parts(ts)
    states = {cid: "available" for cid in EGO}
    if 8 * 60 <= minute < 10 * 60:
        states[A] = "charging"
    if day in (0, 1) and 9 * 60 <= minute < 9 * 60 + 30:
        states[B] = "charging"
    if (day == 0 and minute >= 11 * 60) or (day == 1 and minute < 7 * 60 + 30):
        states[C] = "charging"
    if day == 1 and minute < 8 * 60:
        states[D] = "charging"
    return states


class SyntheticHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(MONDAY_0700_LOCAL)
        for day in range(3):
            cls.world.clock.current = MONDAY_0700_LOCAL + timedelta(days=day)
            cls.world.run(cls.world.clock.now() + timedelta(hours=5), three_mornings)
        cls.stats = Statistics(cls.world.storage, GAP_S)
        cls.ego = Selection(location_term="Ego")

    @classmethod
    def tearDownClass(cls):
        cls.world.storage.close()

    def test_plug_in_distribution(self):
        dist = self.stats.plug_in(self.ego)
        self.assertEqual(dist.count, 6)
        self.assertEqual(dist.excluded, 1)  # D, found charging after the gap
        self.assertEqual({h: n for h, n in enumerate(dist.by_hour) if n}, {8: 3, 9: 2, 11: 1})
        self.assertEqual(dist.by_weekday, [3, 2, 1, 0, 0, 0, 0])

    def test_unplug_distribution(self):
        dist = self.stats.unplug(self.ego)
        self.assertEqual((dist.count, dist.excluded), (7, 0))
        self.assertEqual({h: n for h, n in enumerate(dist.by_hour) if n},
                         {7: 1, 8: 1, 9: 2, 10: 3})

    def test_durations_use_clean_sessions_only(self):
        summary = self.stats.durations(self.ego)
        self.assertEqual(summary.count, 5)      # A x3 (120 min), B x2 (30 min)
        self.assertEqual(summary.excluded, 2)   # C spans the night gap, D starts uncertain
        self.assertEqual(summary.median_min, 120)
        self.assertEqual(summary.q1_min, 30)
        self.assertEqual(summary.q3_min, 120)

    def test_per_connector_selection(self):
        summary = self.stats.durations(Selection(connector_id=B))
        self.assertEqual((summary.count, summary.median_min, summary.q1_min), (2, 30, 30))

    def test_availability_share_counts_observed_samples(self):
        shares = {s.entry.connector_id: s for s in self.stats.availability(self.ego)}
        self.assertEqual(shares[A].total, 300)  # 100 polls x 3 mornings
        self.assertAlmostEqual(shares[A].share, 0.6)
        f_blok = self.stats.availability(Selection(location_term="f blok"))
        self.assertEqual(len(f_blok), 4)
        self.assertEqual({s.share for s in f_blok if s.entry.connector_id == F_BLOK_2}, {0.0})

    def test_free_probability_only_counts_observed_hours(self):
        grid = self.stats.free_probability(self.ego)
        cell = grid.cell(0, 7)  # Monday 07:00-08:00
        self.assertEqual((cell.free, cell.total, cell.days), (20, 20, 1))
        self.assertEqual(grid.cell(0, 13).total, 0)  # never observed
        self.assertIsNone(grid.cell(0, 13).probability)
        observed_hours = {h for (_, h) in grid.cells}
        self.assertEqual(observed_hours, {7, 8, 9, 10, 11})

    def test_all_ac_in_scope_includes_offline_sockets(self):
        everything = self.stats.availability(Selection())
        self.assertEqual(len(everything), 12)  # the two DC connectors are left out


class WaitUntilFreeTest(unittest.TestCase):
    def test_wait_measured_per_day_and_gaps_discard(self):
        w = World(MONDAY_0700_LOCAL)
        self.addCleanup(w.storage.close)
        busy_until = {0: 8 * 60 + 30, 1: 8 * 60 + 45, 2: 9 * 60}
        for day in range(3):
            w.clock.current = MONDAY_0700_LOCAL + timedelta(days=day)
            end = w.clock.now() + timedelta(hours=3)
            while w.clock.now() < end:
                _, minute = local_parts(w.clock.now())
                paused = day == 2 and 8 * 60 + 15 <= minute < 8 * 60 + 45
                if not paused:
                    busy = 8 * 60 <= minute < busy_until[day]
                    w.poll({cid: "charging" if busy else "available" for cid in EGO},
                           default="offline")
                w.clock.advance(POLL_S)
        stats = Statistics(w.storage, GAP_S)
        thursday_0806 = MONDAY_0700_LOCAL + timedelta(days=3, minutes=66)
        summary = stats.wait_until_free(Selection(location_term="ego"), thursday_0806)
        self.assertEqual(summary.days_observed, 3)
        self.assertEqual(summary.days_all_busy, 3)
        self.assertEqual(summary.count, 2)  # Wednesday's wait ran into the gap
        self.assertEqual(summary.median_min, 31.5)  # waits of 24 and 39 minutes
        self.assertEqual(summary.q1_min, 27.75)
        self.assertEqual(summary.q3_min, 35.25)

        free_hour = stats.wait_until_free(Selection(location_term="ego"),
                                          thursday_0806 + timedelta(hours=1, minutes=30))
        self.assertEqual((free_hour.days_observed, free_hour.days_all_busy), (3, 0))


class WaitAcrossMidnightTest(unittest.TestCase):
    def test_time_of_day_window_wraps_midnight(self):
        # Local Mon 23:45 .. Tue 00:30 every 3 min, all busy until Tue 00:15.
        start = datetime(2026, 11, 9, 20, 45, tzinfo=UTC)
        free_from = datetime(2026, 11, 9, 21, 15, tzinfo=UTC)
        polls = [PollView(start + timedelta(minutes=3 * i), start + timedelta(minutes=3 * i)
                          >= free_from) for i in range(16)]
        wednesday_0005 = datetime(2026, 11, 10, 21, 5, tzinfo=UTC)
        summary = wait_until_free(polls, wednesday_0005, timedelta(seconds=GAP_S))
        # Monday's nearest is 23:57 (8 min before 00:05 across midnight), Tuesday's 00:06.
        self.assertEqual((summary.days_observed, summary.count), (2, 2))
        self.assertEqual(summary.q1_min, 11.25)  # waits of 9 and 18 minutes
        self.assertEqual(summary.median_min, 13.5)


class TimezoneTest(unittest.TestCase):
    def test_2130_utc_is_0030_next_weekday_in_istanbul(self):
        monday_2130_utc = datetime(2026, 11, 9, 21, 30, tzinfo=UTC)
        self.assertEqual(local_bucket(monday_2130_utc), (1, 0))  # Tuesday, hour 0
        dist = time_distribution([Transition("x", monday_2130_utc, "available", "charging",
                                             monday_2130_utc - timedelta(minutes=3), False)],
                                 plug_in=True)
        self.assertEqual(dist.by_hour[0], 1)
        self.assertEqual(dist.by_weekday, [0, 1, 0, 0, 0, 0, 0])

    def test_summer_date_uses_same_offset(self):
        # Turkey stays on UTC+3 all year.
        self.assertEqual(local_bucket(datetime(2026, 7, 6, 21, 30, tzinfo=UTC)), (1, 0))


if __name__ == "__main__":
    unittest.main()
