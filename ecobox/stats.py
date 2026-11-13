"""Statistics over the recorded history.

Hour and weekday buckets are Europe/Istanbul. Every result carries the number of
observations it rests on; uncertain transitions (gap before them) and unclean sessions are
excluded from the timing figures and counted separately so the caller can say so.
Occupancy figures count samples, i.e. only moments that were actually observed.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import cached_property

from ecobox.clock import local_bucket, to_local
from ecobox.records import CatalogEntry, Gap, Sample, Transition
from ecobox.scope import ScopeFilter, fold
from ecobox.sessions import Session, derive_sessions
from ecobox.state import AVAILABLE, CHARGING, FAULTED, MAINTENANCE, OFFLINE, UNAVAILABLE
from ecobox.storage import Storage

UNUSABLE_STATES = (FAULTED, OFFLINE, MAINTENANCE, UNAVAILABLE)


@dataclass(frozen=True)
class Selection:
    """Which connectors (and which time window) a statistic covers.

    Default: every AC connector recorded, whole history. `scope_terms` restricts to a scope
    (the display scope, e.g. Hacettepe); `since`/`until` bound the samples, transitions and
    gaps read.
    """

    location_term: str | None = None
    connector_id: str | None = None
    ac_only: bool = True
    scope_terms: tuple[str, ...] = ()
    since: datetime | None = None
    until: datetime | None = None

    def window(self, since: datetime | None, until: datetime | None) -> "Selection":
        return replace(self, since=since, until=until)

    def apply(self, catalog: list[CatalogEntry]) -> list[CatalogEntry]:
        scope = ScopeFilter(self.scope_terms)
        chosen = []
        for entry in catalog:
            if self.ac_only and not entry.is_ac:
                continue
            if not scope.matches_names(entry.display_name, entry.location_name):
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

    def _transitions(self, selection: Selection) -> list[Transition]:
        return self._storage.transitions(self._ids(selection), selection.since,
                                         selection.until)

    def _samples(self, ids: set[str], selection: Selection) -> list[Sample]:
        return self._storage.samples(ids, selection.since, selection.until)

    def plug_in(self, selection: Selection) -> TimeDistribution:
        return time_distribution(self._transitions(selection), plug_in=True)

    def unplug(self, selection: Selection) -> TimeDistribution:
        return time_distribution(self._transitions(selection), plug_in=False)

    def sessions(self, selection: Selection) -> list[Session]:
        ids = self._ids(selection)
        return derive_sessions(self._transitions(selection),
                               self._storage.gaps(ids, selection.since, selection.until))

    def durations(self, selection: Selection) -> DurationSummary:
        return duration_summary(self.sessions(selection))

    def availability(self, selection: Selection) -> list[ConnectorShare]:
        entries = self.entries(selection)
        ids = {e.connector_id for e in entries}
        return connector_shares(entries, self._samples(ids, selection))

    def polls(self, selection: Selection) -> list[PollView]:
        return poll_views(self._samples(self._ids(selection), selection))

    @property
    def gap_threshold(self) -> timedelta:
        return self._gap_threshold

    def history(self, selection: Selection) -> "History":
        """Everything the selection covers, read once; the analysis derives from it in memory."""
        entries = self.entries(selection)
        ids = {e.connector_id for e in entries}
        return History(
            entries=tuple(entries),
            samples=tuple(self._samples(ids, selection)),
            transitions=tuple(self._storage.transitions(ids, selection.since,
                                                        selection.until)),
            gaps=tuple(self._storage.gaps(ids, selection.since, selection.until)),
            gap_threshold=self._gap_threshold)

    def first_sample_of(self, connector_ids: set[str]) -> datetime | None:
        return self._storage.first_sample_time(connector_ids)

    def comparison_start(self, home: Selection, since: datetime) -> datetime | None:
        """Start of the window shared by the home selection and every other recorded site:
        other sites may have been recorded for less time. None when there are no others."""
        home_ids = self._ids(home)
        other_ids = self._ids(Selection(ac_only=True)) - home_ids
        first = self._storage.first_sample_time(other_ids)
        return None if first is None else max(since, first)

    def site_loads(self, home: Selection, home_label: str,
                   since: datetime | None) -> list["SiteLoad"]:
        """Busy share of every recorded site's AC sockets; the home selection is one site.

        Other sites are grouped by their location name. Aggregated in SQL, so it stays cheap
        with every Ecobox site recorded.
        """
        everything = Selection(ac_only=True).apply(self._storage.catalog())
        home_ids = self._ids(home)
        counts = self._storage.state_counts({e.connector_id for e in everything}, since)
        groups: dict[str, list[CatalogEntry]] = defaultdict(list)
        for entry in everything:
            name = home_label if entry.connector_id in home_ids else entry.location_name.strip()
            groups[name].append(entry)
        loads = []
        for name, entries in groups.items():
            per_state = [counts.get(e.connector_id, {}) for e in entries]
            total = sum(sum(c.values()) for c in per_state)
            if not total:
                continue
            loads.append(SiteLoad(name, len(entries), sum(c.get(CHARGING, 0) for c in per_state),
                                  total, name == home_label))
        return loads

    def free_probability(self, selection: Selection) -> FreeGrid:
        return free_grid(self.polls(selection))

    def wait_until_free(self, selection: Selection, now: datetime) -> WaitSummary:
        return wait_until_free(self.polls(selection), now, self._gap_threshold)


# ----- analysis building blocks -------------------------------------------------------------

@dataclass(frozen=True)
class Coverage:
    """How much observation a result rests on."""
    polls: int
    observed: timedelta
    """Time between consecutive polls, counting only steps no longer than the gap threshold."""
    first: datetime | None
    last: datetime | None
    dates: int = 0
    """Distinct local calendar dates with at least one poll."""

    @property
    def days(self) -> float:
        return self.observed.total_seconds() / 86400


def coverage(times: list[datetime], gap_threshold: timedelta) -> Coverage:
    times = sorted(times)
    observed = sum((b - a for a, b in zip(times, times[1:]) if b - a <= gap_threshold),
                   timedelta(0))
    return Coverage(len(times), observed, times[0] if times else None,
                    times[-1] if times else None, len({to_local(t).date() for t in times}))


def hour_profile(grid: FreeGrid, weekdays: set[int]) -> list[FreeCell]:
    """Free chance by local hour, pooled over the given weekdays."""
    profile = []
    for hour in range(24):
        cells = [grid.cell(wd, hour) for wd in weekdays]
        profile.append(FreeCell(sum(c.free for c in cells), sum(c.total for c in cells),
                                sum(c.days for c in cells)))
    return profile


@dataclass(frozen=True)
class ArrivalBucket:
    label: str
    start_hour: int
    end_hour: int
    """Exclusive; may be smaller than start_hour when the bucket wraps midnight."""

    def contains(self, hour: int) -> bool:
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour


ARRIVAL_BUCKETS = (
    ArrivalBucket("sabah", 6, 11),
    ArrivalBucket("öğle", 11, 14),
    ArrivalBucket("öğleden sonra", 14, 18),
    ArrivalBucket("akşam ve gece", 18, 6),
)


def durations_by_arrival(sessions: list[Session],
                         buckets: tuple[ArrivalBucket, ...] = ARRIVAL_BUCKETS
                         ) -> list[tuple[ArrivalBucket, DurationSummary]]:
    """Clean-session durations grouped by the local hour the session started."""
    result = []
    for bucket in buckets:
        inside = [s for s in sessions if bucket.contains(to_local(s.start).hour)]
        result.append((bucket, duration_summary(inside)))
    return result


@dataclass(frozen=True)
class ConnectorProfile:
    entry: CatalogEntry
    counts: dict[str, int]
    """Samples per normalised state."""
    observed: timedelta
    dates: int
    """Distinct local dates this socket was observed on."""
    plug_ins: int
    """Plug-ins whose time is certain."""
    clean_minutes: tuple[float, ...]
    """Occupancy times of this socket's clean sessions."""

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def share(self, *states: str) -> float:
        return sum(self.counts.get(s, 0) for s in states) / self.total if self.total else 0.0

    @property
    def free_share(self) -> float:
        return self.share(AVAILABLE)

    @property
    def unusable_share(self) -> float:
        return self.share(*UNUSABLE_STATES)

    @property
    def main_unusable_state(self) -> str | None:
        ranked = sorted(((self.counts.get(s, 0), s) for s in UNUSABLE_STATES), reverse=True)
        return ranked[0][1] if ranked[0][0] else None

    @property
    def days(self) -> float:
        return self.observed.total_seconds() / 86400

    @property
    def plug_ins_per_day(self) -> float:
        """Per calendar date observed; plug-ins while nothing was recorded are not known."""
        return self.plug_ins / self.dates if self.dates else 0.0


@dataclass(frozen=True)
class SiteLoad:
    name: str
    sockets: int
    busy: int
    total: int
    home: bool

    @property
    def busy_share(self) -> float:
        return self.busy / self.total


@dataclass(frozen=True)
class History:
    """One selection's raw history, read once and derived in memory."""
    entries: tuple[CatalogEntry, ...]
    samples: tuple[Sample, ...]
    transitions: tuple[Transition, ...]
    gaps: tuple[Gap, ...]
    gap_threshold: timedelta

    def between(self, since: datetime, until: datetime | None = None) -> "History":
        """The same history cut to [since, until) (no upper bound when until is None); gaps
        reaching into the window stay."""
        def inside(ts: datetime) -> bool:
            return since <= ts and (until is None or ts < until)
        return History(
            self.entries,
            tuple(s for s in self.samples if inside(s.ts)),
            tuple(t for t in self.transitions if inside(t.ts)),
            tuple(g for g in self.gaps if g.to_ts > since and (until is None
                                                               or g.from_ts < until)),
            self.gap_threshold)

    @cached_property
    def polls(self) -> list[PollView]:
        return poll_views(list(self.samples))

    @cached_property
    def coverage(self) -> Coverage:
        return coverage([p.ts for p in self.polls], self.gap_threshold)

    @cached_property
    def grid(self) -> FreeGrid:
        return free_grid(self.polls)

    @cached_property
    def sessions(self) -> list[Session]:
        return derive_sessions(list(self.transitions), list(self.gaps))

    @cached_property
    def plug_in(self) -> TimeDistribution:
        return time_distribution(list(self.transitions), plug_in=True)

    @cached_property
    def unplug(self) -> TimeDistribution:
        return time_distribution(list(self.transitions), plug_in=False)

    @cached_property
    def durations(self) -> DurationSummary:
        return duration_summary(self.sessions)

    @cached_property
    def profiles(self) -> list[ConnectorProfile]:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        times: dict[str, list[datetime]] = defaultdict(list)
        for s in self.samples:
            counts[s.connector_id][s.state] += 1
            times[s.connector_id].append(s.ts)
        plug_ins: dict[str, int] = defaultdict(int)
        for t in self.transitions:
            if (t.prev_state is not None and not t.uncertain and t.new_state == CHARGING
                    and t.prev_state != CHARGING):
                plug_ins[t.connector_id] += 1
        clean: dict[str, list[float]] = defaultdict(list)
        for session in self.sessions:
            if session.is_clean:
                clean[session.connector_id].append(session.duration.total_seconds() / 60)
        profiles = []
        for e in self.entries:
            seen = coverage(times[e.connector_id], self.gap_threshold)
            profiles.append(ConnectorProfile(e, dict(counts[e.connector_id]), seen.observed,
                                             seen.dates, plug_ins[e.connector_id],
                                             tuple(clean[e.connector_id])))
        return profiles

    @property
    def free_share(self) -> float | None:
        """Share of polls on which at least one selected socket was free."""
        polls = self.polls
        return sum(p.any_free for p in polls) / len(polls) if polls else None
