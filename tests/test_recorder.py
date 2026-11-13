import logging
import unittest
from dataclasses import replace
from datetime import timedelta

from helpers import EGO, GAP_S, POLL_S, World

from ecobox.stats import Selection, Statistics


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.storage.close)

    def test_catalog_holds_only_scope(self):
        catalog = self.world.storage.catalog()
        self.assertEqual(len(catalog), 14)

    def test_first_poll_samples_every_in_scope_connector(self):
        result = self.world.poll()
        self.assertEqual(result.sample_count, 14)
        self.assertEqual(result.missing_connectors, set())
        # first observation of each connector: recorded, flagged uncertain
        self.assertEqual(len(result.transitions), 14)
        self.assertTrue(all(t.prev_state is None and t.uncertain for t in result.transitions))

    def test_scripted_sequence_emits_transitions_only_on_change(self):
        w = self.world
        ego1 = EGO[0]
        script = ["charging", "charging", "available", "available", "faulted", "charging"]
        emitted = []
        for state in script:
            emitted.append(w.poll({ego1: state}).transitions)
            w.clock.advance(POLL_S)
        changes = [[(t.prev_state, t.new_state) for t in ts if t.connector_id == ego1]
                   for ts in emitted[1:]]
        self.assertEqual(changes, [[], [("charging", "available")], [],
                                   [("available", "faulted")], [("faulted", "charging")]])
        self.assertTrue(all(not t.uncertain for ts in emitted[1:] for t in ts))
        # the other 13 connectors kept their fixture state: no transitions after the first poll
        self.assertTrue(all(t.connector_id == ego1 for ts in emitted[1:] for t in ts))
        samples = w.storage.samples({ego1})
        self.assertEqual([s.state for s in samples], script)
        self.assertEqual(w.storage.poll_counts(), (6, 0))

    def test_poll_error_is_logged_and_raised(self):
        self.world.api.fail_status = True
        with self.assertRaises(TimeoutError):
            self.world.poll()
        self.assertEqual(self.world.storage.poll_counts(), (0, 1))

    def test_missing_connector_is_reported_not_sampled(self):
        w = self.world
        w.api.statuses = [s for s in w.api.statuses if s.connector_id != EGO[1]]
        result = w.recorder.poll_once()
        self.assertEqual(result.sample_count, 13)
        self.assertEqual(result.missing_connectors, {EGO[1]})

    def test_listener_failure_does_not_stop_recording(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        w = self.world
        received = []

        def broken(_):
            raise RuntimeError("bot crashed")

        w.recorder.add_listener(broken)
        w.recorder.add_listener(received.append)
        w.poll()
        w.clock.advance(POLL_S)
        w.poll({EGO[0]: "available"})
        self.assertEqual(w.storage.poll_counts(), (2, 0))
        self.assertEqual(len(received), 2)

    def test_tariff_changes_are_kept_as_history(self):
        w = self.world
        cp = next(cp for cp in w.api.stations if cp.connectors[0].id == EGO[0])
        old = cp.connectors[0].tariff
        new_connector = replace(cp.connectors[0], tariff=replace(old, energy_price=10.5))
        index = w.api.stations.index(cp)
        w.api.stations[index] = replace(cp, connectors=(new_connector,))
        w.clock.advance(3600)
        w.recorder.refresh_catalog()
        w.clock.advance(3600)
        w.recorder.refresh_catalog()  # unchanged: extends last_seen, no new row
        history = w.storage.tariff_history(EGO[0])
        self.assertEqual([t.energy_price for t, _, _ in history], [9.29, 10.5])
        self.assertEqual(history[1][2], w.clock.now())  # last_seen extended
        self.assertEqual(history[1][1], w.clock.now() - timedelta(hours=1))
        self.assertEqual(w.storage.current_tariff(EGO[0]).energy_price, 10.5)


class GapTest(unittest.TestCase):
    def test_gap_marks_transition_uncertain_and_is_excluded_from_timing(self):
        w = World()
        self.addCleanup(w.storage.close)
        ego1 = EGO[0]
        w.poll({ego1: "available"})
        w.clock.advance(POLL_S)
        w.poll({ego1: "charging"})              # clean plug-in
        w.clock.advance(POLL_S * 10)            # Mac asleep: 30 min without samples
        result = w.poll({ego1: "available"})    # unplug happened somewhere in the gap
        t = next(t for t in result.transitions if t.connector_id == ego1)
        self.assertTrue(t.uncertain)
        self.assertEqual(t.prev_ts, w.clock.now() - timedelta(seconds=POLL_S * 10))
        self.assertEqual(len([g for g in result.gaps if g.connector_id == ego1]), 1)
        self.assertEqual(len(result.gaps), 14)  # every connector shares the gap

        stats = Statistics(w.storage, GAP_S)
        only = Selection(connector_id=ego1)
        self.assertEqual(stats.plug_in(only).count, 1)
        unplug = stats.unplug(only)
        self.assertEqual((unplug.count, unplug.excluded), (0, 1))
        durations = stats.durations(only)
        self.assertEqual((durations.count, durations.excluded), (0, 1))

    def test_gap_below_threshold_is_not_a_gap(self):
        w = World()
        self.addCleanup(w.storage.close)
        w.poll({EGO[0]: "available"})
        w.clock.advance(GAP_S - 1)
        result = w.poll({EGO[0]: "charging"})
        self.assertEqual(result.gaps, [])
        self.assertFalse(next(t for t in result.transitions if t.connector_id == EGO[0]).uncertain)


if __name__ == "__main__":
    unittest.main()
