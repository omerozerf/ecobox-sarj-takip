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

Shape (`schema` 2)
------------------
Times are UTC ISO strings `YYYY-MM-DDTHH:MM:SSZ`. Weekdays are 0 = Monday .. 6 = Sunday and
hours 0..23, both local (`timezone`). Shares and probabilities are 0..1, rounded to 4 places;
minutes and days are rounded to 2. `null` means "no data", never zero.

The display scope is split into *areas*: `primary` holds the sockets a primary term
(`Config.primary_terms`, e.g. ego, f blok, dc) picks inside the scope, `secondary` the rest of
the scope. Every analysis is computed once per area, over that area's AC sockets only.
Without primary terms there is one area, `primary`, covering the whole scope. The scope's DC
connectors get an analysis of their own (`dc`), with the engine rules that hold for them.

    schema              2
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
    coverage            the whole scope's record, shaped as an area's `coverage`
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
                        {id, position, name, label, groups, kind, power_kw, price, area}
                        position: index in `connectors.json` (the live rows are positional),
                        null when the collector has not seen this connector; name: short name;
                        label: full catalog label; groups: the `groups` words that pick this
                        socket (empty for DC); kind: "AC" | "DC"; price: {energy, currency}
                        from the current tariff, or null; area: the `areas` key it belongs to
    areas               [area], primary first; each area:
      key               "primary" | "secondary"
      label             readable name, e.g. "Ego, F Blok, DC", "Arge tarafı"
      sockets           ids of its sockets (AC and DC), in `sockets` order
      coverage          {first, last, polls, observed_days, dates, ac_sockets, sockets}
                        first/last observation (null without any), polls counted, observed
                        time in days (steps longer than the gap threshold not counted),
                        distinct local dates, AC sockets the statistics cover, all sockets
      findings          [{key, emoji, text, basis}] in the engine's order (most useful first)
      missing           [{key, text}]: what a finding still waits for, one sentence each
      heatmap           {cells, weekday_hours, weekend_hours}
                        cells[weekday][hour] and *_hours[hour] are {p, polls, days} or null
                        without polls; p = share of polls on which at least one AC socket of
                        the area was free, days = distinct dates behind the cell
      ranking           [{id, name, free_share, unusable_share, samples, observed_days}]
                        sockets with at least one sample, most often free first (the bot's
                        order, `ecobox.ranking`); a socket below `min_socket_samples` is not
                        ranked by the bot yet
      durations         {count, excluded, median_min, q1_min, q3_min}: occupancy time of
                        clean sessions; excluded = sessions touching a recording gap;
                        quartiles null below two sessions
      plug_in, unplug   {count, excluded, by_hour[24]}: events whose time is certain, by
                        local hour; excluded = uncertain ones
    sites               null when no other site is recorded, else
                        {since, common_days, overall, others, loads}
                        since: start of the window every site shares; common_days: the whole
                        scope's observed days inside it; overall: the other sites' pooled busy
                        share (sites outside the display scope), null until the site
                        comparison finding's thresholds are met; others: number of other
                        sites; loads: every other site and one row per area
                        {name, sockets, busy, total, busy_share, home, area}, busiest first,
                        single-socket sites included (filter with `min_site_sockets`); an area
                        row has home true, its label as name and its key as area (an area
                        without a sample in the window has no row); other sites have home
                        false and area null.
                        Each area's own `sites` finding compares it with the same other
                        sites (never with the other area of the scope).
    dc                  null when the scope has no DC connector, else the DC connectors only
                        (added after schema 2 was published; readers of schema 2 that do
                        not know it ignore it):
                        {label, sockets, power_kw, coverage, findings, missing, heatmap,
                         durations, plug_in, unplug}
                        shaped as in an area (no `ranking`); label "DC hızlı şarj"; power_kw
                        the strongest connector's; coverage.ac_sockets is 0 and
                        coverage.sockets counts the DC connectors the figures rest on; a
                        heat-map cell's p is the share of polls on which at least one DC
                        connector was free. Findings come from `DC_RULES`: free hours
                        (weekday and weekend apart), stay time, plug-in/unplug hours,
                        reliability, week change.
"""

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ecobox import texts
from ecobox.api import EcoboxApi
from ecobox.charts.book import MIN_SITE_SOCKETS
from ecobox.clock import LOCAL_TZ, Clock, to_db
from ecobox.importer import ImportResult, SnapshotImporter
from ecobox.insights import (DEFAULT_RULES, WEEKDAYS, WEEKEND, InsightContext, InsightEngine,
                             Analysis, Missing, Rule, SiteComparison, Thresholds, arrivals_departures,
                             free_hours, occupancy_time, reliability, site_comparison,
                             site_verdict, week_change)
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

SCHEMA = 2
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


def site_load(load: SiteLoad, area: str | None = None) -> dict[str, Any]:
    return {"name": load.name, "sockets": load.sockets, "busy": load.busy, "total": load.total,
            "busy_share": _share(load.busy_share), "home": load.home, "area": area}


def sites(comparison: SiteComparison, area_loads: list[tuple[str, SiteLoad]], since: datetime,
          t: Thresholds, home_days: float) -> dict[str, Any]:
    """`comparison` is the whole scope against the other sites; `area_loads` replace the
    scope's own row in the list, one per area."""
    verdict = site_verdict(comparison, t, home_days)
    rows = [(l, None) for l in comparison.loads if not l.home] + [(l, k) for k, l in area_loads]
    rows.sort(key=lambda r: (-r[0].busy_share, r[0].name))
    return {
        "since": _time(since),
        "common_days": _amount(comparison.coverage.days),
        "overall": None if isinstance(verdict, Missing) else _share(verdict.overall),
        "others": sum(not l.home for l in comparison.loads),
        "loads": [site_load(l, k) for l, k in rows],
    }


def thresholds(t: Thresholds, gap_threshold_s: float) -> dict[str, float]:
    return {"min_hour_polls": t.min_hour_polls, "min_ranked_hours": t.min_ranked_hours,
            "min_pattern_dates": t.min_pattern_dates, "min_events": t.min_events,
            "min_sessions": t.min_sessions, "min_socket_samples": t.min_socket_samples,
            "min_site_sockets": MIN_SITE_SOCKETS, "gap_s": gap_threshold_s}


def coverage(history: History, sockets: int, ac_sockets: int | None = None) -> dict[str, Any]:
    """`ac_sockets` defaults to the connectors the history covers (AC for every area)."""
    cov = history.coverage
    return {"first": _time(cov.first), "last": _time(cov.last), "polls": cov.polls,
            "observed_days": _amount(cov.days), "dates": cov.dates,
            "ac_sockets": len(history.entries) if ac_sockets is None else ac_sockets,
            "sockets": sockets}


def figures(analysis: Analysis, history: History, cover: dict[str, Any]) -> dict[str, Any]:
    """What an area and the DC part share: coverage, findings, heat map, stays, events."""
    return {
        "coverage": cover,
        "findings": [{"key": f.key, "emoji": f.emoji, "text": f.text, "basis": f.basis}
                     for f in analysis.findings],
        "missing": [{"key": m.key, "text": m.text} for m in analysis.missing],
        "heatmap": heatmap(history.grid),
        "durations": durations(history.durations),
        "plug_in": distribution(history.plug_in),
        "unplug": distribution(history.unplug),
    }


# ----- areas --------------------------------------------------------------------------------

PRIMARY = "primary"
SECONDARY = "secondary"


def _titled(term: str) -> str:
    """"f blok" -> "F Blok", "dc" -> "DC": a word without vowels is read letter by letter."""
    def word(w: str) -> str:
        if not any(c in "aeıioöuü" for c in w.replace("I", "ı").lower()):
            return w.upper()
        return ("İ" if w[0] == "i" else w[0].upper()) + w[1:]
    return " ".join(word(w) for w in term.split())


@dataclass(frozen=True)
class Area:
    """One part of the display scope, analysed on its own."""
    key: str
    label: str
    """How the page names it: "Ego, F Blok, DC", "Arge tarafı"."""
    name: str
    """How a finding's sentence names it (takes a suffix): "Ego, F Blok ve DC", "Arge"."""
    selection: Selection
    """Its AC sockets, for the statistics."""
    members: tuple[CatalogEntry, ...]
    """Its sockets, AC and DC, in catalog order."""


def split_areas(scope: DisplayScope, primary_terms: tuple[str, ...]) -> list[Area]:
    """The display scope cut in two by the primary terms, matched inside the scope only (a
    bare "dc" would pick other sites' DC posts too). The secondary area is everything the
    terms do not pick, so the two never overlap and nothing is left out; it is omitted when
    it is empty. Without primary terms the whole scope is one primary area."""
    terms = tuple(t.strip() for t in primary_terms if t.strip())
    catalog = scope.catalog()
    if not terms:
        return [Area(PRIMARY, scope.label, scope.label, scope.selection(), tuple(catalog))]
    primary = Selection(scope_terms=scope.selection().scope_terms, location_terms=terms)
    secondary = Selection(scope_terms=primary.scope_terms, excluded_terms=terms)
    titled = [_titled(t) for t in terms]
    name = titled[0] if len(titled) == 1 else ", ".join(titled[:-1]) + " ve " + titled[-1]
    areas = [Area(PRIMARY, ", ".join(titled), name, primary,
                  tuple(replace(primary, ac_only=False).apply(catalog)))]
    rest = tuple(replace(secondary, ac_only=False).apply(catalog))
    if rest:
        name = _secondary_name(scope, {e.connector_id for e in secondary.apply(catalog)})
        areas.append(Area(SECONDARY, f"{name} tarafı", name, secondary, rest))
    return areas


def _secondary_name(scope: DisplayScope, ac_ids: set[str]) -> str:
    """The location word that picks the most of the secondary AC sockets and nothing else
    ("arge"), titled; "Diğer" when no word fits."""
    catalog = scope.catalog()
    fits = []
    for term in scope.location_terms():
        picked = {e.connector_id for e in scope.selection(term).apply(catalog)}
        if picked and picked <= ac_ids:
            fits.append((-len(picked), term))
    return _titled(min(fits)[1]) if fits else "Diğer"


def area_rules(sites: Callable[[InsightContext], SiteComparison | None], name: str
               ) -> tuple[Rule, ...]:
    """The engine's rules with the site comparison told about the area against the other
    sites only (the engine's own would count the scope's other area as a site)."""
    def area_sites(ctx: InsightContext):
        return site_comparison(replace(ctx, label=name, sites=lambda: sites(ctx)))
    return tuple(area_sites if rule is site_comparison else rule for rule in DEFAULT_RULES)


DC_LABEL = "DC hızlı şarj"
DC_RULES: tuple[Rule, ...] = (free_hours, occupancy_time, arrivals_departures, reliability,
                              week_change)
"""The engine's rules that hold for a handful of fast-charging connectors. Left out: the
socket ranking and turnover (two connectors of one post are not a choice of where to go),
arrival-time buckets and the 4-hour long-stay rule (built for AC stays of hours), and the site
comparison (its loads are AC only)."""


def dc_selection(scope: DisplayScope) -> Selection:
    """The display scope's DC connectors, whatever their names say."""
    return Selection(ac_only=False, dc_only=True, scope_terms=scope.selection().scope_terms)


# ----- sockets ------------------------------------------------------------------------------

@dataclass(frozen=True)
class SocketCatalog:
    """The display scope's sockets with what the page needs to show each one."""
    scope: DisplayScope
    storage: Storage
    index: list[str]
    """`connectors.json`: connector ids in the order the live rows are written."""
    areas: list[Area]

    def group_members(self) -> dict[str, frozenset[str]]:
        """Each location word and the connectors (AC) it picks, as the bot resolves it."""
        catalog = self.scope.catalog()
        return {term: frozenset(e.connector_id for e in self.scope.selection(term).apply(catalog))
                for term in self.scope.location_terms()}

    def rows(self) -> list[dict[str, Any]]:
        positions = {cid: i for i, cid in enumerate(self.index)}
        names = self.scope.names()
        groups = self.group_members()
        area_of = {e.connector_id: a.key for a in self.areas for e in a.members}
        return [self._row(e, positions.get(e.connector_id), names,
                          [t for t, members in groups.items() if e.connector_id in members],
                          area_of[e.connector_id])
                for e in self.scope.catalog()]

    def _row(self, entry: CatalogEntry, position: int | None, names: ShortNames,
             groups: list[str], area: str) -> dict[str, Any]:
        tariff = self.storage.current_tariff(entry.connector_id)
        return {"id": entry.connector_id, "position": position, "name": names.of(entry),
                "label": entry.label, "groups": groups, "kind": "AC" if entry.is_ac else "DC",
                "power_kw": entry.power_kw, "area": area,
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
                 thresholds: Thresholds = Thresholds(), primary_terms: tuple[str, ...] = ()):
        self._api = api
        self._storage = storage
        self._clock = clock
        self._scope_terms = scope_terms
        self._poll_interval_s = poll_interval_s
        self._gap_threshold_s = gap_threshold_s
        self._t = thresholds
        self._primary_terms = primary_terms

    def build(self, snapshots: Path, snapshot_base_url: str) -> dict[str, Any]:
        imported = self._load(snapshots)
        statistics = Statistics(self._storage, self._gap_threshold_s)
        scope = DisplayScope(self._storage, self._scope_terms)
        now = self._clock.now()
        since = now - self._t.analysis_window
        whole = statistics.history(scope.selection().window(since, None))
        areas = split_areas(scope, self._primary_terms)
        others = OtherSites(statistics, scope, since)
        index = json.loads((snapshots / INDEX_FILE).read_text())
        return {
            "schema": SCHEMA,
            "generated_at": _time(now),
            "timezone": LOCAL_TZ.key,
            "scope": scope.label,
            "snapshot_base_url": snapshot_base_url,
            "newest_snapshot": _time(imported.last_used),
            "window": {"since": _time(since), "until": _time(now)},
            "coverage": coverage(whole, len(scope.catalog())),
            "thresholds": thresholds(self._t, self._gap_threshold_s),
            "state_labels": {s: texts.STATE_TR[s] for s in ALL_STATES},
            "weekday_labels": list(texts.WEEKDAYS_SHORT_TR),
            "states": states_table(),
            "groups": scope.location_terms(),
            "sockets": SocketCatalog(scope, self._storage, index, areas).rows(),
            "areas": [self._area(a, statistics, scope.names(), others, now, since)
                      for a in areas],
            "sites": others.summary(whole, areas, self._t),
            "dc": self._dc(scope, statistics, now, since),
        }

    def _dc(self, scope: DisplayScope, statistics: Statistics, now: datetime,
            since: datetime) -> dict[str, Any] | None:
        """The DC connectors' own analysis; None when the scope has none."""
        selection = dc_selection(scope)
        members = selection.apply(scope.catalog())
        if not members:
            return None
        engine = InsightEngine(statistics, self._t, DC_RULES)
        analysis = engine.analyse(selection, DC_LABEL, scope.names(), now, since)
        history = statistics.history(selection.window(since, None))
        return {"label": DC_LABEL, "sockets": [e.connector_id for e in members],
                "power_kw": max(e.power_kw for e in members),
                **figures(analysis, history, coverage(history, len(members), ac_sockets=0))}

    def _area(self, area: Area, statistics: Statistics, names: ShortNames,
              others: "OtherSites", now: datetime, since: datetime) -> dict[str, Any]:
        engine = InsightEngine(statistics, self._t,
                               area_rules(lambda ctx: others.comparison(area, ctx.history),
                                          area.name))
        analysis = engine.analyse(area.selection, area.name, names, now, since)
        history = statistics.history(area.selection.window(since, None))
        return {
            "key": area.key,
            "label": area.label,
            "sockets": [e.connector_id for e in area.members],
            **figures(analysis, history, coverage(history, len(area.members))),
            "ranking": ranking(history.profiles, names),
        }

    def _load(self, snapshots: Path) -> ImportResult:
        Recorder(self._api, self._storage, ScopeFilter(()), self._clock,
                 self._gap_threshold_s).refresh_catalog()
        result = SnapshotImporter(self._storage, self._clock, self._poll_interval_s,
                                  self._gap_threshold_s).import_from(snapshots)
        if result.catalog_empty:
            raise SiteDataError("the Ecobox station list came back empty")
        return result


class OtherSites:
    """The sites outside the display scope, over the window every site shares; read once and
    compared with the whole scope and with each area."""

    def __init__(self, statistics: Statistics, scope: DisplayScope, since: datetime):
        self._stats = statistics
        self._scope = scope
        self.since = statistics.comparison_start(scope.selection(), since)
        self._loads = ([] if self.since is None else
                       statistics.site_loads(scope.selection(), scope.label, self.since))
        self._area_loads: dict[str, SiteLoad | None] = {}

    def area_load(self, area: Area) -> SiteLoad | None:
        """The area's own row, named by its label; None without a sample in the window."""
        if area.key not in self._area_loads:
            loads = self._stats.site_loads(area.selection, area.label, self.since)
            self._area_loads[area.key] = next((l for l in loads if l.home), None)
        return self._area_loads[area.key]

    def comparison(self, area: Area, history: History) -> SiteComparison | None:
        """What the area's `sites` finding rests on, shaped as the engine's own."""
        if self.since is None:
            return None
        load = self.area_load(area)
        rows = [l for l in self._loads if not l.home] + ([] if load is None else [load])
        return SiteComparison(rows, history.between(self.since).coverage)

    def summary(self, whole: History, areas: list[Area], t: Thresholds
                ) -> dict[str, Any] | None:
        if self.since is None:
            return None
        comparison = SiteComparison(self._loads, whole.between(self.since).coverage)
        area_loads = [(a.key, load) for a in areas if (load := self.area_load(a)) is not None]
        return sites(comparison, area_loads, self.since, t, whole.coverage.days)


def dumps(data: dict[str, Any]) -> str:
    """Compact, key-sorted JSON: the same data always gives the same bytes."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def write(data: dict[str, Any], path: Path) -> int:
    """Writes `data` to `path` (parents created); returns the size in bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = dumps(data).encode()
    path.write_bytes(encoded)
    return len(encoded)
