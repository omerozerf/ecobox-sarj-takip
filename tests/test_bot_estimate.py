import logging
import unittest
from datetime import UTC, datetime

from helpers import (EGO, F_BLOK_2, POLL_S, FakeTelegram, World, display_scope, make_commands,
                     message)

from ecobox.bot import TelegramBot, TelegramOutbox
from ecobox.commands import TELEGRAM, Conversation
from ecobox.estimate import ChargeEstimator
from ecobox.models import Tariff
from ecobox.notifier import SubscriptionNotifier

OWNER = 1111
STRANGER = 2222
TARIFF = Tariff("t1", 9.29, 2, 0, "TRY", 20, None)


class EstimateTest(unittest.TestCase):
    def setUp(self):
        self.estimator = ChargeEstimator(10.6, 0.95, price_includes_vat=True)
        self.now = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)

    def test_arithmetic(self):
        e = self.estimator.estimate(20, 80, self.now, 22, TARIFF)
        self.assertAlmostEqual(e.energy_kwh, 57.0)
        self.assertEqual(e.effective_kw, 10.6)
        self.assertAlmostEqual(e.duration.total_seconds() / 3600, 57 / 10.6)
        self.assertEqual(e.finish_at, self.now + e.duration)
        self.assertAlmostEqual(e.cost, 57 * 9.29)
        self.assertEqual(e.currency, "TRY")

    def test_about_eleven_percent_per_hour(self):
        e = self.estimator.estimate(0, 11, self.now, 22, None)
        self.assertAlmostEqual(e.duration.total_seconds() / 3600, 11 * 0.95 / 10.6)
        self.assertIsNone(e.cost)

    def test_slow_socket_limits_power_and_vat_option(self):
        e = ChargeEstimator(10.6, 0.95, price_includes_vat=False).estimate(
            50, 60, self.now, 3.7, TARIFF)
        self.assertEqual(e.effective_kw, 3.7)
        self.assertAlmostEqual(e.cost, 9.5 * 9.29 * 1.2)

    def test_invalid_range(self):
        for start, target in ((80, 20), (50, 50), (-1, 10), (10, 101)):
            with self.assertRaises(ValueError):
                self.estimator.estimate(start, target, self.now, 22, None)


class BotTest(unittest.TestCase):
    def make(self, chat_id=OWNER):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.world = World(datetime(2026, 11, 10, 6, 0, tzinfo=UTC))
        self.addCleanup(self.world.storage.close)
        self.telegram = FakeTelegram()
        self.bot = TelegramBot(self.telegram, make_commands(self.world), chat_id)
        notifier = SubscriptionNotifier(self.world.storage, display_scope(self.world),
                                        {TELEGRAM: TelegramOutbox(self.telegram)})
        self.world.recorder.add_listener(notifier.on_transitions)
        self.world.poll()  # fixture state: only Ego 4 is free among the 12 AC sockets
        self.world.clock.advance(60)

    def ask(self, text, chat_id=OWNER):
        self.telegram.sent.clear()
        self.bot.handle_update(message(chat_id, text))
        return self.telegram.sent

    def test_status_answer_first_with_short_names(self):
        self.make()
        [(chat, text)] = self.ask("/durum")
        self.assertEqual(chat, OWNER)
        blocks = text.split("\n\n")
        self.assertEqual(blocks[0], "🟢 1/12 AC priz boş\nSon okuma 09:00 (1 dk önce)")
        self.assertEqual(blocks[1], "Boş · ne zamandır\n🟢 Ego 4 · az önce")
        self.assertTrue(blocks[2].startswith("Dolu · ne zamandır\n🔴 "))
        self.assertEqual(blocks[2].count("\n"), 11)  # heading + 11 busy sockets
        self.assertEqual(blocks[-1], "⚡ DC: 1/2 boş")
        self.assertNotIn("Hacettepe", text)
        self.assertNotIn("Kullanılamıyor", text)  # an empty section drops its heading

    def test_status_with_a_location(self):
        self.make()
        [(_, text)] = self.ask("/durum ego")
        self.assertEqual(text.split("\n\n")[0], "🟢 Ego: 1/4 AC priz boş\n"
                                                 "Son okuma 09:00 (1 dk önce)")
        self.assertNotIn("F Blok", text)
        self.assertNotIn("⚡ DC", text)  # no DC socket at Ego
        [(_, unknown)] = self.ask("/durum kızılay")
        self.assertTrue(unknown.startswith("'kızılay' diye bir konum bulamadım."))

    def test_nothing_free_adds_the_history_line_once_it_is_measured(self):
        self.make()
        self.world.clock.advance(POLL_S)
        self.world.poll({cid: "charging" for cid in EGO})
        [(_, text)] = self.ask("/durum ego")
        self.assertTrue(text.startswith("🔴 Ego: boş AC priz yok (0/4)\nSon okuma"))
        self.assertNotIn("boşalıyor", text)  # no past day measured yet: line skipped

    def test_nothing_free_says_how_soon_one_usually_frees_up(self):
        world = World(datetime(2026, 11, 10, 6, 0, tzinfo=UTC))  # Tuesday 09:00 local
        self.addCleanup(world.storage.close)

        def morning(ts):  # Ego all busy 09:00-09:40 local, then Ego 1 free
            busy = ts.minute < 40
            return {cid: "charging" if busy or cid != EGO[0] else "available" for cid in EGO}
        for day in range(3):
            world.clock.current = datetime(2026, 11, 10 + day, 6, 0, tzinfo=UTC)
            world.run(datetime(2026, 11, 10 + day, 7, 0, tzinfo=UTC), morning)
        world.clock.current = datetime(2026, 11, 13, 6, 0, tzinfo=UTC)
        world.poll({cid: "charging" for cid in EGO}, default="offline")
        world.clock.advance(60)
        reply = make_commands(world).reply("/durum ego", Conversation(TELEGRAM, OWNER, "/"))
        self.assertEqual(reply.text.split("\n\n")[0].split("\n")[2],
                         "Bu saatte genelde 42 dk içinde biri boşalıyor · /haberver ego")

    def test_free_text_naming_a_location_is_answered_as_durum(self):
        self.make()
        for text in ("ego", "Ego boş mu", "f blok boş mu?"):
            [(_, reply)] = self.ask(text)
            self.assertRegex(reply, r"^(🟢|🔴) (Ego|F blok): ", text)
        [(_, reply)] = self.ask("ego boş mu")
        self.assertTrue(reply.startswith("🟢 Ego: "))
        [(_, reply)] = self.ask("merhaba")
        self.assertTrue(reply.startswith("Bu komutu tanımıyorum."))
        self.assertLess(reply.count("\n"), 4)  # short, not the whole help

    def test_subscribe_fires_once_on_busy_to_available(self):
        self.make()
        [(_, reply)] = self.ask("/haberver ego")
        self.assertEqual(reply, "Tamam: Ego'da bir priz boşalınca bir kez haber vereceğim.\n"
                                "Şu an boş: Ego 4")  # already free right now
        self.telegram.sent.clear()

        self.world.poll({F_BLOK_2: "available"})  # frees up, but not in Ego
        self.assertEqual(self.telegram.sent, [])
        self.world.clock.advance(POLL_S)
        self.world.poll({F_BLOK_2: "available", EGO[0]: "available"})
        self.assertEqual(len(self.telegram.sent), 1)
        chat, text = self.telegram.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertEqual(text, "🟢 Ego 1 boşaldı · 09:04\nŞu an 2/4 boş · /durum ego\n"
                               "Tekrar haber için: /haberver ego")  # keeps its location

        for state in ("charging", "available"):  # a second freeing must not notify again
            self.world.clock.advance(POLL_S)
            self.world.poll({EGO[0]: state})
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.world.storage.subscriptions(), [])

    def test_failed_notification_keeps_subscription(self):
        self.make()
        self.ask("/haberver")
        self.telegram.fail_send = True
        self.world.poll({EGO[0]: "available"})
        self.assertEqual(self.world.storage.poll_counts(), (2, 0))  # recorder unaffected
        self.assertEqual(len(self.world.storage.subscriptions()), 1)

    def test_unsubscribe(self):
        self.make()
        self.ask("/haberver f blok")
        [(_, text)] = self.ask("/iptal")
        self.assertEqual(text, "Tamam, artık haber vermeyeceğim.")
        self.world.poll({F_BLOK_2: "available"})
        self.assertEqual(self.telegram.sent, [(OWNER, "Tamam, artık haber vermeyeceğim.")])
        [(_, again)] = self.ask("/iptal")
        self.assertEqual(again, "Bekleyen bir haber isteğin yok. Kurmak için: /haberver")

    def test_unknown_location_lists_locations(self):
        self.make()
        [(_, text)] = self.ask("/haberver kızılay")
        self.assertEqual(text, "'kızılay' diye bir konum bulamadım.\n"
                               "Şunlardan birini yaz: arge · ego · f blok · otopark")
        self.assertNotIn("dc", text.split("\n")[1])  # a DC-only place is not offered
        self.assertEqual(self.world.storage.subscriptions(), [])

    def test_unauthorised_chat_is_refused(self):
        self.make()
        sent = self.ask("/haberver", chat_id=STRANGER)
        self.assertEqual(sent, [(STRANGER, "Bu bot özel kullanım içindir.")])
        self.assertEqual(self.world.storage.subscriptions(), [])
        self.assertEqual(self.ask("/durum", chat_id=STRANGER)[0][1],
                         "Bu bot özel kullanım içindir.")

    def test_no_chat_id_onboarding(self):
        self.make(chat_id=None)
        [(chat, text)] = self.ask("/durum", chat_id=STRANGER)
        self.assertEqual(chat, STRANGER)
        self.assertIn(f"TELEGRAM_CHAT_ID={STRANGER}", text)
        self.assertNotIn("AC priz boş", text)
        self.ask("/haberver", chat_id=STRANGER)
        self.assertEqual(self.world.storage.subscriptions(), [])

    def test_estimate_command(self):
        self.make()
        [(_, text)] = self.ask("/tahmin %20 80")
        # 06:01 UTC = 09:01 local, + 5 h 22.6 min
        self.assertEqual(text, "🔋 %20 → %80\n"
                               "⏱ 5 sa 23 dk · bugün 14:23'te hazır\n"
                               "💰 ~530 TL\n"
                               "⚡ 57 kWh, 10,6 kW ile\n"
                               "Not: 2 TL hizmet bedeli dahil değil.")
        [(_, usage)] = self.ask("/tahmin 80")
        self.assertIn("Kullanım", usage)

    def test_stats_command_states_observation_counts(self):
        self.make()
        for _ in range(3):
            self.world.clock.advance(POLL_S)
            self.world.poll()
        [(_, text)] = self.ask("/istatistik ego")
        self.assertTrue(text.startswith("📊 Ego · şu an 1/4 priz boş\n\n"))
        self.assertIn("Takma: henüz kayıt yok", text)
        self.assertIn("Prizde kalma: henüz ölçülen araç yok", text)
        self.assertIn("██████████ %100 · Ego 4", text)
        self.assertNotIn("gözlem", text)
        self.assertTrue(text.endswith("\n\n10 dakikalık kayda dayanıyor · 0 takma, "
                                       "0 çıkarma, 0 araç."))

    def test_stats_when_all_busy_mentions_wait(self):
        self.make()
        self.world.clock.advance(POLL_S)
        self.world.poll({cid: "charging" for cid in EGO})
        [(_, text)] = self.ask("/istatistik ego")
        self.assertTrue(text.startswith("📊 Ego · şu an hepsi dolu\n"
                                        "Bu saat için geçmiş gün kaydı yok.\n\n"), text)

    def test_unknown_command_and_bot_suffix(self):
        self.make()
        self.assertIn("tanımıyorum", self.ask("/foo")[0][1])
        self.assertIn("/haberver", self.ask("/yardim@ecobox_bot")[0][1])

    def test_command_menu_is_registered_and_its_failure_is_harmless(self):
        self.make()
        self.bot.register_commands()
        self.assertEqual(dict(self.telegram.commands)["durum"], "[konum] – şu an hangi priz boş")
        self.telegram.commands, self.telegram.fail_commands = None, True
        self.bot.register_commands()  # logged, not raised
        self.assertIsNone(self.telegram.commands)
        self.assertIn("🟢", self.ask("/durum")[0][1])  # the bot still answers

    def test_non_text_updates_are_ignored(self):
        self.make()
        self.bot.handle_update({"update_id": 5, "edited_message": {}})
        self.bot.handle_update({"update_id": 6, "message": {"chat": {"id": OWNER}}})
        self.assertEqual(self.telegram.sent, [])


if __name__ == "__main__":
    unittest.main()
