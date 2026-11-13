import struct
import unittest
import unittest.mock
from datetime import UTC, datetime

import synthetic
from helpers import (EVERYTHING, GAP_S, PNG_SIGNATURE, POLL_S, FakeRenderer, World,
                     display_scope, make_commands)

from ecobox import texts
from ecobox.charts import ChartBook, ChartRequest, available
from ecobox.charts.render import OCCUPANCY_BINS, heatmap_hours, median_position
from ecobox.commands import Conversation
from ecobox.insights import Thresholds
from ecobox.stats import FreeCell, FreeGrid, Statistics

CHAT = Conversation("telegram", 1, "/")


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height from the IHDR chunk."""
    return struct.unpack(">II", data[16:24])


def request(world: World) -> ChartRequest:
    scope = display_scope(world)
    return ChartRequest(scope.selection(), scope.label, scope.names(), world.clock.now())


def thin_world() -> World:
    world = World()
    for _ in range(20):
        world.poll()
        world.clock.advance(POLL_S)
    return world


class ChartBookTest(unittest.TestCase):
    """What each chart is asked to draw; runs without matplotlib."""

    @classmethod
    def setUpClass(cls):
        cls.world = synthetic.two_office_weeks()
        cls.renderer = FakeRenderer()
        cls.book = ChartBook(Statistics(cls.world.storage, GAP_S), cls.renderer)

    @classmethod
    def tearDownClass(cls):
        cls.world.storage.close()

    def draw(self, key: str):
        self.renderer.calls.clear()
        images = self.book.build(ChartBook.resolve(key), request(self.world))
        return images, self.renderer.calls

    def test_words_pick_a_chart_with_or_without_turkish_letters(self):
        for word, key in (("isi", "harita"), ("ISI", "harita"), ("ısı", "harita"),
                          ("harita", "harita"), ("saat", "saat"), ("çizelge", "zaman"),
                          ("süre", "sure"), ("İşgal", "sure"), ("karşılaştır", "site")):
            self.assertEqual(ChartBook.resolve(word).key, key, word)
        self.assertIsNone(ChartBook.resolve("ego"))

    def test_heatmap_gets_the_grid_and_states_its_basis(self):
        [image], [(name, grid, title, subtitle)] = self.draw("harita")
        self.assertEqual(name, "heatmap")
        self.assertEqual(grid.cell(0, 9).probability, 0.5)
        self.assertEqual(title, "Hafta içi boş priz en kolay 07:00–08:00")  # the takeaway
        self.assertEqual(subtitle, "Hacettepe · 12 AC priz · 5,6 günlük kayıt\n"
                                   "Sayı: o saatte boş priz bulma yüzdesi · taralı: kayıt yok")
        self.assertEqual(image.filename, "bos-priz-isi-haritasi.png")
        self.assertEqual(image.caption,
                         "🟢 Hafta içi boş priz en kolay 07:00–08:00, en zor 09:00–10:00")

    def test_arrivals_title_names_the_busy_windows(self):
        [image], [(_, _, _, title, subtitle)] = self.draw("saat")
        self.assertEqual(title, "Takma 07–09, çıkarma 11–13 arası yoğun")
        self.assertIn("175 takma, 175 çıkarma (saati kesin olanlar)", subtitle)
        self.assertEqual(image.caption, "🚗 " + title)

    def test_timeline_sends_last_day_and_last_week(self):
        images, calls = self.draw("zaman")
        self.assertEqual([i.filename for i in images], ["priz-zaman-cizelgesi-24s.png",
                                                        "priz-zaman-cizelgesi-7g.png"])
        self.assertEqual(calls[0][4], "Son okumada 11/12 priz boş")  # last poll: Sunday 14:00
        self.assertEqual([i.caption for i in images],
                         ["🕒 Son okumada 11/12 priz boş", "🕒 Son 7 gün"])
        week_rows = calls[1][1]
        ego_1 = next(row for row in week_rows if row.name == "Ego 1")
        busy = [s for s in ego_1.segments if s.state == "charging"]
        self.assertEqual(len(busy), 5)  # one 08:00-13:00 block per weekday, merged
        self.assertEqual(busy[0].end - busy[0].start, busy[1].end - busy[1].start)
        sunday_ego_1 = next(row for row in calls[0][1] if row.name == "Ego 1")
        self.assertEqual([s.state for s in sunday_ego_1.segments], ["available"])  # one block

    def test_occupancy_gets_clean_session_minutes(self):
        [image], [(_, minutes, median, title, subtitle)] = self.draw("sure")
        self.assertEqual(len(minutes), 175)
        self.assertEqual(median, 300)
        self.assertEqual(title, "Araçlar genelde 5 sa kalıyor")
        self.assertTrue(subtitle.endswith("\n175 araç · çizgi: ortanca 5 sa"))
        self.assertEqual(image.caption, "⏱ Araçlar genelde 5 sa kalıyor")

    def test_sites_without_other_sites_draws_a_notice(self):
        [image], [(name, title, _, message)] = self.draw("site")
        self.assertEqual(name, "notice")
        self.assertEqual(title, "Ecobox siteleri: AC doluluk")
        self.assertIn("henüz yeterince kaydedilmedi", message)
        self.assertEqual(image.caption, "🗺 Site karşılaştırması · henüz az kayıt")

    def test_heatmap_digest_window(self):
        since = self.world.clock.now() - synthetic.timedelta(days=7)
        scope = display_scope(self.world)
        request = ChartRequest(scope.selection(), scope.label, scope.names(),
                               self.world.clock.now(), since)
        self.renderer.calls.clear()
        self.book.build(ChartBook.resolve("harita"), request)
        [(_, grid, _, subtitle)] = self.renderer.calls
        self.assertIn("2,8 günlük kayıt", subtitle)  # the last week, not eight
        self.assertEqual(grid.cell(0, 9).probability, 1.0)  # week two: Ego 2 free at 09


class ChartThresholdTest(unittest.TestCase):
    """Charts wait for the same data the analysis waits for."""

    def every_site_world(self, polls: int) -> World:
        world = World(datetime(2026, 11, 13, 6, 0, tzinfo=UTC), record=EVERYTHING)
        self.addCleanup(world.storage.close)
        for _ in range(polls):
            world.poll()
            world.clock.advance(POLL_S)
        return world

    def test_site_chart_waits_for_a_common_day(self):
        world = self.every_site_world(20)
        renderer = FakeRenderer()
        [image] = ChartBook(Statistics(world.storage, GAP_S), renderer).build(
            ChartBook.resolve("site"), request(world))
        [(name, _, _, message)] = renderer.calls
        self.assertEqual(name, "notice")
        self.assertEqual(message, "Karşılaştırma için en az 1 gün ortak kayıt gerekiyor "
                                  "(şu an 57 dakika).")
        self.assertEqual(image.caption,
                         "🗺 Site karşılaştırması · henüz az kayıt (57 dakika)")

    def test_site_chart_shows_neighbours_of_home_and_the_average(self):
        world = self.every_site_world(20)
        renderer = FakeRenderer()
        book = ChartBook(Statistics(world.storage, GAP_S), renderer,
                         thresholds=Thresholds(min_site_days=0))
        [image] = book.build(ChartBook.resolve("site"), request(world))
        [(name, bars, others_share, title, _)] = renderer.calls
        self.assertEqual(name, "sites")
        self.assertLessEqual(len(bars), 10)
        home = [i for i, bar in enumerate(bars) if bar.home]
        self.assertEqual(len(home), 1)
        self.assertLessEqual(home[0], 4)  # at most four sites above it
        self.assertTrue(bars[-1].average)
        self.assertEqual(bars[-1].name, "Diğer 90 site ortalaması")
        self.assertEqual(bars[-1].busy_share, others_share)
        self.assertTrue(title.startswith("Hacettepe %92 dolu, diğer siteler %"), title)
        loads = Statistics(world.storage, GAP_S).site_loads(
            display_scope(world).selection(), "Hacettepe", None)
        single = {l.name for l in loads if l.sockets < 2}
        self.assertTrue(single)
        self.assertFalse(single & {bar.name for bar in bars})  # one-socket sites left out

    def test_thin_timeline_sends_only_the_day_and_says_when_the_week_comes(self):
        world = thin_world()
        self.addCleanup(world.storage.close)
        [image] = ChartBook(Statistics(world.storage, GAP_S), FakeRenderer()).build(
            ChartBook.resolve("zaman"), request(world))
        self.assertEqual(image.filename, "priz-zaman-cizelgesi-24s.png")
        self.assertTrue(image.caption.endswith(
            "\n7 günlük çizelge 1 gün kayıttan sonra gelecek."), image.caption)

    def test_thin_heatmap_keeps_a_descriptive_title_and_says_so_far(self):
        world = World(datetime(2026, 11, 13, 8, 0, tzinfo=UTC))
        self.addCleanup(world.storage.close)
        world.run(datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
                  lambda ts: {synthetic.EGO_1: "available" if ts.hour % 2 else "charging"})
        renderer = FakeRenderer()
        [image] = ChartBook(Statistics(world.storage, GAP_S), renderer).build(
            ChartBook.resolve("harita"), request(world))
        self.assertEqual(renderer.calls[0][2], "Gün × saat boş priz şansı")
        self.assertTrue(image.caption.startswith("🟢 Şimdiye kadar (1 gün): boş priz en kolay"),
                        image.caption)

    def test_heatmap_rows_and_median_position(self):
        grid = FreeGrid({(0, 9): FreeCell(1, 2, 1), (3, 17): FreeCell(1, 1, 1)})
        self.assertEqual(heatmap_hours(grid), (8, 18))
        self.assertEqual(heatmap_hours(FreeGrid({(1, 0): FreeCell(1, 1, 1),
                                                 (1, 23): FreeCell(1, 1, 1)})), (0, 23))
        self.assertEqual(median_position(300), 5)  # middle of "4–6 sa"
        self.assertEqual(median_position(120), 2.5)  # the 1–2 / 2–3 sa boundary
        self.assertEqual(median_position(600), len(OCCUPANCY_BINS) - 1)


class ChartCommandTest(unittest.TestCase):
    def setUp(self):
        self.world = thin_world()
        self.addCleanup(self.world.storage.close)

    def test_default_chart_and_location_parsing(self):
        renderer = FakeRenderer()
        commands = make_commands(self.world, renderer)
        reply = commands.reply("/grafik", CHAT)
        self.assertEqual(reply.text, "")  # the photo and its caption say it all
        [image] = reply.images
        self.assertTrue(image.data.startswith(PNG_SIGNATURE))
        self.assertEqual(image.caption, "🟢 Gün × saat boş priz şansı · henüz az kayıt\n"
                                        "Diğer: /grafik saat · zaman · sure · site")
        reply = commands.reply("/grafik saat f blok", CHAT)
        self.assertTrue(renderer.calls[-1][4].startswith("F blok · 4 AC priz"))
        self.assertTrue(reply.images[0].caption.endswith(
            "\nDiğer: /grafik harita · zaman · sure · site"))
        commands.reply("/grafik ego", CHAT)  # no type word: the whole text is a place
        self.assertEqual(renderer.calls[-1][0], "heatmap")
        self.assertTrue(renderer.calls[-1][3].startswith("Ego · 4 AC priz"))

    def test_unknown_place_lists_locations_and_chart_types(self):
        reply = make_commands(self.world, FakeRenderer()).reply("/grafik zaman kızılay", CHAT)
        self.assertEqual(reply.images, ())
        self.assertIn("'kızılay' diye bir konum bulamadım.", reply.text)
        self.assertIn("Kullanım: /grafik [tür] [konum]", reply.text)
        self.assertIn("harita – gün × saat boş priz şansı (varsayılan)", reply.text)

    def test_without_matplotlib_the_reply_says_so_in_turkish(self):
        commands = make_commands(self.world, FakeRenderer(unavailable=True))
        reply = commands.reply("/grafik", CHAT)
        self.assertEqual(reply.text, texts.CHARTS_UNAVAILABLE)
        self.assertEqual(reply.images, ())
        self.assertIn("matplotlib kurulu değil", reply.text)
        self.assertIn("🟢", commands.reply("/durum", CHAT).text)  # text commands unaffected


@unittest.skipUnless(available(), "matplotlib is not installed in this interpreter")
class RenderTest(unittest.TestCase):
    """Real PNGs from matplotlib for empty, thin and rich histories."""

    EXPECTED = {"bos-priz-isi-haritasi.png": (1080, 1350),
                "takma-cikarma-saatleri.png": (1080, 1080),
                "priz-zaman-cizelgesi-24s.png": (1080, 1350),
                "priz-zaman-cizelgesi-7g.png": (1080, 1350),
                "isgal-suresi.png": (1080, 1080),
                "site-karsilastirma.png": (1080, 1350)}

    def render_all(self, world: World) -> dict[str, bytes]:
        book = ChartBook(Statistics(world.storage, GAP_S))
        return {image.filename: image.data for kind in ChartBook.kinds()
                for image in book.build(kind, request(world))}

    def check(self, images: dict[str, bytes], week: bool = True) -> None:
        """`week`: whether there is a day of record, so the 7-day timeline is drawn too."""
        expected = set(self.EXPECTED) - (set() if week else {"priz-zaman-cizelgesi-7g.png"})
        self.assertEqual(set(images), expected)
        for name, data in images.items():
            with self.subTest(chart=name):
                self.assertTrue(data.startswith(PNG_SIGNATURE))
                self.assertEqual(png_size(data), self.EXPECTED[name])
                self.assertGreater(len(data), 10_000)      # not blank
                self.assertLess(len(data), 1_500_000)      # fine to send to a phone

    def test_no_text_is_smaller_than_the_minimum(self):
        import matplotlib.text
        from ecobox.charts import style
        sizes: dict[str, list[float]] = {}
        real_png = style.png

        def capture(plt, fig):
            sizes.setdefault(fig._suptitle or str(len(sizes)), []).extend(
                t.get_fontsize() for t in fig.findobj(matplotlib.text.Text) if t.get_text())
            return real_png(plt, fig)
        world = synthetic.two_office_weeks(World(datetime(2026, 11, 2, 4, 0, tzinfo=UTC),
                                                 record=EVERYTHING))
        self.addCleanup(world.storage.close)
        with unittest.mock.patch.object(style, "png", capture):
            self.render_all(world)
            self.render_all(thin_world())
        drawn = [size for chart in sizes.values() for size in chart]
        self.assertGreater(len(drawn), 50)
        self.assertGreaterEqual(min(drawn), style.MIN_SIZE)

    def test_empty(self):
        world = World()
        self.addCleanup(world.storage.close)
        self.check(self.render_all(world), week=False)

    def test_thin(self):
        world = thin_world()
        self.addCleanup(world.storage.close)
        self.check(self.render_all(world), week=False)

    def test_rich(self):
        world = synthetic.two_office_weeks(World(datetime(2026, 11, 2, 4, 0, tzinfo=UTC),
                                                 record=EVERYTHING))
        self.addCleanup(world.storage.close)
        images = self.render_all(world)
        self.check(images)
        self.assertGreater(len(images["bos-priz-isi-haritasi.png"]),
                           len(self.render_all_empty()["bos-priz-isi-haritasi.png"]))

    def render_all_empty(self) -> dict[str, bytes]:
        world = World()
        self.addCleanup(world.storage.close)
        return self.render_all(world)


if __name__ == "__main__":
    unittest.main()
