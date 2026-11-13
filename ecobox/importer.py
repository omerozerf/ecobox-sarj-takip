"""Merges the remote collector's snapshots into the database where this machine saw nothing.

Import rule
-----------
A snapshot taken at `t` is skipped ("the Mac saw it") when either

- a successful local status poll lies within half a poll interval of `t` (90 s by default), or
- `t` falls between two consecutive successful local polls at most `gap_threshold_s` apart
  (450 s by default), i.e. inside a stretch the recorder itself counts as observed.

Otherwise it is used. While the Mac is awake it polls every `poll_interval_s`, so every
snapshot is within half an interval of a local poll: local samples always win and the two
sources never interleave, even when local polls are delayed by a failure backoff. Half an
interval (not a whole one) keeps the step from the last local poll to the first snapshot of a
hole below the gap threshold with a 5-minute collector (90 s + 300 s < 450 s), so filling a
hole does not itself create a gap at its edges.

A snapshot is imported only once it has settled: older than `gap_threshold_s`. A newer one
could still get a local poll next to it (the recorder dates a poll when it starts it), and
the honesty rule needs the whole neighbourhood before deciding about a gap.

Connectors the snapshot knows but the local catalog does not are skipped, exactly as the
recorder skips status rows outside its record scope: the catalog decides what is recorded.

After inserting samples into the past, the transitions and gaps around them are replayed with
the recorder's own rule (`ecobox.sequence`), from the sample before the first inserted one
through the first sample after the last, so the derived tables read as if the recorder had
seen the merged sequence in order.

Progress is kept in `service_state` (the newest settled snapshot considered), so each run
only reads what is new; a snapshot already stored as a 'snapshot' poll is never imported
twice even if that cursor is lost.
"""

import logging
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ecobox.clock import Clock, from_db, to_db
from ecobox.models import ConnectorStatus
from ecobox.records import CatalogEntry
from ecobox.sequence import replay
from ecobox.snapshot import RawRow, read_snapshots
from ecobox.snapshot_source import SnapshotSource
from ecobox.state import normalise
from ecobox.storage import POLL_SNAPSHOT, POLL_STATUS, SampleWrite, Storage

log = logging.getLogger(__name__)

CURSOR_KEY = "snapshot_import_cursor"


@dataclass
class ImportResult:
    read: int = 0
    """Snapshots newer than the stored progress."""
    used: int = 0
    skipped_local: int = 0
    """A local poll was close enough: the Mac saw this moment itself."""
    skipped_imported: int = 0
    skipped_empty: int = 0
    """No row for a connector in the local catalog."""
    deferred: int = 0
    """Too recent to settle; read again next run."""
    samples: int = 0
    unknown_connectors: set[str] = field(default_factory=set)
    first_used: datetime | None = None
    last_used: datetime | None = None
    catalog_empty: bool = False

    def note_used(self, ts: datetime, sample_count: int) -> None:
        self.used += 1
        self.samples += sample_count
        self.first_used = ts if self.first_used is None else min(self.first_used, ts)
        self.last_used = ts if self.last_used is None else max(self.last_used, ts)


@dataclass(frozen=True)
class _Snapshot:
    ts: datetime
    rows_total: int
    samples: list[SampleWrite]


class SnapshotImporter:
    def __init__(self, storage: Storage, clock: Clock, poll_interval_s: float,
                 gap_threshold_s: float):
        self._storage = storage
        self._clock = clock
        self._tolerance = timedelta(seconds=poll_interval_s / 2)
        self._gap_threshold = timedelta(seconds=gap_threshold_s)

    def import_from(self, directory: Path) -> ImportResult:
        result = ImportResult()
        catalog = {entry.connector_id: entry for entry in self._storage.catalog()}
        if not catalog:  # nothing is recorded yet; keep the snapshots for a later run
            result.catalog_empty = True
            return result
        stored_cursor = self._storage.get_state(CURSOR_KEY)
        cursor = None if stored_cursor is None else from_db(stored_cursor)
        horizon = self._clock.now() - self._gap_threshold
        local = self._storage.ok_poll_times(POLL_STATUS)
        imported = set(self._storage.ok_poll_times(POLL_SNAPSHOT))

        snapshots = read_snapshots(directory, None if cursor is None else cursor.date())
        for day in _by_utc_day(s for s in snapshots if cursor is None or s[0] > cursor):
            batch: list[_Snapshot] = []
            settled: datetime | None = None
            for ts, rows in day:
                result.read += 1
                if ts > horizon:
                    result.deferred += 1
                    continue
                settled = ts
                if ts in imported:
                    result.skipped_imported += 1
                elif self._near_local(local, ts):
                    result.skipped_local += 1
                else:
                    snapshot = _snapshot(ts, rows, catalog, result.unknown_connectors)
                    if snapshot.samples:
                        batch.append(snapshot)
                    else:
                        result.skipped_empty += 1
            if settled is not None:
                self._commit(batch, settled)
                for snapshot in batch:
                    result.note_used(snapshot.ts, len(snapshot.samples))
        return result

    def _near_local(self, local: list[datetime], ts: datetime) -> bool:
        i = bisect_left(local, ts)
        before = local[i - 1] if i > 0 else None
        after = local[i] if i < len(local) else None
        if before is not None and ts - before <= self._tolerance:
            return True
        if after is not None and after - ts <= self._tolerance:
            return True
        return (before is not None and after is not None
                and after - before <= self._gap_threshold)

    def _commit(self, batch: list[_Snapshot], cursor: datetime) -> None:
        """One transaction per UTC day: samples, the replayed history, then the progress."""
        spans: dict[str, tuple[datetime, datetime]] = {}
        with self._storage.transaction():
            for snapshot in batch:
                self._storage.add_snapshot_poll_in_tx(snapshot.ts, snapshot.rows_total,
                                                      snapshot.samples)
                for sample in snapshot.samples:
                    cid = sample.status.connector_id
                    first, last = spans.get(cid, (snapshot.ts, snapshot.ts))
                    spans[cid] = (min(first, snapshot.ts), max(last, snapshot.ts))
            for cid, (first, last) in spans.items():
                window = self._storage.sample_window_in_tx(cid, first, last)
                transitions, gaps = replay(cid, window.anchor, window.samples,
                                           self._gap_threshold)
                self._storage.replace_derived_in_tx(window, transitions, gaps)
            self._storage.set_state_in_tx(CURSOR_KEY, to_db(cursor), self._clock.now())


def _by_utc_day(snapshots: Iterable[tuple[datetime, list[RawRow]]]
                ) -> Iterable[list[tuple[datetime, list[RawRow]]]]:
    day: list[tuple[datetime, list[RawRow]]] = []
    for snapshot in snapshots:
        if day and snapshot[0].date() != day[0][0].date():
            yield day
            day = []
        day.append(snapshot)
    if day:
        yield day


def _snapshot(ts: datetime, rows: list[RawRow], catalog: dict[str, CatalogEntry],
              unknown: set[str]) -> _Snapshot:
    samples = []
    for row in rows:
        entry = catalog.get(row["connectorId"])
        if entry is None:
            unknown.add(row["connectorId"])
            continue
        status = ConnectorStatus(entry.charge_point_id, entry.connector_id,
                                 entry.connector_order, row["availability"],
                                 row["connectorChargingState"], None,
                                 row["isUnderMaintenance"])
        samples.append(SampleWrite(status, normalise(status.availability,
                                                     status.is_under_maintenance,
                                                     status.charging_state)))
    return _Snapshot(ts, len(rows), samples)


class SnapshotImportJob:
    """Refresh the source, then import: one step of the service's hourly loop or the CLI."""

    def __init__(self, source: SnapshotSource, importer: SnapshotImporter):
        self._source = source
        self._importer = importer

    def run(self) -> ImportResult:
        result = self._importer.import_from(self._source.refresh())
        log.info("snapshot import: %d read, %d used (%d samples), %d local, %d already, "
                 "%d empty, %d deferred, %d unknown connectors", result.read, result.used,
                 result.samples, result.skipped_local, result.skipped_imported,
                 result.skipped_empty, result.deferred, len(result.unknown_connectors))
        return result
