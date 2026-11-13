"""Statistics over the recorded history.

Hour and weekday buckets are Europe/Istanbul. Every result carries the number of
observations it rests on; uncertain transitions (gap before them) and unclean sessions are
excluded from the timing figures and counted separately so the caller can say so.
Occupancy figures count samples, i.e. only moments that were actually observed.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ecobox.clock import local_bucket, to_local
from ecobox.records import CatalogEntry, Sample, Transition
from ecobox.scope import fold
from ecobox.sessions import Session, derive_sessions
from ecobox.state import AVAILABLE, CHARGING
from ecobox.storage import Storage


@dataclass(frozen=True)
class Selection:
    """Which connectors a statistic covers. Default: every AC connector in scope."""

    location_term: str | None = None
    connector_id: str | None = None
    ac_only: bool = True

    def apply(self, catalog: list[CatalogEntry]) -> list[CatalogEntry]:
        chosen = []
        for entry in catalog:
            if self.ac_only and not entry.is_ac:
                continue
            if self.connector_id is not None and entry.connector_id != self.connector_id:
                continue
            if self.location_term is not None and fold(self.location_term) not in fold(
                    f"{entry.location_name} {entry.display_name}"):
                continue
            chosen.append(entry)
        return chosen


@dataclass(frozen=True)
class TimeDistribution:
    count: int
    excluded: int
    by_hour: list[int]
    by_weekday: list[int]

    def peak_hours(self, top: int = 3) -> list[tuple[int, int]]:
        ranked = sorted(((n, h) for h, n in enumerate(self.by_hour) if n), reverse=True)
        return [(h, n) for n, h in ranked[:top]]


@dataclass(frozen=True)
class DurationSummary:
    count: int
    excluded: int
    median_min: float | None
    q1_min: float | None
    q3_min: float | None


@dataclass(frozen=True)
class ConnectorShare:
    entry: CatalogEntry
    available: int
    total: int

    @property
    def share(self) -> float | None:
        return self.available / self.total if self.total else None


@dataclass(frozen=True)
class FreeCell:
    free: int
    total: int
    days: int

    @property
    def probability(self) -> float | None:
        return self.free / self.total if self.total else None


@dataclass(frozen=True)
class FreeGrid:
    cells: dict[tuple[int, int], FreeCell] = field(default_factory=dict)

    def cell(self, weekday: int, hour: int) -> FreeCell:
        return self.cells.get((weekday, hour), FreeCell(0, 0, 0))


@dataclass(frozen=True)
class WaitSummary:
    days_observed: int
    """Days with an observation near this time of day."""
    days_all_busy: int
    """Of those, days on which every selected socket was busy then."""
    count: int
    """Busy days whose wait could be measured (no gap, freed up within the data)."""
    median_min: float | None
    q1_min: float | None
    q3_min: float | None


@dataclass(frozen=True)
class PollView:
    ts: datetime
    any_free: bool


def _quartiles(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    if len(values) == 1:
        return values[0], None, None
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return median, q1, q3


def time_distribution(transitions: list[Transition], plug_in: bool) -> TimeDistribution:
    by_hour, by_weekday = [0] * 24, [0] * 7
    count = excluded = 0
    for t in transitions:
        if t.prev_state is None:
            if plug_in and t.new_state == CHARGING:
                excluded += 1
            continue
        relevant = (t.new_state == CHARGING and t.prev_state != CHARGING) if plug_in else (
            t.prev_state == CHARGING and t.new_state != CHARGING)
        if not relevant:
            continue
        if t.uncertain:
            excluded += 1
            continue
        weekday, hour = local_bucket(t.ts)
        by_hour[hour] += 1
        by_weekday[weekday] += 1
        count += 1
    return TimeDistribution(count, excluded, by_hour, by_weekday)


def duration_summary(sessions: list[Session]) -> DurationSummary:
    closed = [s for s in sessions if not s.is_open]
    clean = [s.duration.total_seconds() / 60 for s in closed if s.is_clean]
    median, q1, q3 = _quartiles(sorted(clean))
    return DurationSummary(len(clean), len(closed) - len(clean), median, q1, q3)


def connector_shares(entries: list[CatalogEntry], samples: list[Sample]) -> list[ConnectorShare]:
    available: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for s in samples:
        total[s.connector_id] += 1
        available[s.connector_id] += s.state == AVAILABLE
    return [ConnectorShare(e, available[e.connector_id], total[e.connector_id]) for e in entries]


def poll_views(samples: list[Sample]) -> list[PollView]:
    """One view per poll: did any selected connector show available?"""
    by_poll: dict[int, list[Sample]] = defaultdict(list)
    for s in samples:
        by_poll[s.poll_id].append(s)
    views = [PollView(rows[0].ts, any(r.state == AVAILABLE for r in rows))
             for rows in by_poll.values()]
    views.sort(key=lambda v: v.ts)
    return views


def free_grid(polls: list[PollView]) -> FreeGrid:
    free: dict[tuple[int, int], int] = defaultdict(int)
    total: dict[tuple[int, int], int] = defaultdict(int)
    days: dict[tuple[int, int], set[date]] = defaultdict(set)
    for p in polls:
        key = local_bucket(p.ts)
        total[key] += 1
        free[key] += p.any_free
        days[key].add(to_local(p.ts).date())
    return FreeGrid({k: FreeCell(free[k], total[k], len(days[k])) for k in total})


def wait_until_free(polls: list[PollView], now: datetime, gap_threshold: timedelta,
                    window: timedelta = timedelta(minutes=30)) -> WaitSummary:
    """For each past day, take the observation nearest to now's time of day (within window);
    if every socket was busy then, measure how long until one was free.

    A wait that runs into a recording gap, or never ends within the data, is not counted.
    """
    local_now = to_local(now)
    target = timedelta(hours=local_now.hour, minutes=local_now.minute, seconds=local_now.second)
    nearest: dict[date, tuple[timedelta, int]] = {}
    for index, p in enumerate(polls):
        local = to_local(p.ts)
        if local.date() >= local_now.date():
            continue
        offset = abs(timedelta(hours=local.hour, minutes=local.minute,
                               seconds=local.second) - target)
        offset = min(offset, timedelta(days=1) - offset)  # 23:50 is 20 min from 00:10
        if offset > window:
            continue
        best = nearest.get(local.date())
        if best is None or offset < best[0]:
            nearest[local.date()] = (offset, index)

    waits: list[float] = []
    busy_days = 0
    for _, index in nearest.values():
        if polls[index].any_free:
            continue
        busy_days += 1
        for later in range(index + 1, len(polls)):
            if polls[later].ts - polls[later - 1].ts > gap_threshold:
                break
            if polls[later].any_free:
                waits.append((polls[later].ts - polls[index].ts).total_seconds() / 60)
                break
    median, q1, q3 = _quartiles(sorted(waits))
    return WaitSummary(len(nearest), busy_days, len(waits), median, q1, q3)


class Statistics:
    """Reads the history from storage and answers the questions above."""

    def __init__(self, storage: Storage, gap_threshold_s: float):
        self._storage = storage
        self._gap_threshold = timedelta(seconds=gap_threshold_s)

    def entries(self, selection: Selection) -> list[CatalogEntry]:
        return selection.apply(self._storage.catalog())

    def _ids(self, selection: Selection) -> set[str]:
        return {e.connector_id for e in self.entries(selection)}

    def plug_in(self, selection: Selection) -> TimeDistribution:
        return time_distribution(self._storage.transitions(self._ids(selection)), plug_in=True)

    def unplug(self, selection: Selection) -> TimeDistribution:
        return time_distribution(self._storage.transitions(self._ids(selection)), plug_in=False)

    def sessions(self, selection: Selection) -> list[Session]:
        ids = self._ids(selection)
        return derive_sessions(self._storage.transitions(ids), self._storage.gaps(ids))

    def durations(self, selection: Selection) -> DurationSummary:
        return duration_summary(self.sessions(selection))

    def availability(self, selection: Selection) -> list[ConnectorShare]:
        entries = self.entries(selection)
        ids = {e.connector_id for e in entries}
        return connector_shares(entries, self._storage.samples(ids))

    def polls(self, selection: Selection) -> list[PollView]:
        return poll_views(self._storage.samples(self._ids(selection)))

    def free_probability(self, selection: Selection) -> FreeGrid:
        return free_grid(self.polls(selection))

    def wait_until_free(self, selection: Selection, now: datetime) -> WaitSummary:
        return wait_until_free(self.polls(selection), now, self._gap_threshold)
