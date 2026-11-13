import logging
import unittest
from datetime import UTC, datetime

from helpers import (EGO, F_BLOK_2, POLL_S, FakeTelegram, World, display_scope, make_commands,
                     message)

from ecobox.bot import TelegramBot, TelegramOutbox
from ecobox.commands import TELEGRAM
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
        self.assertEqual(blocks[1], "Boş\n🟢 Ego 4 · az önce")
        self.assertTrue(blocks[2].startswith("Dolu\n🔴 "))
        self.assertEqual(blocks[2].count("\n"), 11)  # heading + 11 busy sockets
        self.assertEqual(blocks[-1], "⚡ DC: 1/2 boş")
        self.assertNotIn("Hacettepe", text)
        self.assertNotIn("Kullanılamıyor", text)  # an empty section drops its heading

    def test_subscribe_fires_once_on_busy_to_available(self):
        self.make()
        [(_, reply)] = self.ask("/haberver ego")
        self.assertIn("haber vereceğim", reply)
        self.assertIn("Şu an boş: Ego 4", reply)  # already free right now
        self.telegram.sent.clear()

        self.world.poll({F_BLOK_2: "available"})  # frees up, but not in Ego
        self.assertEqual(self.telegram.sent, [])
        self.world.clock.advance(POLL_S)
        self.world.poll({F_BLOK_2: "available", EGO[0]: "available"})
        self.assertEqual(len(self.telegram.sent), 1)
        chat, text = self.telegram.sent[0]
        self.assertEqual(chat, OWNER)
        self.assertEqual(text, "🟢 Boşaldı: Ego 1\nTekrar haber için: /haberver")

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
        self.assertEqual(text, "Abonelik iptal edildi.")
        self.world.poll({F_BLOK_2: "available"})
        self.assertEqual(self.telegram.sent, [(OWNER, "Abonelik iptal edildi.")])

    def test_unknown_location_lists_locations(self):
        self.make()
        [(_, text)] = self.ask("/haberver kızılay")
        self.assertIn("eşleşen AC priz yok", text)
        self.assertIn("Hacettepe Teknokent Ego", text)
        self.assertNotIn("Hacettepe Teknokent DC", text)  # DC-only location is not offered
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
                               "⚡ 57.0 kWh, 10.6 kW ile\n"
                               "Not: 2 TL hizmet bedeli hesaba katılmadı.")
        [(_, usage)] = self.ask("/tahmin 80")
        self.assertIn("Kullanım", usage)

    def test_stats_command_states_observation_counts(self):
        self.make()
        for _ in range(3):
            self.world.clock.advance(POLL_S)
            self.world.poll()
        [(_, text)] = self.ask("/istatistik ego")
        self.assertTrue(text.startswith("📊 Ego (AC)\n\n"))
        self.assertIn("Ne zaman takıyorlar? veri yok", text)
        self.assertIn("Ne kadar kalıyorlar? veri yok", text)
        self.assertIn("██████████ %100 · Ego 4", text)
        self.assertNotIn("gözlem", text)  # counts live on the one Dayanak line
        self.assertTrue(text.endswith("Dayanak: 4 okuma, 0 takma, 0 alma, 0 oturum; "
                                       "3 belirsiz kayıt sayılmadı."))

    def test_stats_when_all_busy_mentions_wait(self):
        self.make()
        self.world.clock.advance(POLL_S)
        self.world.poll({cid: "charging" for cid in EGO})
        [(_, text)] = self.ask("/istatistik ego")
        self.assertIn("Şu an hepsi dolu.", text)
        self.assertIn("geçmiş gün gözlemi yok", text)

    def test_unknown_command_and_bot_suffix(self):
        self.make()
        self.assertIn("tanımıyorum", self.ask("/foo")[0][1])
        self.assertIn("/haberver", self.ask("/yardim@ecobox_bot")[0][1])

    def test_non_text_updates_are_ignored(self):
        self.make()
        self.bot.handle_update({"update_id": 5, "edited_message": {}})
        self.bot.handle_update({"update_id": 6, "message": {"chat": {"id": OWNER}}})
        self.assertEqual(self.telegram.sent, [])


if __name__ == "__main__":
    unittest.main()
