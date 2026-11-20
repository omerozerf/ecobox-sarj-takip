import unittest
from datetime import UTC, datetime, time, timedelta

import helpers  # noqa: F401  (path setup and the network guard)

from ecobox.clock import LOCAL_TZ
from ecobox.estimate import ChargeEstimator, EnergyCurve
from ecobox.idle_fee import IdleFeePolicy
from ecobox.models import Tariff
from ecobox.texts import estimate_text

POLICY = IdleFeePolicy(timedelta(minutes=30), 2.0, time(8, 30))


def local(hour: int, minute: int = 0, day: int = 10) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=LOCAL_TZ)


class IdleDeadlineTest(unittest.TestCase):
    def test_daytime_charge_gets_the_tolerance(self):
        # The notice's own example: completed at 18:00, free until 18:30.
        deadline = POLICY.deadline(local(18))
        self.assertEqual(deadline.leave_by, local(18, 30))
        self.assertFalse(deadline.night_free)
        self.assertEqual(deadline.per_minute, 2.0)

    def test_night_charge_must_leave_when_the_free_window_ends(self):
        # The notice's example: completed at 03:00, no fee until 08:30, no extra tolerance.
        deadline = POLICY.deadline(local(3))
        self.assertEqual(deadline.leave_by, local(8, 30))
        self.assertTrue(deadline.night_free)

    def test_window_edges(self):
        self.assertEqual(POLICY.deadline(local(0)).leave_by, local(8, 30))
        self.assertEqual(POLICY.deadline(local(8, 29)).leave_by, local(8, 30))
        # At 08:30 the window is over: an ordinary charge with its tolerance.
        self.assertEqual(POLICY.deadline(local(8, 30)).leave_by, local(9))

    def test_tolerance_crossing_midnight_is_not_extended_to_the_morning(self):
        deadline = POLICY.deadline(local(23, 45))
        self.assertEqual(deadline.leave_by, local(0, 15, day=11))
        self.assertFalse(deadline.night_free)

    def test_the_window_is_local_time_whatever_zone_the_moment_carries(self):
        # 00:30 UTC is 03:30 in Istanbul: inside the window.
        deadline = POLICY.deadline(datetime(2026, 11, 10, 0, 30, tzinfo=UTC))
        self.assertEqual(deadline.leave_by, local(8, 30))


class EstimateWithIdleFeeTest(unittest.TestCase):
    NOW = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)  # 09:00 local

    def estimate(self, start, target, service=2, policy=POLICY, now=NOW):
        estimator = ChargeEstimator(10.0, EnergyCurve.flat(1.0), True, policy)
        return estimator.estimate(start, target, now, 22,
                                  Tariff("t", 10.0, service, 0, "TRY", 20, None))

    def test_reply_names_the_time_to_move_the_car(self):
        # 50 kWh at 10 kW: ready at 14:00, so 14:30 at the latest.
        text = estimate_text(self.estimate(20, 70), self.NOW)
        self.assertEqual(text, "🔋 %20 → %70\n"
                               "⏱ 5 sa · bugün 14:00'te hazır\n"
                               "💰 ~500 TL\n"
                               "⚡ 50 kWh, 10 kW ile\n"
                               "🚗 En geç bugün 14:30'da çek\n"
                               "Geç kalırsan 2 TL/dk işgaliye")

    def test_overnight_charge_points_at_the_morning(self):
        evening = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)  # 20:00 local, + 8 h = 04:00
        text = estimate_text(self.estimate(10, 90, now=evening), evening)
        self.assertIn("🚗 En geç yarın 08:30'da çek\nGece işgaliye yok; sonrası 2 TL/dk", text)

    def test_a_service_price_that_is_not_the_idle_fee_is_still_reported(self):
        text = estimate_text(self.estimate(20, 70, service=5), self.NOW)
        self.assertIn("Geç kalırsan 2 TL/dk işgaliye", text)
        self.assertIn("Not: 5 TL hizmet bedeli dahil değil.", text)

    def test_without_a_policy_the_reply_is_as_before(self):
        text = estimate_text(self.estimate(20, 70, policy=None), self.NOW)
        self.assertNotIn("çek", text)
        self.assertIn("Not: 2 TL hizmet bedeli dahil değil.", text)


if __name__ == "__main__":
    unittest.main()
