"""The alert for a remote collector that stopped writing snapshots."""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from helpers import FakeClock, POLL_S
from test_snapshot_import import at, every, morning, raw_rows

from ecobox.collector_watch import CollectorWatch
from ecobox.digest import DigestTarget
from ecobox.importer import SnapshotImporter, SnapshotImportJob
from ecobox.snapshot import append_snapshot, newest_snapshot_time
from ecobox.snapshot_source import DirectorySource
from ecobox.storage import Storage

STALE_S = 1800


class FakeOutbox:
    command_prefix = "/"

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[int | str, str]] = []
        self.fail = fail

    def send(self, chat_id, text):
        if self.fail:
            raise RuntimeError("transport down")
        self.sent.append((chat_id, text))


class CollectorWatchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.storage = Storage(":memory:")
        self.addCleanup(self.storage.close)
        self.clock = FakeClock(at(9))
        self.outbox = FakeOutbox()
        self.chat: int | None = 42
        self.watch = CollectorWatch(self.storage, self.clock, STALE_S,
                                    [DigestTarget("telegram", self.outbox, lambda: self.chat)])
        self.snapshot(at(8), at(9))

    def snapshot(self, start, end):
        for ts in every(start, end, POLL_S):
            append_snapshot(self.directory, ts, raw_rows(morning(ts)))

    def check(self, now) -> list[str]:
        self.clock.current = now
        return self.watch.check(self.directory)

    def test_newest_snapshot_is_the_last_line_of_the_last_day_file(self):
        self.snapshot(at(9) + timedelta(days=1), at(9, 4) + timedelta(days=1))
        self.assertEqual(newest_snapshot_time(self.directory), at(9, 3) + timedelta(days=1))

    def test_a_healthy_collector_is_never_announced(self):
        self.assertEqual(self.check(at(9, 10)), [])
        self.assertEqual(self.outbox.sent, [])

    def test_stopped_collector_alerts_once_with_the_last_record(self):
        self.assertEqual(self.check(at(9, 40)), ["telegram"])
        self.assertEqual(self.check(at(10, 40)), [])
        (chat, text), = self.outbox.sent
        self.assertEqual(chat, 42)
        self.assertEqual(text.splitlines()[:2],
                         ["⚠️ GitHub toplayıcı durmuş", "Son kayıt 11:57 (43 dk önce)"])
        self.assertIn("gh workflow run collect.yml", text)

    def test_exactly_at_the_threshold_is_not_yet_stopped(self):
        self.assertEqual(self.check(at(8, 57) + timedelta(seconds=STALE_S)), [])

    def test_recovery_is_announced_once_and_a_second_stop_alerts_again(self):
        self.check(at(9, 40))
        self.snapshot(at(10), at(10, 4))
        self.assertEqual(self.check(at(10, 5)), ["telegram"])
        self.assertEqual(self.check(at(10, 8)), [])
        self.assertEqual(self.outbox.sent[-1][1],
                         "✅ GitHub toplayıcı yeniden çalışıyor\nSon kayıt 13:03")
        self.assertEqual(self.check(at(11)), ["telegram"])
        self.assertEqual(len(self.outbox.sent), 3)

    def test_failed_send_is_retried_at_the_next_check(self):
        self.outbox.fail = True
        self.assertEqual(self.check(at(9, 40)), [])
        self.outbox.fail = False
        self.assertEqual(self.check(at(9, 43)), ["telegram"])

    def test_transport_without_a_conversation_waits_and_alerts_later(self):
        self.chat = None
        self.assertEqual(self.check(at(9, 40)), [])
        self.chat = 7
        self.assertEqual(self.check(at(9, 43)), ["telegram"])

    def test_state_survives_a_restart(self):
        self.check(at(9, 40))
        again = CollectorWatch(self.storage, self.clock, STALE_S,
                               [DigestTarget("telegram", self.outbox, lambda: 42)])
        self.assertEqual(again.check(self.directory), [])


class ImportJobWatcherTest(unittest.TestCase):
    def test_job_tells_watchers_the_directory_and_survives_a_crashing_one(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name)
        append_snapshot(directory, at(8), raw_rows(morning(at(8))))
        seen = []

        def crash(_directory):
            raise RuntimeError("boom")

        storage = Storage(":memory:")
        self.addCleanup(storage.close)
        importer = SnapshotImporter(storage, FakeClock(at(9)), POLL_S, POLL_S * 2.5)
        with self.assertLogs("ecobox.importer", level="ERROR"):
            result = SnapshotImportJob(DirectorySource(directory), importer,
                                       [crash, seen.append]).run()
        self.assertEqual(seen, [directory])
        self.assertTrue(result.catalog_empty)


if __name__ == "__main__":
    unittest.main()
