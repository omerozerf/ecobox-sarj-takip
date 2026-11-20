"""The public page's `data.json`, built from snapshots exactly as the GitHub runner builds it.

The scenario is two office days of the synthetic history (`synthetic.office_states`), written
as collector snapshots every 5 minutes: Monday 2 November 2026 07:40–19:00 and Tuesday
07:00–19:00 local. Monday starts at 07:40 so its 07:00 hour holds four polls (a thin cell);
on both days no socket is free 09:00–10:00, and nights and the weekend have no data at all.

The collector's `connectors.json` is written in reverse id order (so positions differ from
both catalog and id order) and never sees the first Hacettepe DC connector. F Blok 2 comes
without a tariff in the station list.

The scope is split as the page splits it (`Config.primary_terms`): Ego, F Blok and DC are the
primary area, Arge and 3. Arge Otopark (all four in the synthetic `REST`) the secondary one.
`FigureTest` builds without a split, so its figures cover the whole scope as before.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from helpers import (AVAILABILITY, EGO, F_BLOK_2, GAP_S, HACETTEPE_TERMS, POLL_S, FakeApi,
                     FakeClock, connector_id, fixture_stations, load_fixture)
from synthetic import REST, office_states

from ecobox import cli, site_data
from ecobox.clock import LOCAL_TZ
from ecobox.config import Config
from ecobox.insights import Thresholds
from ecobox.ranking import by_free_share
from ecobox.snapshot import INDEX_FILE, append_snapshot
from ecobox.state import normalise
from ecobox.stats import Selection, Statistics
from ecobox.storage import Storage
from ecobox.view import DisplayScope

EGO_1, EGO_2, EGO_3, EGO_4 = EGO
DC_1 = connector_id("Hacettepe-DC", 1)
DC_2 = connector_id("Hacettepe-DC", 2)
DAYS = ((datetime(2026, 11, 2, 7, 40, tzinfo=LOCAL_TZ), datetime(2026, 11, 2, 19, tzinfo=LOCAL_TZ)),
        (datetime(2026, 11, 3, 7, tzinfo=LOCAL_TZ), datetime(2026, 11, 3, 19, tzinfo=LOCAL_TZ)))
STEP = timedelta(minutes=5)
NOW = datetime(2026, 11, 3, 20, tzinfo=LOCAL_TZ).astimezone(UTC)
BASE_URL = "https://raw.example.test/owner/repo/data/snapshots"

PRIMARY_TERMS = Config.primary_terms
SECONDARY_IDS = {connector_id(name) for name in (
    "Hacettepe 3. Arge Otopark - 1", "Hacettepe 3. Arge Otopark - 2",
    "Hacettepe 3. Arge Otopark - 3", "Hacettepe-Arge")}

TOP_KEYS = {"schema", "generated_at", "timezone", "scope", "snapshot_base_url",
            "newest_snapshot", "window", "coverage", "thresholds", "state_labels",
            "weekday_labels", "states", "groups", "sockets", "areas", "sites", "dc"}
DC_KEYS = {"label", "sockets", "power_kw", "coverage", "findings", "missing", "heatmap",
           "durations", "plug_in", "unplug"}
AREA_KEYS = {"key", "label", "sockets", "coverage", "findings", "missing", "heatmap", "ranking",
             "durations", "plug_in", "unplug"}
SOCKET_KEYS = {"id", "position", "name", "label", "groups", "kind", "power_kw", "price", "area"}
COVERAGE_KEYS = {"first", "last", "polls", "observed_days", "dates", "ac_sockets", "sockets"}
CELL_KEYS = {"p", "polls", "days"}
LOAD_KEYS = {"name", "sockets", "busy", "total", "busy_share", "home", "area"}


def snapshot_times() -> list[datetime]:
    times = []
    for start, end in DAYS:
        ts = start
        while ts < end:
            times.append(ts.astimezone(UTC))
            ts += STEP
    return times


def raw_rows(ts: datetime) -> list[dict]:
    """Every fixture connector, Hacettepe scripted by the office pattern, the rest offline;
    DC 1 is never in the response."""
    states = office_states(ts)
    return [dict(row, availability=AVAILABILITY[states.get(row["connectorId"], "offline")],
                 connectorChargingState=None, isUnderMaintenance=False)
            for row in load_fixture("connector_status.json")["result"]
            if row["connectorId"] != DC_1]


def write_snapshots(directory: Path) -> list[str]:
    """The snapshot directory; returns the index it wrote."""
    directory.mkdir(parents=True)
    index = sorted({row["connectorId"] for row in raw_rows(NOW)}, reverse=True)
    (directory / INDEX_FILE).write_text(json.dumps(index))
    for ts in snapshot_times():
        append_snapshot(directory, ts, raw_rows(ts))
    return index


def station_api() -> FakeApi:
    """The fixture station list with F Blok 2's tariff removed."""
    api = FakeApi()
    api.stations = [replace(cp, connectors=tuple(
        replace(c, tariff=None) if c.id == F_BLOK_2 else c for c in cp.connectors))
        for cp in fixture_stations()]
    return api


def build(directory: Path, storage: Storage, primary_terms: tuple[str, ...] = PRIMARY_TERMS
          ) -> dict:
    builder = site_data.SiteDataBuilder(station_api(), storage, FakeClock(NOW), HACETTEPE_TERMS,
                                        POLL_S, GAP_S, primary_terms=primary_terms)
    return builder.build(directory, BASE_URL)


class SiteDataCase(unittest.TestCase):
    primary_terms = PRIMARY_TERMS

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.tmp.name) / "snapshots"
        cls.index = write_snapshots(cls.directory)
        cls.storage = Storage(":memory:")
        cls.data = build(cls.directory, cls.storage, cls.primary_terms)

    @classmethod
    def tearDownClass(cls):
        cls.storage.close()
        cls.tmp.cleanup()

    def socket(self, cid: str) -> dict:
        return next(s for s in self.data["sockets"] if s["id"] == cid)

    def area(self, key: str) -> dict:
        return next(a for a in self.data["areas"] if a["key"] == key)


class ShapeTest(SiteDataCase):
    def test_top_level_keys_are_exactly_the_documented_ones(self):
        self.assertEqual(set(self.data), TOP_KEYS)
        self.assertEqual(self.data["schema"], 2)
        self.assertEqual(self.data["timezone"], "Europe/Istanbul")
        self.assertEqual(self.data["scope"], "Hacettepe")
        self.assertEqual(self.data["snapshot_base_url"], BASE_URL)

    def test_nested_objects_carry_no_extra_keys(self):
        d = self.data
        self.assertEqual(set(d["window"]), {"since", "until"})
        self.assertEqual(set(d["coverage"]), COVERAGE_KEYS)
        self.assertEqual(set(d["thresholds"]), {"min_hour_polls", "min_ranked_hours",
                                                "min_pattern_dates", "min_events",
                                                "min_sessions", "min_socket_samples",
                                                "min_site_sockets", "gap_s"})
        for socket in d["sockets"]:
            self.assertEqual(set(socket), SOCKET_KEYS)
            if socket["price"] is not None:
                self.assertEqual(set(socket["price"]), {"energy", "currency"})
        self.assertEqual([a["key"] for a in d["areas"]], ["primary", "secondary"])
        for area in d["areas"]:
            self.assertEqual(set(area), AREA_KEYS)
            self.assertEqual(set(area["coverage"]), COVERAGE_KEYS)
            self.assertEqual(set(area["heatmap"]), {"cells", "weekday_hours", "weekend_hours"})
            self.assertEqual(set(area["durations"]), {"count", "excluded", "median_min",
                                                      "q1_min", "q3_min"})
            for key in ("plug_in", "unplug"):
                self.assertEqual(set(area[key]), {"count", "excluded", "by_hour"})
            for finding in area["findings"]:
                self.assertEqual(set(finding), {"key", "emoji", "text", "basis"})
            for missing in area["missing"]:
                self.assertEqual(set(missing), {"key", "text"})
            for item in area["ranking"]:
                self.assertEqual(set(item), {"id", "name", "free_share", "unusable_share",
                                             "samples", "observed_days"})
            cells = [c for row in area["heatmap"]["cells"] for c in row]
            cells += area["heatmap"]["weekday_hours"] + area["heatmap"]["weekend_hours"]
            for c in cells:
                if c is not None:
                    self.assertEqual(set(c), CELL_KEYS)
        self.assertEqual(set(d["sites"]), {"since", "common_days", "overall", "others", "loads"})
        for load in d["sites"]["loads"]:
            self.assertEqual(set(load), LOAD_KEYS)

    def test_times_are_utc_and_follow_the_clock_and_the_snapshots(self):
        d = self.data
        self.assertEqual(d["generated_at"], "2026-11-03T17:00:00Z")
        self.assertEqual(d["window"], {"since": "2026-09-08T17:00:00Z",
                                       "until": "2026-11-03T17:00:00Z"})
        self.assertEqual(d["newest_snapshot"], "2026-11-03T15:55:00Z")  # 18:55 local
        self.assertEqual(d["coverage"]["first"], "2026-11-02T04:40:00Z")
        self.assertEqual(d["coverage"]["polls"], len(snapshot_times()))
        self.assertEqual(d["coverage"]["dates"], 2)
        self.assertEqual((d["coverage"]["ac_sockets"], d["coverage"]["sockets"]), (12, 14))

    def test_output_is_byte_identical_for_the_same_input(self):
        storage = Storage(":memory:")
        self.addCleanup(storage.close)
        again = build(self.directory, storage)
        self.assertEqual(site_data.dumps(again), site_data.dumps(self.data))


class StatesTest(SiteDataCase):
    def test_table_is_normalise_over_the_whole_raw_domain(self):
        states = self.data["states"]
        self.assertEqual(len(states), 8 * 7 * 2)
        for key, state in states.items():
            a, c, m = (None if v == "" else int(v) for v in key.split(","))
            self.assertEqual(state, normalise(a, bool(m), c), key)

    def test_key_order_is_availability_charging_state_maintenance(self):
        states = self.data["states"]
        self.assertEqual(states["1,3,0"], "available")
        self.assertEqual(states["2,3,1"], "charging")  # availability wins
        self.assertEqual(states[",,1"], "maintenance")
        self.assertEqual(states[",,0"], "charging")
        self.assertEqual(states[",3,0"], "available")
        self.assertEqual(states["0,1,0"], "offline")
        self.assertEqual(states[",1,1"], "maintenance")  # maintenance before charging state

    def test_every_state_has_a_turkish_label(self):
        self.assertEqual(set(self.data["state_labels"]), set(self.data["states"].values()))
        self.assertEqual(self.data["state_labels"]["available"], "boş")
        self.assertEqual(len(self.data["weekday_labels"]), 7)


class SocketTest(SiteDataCase):
    def test_sockets_are_the_display_scope_ac_and_dc(self):
        expected = [e.connector_id for e in DisplayScope(self.storage, HACETTEPE_TERMS).catalog()]
        self.assertEqual([s["id"] for s in self.data["sockets"]], expected)
        self.assertEqual(self.socket(DC_2)["kind"], "DC")
        self.assertEqual(self.socket(EGO_1)["kind"], "AC")
        self.assertEqual(self.socket(DC_2)["power_kw"], 120.0)

    def test_positions_are_indexes_into_connectors_json(self):
        on_disk = json.loads((self.directory / INDEX_FILE).read_text())
        self.assertEqual(on_disk, self.index)
        for socket in self.data["sockets"]:
            if socket["id"] != DC_1:
                self.assertEqual(socket["position"], on_disk.index(socket["id"]), socket["name"])

    def test_a_socket_the_collector_never_saw_has_no_position(self):
        self.assertIsNone(self.socket(DC_1)["position"])

    def test_names_and_groups_are_the_bots(self):
        ego_1 = self.socket(EGO_1)
        self.assertEqual(ego_1["name"], "Ego 1")
        self.assertEqual(ego_1["label"], "Hacettepe Ego - 1 Ac")
        self.assertIn("ego", ego_1["groups"])
        self.assertIn("ego", self.data["groups"])
        self.assertEqual(self.socket(DC_2)["groups"], [])
        for socket in self.data["sockets"]:
            self.assertLessEqual(set(socket["groups"]), set(self.data["groups"]))

    def test_price_comes_from_the_tariff_and_is_null_without_one(self):
        self.assertEqual(self.socket(EGO_1)["price"], {"energy": 9.29, "currency": "TRY"})
        self.assertIsNone(self.socket(F_BLOK_2)["price"])


class AreaTest(SiteDataCase):
    def test_primary_terms_split_the_scope_without_overlap_or_leftovers(self):
        primary, secondary = (set(self.area(k)["sockets"]) for k in ("primary", "secondary"))
        self.assertEqual(secondary, SECONDARY_IDS)
        self.assertFalse(primary & secondary)
        self.assertEqual(primary | secondary, {s["id"] for s in self.data["sockets"]})
        for socket in self.data["sockets"]:
            self.assertEqual(socket["area"], "secondary" if socket["id"] in SECONDARY_IDS
                             else "primary", socket["label"])

    def test_f_blok_picks_both_spellings_and_dc_only_the_scopes_post(self):
        primary = {self.socket(cid)["label"] for cid in self.area("primary")["sockets"]}
        self.assertLessEqual({"Hacettepe-F", "Hacettepe - F Blok 2", "Hacettepe - F Blok 3",
                              "Hacettepe - F Blok 4", "Hacettepe-DC", "Hacettepe-DC #2"}, primary)
        self.assertEqual(self.socket(DC_1)["area"], "primary")
        self.assertTrue(all("Mira" not in label for label in primary))

    def test_area_order_labels_and_socket_order(self):
        self.assertEqual([(a["key"], a["label"]) for a in self.data["areas"]],
                         [("primary", "Ego, F Blok, DC"), ("secondary", "Arge tarafı")])
        order = [s["id"] for s in self.data["sockets"]]
        for area in self.data["areas"]:
            self.assertEqual(area["sockets"], [cid for cid in order if cid in area["sockets"]])

    def test_each_area_counts_its_own_sockets(self):
        primary, secondary = self.area("primary")["coverage"], self.area("secondary")["coverage"]
        self.assertEqual((primary["ac_sockets"], primary["sockets"]), (8, 10))
        self.assertEqual((secondary["ac_sockets"], secondary["sockets"]), (4, 4))
        self.assertEqual((self.data["coverage"]["ac_sockets"], self.data["coverage"]["sockets"]),
                         (12, 14))

    def test_secondary_figures_rest_on_secondary_sockets_only(self):
        secondary = self.area("secondary")
        self.assertEqual({r["id"] for r in secondary["ranking"]}, SECONDARY_IDS)
        self.assertEqual(set(REST) & SECONDARY_IDS, SECONDARY_IDS)
        # Tuesday 08:00: every REST socket charges, Ego 2 is free.
        self.assertEqual(secondary["heatmap"]["cells"][1][8], {"p": 0.0, "polls": 12, "days": 1})
        self.assertEqual(self.area("primary")["heatmap"]["cells"][1][8],
                         {"p": 1.0, "polls": 12, "days": 1})
        # REST plugs in at 07:30 (Tuesday only) and 13:30, unplugs at 12:30 and 18:30.
        self.assertEqual({h: n for h, n in enumerate(secondary["plug_in"]["by_hour"]) if n},
                         {7: 4, 13: 8})
        self.assertEqual({h: n for h, n in enumerate(secondary["unplug"]["by_hour"]) if n},
                         {12: 8, 18: 8})

    def test_secondary_findings_name_no_primary_socket(self):
        secondary = self.area("secondary")
        self.assertTrue(secondary["findings"])
        for finding in secondary["findings"]:
            for name in ("Ego", "F Blok", "DC"):
                self.assertNotIn(name, finding["text"], finding["key"])
        ranking = next(f for f in secondary["findings"] if f["key"] == "socket_ranking")
        self.assertIn("Arge", ranking["text"])
        self.assertTrue(ranking["basis"].startswith("4 priz"))

    def test_findings_are_the_engines_over_each_area(self):
        stats = Statistics(self.storage, GAP_S)
        since = NOW - Thresholds().analysis_window
        for area, terms in (("primary", {"location_terms": PRIMARY_TERMS}),
                            ("secondary", {"excluded_terms": PRIMARY_TERMS})):
            selection = Selection(scope_terms=HACETTEPE_TERMS, **terms)
            history = stats.history(selection.window(since, None))
            self.assertEqual(self.area(area)["durations"]["count"], history.durations.count)
            self.assertEqual(self.area(area)["coverage"]["polls"], history.coverage.polls)

    def test_sites_list_one_row_per_area(self):
        home = {l["area"]: l for l in self.data["sites"]["loads"] if l["home"]}
        self.assertEqual(set(home), {"primary", "secondary"})
        self.assertEqual(home["primary"]["name"], "Ego, F Blok, DC")
        self.assertEqual((home["primary"]["sockets"], home["secondary"]["sockets"]), (8, 4))
        others = [l for l in self.data["sites"]["loads"] if not l["home"]]
        self.assertEqual(len(others), self.data["sites"]["others"])
        self.assertFalse({l["name"] for l in others} & {"Hacettepe Teknokent Ar-Ge",
                                                         "Hacettepe Teknokent Ego"})


class DcTest(SiteDataCase):
    """The scope's DC connectors analysed on their own. In the scenario DC 2 reports offline
    on every poll and DC 1 never appears."""

    def history(self):
        selection = Selection(ac_only=False, dc_only=True, scope_terms=HACETTEPE_TERMS)
        since = NOW - Thresholds().analysis_window
        return Statistics(self.storage, GAP_S).history(selection.window(since, None))

    def test_only_the_scopes_dc_connectors(self):
        dc = self.data["dc"]
        self.assertEqual(set(dc), DC_KEYS)
        self.assertEqual(dc["sockets"], [DC_1, DC_2])
        self.assertTrue(all(self.socket(cid)["kind"] == "DC" for cid in dc["sockets"]))
        self.assertEqual([s["id"] for s in self.data["sockets"] if s["kind"] == "DC"],
                         dc["sockets"])
        self.assertEqual((dc["label"], dc["power_kw"]), ("DC hızlı şarj", 120.0))
        self.assertEqual((dc["coverage"]["ac_sockets"], dc["coverage"]["sockets"]), (0, 2))

    def test_figures_are_the_statistics_of_the_dc_selection(self):
        dc, history = self.data["dc"], self.history()
        self.assertEqual(dc["heatmap"], site_data.heatmap(history.grid))
        self.assertEqual(dc["durations"], site_data.durations(history.durations))
        self.assertEqual(dc["plug_in"], site_data.distribution(history.plug_in))
        self.assertEqual(dc["unplug"], site_data.distribution(history.unplug))
        self.assertEqual(dc["coverage"]["polls"], history.coverage.polls)
        self.assertEqual(dc["heatmap"]["cells"][1][9], {"p": 0.0, "polls": 12, "days": 1})

    def test_weekday_and_weekend_strips_pool_their_days(self):
        heat = self.data["dc"]["heatmap"]
        for hour in range(24):
            days = [heat["cells"][d][hour] for d in range(5) if heat["cells"][d][hour]]
            pooled = heat["weekday_hours"][hour]
            if not days:
                self.assertIsNone(pooled)
                continue
            self.assertEqual(pooled["polls"], sum(c["polls"] for c in days))
            self.assertEqual(pooled["days"], sum(c["days"] for c in days))
        self.assertEqual(heat["weekday_hours"][8], {"p": 0.0, "polls": 24, "days": 2})
        self.assertTrue(all(c is None for c in heat["weekend_hours"]))  # no weekend recorded

    def test_findings_use_only_the_rules_that_hold_for_dc(self):
        dc = self.data["dc"]
        keys = {f["key"] for f in dc["findings"]} | {m["key"] for m in dc["missing"]}
        self.assertFalse(keys & {"socket_ranking", "turnover", "long_occupancy",
                                 "occupancy_by_arrival", "sites"})
        reliability = next(f for f in dc["findings"] if f["key"] == "reliability")
        self.assertEqual(reliability["text"], "Kullanılamayan prizler: DC 2 (%100 çevrimdışı).")
        self.assertNotIn("Ego", " ".join(f["text"] for f in dc["findings"]))

    def test_a_scope_without_dc_has_none(self):
        storage = Storage(":memory:")
        self.addCleanup(storage.close)
        builder = site_data.SiteDataBuilder(station_api(), storage, FakeClock(NOW),
                                            ("hacettepe ego",), POLL_S, GAP_S)
        self.assertIsNone(builder.build(self.directory, BASE_URL)["dc"])


class AreaSitesFindingTest(SiteDataCase):
    """With the common-record threshold lowered, each area's `sites` finding is told: against
    the sites outside the scope only, never the scope's other area."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        storage = Storage(":memory:")
        builder = site_data.SiteDataBuilder(
            station_api(), storage, FakeClock(NOW), HACETTEPE_TERMS, POLL_S, GAP_S,
            thresholds=replace(Thresholds(), min_site_days=0.1), primary_terms=PRIMARY_TERMS)
        cls.told = builder.build(cls.directory, BASE_URL)
        storage.close()

    def test_each_area_compares_itself_with_the_other_sites_only(self):
        others = self.told["sites"]["others"]
        self.assertIsNotNone(self.told["sites"]["overall"])
        for area, name in (("primary", "Ego, F Blok ve DC'de"), ("secondary", "Arge'de")):
            found = next(a for a in self.told["areas"] if a["key"] == area)["findings"]
            sites = next(f for f in found if f["key"] == "sites")
            self.assertTrue(sites["text"].startswith(name), sites["text"])
            self.assertTrue(sites["basis"].startswith(f"{others} site,"), sites["basis"])


class FigureTest(SiteDataCase):
    """Without primary terms the one area is the whole scope: the figures as the bot has them."""
    primary_terms = ()

    @property
    def whole(self) -> dict:
        (area,) = self.data["areas"]
        return area

    def test_the_one_area_is_the_whole_scope(self):
        whole = self.whole
        self.assertEqual((whole["key"], whole["label"]), ("primary", "Hacettepe"))
        self.assertEqual(whole["sockets"], [s["id"] for s in self.data["sockets"]])
        self.assertEqual(whole["coverage"], self.data["coverage"])
        self.assertTrue(all(s["area"] == "primary" for s in self.data["sockets"]))

    def test_heat_map_cells_without_data_are_null(self):
        cells = self.whole["heatmap"]["cells"]
        self.assertIsNone(cells[0][3])  # Monday 03:00: the collector data starts at 07:40
        self.assertTrue(all(c is None for wd in (5, 6) for c in cells[wd]))
        self.assertTrue(all(c is None for c in self.whole["heatmap"]["weekend_hours"]))

    def test_heat_map_cells_carry_their_sample_counts(self):
        cells = self.whole["heatmap"]["cells"]
        self.assertEqual(cells[0][7], {"p": 1.0, "polls": 4, "days": 1})  # thin
        self.assertLess(cells[0][7]["polls"], self.data["thresholds"]["min_hour_polls"])
        self.assertEqual(cells[1][9], {"p": 0.0, "polls": 12, "days": 1})  # all busy
        self.assertEqual(self.whole["heatmap"]["weekday_hours"][7],
                         {"p": 1.0, "polls": 16, "days": 2})
        self.assertEqual(self.data["thresholds"]["min_hour_polls"],
                         Thresholds().min_hour_polls)

    def test_gap_threshold_is_the_one_the_history_was_cut_with(self):
        self.assertEqual(self.data["thresholds"]["gap_s"], GAP_S)

    def test_ranking_is_the_bots_order_with_sample_counts(self):
        ranking = self.whole["ranking"]
        self.assertEqual(len(ranking), 12)  # AC only: DC sockets are not ranked
        self.assertEqual(ranking, by_free_share(ranking, lambda r: r["free_share"],
                                                lambda r: r["name"]))
        self.assertEqual(ranking[0]["name"], "Ego 2")
        self.assertEqual(ranking[-1]["name"], "Ego 4")
        self.assertEqual((ranking[-1]["free_share"], ranking[-1]["unusable_share"]), (0.0, 1.0))
        self.assertTrue(all(r["samples"] == len(snapshot_times()) for r in ranking))

    def test_plug_in_and_unplug_by_local_hour(self):
        plug, unplug = self.whole["plug_in"], self.whole["unplug"]
        # REST at 07:30 (Tuesday only: Monday starts with them already charging), Ego 1 and
        # F Blok 2 at 08:xx, Ego 2 and Ego 3 at 09:00, REST again at 13:30.
        self.assertEqual({h: n for h, n in enumerate(plug["by_hour"]) if n},
                         {7: 7, 8: 4, 9: 4, 13: 14})
        self.assertEqual((plug["count"], plug["excluded"]), (29, 7))
        self.assertEqual({h: n for h, n in enumerate(unplug["by_hour"]) if n},
                         {10: 4, 11: 2, 12: 14, 13: 2, 18: 14})
        self.assertEqual(unplug["count"], 36)

    def test_occupancy_durations(self):
        durations = self.whole["durations"]
        self.assertGreaterEqual(durations["count"], 22)
        self.assertEqual(durations["median_min"], 300.0)

    def test_findings_and_missing_come_from_the_engine(self):
        keys = [f["key"] for f in self.whole["findings"]]
        self.assertEqual(keys[0], "free_hours_Hafta içi")
        self.assertTrue(self.whole["findings"][0]["text"].startswith(
            "Şimdilik yalnızca 2 gün kayıt var."))
        missing = {m["key"]: m["text"] for m in self.whole["missing"]}
        self.assertEqual(missing["week_change"], "Haftalık değişim: 2 haftalık kayıt gerekiyor.")

    def test_site_comparison_waits_for_a_full_common_day(self):
        sites = self.data["sites"]
        self.assertIsNone(sites["overall"])  # under a day of common record
        self.assertLess(sites["common_days"], 1)
        home = [l for l in sites["loads"] if l["home"]]
        self.assertEqual([(l["name"], l["area"]) for l in home], [("Hacettepe", "primary")])
        self.assertGreater(sites["others"], 3)
        self.assertEqual(sites["others"], len(sites["loads"]) - 1)
        self.assertTrue(all(l["area"] is None for l in sites["loads"] if not l["home"]))
        shares = [l["busy_share"] for l in sites["loads"]]
        self.assertEqual(shares, sorted(shares, reverse=True))


class PrimaryTermsConfigTest(unittest.TestCase):
    def test_env_sets_the_terms_and_an_empty_value_turns_the_split_off(self):
        missing = Path("/nonexistent/.env")
        self.assertEqual(Config.load(missing, {}).primary_terms, ("ego", "f blok", "dc"))
        self.assertEqual(Config.load(missing, {"ECOBOX_PRIMARY_TERMS": " ego , F Blok"})
                         .primary_terms, ("ego", "F Blok"))
        self.assertEqual(Config.load(missing, {"ECOBOX_PRIMARY_TERMS": ""}).primary_terms, ())


class CliTest(SiteDataCase):
    def test_site_data_command_writes_the_file_without_touching_the_local_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "never.sqlite3"
            out = Path(tmp) / "site" / "data.json"
            with mock.patch.object(cli.Config, "load", return_value=Config(db_path=db_path)), \
                    mock.patch.object(cli, "HttpEcoboxApi", lambda _timeout: station_api()), \
                    mock.patch.object(cli, "SystemClock", lambda: FakeClock(NOW)), \
                    mock.patch.object(cli.logging, "basicConfig"), \
                    redirect_stdout(io.StringIO()):
                code = cli.main(["site-data", "--snapshots", str(self.directory),
                                 "--out", str(out), "--snapshot-base-url", BASE_URL])
            self.assertEqual(code, 0)
            self.assertFalse(db_path.exists())
            self.assertEqual(out.read_text(), site_data.dumps(self.data))


if __name__ == "__main__":
    unittest.main()
