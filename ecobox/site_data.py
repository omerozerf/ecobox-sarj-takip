"""The public page's `data.json`: every figure the static site shows, computed by the bot's code.

Runs where there is no `.env` and no database (a GitHub Actions runner): the station list comes
from the public Ecobox API, the history from the collector's snapshot directory (the `data`
branch), imported into a throwaway `Storage`. Every snapshot that has settled (older than the
gap threshold at build time) is used, since there are no local polls to prefer.

The page never re-derives a number: findings, heat map, ranking and distributions come from
`InsightEngine`, `History` and `Statistics`, over the same window `analiz` reads
(`Thresholds.analysis_window` back from the build time). Figures resting on too little data
stay visible as such: each carries its sample count, and the thresholds are included.

Output is deterministic for the same snapshots, station list and clock: keys are sorted, lists
are in a fixed order, and the only clock-dependent values derive from `generated_at`.

Shape (`schema` 1)
------------------
Times are UTC ISO strings `YYYY-MM-DDTHH:MM:SSZ`. Weekdays are 0 = Monday .. 6 = Sunday and
hours 0..23, both local (`timezone`). Shares and probabilities are 0..1, rounded to 4 places;
minutes and days are rounded to 2. `null` means "no data", never zero.

    schema              1
    generated_at        build time (also the "now" of every relative window)
    timezone            "Europe/Istanbul"
    scope               display scope label, e.g. "Hacettepe"
    snapshot_base_url   passed in verbatim; the page fetches `connectors.json` and
                        `YYYY-MM-DD.jsonl` (UTC day) from there for the live status. In a
                        line `{"t", "r"}`, `r[position]` is `[availability, chargingState,
                        maintenance]`; it is null when the connector was missing from that
                        poll, and `r` may be shorter than the index (connectors appended
                        later): treat both as "no reading" (see `ecobox.snapshot`)
    newest_snapshot     time of the newest snapshot imported, or null when none was
    window              {since, until}: the history every figure below rests on
                        (`until` = generated_at)
    coverage            {first, last, polls, observed_days, dates, ac_sockets, sockets}
                        first/last observation (null without any), polls counted, observed
                        time in days (steps longer than the gap threshold not counted),
                        distinct local dates, AC sockets the statistics cover, all sockets
    thresholds          {min_hour_polls, min_ranked_hours, min_pattern_dates, min_events,
                         min_sessions, min_socket_samples, min_site_sockets, gap_s}
                        a heat-map cell with fewer than `min_hour_polls` polls is thin; note
                        the engine applies it to an hour pooled over weekdays (or the
                        weekend), which is what `heatmap.weekday_hours`/`weekend_hours` hold;
                        the bot's site chart leaves out sites with fewer than
                        `min_site_sockets` AC sockets (they swing between 0 and 100 %);
                        gap_s: two readings further apart than this many seconds leave the
                        time in between unknown (the page's "since when" stops there)
    state_labels        {state: Turkish label} for the six normalised states
    weekday_labels      ["Pzt", ..., "Paz"]
    states              {"<availability>,<chargingState>,<maintenance>": state}
                        every combination of availability null/0..6, chargingState null/0..5,
                        maintenance 0/1 (112 keys), mapped by `ecobox.state.normalise`. null
                        is written as the empty string: `[null, 3, 0]` -> ",3,0". A live row
                        `[a, c, m]` from the snapshot file is looked up as `${a ?? ""},${c ??
                        ""},${m}`; a key outside the table is a new API value (show unknown)
    groups              location words (AC only) a person would type, e.g. ["arge", "ego"]
    sockets             every connector in the display scope, AC and DC, in catalog order:
                        {id, position, name, label, groups, kind, power_kw, price}
                        position: index in `connectors.json` (the live rows are positional),
                        null when the collector has not seen this connector; name: short name;
                        label: full catalog label; groups: the `groups` words that pick this
                        socket (empty for DC); kind: "AC" | "DC"; price: {energy, currency}
                        from the current tariff, or null
    findings            [{key, emoji, text, basis}] in the engine's order (most useful first)
    missing             [{key, text}]: what a finding still waits for, one sentence each
    heatmap             {cells, weekday_hours, weekend_hours}
                        cells[weekday][hour] and *_hours[hour] are {p, polls, days} or null
                        without polls; p = share of polls on which at least one AC socket of
                        the scope was free, days = distinct dates behind the cell
    ranking             [{id, name, free_share, unusable_share, samples, observed_days}]
                        sockets with at least one sample, most often free first (the bot's
                        order, `ecobox.ranking`); a socket below `min_socket_samples` is not
                        ranked by the bot yet
    durations           {count, excluded, median_min, q1_min, q3_min}: occupancy time of
                        clean sessions; excluded = sessions touching a recording gap;
                        quartiles null below two sessions
    plug_in, unplug     {count, excluded, by_hour[24]}: events whose time is certain, by
                        local hour; excluded = uncertain ones
    sites               null when no other site is recorded, else
                        {since, common_days, home, overall, others, loads}
                        since: start of the window every site shares; common_days: the scope's
                        observed days inside it; home: the scope's load, null when the scope
                        has no sample in that window; overall: the other
                        sites' pooled busy share, null until the site comparison finding's
                        thresholds are met; others: number of other sites; loads: every site
                        {name, sockets, busy, total, busy_share, home}, busiest first,
                        single-socket sites included (filter with `min_site_sockets`)
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ecobox import texts
from ecobox.api import EcoboxApi
from ecobox.charts.book import MIN_SITE_SOCKETS
from ecobox.clock import LOCAL_TZ, Clock, to_db
from ecobox.importer import ImportResult, SnapshotImporter
from ecobox.insights import (WEEKDAYS, WEEKEND, InsightEngine, Missing, SiteComparison,
                             Thresholds, site_verdict)
from ecobox.labels import ShortNames
from ecobox.ranking import by_free_share
from ecobox.recorder import Recorder
from ecobox.records import CatalogEntry
from ecobox.scope import ScopeFilter
from ecobox.snapshot import INDEX_FILE
from ecobox.state import ALL_STATES, normalise
from ecobox.stats import (ConnectorProfile, DurationSummary, FreeCell, FreeGrid, History,
                          Selection, SiteLoad, Statistics, TimeDistribution, hour_profile)
from ecobox.storage import Storage
from ecobox.view import DisplayScope

SCHEMA = 1
AVAILABILITY_DOMAIN = (None, *range(7))
CHARGING_STATE_DOMAIN = (None, *range(6))
MAINTENANCE_DOMAIN = (0, 1)


class SiteDataError(RuntimeError):
    pass


# ----- small serialisers --------------------------------------------------------------------

def _share(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _amount(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _time(moment: datetime | None) -> str | None:
    return None if moment is None else to_db(moment)


def state_key(availability: int | None, charging_state: int | None, maintenance: int) -> str:
    """The `states` key of one raw snapshot row; null is the empty string."""
    return ",".join("" if v is None else str(v) for v in (availability, charging_state,
                                                           maintenance))


def states_table() -> dict[str, str]:
    """`normalise` over the whole raw domain, so the page never re-implements it."""
    return {state_key(a, c, m): normalise(a, bool(m), c)
            for a in AVAILABILITY_DOMAIN for c in CHARGING_STATE_DOMAIN
            for m in MAINTENANCE_DOMAIN}


def cell(c: FreeCell) -> dict[str, Any] | None:
    if not c.total:
        return None
    return {"p": _share(c.probability), "polls": c.total, "days": c.days}


def heatmap(grid: FreeGrid) -> dict[str, Any]:
    return {
        "cells": [[cell(grid.cell(wd, hour)) for hour in range(24)] for wd in range(7)],
        "weekday_hours": [cell(c) for c in hour_profile(grid, set(WEEKDAYS))],
        "weekend_hours": [cell(c) for c in hour_profile(grid, set(WEEKEND))],
    }


def distribution(d: TimeDistribution) -> dict[str, Any]:
    return {"count": d.count, "excluded": d.excluded, "by_hour": list(d.by_hour)}


def durations(d: DurationSummary) -> dict[str, Any]:
    return {"count": d.count, "excluded": d.excluded, "median_min": _amount(d.median_min),
            "q1_min": _amount(d.q1_min), "q3_min": _amount(d.q3_min)}


def ranking(profiles: list[ConnectorProfile], names: ShortNames) -> list[dict[str, Any]]:
    seen = [p for p in profiles if p.total]
    ranked = by_free_share(seen, lambda p: p.free_share, lambda p: names.of(p.entry))
    return [{"id": p.entry.connector_id, "name": names.of(p.entry),
             "free_share": _share(p.free_share), "unusable_share": _share(p.unusable_share),
             "samples": p.total, "observed_days": _amount(p.days)} for p in ranked]


def site_load(load: SiteLoad) -> dict[str, Any]:
    return {"name": load.name, "sockets": load.sockets, "busy": load.busy, "total": load.total,
            "busy_share": _share(load.busy_share), "home": load.home}


def sites(comparison: SiteComparison, since: datetime, t: Thresholds,
          home_days: float) -> dict[str, Any]:
    verdict = site_verdict(comparison, t, home_days)
    loads = sorted(comparison.loads, key=lambda l: (-l.busy_share, l.name))
    home = next((l for l in loads if l.home), None)
    return {
        "since": _time(since),
        "common_days": _amount(comparison.coverage.days),
        "home": None if home is None else site_load(home),
        "overall": None if isinstance(verdict, Missing) else _share(verdict.overall),
        "others": sum(not l.home for l in loads),
        "loads": [site_load(l) for l in loads],
    }


def thresholds(t: Thresholds, gap_threshold_s: float) -> dict[str, float]:
    return {"min_hour_polls": t.min_hour_polls, "min_ranked_hours": t.min_ranked_hours,
            "min_pattern_dates": t.min_pattern_dates, "min_events": t.min_events,
            "min_sessions": t.min_sessions, "min_socket_samples": t.min_socket_samples,
            "min_site_sockets": MIN_SITE_SOCKETS, "gap_s": gap_threshold_s}


# ----- sockets ------------------------------------------------------------------------------

@dataclass(frozen=True)
class SocketCatalog:
    """The display scope's sockets with what the page needs to show each one."""
    scope: DisplayScope
    storage: Storage
    index: list[str]
    """`connectors.json`: connector ids in the order the live rows are written."""

    def group_members(self) -> dict[str, frozenset[str]]:
        """Each location word and the connectors (AC) it picks, as the bot resolves it."""
        catalog = self.scope.catalog()
        return {term: frozenset(e.connector_id for e in self.scope.selection(term).apply(catalog))
                for term in self.scope.location_terms()}

    def rows(self) -> list[dict[str, Any]]:
        positions = {cid: i for i, cid in enumerate(self.index)}
        names = self.scope.names()
        groups = self.group_members()
        return [self._row(e, positions.get(e.connector_id), names,
                          [t for t, members in groups.items() if e.connector_id in members])
                for e in self.scope.catalog()]

    def _row(self, entry: CatalogEntry, position: int | None, names: ShortNames,
             groups: list[str]) -> dict[str, Any]:
        tariff = self.storage.current_tariff(entry.connector_id)
        return {"id": entry.connector_id, "position": position, "name": names.of(entry),
                "label": entry.label, "groups": groups, "kind": "AC" if entry.is_ac else "DC",
                "power_kw": entry.power_kw,
                "price": None if tariff is None else {"energy": tariff.energy_price,
                                                      "currency": tariff.currency}}


# ----- builder ------------------------------------------------------------------------------

class SiteDataBuilder:
    """Loads the catalog and the snapshots into `storage`, then reads every figure back.

    `storage` should be empty and throwaway (`:memory:`): the catalog is recorded for every
    Ecobox site, because the site comparison needs the others.
    """

    def __init__(self, api: EcoboxApi, storage: Storage, clock: Clock,
                 scope_terms: tuple[str, ...], poll_interval_s: float, gap_threshold_s: float,
                 thresholds: Thresholds = Thresholds()):
        self._api = api
        self._storage = storage
        self._clock = clock
        self._scope_terms = scope_terms
        self._poll_interval_s = poll_interval_s
        self._gap_threshold_s = gap_threshold_s
        self._t = thresholds

    def build(self, snapshots: Path, snapshot_base_url: str) -> dict[str, Any]:
        imported = self._load(snapshots)
        statistics = Statistics(self._storage, self._gap_threshold_s)
        scope = DisplayScope(self._storage, self._scope_terms)
        engine = InsightEngine(statistics, self._t)
        now = self._clock.now()
        since = now - self._t.analysis_window
        home = scope.selection()
        names = scope.names()
        analysis = engine.analyse(home, scope.label, names, now, since)
        history = statistics.history(home.window(since, None))
        index = json.loads((snapshots / INDEX_FILE).read_text())
        return {
            "schema": SCHEMA,
            "generated_at": _time(now),
            "timezone": LOCAL_TZ.key,
            "scope": scope.label,
            "snapshot_base_url": snapshot_base_url,
            "newest_snapshot": _time(imported.last_used),
            "window": {"since": _time(since), "until": _time(now)},
            "coverage": self._coverage(history, scope),
            "thresholds": thresholds(self._t, self._gap_threshold_s),
            "state_labels": {s: texts.STATE_TR[s] for s in ALL_STATES},
            "weekday_labels": list(texts.WEEKDAYS_SHORT_TR),
            "states": states_table(),
            "groups": scope.location_terms(),
            "sockets": SocketCatalog(scope, self._storage, index).rows(),
            "findings": [{"key": f.key, "emoji": f.emoji, "text": f.text, "basis": f.basis}
                         for f in analysis.findings],
            "missing": [{"key": m.key, "text": m.text} for m in analysis.missing],
            "heatmap": heatmap(history.grid),
            "ranking": ranking(history.profiles, names),
            "durations": durations(history.durations),
            "plug_in": distribution(history.plug_in),
            "unplug": distribution(history.unplug),
            "sites": self._sites(statistics, home, scope.label, history, since),
        }

    def _load(self, snapshots: Path) -> ImportResult:
        Recorder(self._api, self._storage, ScopeFilter(()), self._clock,
                 self._gap_threshold_s).refresh_catalog()
        result = SnapshotImporter(self._storage, self._clock, self._poll_interval_s,
                                  self._gap_threshold_s).import_from(snapshots)
        if result.catalog_empty:
            raise SiteDataError("the Ecobox station list came back empty")
        return result

    @staticmethod
    def _coverage(history: History, scope: DisplayScope) -> dict[str, Any]:
        cov = history.coverage
        return {"first": _time(cov.first), "last": _time(cov.last), "polls": cov.polls,
                "observed_days": _amount(cov.days), "dates": cov.dates,
                "ac_sockets": len(history.entries), "sockets": len(scope.catalog())}

    def _sites(self, statistics: Statistics, home: Selection, label: str, history: History,
               since: datetime) -> dict[str, Any] | None:
        """The comparison exactly as `InsightEngine` builds it for the `analiz` finding."""
        common = statistics.comparison_start(home, since)
        if common is None:
            return None
        comparison = SiteComparison(statistics.site_loads(home, label, common),
                                    history.between(common).coverage)
        return sites(comparison, common, self._t, history.coverage.days)


def dumps(data: dict[str, Any]) -> str:
    """Compact, key-sorted JSON: the same data always gives the same bytes."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def write(data: dict[str, Any], path: Path) -> int:
    """Writes `data` to `path` (parents created); returns the size in bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = dumps(data).encode()
    path.write_bytes(encoded)
    return len(encoded)
