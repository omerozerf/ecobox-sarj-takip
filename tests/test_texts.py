import unittest
from datetime import UTC, datetime, timedelta

from helpers import World

from ecobox.estimate import ChargeEstimate
from ecobox.labels import ShortNames
from ecobox.records import CatalogEntry, CurrentStatus
from ecobox.state import AVAILABLE, CHARGING, FAULTED
from ecobox.stats import (ConnectorShare, DurationSummary, FreeCell, FreeGrid,
                          TimeDistribution, WaitSummary)
from ecobox.texts import (estimate_text, help_text, notification_text, stats_text,
                          status_text)

NOW = datetime(2026, 11, 10, 14, 20, tzinfo=UTC)  # 17:20 in Istanbul


def entry(n: int, name: str, order: int = 1, kind: int = 1) -> CatalogEntry:
    return CatalogEntry(f"c{n}", f"cp{n}", name, "Site", order, kind, 22 if kind == 1 else 120)


def status(e: CatalogEntry, state: str, minutes: float | None, uncertain: bool = False,
           read_ago: float = 1) -> CurrentStatus:
    sampled = NOW - timedelta(minutes=read_ago)
    since = None if minutes is None else sampled - timedelta(minutes=minutes)
    return CurrentStatus(e, state, sampled, since, uncertain, None)


class ShortNamesTest(unittest.TestCase):
    def test_fixture_catalog(self):
        world = World()
        self.addCleanup(world.storage.close)
        catalog = world.storage.catalog()
        names = ShortNames(catalog)
        self.assertEqual(sorted(names.of(e) for e in catalog),
                         sorted(["Ego 1", "Ego 2", "Ego 3", "Ego 4", "F Blok 2", "F Blok 3",
                                 "F Blok 4", "3. Arge Otopark 1", "3. Arge Otopark 2",
                                 "3. Arge Otopark 3", "Arge", "DC", "DC 2", "F"]))

    def test_names_come_from_the_whole_catalog_not_the_printed_subset(self):
        catalog = [entry(1, "Hacettepe Ego - 1 Ac"), entry(2, "Hacettepe-F"),
                   entry(3, "Hacettepe Ego - 2 Ac")]
        names = ShortNames(catalog)
        self.assertEqual([names.of(e) for e in catalog], ["Ego 1", "F", "Ego 2"])
        only_ego = ShortNames([catalog[0], catalog[2]])
        self.assertEqual(only_ego.of(catalog[0]), "1")  # a subset would over-shorten

    def test_single_label_drops_nothing_and_ac_stays_when_alone(self):
        lone = entry(1, "Hacettepe Ego - 1 Ac")
        self.assertEqual(ShortNames([lone]).of(lone), "Hacettepe Ego 1")
        bare = [entry(1, "Site Ac"), entry(2, "Site B")]
        self.assertEqual([ShortNames(bare).of(e) for e in bare], ["Ac", "B"])

    def test_collisions_fall_back_to_the_full_label(self):
        a, b, c = entry(1, "Site - A 1"), entry(2, "Site A-1"), entry(3, "Site Z")
        names = ShortNames([a, b, c])
        self.assertEqual((names.of(a), names.of(b), names.of(c)),
                         ("Site - A 1", "Site A-1", "Z"))

    def test_shared_prefix_is_case_insensitive(self):
        a, b = entry(1, "HACETTEPE Ego 1"), entry(2, "hacettepe F")
        names = ShortNames([a, b])
        self.assertEqual((names.of(a), names.of(b)), ("Ego 1", "F"))


class StatusTextTest(unittest.TestCase):
    def setUp(self):
        self.entries = [entry(1, "Hacettepe Ego - 1 Ac"), entry(2, "Hacettepe Ego - 2 Ac"),
                        entry(3, "Hacettepe Ego - 3 Ac"), entry(4, "Hacettepe Ego - 4 Ac"),
                        entry(5, "Hacettepe-F"), entry(6, "Hacettepe-Arge"),
                        entry(7, "Hacettepe-DC", kind=2), entry(8, "Hacettepe-DC", 2, 2)]
        self.names = ShortNames(self.entries)
        e = self.entries
        self.statuses = [
            status(e[0], CHARGING, 3 * 60 + 7), status(e[1], AVAILABLE, 0.2),
            status(e[2], CHARGING, 5 * 60 + 44, uncertain=True),
            status(e[3], CHARGING, 5 * 60 + 6), status(e[4], AVAILABLE, 72),
            status(e[5], FAULTED, 400), status(e[6], AVAILABLE, 5),
            status(e[7], CHARGING, 5)]

    def test_full_reply(self):
        self.assertEqual(status_text(self.statuses, NOW, self.names), "\n".join([
            "🟢 2/6 AC priz boş",
            "Son okuma 17:19 (1 dk önce)",
            "",
            "Boş",
            "🟢 Ego 2 · az önce",
            "🟢 F · 1 sa 12 dk",
            "",
            "Dolu",
            "🔴 Ego 3 · en az 5 sa 44 dk",
            "🔴 Ego 4 · 5 sa 6 dk",
            "🔴 Ego 1 · 3 sa 7 dk",
            "",
            "Kullanılamıyor",
            "⚪ Arge · arızalı",
            "",
            "⚡ DC: 1/2 boş"]))

    def test_nothing_free_and_stale_reading(self):
        busy = [status(e, CHARGING, 30, read_ago=77) for e in self.entries[:2]]
        text = status_text(busy, NOW, self.names)
        self.assertEqual(text.split("\n\n")[0], "🔴 Boş AC priz yok (0/2)\n"
                         "⚠️ Son okuma 16:03 (1 sa 17 dk önce), veri eski olabilir")
        self.assertNotIn("Boş\n", text)  # empty section: no heading
        self.assertNotIn("⚡", text)  # no DC socket in scope

    def test_ten_minutes_is_not_stale_yet(self):
        text = status_text([status(self.entries[0], AVAILABLE, 5, read_ago=10)], NOW,
                           self.names)
        self.assertNotIn("⚠️", text)


class NotificationTextTest(unittest.TestCase):
    def test_several_sockets_and_prefix(self):
        catalog = [entry(1, "Hacettepe Ego - 1 Ac"), entry(2, "Hacettepe Ego - 2 Ac"),
                   entry(3, "Hacettepe-F")]
        names = ShortNames(catalog)
        self.assertEqual(notification_text(catalog[:2], "/", names),
                         "🟢 Boşaldı: Ego 1, Ego 2\nTekrar haber için: /haberver")
        self.assertEqual(notification_text(catalog[2:], "", names),
                         "🟢 Boşaldı: F\nTekrar haber için: haberver")


class HelpTextTest(unittest.TestCase):
    def test_lines_and_prefix(self):
        self.assertEqual(help_text("/"), "\n".join([
            "Komutlar", "",
            "/durum – hangi priz boş",
            "/haberver [konum] – boşalınca haber ver (ör. /haberver ego)",
            "/iptal – haber vermeyi kapat",
            "/istatistik [konum] – takma ve alma saatleri",
            "/analiz [konum] – prizler nasıl kullanılıyor, yorumlu",
            "/grafik [tür] [konum] – grafik; tür: isi, saat, zaman, sure, site",
            "/tahmin 20 80 – süre, bitiş saati, ücret",
            "/yardim – bu liste"]))
        self.assertNotIn("/", help_text(""))


class StatsTextTest(unittest.TestCase):
    def render(self, **overrides):
        catalog = [entry(i, f"Hacettepe Blok{i} - 1 Ac") for i in range(1, 8)]
        catalog.append(entry(8, "Hacettepe-F"))
        args = dict(
            scope_label="tüm AC prizler", polls=(117, 0),
            plug_in=TimeDistribution(16, 0, [{12: 4, 15: 5, 17: 7, 9: 1}.get(h, 0)
                                             for h in range(24)], [0] * 7),
            unplug=TimeDistribution(0, 0, [0] * 24, [0] * 7),
            durations=DurationSummary(8, 0, 123.0, 112.0, 259.0),
            shares=[ConnectorShare(e, 10 + i, 100) for i, e in enumerate(catalog)],
            grid=FreeGrid({(4, 17): FreeCell(3, 3, 1)}),
            now=datetime(2026, 11, 13, 14, 20, tzinfo=UTC),  # Friday 17:20 local
            wait=None, names=ShortNames(catalog))
        args.update(overrides)
        return stats_text(**args)

    def test_layout(self):
        text = self.render()
        self.assertEqual(text.split("\n\n")[0], "📊 Tüm AC prizler")
        self.assertIn("Ne zaman takıyorlar? En çok 17:00, 15:00, 12:00", text)
        self.assertIn("Ne zaman alıyorlar? veri yok", text)
        self.assertIn("Ne kadar kalıyorlar? Genelde 2 sa 3 dk (çoğu 1 sa 52 dk – 4 sa 19 dk)",
                      text)
        self.assertIn("En sık boş olanlar\n██░░░░░░░░ %17 · F\n██░░░░░░░░ %16 · Blok7 1\n"
                      "██░░░░░░░░ %15 · Blok6 1\n█░░░░░░░░░ %14 · Blok5 1\n"
                      "█░░░░░░░░░ %13 · Blok4 1\n\n", text)  # top 5 only
        self.assertIn("Boş priz bulma şansı · Cuma\n17:00 ██████████ %100\n"
                      "18:00 ·········· veri yok\n19:00 ·········· veri yok\n", text)
        self.assertIn("23:00 ·········· veri yok\nCmt 00:00 ·········· veri yok\n\n", text)
        self.assertTrue(text.endswith("\n\nDayanak: 117 okuma, 16 takma, 0 alma, 8 oturum."))
        self.assertNotIn("gözlem", text)

    def test_free_chance_borrows_the_same_hour_of_other_days(self):
        grid = FreeGrid({(4, 17): FreeCell(1, 4, 1), (2, 18): FreeCell(3, 4, 1),
                         (3, 18): FreeCell(0, 0, 0)})
        text = self.render(grid=grid)
        self.assertIn("17:00 ███░░░░░░░ %25\n18:00 ████████░░ %75≈\n", text)
        self.assertIn("≈ bu gün için veri yok; diğer günlerin aynı saati", text)
        self.assertNotIn("≈ bu gün", self.render())  # nothing borrowed, no note

    def test_zero_percent_and_no_data_look_different(self):
        text = self.render(grid=FreeGrid({(4, 17): FreeCell(0, 5, 1)}))
        self.assertIn("17:00 ░░░░░░░░░░ %0\n18:00 ·········· veri yok", text)

    def test_basis_line_failed_polls_and_uncertain(self):
        text = self.render(polls=(117, 4),
                           plug_in=TimeDistribution(16, 10, [0] * 24, [0] * 7),
                           unplug=TimeDistribution(19, 0, [0] * 24, [0] * 7),
                           durations=DurationSummary(8, 13, 123.0, None, None))
        self.assertIn("Dayanak: 117 okuma, 16 takma, 19 alma, 8 oturum, 4 hata; "
                      "23 belirsiz kayıt sayılmadı.", text)
        self.assertIn("Genelde 2 sa 3 dk\n", text)  # no quartiles: no range

    def test_all_busy_wait_paragraph_comes_before_the_basis(self):
        wait = WaitSummary(0, 0, 0, None, None, None)
        paragraphs = self.render(wait=wait).split("\n\n")
        self.assertEqual(paragraphs[-2],
                         "Şu an hepsi dolu. Bu saat için geçmiş gün gözlemi yok.")
        self.assertTrue(paragraphs[-1].startswith("Dayanak:"))

    def test_turkish_capital_i(self):
        self.assertTrue(self.render(scope_label="incek (AC)").startswith("📊 İncek (AC)"))


class EstimateTextTest(unittest.TestCase):
    NOW = datetime(2026, 11, 10, 13, 0, tzinfo=UTC)  # 16:00 local

    def estimate(self, hours: float, cost=600.4, service=2.0, currency="TRY"):
        duration = timedelta(hours=hours)
        return ChargeEstimate(32, 100, 64.6, 10.6, duration, self.NOW + duration, cost,
                              currency, service)

    def test_full_reply(self):
        self.assertEqual(estimate_text(self.estimate(7.4), self.NOW), "\n".join([
            "🔋 %32 → %100",
            "⏱ 7 sa 24 dk · bugün 23:24'te hazır",
            "💰 ~600 TL",
            "⚡ 64.6 kWh, 10.6 kW ile",
            "Not: 2 TL hizmet bedeli hesaba katılmadı."]))

    def test_day_words(self):
        self.assertIn("yarın 02:00'de hazır", estimate_text(self.estimate(10), self.NOW))
        self.assertIn("Perşembe 16:00'da hazır", estimate_text(self.estimate(48), self.NOW))

    def test_midnight_is_judged_by_the_local_date(self):
        # 20:59 UTC is 23:59 local on the 10th; 21:01 UTC is 00:01 on the 11th
        now = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
        for finish, expected in ((datetime(2026, 11, 10, 20, 59, tzinfo=UTC), "bugün 23:59'da"),
                                 (datetime(2026, 11, 10, 21, 1, tzinfo=UTC), "yarın 00:01'de")):
            reply = estimate_text(ChargeEstimate(0, 1, 1, 1, timedelta(0), finish, None, None,
                                                 None), now)
            self.assertIn(expected, reply)

    def test_locative_follows_the_last_spoken_number(self):
        cases = {"14:23": "te", "10:20": "de", "10:30": "da", "10:40": "ta", "10:50": "de",
                 "10:06": "da", "10:09": "da", "10:01": "de", "17:00": "de", "20:00": "de",
                 "10:00": "da", "08:00": "de", "00:00": "da"}
        for clock, suffix in cases.items():
            hour, minute = map(int, clock.split(":"))
            finish = datetime(2026, 11, 10, hour - 3, minute, tzinfo=UTC) if hour >= 3 else \
                datetime(2026, 11, 9, 21, minute, tzinfo=UTC)
            reply = estimate_text(ChargeEstimate(0, 1, 1, 1, timedelta(0), finish, None, None,
                                                 None), finish)
            self.assertIn(f"{clock}'{suffix} hazır", reply, clock)

    def test_unknown_tariff_and_no_service_price(self):
        text = estimate_text(self.estimate(1, cost=None, currency=None, service=None), self.NOW)
        self.assertIn("💰 tarife bilinmiyor", text)
        self.assertNotIn("Not:", text)
        self.assertNotIn("Not:", estimate_text(self.estimate(1, service=None), self.NOW))

    def test_other_currency_keeps_its_code(self):
        self.assertIn("💰 ~600 EUR", estimate_text(self.estimate(1, currency="EUR"), self.NOW))


if __name__ == "__main__":
    unittest.main()
