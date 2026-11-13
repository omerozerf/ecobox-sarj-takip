import struct
import unittest
from datetime import UTC, datetime

import synthetic
from helpers import (EVERYTHING, GAP_S, PNG_SIGNATURE, POLL_S, FakeRenderer, World,
                     display_scope, make_commands)

from ecobox import texts
from ecobox.charts import ChartBook, ChartRequest, available
from ecobox.commands import Conversation
from ecobox.stats import Statistics

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
        for word, key in (("isi", "isi"), ("ISI", "isi"), ("ısı", "isi"), ("harita", "isi"),
                          ("saat", "saat"), ("çizelge", "zaman"), ("süre", "sure"),
                          ("İşgal", "sure"), ("karşılaştır", "site")):
            self.assertEqual(ChartBook.resolve(word).key, key, word)
        self.assertIsNone(ChartBook.resolve("ego"))

    def test_heatmap_gets_the_grid_and_states_its_basis(self):
        [image], [(name, grid, title, subtitle)] = self.draw("isi")
        self.assertEqual(name, "heatmap")
        self.assertEqual(grid.cell(0, 9).probability, 0.5)
        self.assertEqual(title, "Hacettepe: boş priz şansı")
        self.assertIn("12 AC priz · 5,6 gün gözlem (2720 okuma)", subtitle)
        self.assertEqual(image.filename, "bos-priz-isi-haritasi.png")

    def test_timeline_sends_last_day_and_last_week(self):
        images, calls = self.draw("zaman")
        self.assertEqual([i.filename for i in images], ["priz-zaman-cizelgesi-24s.png",
                                                        "priz-zaman-cizelgesi-7g.png"])
        week_rows = calls[1][1]
        ego_1 = next(row for row in week_rows if row.name == "Ego 1")
        busy = [s for s in ego_1.segments if s.state == "charging"]
        self.assertEqual(len(busy), 5)  # one 08:00-13:00 block per weekday, merged
        self.assertEqual(busy[0].end - busy[0].start, busy[1].end - busy[1].start)
        sunday_ego_1 = next(row for row in calls[0][1] if row.name == "Ego 1")
        self.assertEqual([s.state for s in sunday_ego_1.segments], ["available"])  # one block

    def test_occupancy_gets_clean_session_minutes(self):
        _, [(_, minutes, median, _, subtitle)] = self.draw("sure")
        self.assertEqual(len(minutes), 175)
        self.assertEqual(median, 300)
        self.assertIn("175 temiz oturum · yeşil: ortanca 5 sa", subtitle)

    def test_sites_without_other_sites_draws_an_empty_comparison(self):
        _, [(_, loads, others_share, _, _)] = self.draw("site")
        self.assertEqual((loads, others_share), ([], None))


class ChartCommandTest(unittest.TestCase):
    def setUp(self):
        self.world = thin_world()
        self.addCleanup(self.world.storage.close)

    def test_default_chart_and_location_parsing(self):
        renderer = FakeRenderer()
        commands = make_commands(self.world, renderer)
        reply = commands.reply("/grafik", CHAT)
        self.assertEqual(reply.text.split("\n")[0], "📈 Hacettepe: gün × saat boş priz şansı")
        self.assertEqual(len(reply.images), 1)
        self.assertTrue(reply.images[0].data.startswith(PNG_SIGNATURE))
        reply = commands.reply("/grafik saat f blok", CHAT)
        self.assertTrue(reply.text.startswith("📈 F blok: takma ve çıkarma saatleri"))
        reply = commands.reply("/grafik ego", CHAT)  # no type word: the whole text is a place
        self.assertTrue(reply.text.startswith("📈 Ego: gün × saat"))

    def test_unknown_place_lists_locations_and_chart_types(self):
        reply = make_commands(self.world, FakeRenderer()).reply("/grafik zaman kızılay", CHAT)
        self.assertEqual(reply.images, ())
        self.assertIn("'kızılay' ile eşleşen AC priz yok", reply.text)
        self.assertIn("Kullanım: /grafik [tür] [konum]", reply.text)
        self.assertIn("isi – gün × saat boş priz şansı (varsayılan)", reply.text)

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

    def check(self, images: dict[str, bytes]) -> None:
        self.assertEqual(set(images), set(self.EXPECTED))
        for name, data in images.items():
            with self.subTest(chart=name):
                self.assertTrue(data.startswith(PNG_SIGNATURE))
                self.assertEqual(png_size(data), self.EXPECTED[name])
                self.assertGreater(len(data), 10_000)      # not blank
                self.assertLess(len(data), 1_500_000)      # fine to send to a phone

    def test_empty(self):
        world = World()
        self.addCleanup(world.storage.close)
        self.check(self.render_all(world))

    def test_thin(self):
        world = thin_world()
        self.addCleanup(world.storage.close)
        self.check(self.render_all(world))

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
