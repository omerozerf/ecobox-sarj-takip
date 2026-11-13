"""Importing the remote collector's snapshots into the local history.

Every scenario is a synthetic morning with a known answer. The reference for the derived
tables is a second recorder fed the merged sample sequence in time order: after an import,
transitions and gaps must be exactly what that recorder produced.
"""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from helpers import AVAILABILITY, EGO, GAP_S, POLL_S, World, load_fixture

from ecobox import cli, snapshot
from ecobox.api import HttpEcoboxApi
from ecobox.config import Config
from ecobox.importer import CURSOR_KEY, SnapshotImporter, SnapshotImportJob
from ecobox.snapshot import INDEX_FILE, append_snapshot
from ecobox.snapshot_source import DirectorySource, GitSnapshotSource, run_git
from ecobox.storage import POLL_SNAPSHOT, POLL_STATUS

EGO_1, EGO_2 = EGO[0], EGO[1]
DAY = datetime(2026, 11, 10, tzinfo=UTC)
SNAP_S = 300


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute, second=second)


def every(start: datetime, end: datetime, step_s: int) -> list[datetime]:
    """start, start+step ... while < end."""
    times, ts = [], start
    while ts < end:
        times.append(ts)
        ts += timedelta(seconds=step_s)
    return times


def morning(ts: datetime) -> dict[str, str]:
    """Ego 1 charges 08:30–09:30 (inside the hole), Ego 2 09:58–10:20 (across its edge)."""
    states = {EGO_1: "available", EGO_2: "available"}
    if at(8, 30) <= ts < at(9, 30):
        states[EGO_1] = "charging"
    if at(9, 58) <= ts < at(10, 20):
        states[EGO_2] = "charging"
    return states


def raw_rows(states: dict[str, str], omit: frozenset[str] = frozenset()) -> list[dict]:
    """The fixture's raw status rows (all 168 connectors), scripted like `World.poll`."""
    rows = []
    for row in load_fixture("connector_status.json")["result"]:
        if row["connectorId"] in omit:
            continue
        state = states.get(row["connectorId"], "offline")
        rows.append(dict(row, availability=AVAILABILITY[state], connectorChargingState=None,
                         isUnderMaintenance=False))
    return rows


def local_polls(world: World, times: list[datetime], script) -> None:
    for ts in times:
        world.clock.current = ts
        world.poll(script(ts), default="offline")


def history(world: World) -> tuple[list, list, list]:
    ids = set(EGO) | {e.connector_id for e in world.storage.catalog()}
    samples = [(s.connector_id, s.ts, s.state) for s in world.storage.samples(ids)]
    transitions = sorted(world.storage.transitions(ids), key=lambda t: (t.connector_id, t.ts))
    gaps = sorted(world.storage.gaps(ids), key=lambda g: (g.connector_id, g.from_ts))
    return samples, transitions, gaps


def table_counts(world: World) -> dict[str, int]:
    conn = world.storage._conn
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("poll_log", "samples", "transitions", "gaps")}


class ImportCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name) / "snapshots"
        self.world = self._world()

    def _world(self) -> World:
        world = World(at(6))
        self.addCleanup(world.storage.close)
        return world

    def snapshots(self, times: list[datetime], script, omit: frozenset[str] = frozenset()):
        for ts in times:
            append_snapshot(self.directory, ts, raw_rows(script(ts), omit))

    def importer(self, world: World | None = None) -> SnapshotImporter:
        world = world or self.world
        return SnapshotImporter(world.storage, world.clock, POLL_S, GAP_S)

    def run_import(self, now: datetime, world: World | None = None):
        world = world or self.world
        world.clock.current = now
        return self.importer(world).import_from(self.directory)

    def reference(self, times: list[datetime], script) -> World:
        """A recorder that saw `times` (local and used snapshot times merged) in order."""
        world = self._world()
        local_polls(world, sorted(times), script)
        return world


LOCAL = every(at(7), at(8), POLL_S) + every(at(10), at(11), POLL_S)
SNAPSHOTS = every(at(7), at(10, 31), SNAP_S)  # 07:00 … 10:30
USED = every(at(8), at(10), SNAP_S)           # 08:00 … 09:55: the hole minus its edges


class HoleTest(ImportCase):
    def test_hole_is_filled_and_history_matches_the_merged_recorder(self):
        local_polls(self.world, LOCAL, morning)
        self.snapshots(SNAPSHOTS, morning)
        before = history(self.world)
        self.assertEqual(len([g for g in before[2] if g.connector_id == EGO_1]), 1)

        result = self.run_import(at(12))

        self.assertEqual((result.read, result.used, result.skipped_local), (43, 24, 19))
        self.assertEqual((result.first_used, result.last_used), (at(8), at(9, 55)))
        self.assertEqual(result.samples, 24 * 14)
        self.assertEqual(history(self.world), history(self.reference(LOCAL + USED, morning)))
        samples, transitions, gaps = history(self.world)
        self.assertEqual(gaps, [])  # 07:57 → 08:00 and 09:55 → 10:00 are both short
        ego1 = [(t.ts, t.new_state, t.uncertain) for t in transitions
                if t.connector_id == EGO_1 and t.prev_state is not None]
        self.assertEqual(ego1, [(at(8, 30), "charging", False),
                                (at(9, 30), "available", False)])
        # Ego 2 plugged in at 09:58, between the last snapshot (09:55) and the Mac waking.
        ego2 = next(t for t in transitions if t.connector_id == EGO_2 and t.prev_state)
        self.assertEqual((ego2.ts, ego2.prev_ts, ego2.uncertain), (at(10), at(9, 55), False))

    def test_import_before_the_mac_polls_again_then_recording_continues(self):
        local_polls(self.world, every(at(7), at(8), POLL_S), morning)
        self.snapshots(every(at(7), at(10), SNAP_S), morning)
        result = self.run_import(at(10))  # settled up to 09:52:30
        self.assertEqual((result.used, result.deferred), (23, 1))  # 09:55 is not settled
        local_polls(self.world, every(at(10), at(11), POLL_S), morning)
        used = every(at(8), at(9, 51), SNAP_S)
        self.assertEqual(history(self.world),
                         history(self.reference(LOCAL + used, morning)))

    def test_gap_in_the_snapshots_is_still_an_honest_gap(self):
        skipped_cron = [ts for ts in SNAPSHOTS if not at(9) <= ts <= at(9, 40)]
        local_polls(self.world, LOCAL, morning)
        self.snapshots(skipped_cron, morning)
        self.run_import(at(12))
        used = [ts for ts in USED if ts in skipped_cron]
        self.assertEqual(history(self.world), history(self.reference(LOCAL + used, morning)))
        _, transitions, gaps = history(self.world)
        self.assertIn((EGO_1, at(8, 55), at(9, 45)),
                      [(g.connector_id, g.from_ts, g.to_ts) for g in gaps])
        unplug = next(t for t in transitions
                      if t.connector_id == EGO_1 and t.new_state == "available" and t.prev_state)
        self.assertEqual((unplug.ts, unplug.prev_ts, unplug.uncertain),
                         (at(9, 45), at(8, 55), True))

    def test_snapshots_before_the_first_local_poll_move_the_first_observation(self):
        local_polls(self.world, every(at(9), at(10), POLL_S), morning)
        self.snapshots(every(at(7), at(9), SNAP_S), morning)
        self.run_import(at(12))
        merged = every(at(7), at(8, 59), SNAP_S) + every(at(9), at(10), POLL_S)
        self.assertEqual(history(self.world), history(self.reference(merged, morning)))
        first = [t for t in history(self.world)[1] if t.prev_state is None]
        self.assertEqual({t.ts for t in first}, {at(7)})


class OverlapTest(ImportCase):
    def test_local_samples_win_where_both_sources_observed(self):
        def disagreeing(ts):  # the remote collector claims Ego 1 is charging all morning
            return {EGO_1: "charging", EGO_2: "available"}

        local_polls(self.world, every(at(7), at(9), POLL_S), morning)
        self.snapshots(every(at(7), at(9), SNAP_S), disagreeing)
        before = history(self.world)
        result = self.run_import(at(12))
        self.assertEqual((result.read, result.used, result.skipped_local), (24, 0, 24))
        self.assertEqual(history(self.world), before)
        self.assertEqual(self.world.storage.ok_poll_times(POLL_SNAPSHOT), [])

    def test_delayed_local_polls_still_cover_the_time_between_them(self):
        # Backoff spaced two polls 420 s apart (below the 450 s threshold): a snapshot 210 s
        # from both is not near either, but the Mac still counts that stretch as observed.
        local_polls(self.world, [at(7), at(7, 7)], morning)
        self.snapshots([at(7, 3, 30)], morning)
        result = self.run_import(at(12))
        self.assertEqual((result.used, result.skipped_local), (0, 1))


class IdempotenceTest(ImportCase):
    def test_second_run_changes_nothing_even_without_the_progress_marker(self):
        local_polls(self.world, LOCAL, morning)
        self.snapshots(SNAPSHOTS, morning)
        self.run_import(at(12))
        counts, snapshot_history = table_counts(self.world), history(self.world)

        again = self.run_import(at(12, 30))
        self.assertEqual((again.read, again.used), (0, 0))
        self.assertEqual(table_counts(self.world), counts)

        with self.world.storage.transaction():
            self.world.storage._conn.execute("DELETE FROM service_state WHERE key=?",
                                             (CURSOR_KEY,))
        lost = self.run_import(at(13))
        self.assertEqual((lost.read, lost.used, lost.skipped_imported), (43, 0, 24))
        self.assertEqual(table_counts(self.world), counts)
        self.assertEqual(history(self.world), snapshot_history)

    def test_progress_is_incremental(self):
        local_polls(self.world, every(at(7), at(8), POLL_S), morning)
        self.snapshots(every(at(7), at(9), SNAP_S), morning)
        first = self.run_import(at(9, 30))
        self.snapshots(every(at(9), at(9, 30), SNAP_S), morning)
        second = self.run_import(at(10))
        self.assertEqual((first.read, second.read), (24, 6))
        self.assertEqual(self.world.storage.get_state(CURSOR_KEY), "2026-11-10T09:25:00Z")

    def test_recent_snapshots_wait_until_they_settle(self):
        self.snapshots([at(9), at(9, 5)], morning)
        early = self.run_import(at(9, 10))  # 09:05 is younger than the 450 s threshold
        self.assertEqual((early.used, early.deferred), (1, 1))
        later = self.run_import(at(9, 20))
        self.assertEqual((later.read, later.used, later.deferred), (1, 1, 0))


class SnapshotShapeTest(ImportCase):
    def test_short_line_and_null_entries_only_sample_what_was_present(self):
        # 07:00 was written before Ego 2 was in the index (shorter line); at 07:05 Ego 1 was
        # missing from the response (null entry).
        append_snapshot(self.directory, at(7), [r for r in raw_rows(morning(at(7)))
                                                 if r["connectorId"] != EGO_2])
        append_snapshot(self.directory, at(7, 5), raw_rows(morning(at(7, 5)), {EGO_1}))
        index = json.loads((self.directory / INDEX_FILE).read_text())
        self.assertEqual(index[-1], EGO_2)
        line = (self.directory / "2026-11-10.jsonl").read_text().splitlines()
        self.assertEqual(len(json.loads(line[0])["r"]), len(index) - 1)
        self.assertIsNone(json.loads(line[1])["r"][index.index(EGO_1)])

        self.run_import(at(12))
        sampled = {(s.connector_id, s.ts) for s in self.world.storage.samples({EGO_1, EGO_2})}
        self.assertEqual(sampled, {(EGO_1, at(7)), (EGO_2, at(7, 5))})

    def test_connectors_unknown_to_the_local_catalog_are_skipped(self):
        self.snapshots([at(7)], morning)
        result = self.run_import(at(12))
        self.assertEqual(len(result.unknown_connectors), 168 - 14)
        self.assertEqual(result.samples, 14)
        catalog = {e.connector_id for e in self.world.storage.catalog()}
        self.assertTrue(result.unknown_connectors.isdisjoint(catalog))

    def test_snapshot_without_a_known_connector_is_not_an_observation(self):
        hacettepe = frozenset(e.connector_id for e in self.world.storage.catalog())
        self.snapshots([at(7)], morning, omit=hacettepe)
        result = self.run_import(at(12))
        self.assertEqual((result.used, result.skipped_empty), (0, 1))
        self.assertEqual(self.world.storage.ok_poll_times(POLL_SNAPSHOT), [])

    def test_empty_catalog_keeps_everything_for_later(self):
        from ecobox.storage import Storage
        empty = Storage(":memory:")
        self.addCleanup(empty.close)
        self.snapshots([at(7)], morning)
        result = SnapshotImporter(empty, self.world.clock, POLL_S, GAP_S).import_from(
            self.directory)
        self.assertTrue(result.catalog_empty)
        self.assertIsNone(empty.get_state(CURSOR_KEY))


class SourceAndCoverageTest(ImportCase):
    def test_imported_polls_are_reported_per_source(self):
        local_polls(self.world, LOCAL, morning)
        self.snapshots(SNAPSHOTS, morning)
        self.run_import(at(12))
        self.assertEqual(len(self.world.storage.ok_poll_times(POLL_STATUS)), len(LOCAL))
        self.assertEqual(self.world.storage.ok_poll_times(POLL_SNAPSHOT), USED)
        self.assertEqual(self.world.storage.poll_counts(), (len(LOCAL), 0))

    def test_writers_refuse_to_run_outside_a_transaction(self):
        with self.assertRaises(RuntimeError):
            self.world.storage.set_state_in_tx("k", "v", at(7))

    def test_job_refreshes_the_source_then_imports(self):
        self.snapshots([at(7)], morning)
        job = SnapshotImportJob(DirectorySource(self.directory), self.importer())
        self.world.clock.current = at(12)
        self.assertEqual(job.run().used, 1)


class GitSourceTest(unittest.TestCase):
    def test_clones_once_then_fetches_by_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "remote"
            calls = []

            def git(args):
                calls.append(args)
                if args[0] == "clone":
                    (checkout / ".git").mkdir(parents=True)

            source = GitSnapshotSource("https://example.invalid/r.git", "data", checkout, git)
            self.assertEqual(source.refresh(), checkout / "snapshots")
            source.refresh()
        self.assertEqual(calls[0][:1] + calls[0][-4:],
                         ["clone", "--branch", "data", "https://example.invalid/r.git",
                          str(checkout)])
        self.assertIn("--depth", calls[0])
        self.assertEqual(calls[1][2:], ["fetch", "--quiet", "--depth", "1",
                                        "https://example.invalid/r.git", "data"])
        self.assertEqual(calls[2][2:], ["reset", "--quiet", "--hard", "FETCH_HEAD"])

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_real_git_against_a_local_repository(self):
        """No network: the 'remote' is a repository in a temporary directory."""
        with tempfile.TemporaryDirectory() as tmp:
            origin = Path(tmp) / "origin"

            def commit(ts):
                append_snapshot(origin / "snapshots", ts, raw_rows(morning(ts)))
                for args in (["add", "snapshots"], ["commit", "-q", "-m", "snapshot"]):
                    subprocess.run(["git", "-C", str(origin), "-c", "user.name=t",
                                    "-c", "user.email=t@example.invalid",
                                    "-c", "commit.gpgsign=false", *args], check=True)

            subprocess.run(["git", "init", "-q", "-b", "data", str(origin)], check=True)
            commit(at(7))
            source = GitSnapshotSource(origin.as_uri(), "data", Path(tmp) / "remote", run_git)
            self.assertEqual(len(list(snapshot.read_snapshots(source.refresh()))), 1)
            commit(at(7, 5))
            self.assertEqual(len(list(snapshot.read_snapshots(source.refresh()))), 2)


class CliTest(ImportCase):
    def test_empty_url_disables_the_feature(self):
        config = Config.load(Path("/nonexistent/.env"),
                             {"ECOBOX_SNAPSHOT_REPO_URL": "", "ECOBOX_DB_PATH": "/tmp/x.db"})
        self.assertIsNone(config.snapshot_repo_url)
        self.assertIsNone(cli._git_source(config))
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.cmd_import_snapshots(Namespace(dir=None), config, self.world.storage)
        self.assertEqual(code, 0)
        self.assertIn("ECOBOX_SNAPSHOT_REPO_URL boş", out.getvalue())
        self.assertEqual(self.world.storage.ok_poll_times(POLL_SNAPSHOT), [])

    def test_url_from_env_enables_git(self):
        config = Config.load(Path("/nonexistent/.env"),
                             {"ECOBOX_SNAPSHOT_REPO_URL": "https://example.invalid/r.git"})
        self.assertIsInstance(cli._git_source(config), GitSnapshotSource)

    def test_command_prints_the_summary(self):
        local_polls(self.world, LOCAL, morning)
        self.snapshots(SNAPSHOTS, morning)
        config = Config.load(Path("/nonexistent/.env"), {"ECOBOX_DB_PATH": "/tmp/x.db"})
        out = io.StringIO()
        with mock.patch.object(cli, "SystemClock", lambda: self.world.clock), \
                redirect_stdout(out):
            self.world.clock.current = at(12)
            cli.cmd_import_snapshots(Namespace(dir=str(self.directory)), config,
                                     self.world.storage)
        text = out.getvalue()
        self.assertIn("43 okundu, 24 kullanıldı (336 örnek)", text)
        self.assertIn("19 yerel kayıt varken", text)
        self.assertIn("Doldurulan aralık: 10 Kasım 11:00 – 10 Kasım 12:55", text)
        self.assertIn("· GitHub: 24 okuma", text)
        self.assertEqual(
            cli.build_parser().parse_args(["import-snapshots", "--dir", "x"]).dir, "x")


class RawStatusAccessorTest(unittest.TestCase):
    def test_snapshot_collector_uses_the_public_raw_rows(self):
        rows = load_fixture("connector_status.json")["result"]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(HttpEcoboxApi, "_request",
                                  return_value={"result": rows}) as request, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(HttpEcoboxApi(5).fetch_status_rows(), rows)
            snapshot.main(["snapshot", tmp])
            self.assertEqual(len(json.loads((Path(tmp) / INDEX_FILE).read_text())), 168)
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
