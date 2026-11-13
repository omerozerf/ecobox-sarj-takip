import unittest
from collections import Counter

from helpers import fixture_stations, fixture_statuses, HACETTEPE

from ecobox.scope import ScopeFilter, fold
from ecobox.state import normalise


class NormaliseTest(unittest.TestCase):
    def test_availability_table(self):
        expected = {1: "available", 2: "charging", 3: "unavailable", 4: "faulted",
                    5: "offline", 6: "maintenance"}
        for code, state in expected.items():
            # availability wins over every other field
            self.assertEqual(normalise(code, True, 1), state)

    def test_fallbacks_when_availability_unknown(self):
        for availability in (None, 0, 7):
            self.assertEqual(normalise(availability, True, 3), "maintenance")
            self.assertEqual(normalise(availability, False, 3), "available")
            self.assertEqual(normalise(availability, None, 1), "offline")
            self.assertEqual(normalise(availability, False, 2), "charging")
            self.assertEqual(normalise(availability, False, None), "charging")

    def test_fixture_distribution(self):
        counts = Counter(normalise(s.availability, s.is_under_maintenance, s.charging_state)
                         for s in fixture_statuses())
        self.assertEqual(counts, {"available": 106, "charging": 53, "offline": 7, "faulted": 2})


class ScopeTest(unittest.TestCase):
    def test_hacettepe_filter_finds_13_points_14_connectors(self):
        selected = HACETTEPE.select(fixture_stations())
        self.assertEqual(len(selected), 13)
        connectors = [c for cp in selected for c in cp.connectors]
        self.assertEqual(len(connectors), 14)
        self.assertEqual(sum(c.is_ac for c in connectors), 12)
        self.assertEqual(sum(not c.is_ac for c in connectors), 2)

    def test_typo_location_still_matches_by_display_name(self):
        names = {cp.location_name for cp in HACETTEPE.select(fixture_stations())}
        self.assertIn("Hacettpe Teknokent F Blok", names)

    def test_every_in_scope_connector_has_a_status_row(self):
        status_ids = {s.connector_id for s in fixture_statuses()}
        for cp in HACETTEPE.select(fixture_stations()):
            for c in cp.connectors:
                self.assertIn(c.id, status_ids)

    def test_empty_filter_keeps_everything(self):
        everything = ScopeFilter(()).select(fixture_stations())
        self.assertEqual(len(everything), 163)
        self.assertEqual(sum(len(cp.connectors) for cp in everything), 168)

    def test_fold_is_turkish_tolerant(self):
        self.assertEqual(fold("  HACETTEPE İ ı I "), "hacettepe i i i")
        self.assertTrue(ScopeFilter(("HACETTEPE",)).matches(fixture_stations_by_name("Hacettepe-F")))


def fixture_stations_by_name(name):
    return next(cp for cp in fixture_stations() if cp.display_name == name)


if __name__ == "__main__":
    unittest.main()
