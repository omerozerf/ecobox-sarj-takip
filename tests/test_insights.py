import unittest
from datetime import UTC, datetime, timedelta

import synthetic
from helpers import EGO, EVERYTHING, GAP_S, POLL_S, World, display_scope

from ecobox.fmt import bar, decimal, half_up, percent_of, possessive, span_adjective
from ecobox.insights import InsightEngine, Thresholds
from ecobox.recorder import Recorder
from ecobox.stats import Statistics
from ecobox.texts import analysis_text


def analyse(world: World, since: datetime | None = None, engine: InsightEngine | None = None):
    scope = display_scope(world)
    engine = engine or InsightEngine(Statistics(world.storage, GAP_S))
    return engine.analyse(scope.selection(), scope.label, scope.names(), world.clock.now(),
                          since)


class FortnightTest(unittest.TestCase):
    """Known answers from `synthetic.two_office_weeks` (see its docstring)."""

    @classmethod
    def setUpClass(cls):
        cls.world = synthetic.two_office_weeks()
        cls.analysis = analyse(cls.world)
        cls.findings = {f.key: f for f in cls.analysis.findings}

    @classmethod
    def tearDownClass(cls):
        cls.world.storage.close()

    def test_coverage_counts_only_observed_time(self):
        # 10 weekdays x 239 steps + 4 weekend days x 79 steps of 3 min; nights are gaps
        self.assertEqual(self.analysis.coverage.observed, timedelta(minutes=8118))
        self.assertEqual(self.analysis.coverage.dates, 14)
        self.assertEqual(self.analysis.sockets, 12)

    def test_free_hours_weekday_and_weekend(self):
        weekday = self.findings["free_hours_Hafta içi"]
        self.assertEqual(weekday.text, "Hafta içi boş priz bulmak en kolay 07:00–08:00 arası "
                                       "(%100), en zor 09:00–10:00 arası (%50).")
        self.assertEqual(weekday.basis, "12 saat dilimi · 5,6 gün gözlem")
        weekend = self.findings["free_hours_Hafta sonu"]
        self.assertEqual(weekend.text, "Hafta sonu gözlenen her saatte boş priz bulma şansı %100.")

    def test_socket_ranking_names_the_faulted_socket(self):
        text = self.findings["socket_ranking"].text
        self.assertTrue(text.startswith("En sık boş: Ego 2 %96, Ego 3 %85, F Blok 2 %85."), text)
        self.assertIn("Neredeyse hiç boş değil: Ego 4 %0 (arızalı).", text)
        self.assertTrue(text.endswith("İlk bakılacak yer: Ego 2."))

    def test_occupancy_time(self):
        finding = self.findings["occupancy"]
        self.assertEqual(finding.text, "Bir araç prizi genelde 5 sa işgal ediyor. Dolu bir "
                                       "prizin kısa sürede boşalmasını bekleme.")
        self.assertEqual(finding.basis, "175 temiz oturum")

    def test_arrivals_and_departures(self):
        finding = self.findings["arrivals"]
        # 07:30 x 7 + 08:00 + 08:30 = 9 of 18 (17 in week two) plug-ins a day
        self.assertEqual(finding.text, "İnsanlar en çok 07:00–09:00 arası takıyor (takmaların "
                                       "%51'i), en çok 11:00–13:00 arası çıkarıyor "
                                       "(çıkarmaların %46'sı).")
        self.assertEqual(finding.basis, "175 takma, 175 çıkarma")

    def test_occupancy_by_arrival(self):
        finding = self.findings["occupancy_by_arrival"]
        self.assertEqual(finding.text, "Prizde kalma, geliş saatine göre: sabah 5 sa · öğle 5 sa.")
        self.assertEqual(finding.basis, "105 + 70 temiz oturum")

    def test_turnover_leaves_the_faulted_socket_to_reliability(self):
        text = self.findings["turnover"].text
        # REST: 20 plug-ins over 14 observed dates; Ego 2 only 5
        self.assertIn("En hareketli priz 3. Arge Otopark 1: günde 1,4 kullanım.", text)
        self.assertIn("En durgun Ego 2: günde 0,4.", text)
        self.assertIn("günde ~12,5 takma", text)
        self.assertNotIn("Ego 4", text)

    def test_long_occupancy(self):
        text = self.findings["long_occupancy"].text
        self.assertTrue(text.startswith("Oturumların %86'sı 4 saatten uzun."))  # 150 of 175

    def test_reliability(self):
        self.assertEqual(self.findings["reliability"].text,
                         "Kullanılamayan prizler: Ego 4 %100 arızalı. Diğer 11 priz sorunsuz.")

    def test_week_change(self):
        finding = self.findings["week_change"]
        # week one: no socket free 09-10 on weekdays (100 of 1360 polls)
        self.assertEqual(finding.text, "Bu hafta boş priz bulma şansı %100, geçen hafta %93: 7 "
                                       "puan arttı. Günde ortalama takma: 12,9 → 12,1.")

    def test_site_comparison_says_what_it_lacks(self):
        self.assertEqual([m.key for m in self.analysis.missing], ["sites"])

    def test_findings_come_most_useful_first(self):
        keys = [f.key for f in self.analysis.findings]
        self.assertEqual(keys[:3], ["free_hours_Hafta içi", "free_hours_Hafta sonu",
                                    "socket_ranking"])
        self.assertEqual(keys[-1], "week_change")

    def test_rendered_reply(self):
        text = analysis_text(self.analysis)
        self.assertTrue(text.startswith("🔎 Hacettepe · 12 AC priz\n5,6 günlük veriye "
                                        "dayanıyor (2720 okuma)."))
        self.assertIn("\n\n🟢 Hafta içi boş priz", text)
        self.assertIn("\n↳ 12 saat dilimi", text)
        self.assertTrue(text.endswith("⏳ Henüz erken:\n· Diğer sitelerle karşılaştırma: diğer "
                                      "Ecobox siteleri henüz yeterince kaydedilmedi."))

    def test_digest_window_is_the_last_week(self):
        week = analyse(self.world, since=self.world.clock.now() - timedelta(days=7))
        self.assertEqual(week.coverage.observed, timedelta(minutes=5 * 717 + 2 * 237))
        texts = {f.key: f.text for f in week.findings}
        self.assertEqual(texts["free_hours_Hafta içi"],
                         "Hafta içi gözlenen her saatte boş priz bulma şansı %100.")
        self.assertIn("week_change", texts)  # compares with the week before the window


class ThinDataTest(unittest.TestCase):
    def test_empty_database(self):
        world = World()
        self.addCleanup(world.storage.close)
        analysis = analyse(world)
        self.assertEqual(analysis.findings, [])
        self.assertEqual(analysis_text(analysis),
                         "🔎 Hacettepe\nHenüz kayıt yok. Kaydedici veri topladıkça analiz dolacak.")

    def test_every_rule_says_what_it_needs(self):
        world = World()
        self.addCleanup(world.storage.close)
        for _ in range(5):  # 12 minutes of data
            world.poll()
            world.clock.advance(POLL_S)
        analysis = analyse(world)
        self.assertEqual(analysis.findings, [])
        missing = {m.key: m.text for m in analysis.missing}
        self.assertEqual(set(missing), {"free_hours", "socket_ranking", "arrivals", "occupancy",
                                        "occupancy_by_arrival", "turnover", "long_occupancy",
                                        "reliability", "sites", "week_change"})
        self.assertEqual(missing["occupancy"],
                         "Prizi işgal süresi: en az 5 temiz oturum gerekiyor (şu an 0).")
        self.assertEqual(missing["week_change"],
                         "Haftalık değişim: iki haftalık veri gerekiyor (şu an 12 dakika).")
        self.assertEqual(missing["turnover"],
                         "Priz başına günlük kullanım: her priz için en az 1 günlük gözlem "
                         "gerekiyor (şu an 12 dakika).")
        for text in missing.values():
            self.assertNotIn("\n", text)  # one short line each

    def test_thresholds_are_the_switch(self):
        world = World()
        self.addCleanup(world.storage.close)
        for minute in range(0, 60, 3):
            world.poll({EGO[0]: "charging" if minute % 12 < 6 else "available"})
            world.clock.advance(POLL_S)
        strict = analyse(world)
        loose = analyse(world, engine=InsightEngine(
            Statistics(world.storage, GAP_S),
            Thresholds(min_sessions=2, min_events=2, min_socket_samples=10)))
        self.assertNotIn("occupancy", {f.key for f in strict.findings})
        self.assertIn("occupancy", {f.key for f in loose.findings})
        self.assertIn("arrivals", {f.key for f in loose.findings})


class SiteComparisonTest(unittest.TestCase):
    def test_hacettepe_against_the_other_sites(self):
        world = World(datetime(2026, 11, 13, 6, 0, tzinfo=UTC), record=EVERYTHING)
        self.addCleanup(world.storage.close)
        for _ in range(60):  # 3 hours of every site as in the fixture
            world.poll()
            world.clock.advance(POLL_S)
        analysis = analyse(world)
        [finding] = [f for f in analysis.findings if f.key == "sites"]
        self.assertTrue(finding.text.startswith("Hacettepe AC prizlerinin doluluk oranı %92, "
                                                "diğer Ecobox sitelerinde %27. "), finding.text)
        self.assertIn(" site arasında yoğunlukta ", finding.text)
        self.assertTrue(finding.text.endswith("Hacettepe ortalamadan belirgin yoğun."))
        self.assertTrue(finding.basis.startswith("90 site, 145 AC priz · ortak 3 saat"),
                        finding.basis)

    def test_common_window_starts_when_the_other_sites_did(self):
        """The live database: Hacettepe recorded for hours, then every site from a restart."""
        world = World(datetime(2026, 11, 13, 6, 0, tzinfo=UTC))
        self.addCleanup(world.storage.close)
        for _ in range(40):
            world.poll()
            world.clock.advance(POLL_S)
        stats = Statistics(world.storage, GAP_S)
        home = display_scope(world).selection()
        window_start = world.clock.now() - timedelta(days=56)
        self.assertIsNone(stats.comparison_start(home, window_start))
        switched = world.clock.now()
        world.recorder = Recorder(world.api, world.storage, EVERYTHING, world.clock, GAP_S)
        world.recorder.refresh_catalog()
        for _ in range(21):
            world.poll()
            world.clock.advance(POLL_S)
        self.assertEqual(stats.comparison_start(home, window_start), switched)
        [finding] = [f for f in analyse(world).findings if f.key == "sites"]
        self.assertIn("ortak 1 saat gözlem", finding.basis)  # not the 3 h of Hacettepe alone


class FormatTest(unittest.TestCase):
    def test_possessive_follows_the_last_number_word(self):
        cases = {1: "'i", 2: "'si", 3: "'ü", 4: "'ü", 5: "'i", 6: "'sı", 7: "'si", 8: "'i",
                 9: "'u", 10: "'u", 20: "'si", 30: "'u", 40: "'ı", 45: "'i", 50: "'si", 60: "'ı",
                 70: "'i", 80: "'i", 90: "'ı", 100: "'ü", 0: "'ı"}
        for number, suffix in cases.items():
            self.assertEqual(possessive(number), suffix, number)
        self.assertEqual(percent_of(0.456), "%46'sı")

    def test_numbers_and_spans(self):
        self.assertEqual(decimal(2.34), "2,3")
        self.assertEqual(decimal(3.0), "3")
        self.assertEqual(half_up(2.5), 3)
        self.assertEqual(span_adjective(0.25), "6 saatlik")
        self.assertEqual(span_adjective(12 / 1440), "12 dakikalık")
        self.assertEqual(span_adjective(2.31), "2,3 günlük")
        self.assertEqual(bar(0.0), "░" * 10)
        self.assertEqual(bar(None), "·" * 10)
        self.assertEqual(bar(0.25), "███░░░░░░░")


if __name__ == "__main__":
    unittest.main()
