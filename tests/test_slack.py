import io
import json
import logging
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from helpers import (EGO, F_BLOK_2, FIXTURES, GAP_S, POLL_S, FakeSlack, FakeTelegram, World,
                     message)

from ecobox.bot import TelegramBot, TelegramOutbox
from ecobox.commands import SLACK, TELEGRAM, CommandHandler, Conversation, parse_command
from ecobox.config import Config
from ecobox.estimate import ChargeEstimator
from ecobox.notifier import SubscriptionNotifier
from ecobox.service import Backoff, SlackLoop
from ecobox.slack import HttpSlackClient, SlackError, SlackRateLimited
from ecobox.slack_bot import (STATE_CHANNEL_ID, STATE_LAST_TS, STATE_READY, SlackBot,
                              SlackOutbox)
from ecobox.stats import Statistics
from ecobox.storage import Storage

OWNER_SLACK = "U0OWNER"
OTHER_SLACK = "U0OTHER"
OWNER_TELEGRAM = 1111


def quiet(test: unittest.TestCase) -> None:
    logging.disable(logging.CRITICAL)
    test.addCleanup(logging.disable, logging.NOTSET)


def make_commands(world: World) -> CommandHandler:
    return CommandHandler(world.storage, Statistics(world.storage, GAP_S),
                          ChargeEstimator(10.6, 0.95, True), world.clock, True)


class ParseCommandTest(unittest.TestCase):
    def test_plain_words_prefixes_and_case(self):
        cases = {
            "durum": ("durum", ""),
            "Durum": ("durum", ""),
            "!durum": ("durum", ""),
            "/durum": ("durum", ""),
            "haberver ego": ("haberver", "ego"),
            "  istatistik f blok  ": ("istatistik", "f blok"),
            "TAHMİN 20 80": ("tahmin", "20 80"),
            "yardım": ("yardim", ""),
            "YARDIM": ("yardim", ""),
            "/yardim@ecobox_bot": ("yardim", ""),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_command(text), expected)


class SlackCommandsTest(unittest.TestCase):
    def setUp(self):
        self.world = World(datetime(2026, 11, 10, 6, 0, tzinfo=UTC))
        self.addCleanup(self.world.storage.close)
        self.world.poll()
        self.commands = make_commands(self.world)
        self.chat = Conversation(SLACK, FakeSlack.CHANNEL, "")

    def test_help_shows_plain_word_commands(self):
        for text in ("yardım", "yardim", "!yardim", "/yardim"):
            help_text = self.commands.reply(text, self.chat)
            self.assertIn("\ndurum – ", help_text)
            self.assertIn("haberver ego", help_text)
            self.assertIn("tahmin 20 80", help_text)
            self.assertNotIn("/", help_text.replace("/çıkarma", ""))

    def test_unknown_command_and_usage_use_plain_words(self):
        self.assertIn("\niptal – ", self.commands.reply("merhaba", self.chat))
        self.assertIn("ör. tahmin 20 80", self.commands.reply("tahmin 80", self.chat))

    def test_every_command_works_without_slash(self):
        self.assertIn("Boş AC priz: 1/12", self.commands.reply("Durum", self.chat))
        self.assertIn("haber vereceğim", self.commands.reply("haberver f blok", self.chat))
        self.assertEqual(self.commands.reply("İPTAL", self.chat), "Abonelik iptal edildi.")
        self.assertIn("İstatistik: f blok (AC)", self.commands.reply("istatistik f blok",
                                                                     self.chat))
        self.assertIn("57.0 kWh", self.commands.reply("tahmin 20 80", self.chat))


class SlackBotTest(unittest.TestCase):
    def setUp(self):
        quiet(self)
        self.world = World(datetime(2026, 11, 10, 6, 0, tzinfo=UTC))
        self.addCleanup(self.world.storage.close)
        self.world.poll()
        self.slack = FakeSlack()
        self.commands = make_commands(self.world)

    def bot(self, name="ecobox-sarj") -> SlackBot:
        """A fresh SlackBot over the same storage, as after a process restart."""
        return SlackBot(self.slack, self.commands, self.world.storage, self.world.clock,
                        OWNER_SLACK, name)

    def test_bootstrap_creates_invites_stores_and_posts_help(self):
        self.bot().poll_once()
        self.assertEqual(self.slack.calls[:3], [("create", "ecobox-sarj"),
                                                ("invite", FakeSlack.CHANNEL, OWNER_SLACK),
                                                ("post", FakeSlack.CHANNEL)])
        storage = self.world.storage
        self.assertEqual(storage.get_state(STATE_CHANNEL_ID), FakeSlack.CHANNEL)
        self.assertEqual(storage.get_state(STATE_READY), "1")
        [(channel, help_text)] = self.slack.posted
        self.assertEqual(channel, FakeSlack.CHANNEL)
        self.assertIn("haberver ego", help_text)
        self.assertNotIn("/haberver", help_text)
        help_ts = storage.get_state(STATE_LAST_TS)
        self.assertEqual(self.slack.calls[3], ("history", FakeSlack.CHANNEL, help_ts))

    def test_restart_does_not_create_again(self):
        self.bot().poll_once()
        self.slack.calls.clear()
        self.bot().poll_once()
        self.assertEqual(self.slack.methods(), ["history"])
        self.assertEqual(len(self.slack.posted), 1)

    def test_failed_invite_is_retried_without_a_second_create(self):
        self.slack.fail_next["invite"] = SlackError("conversations.invite", "internal_error")
        with self.assertRaises(SlackError):
            self.bot().poll_once()
        self.assertEqual(self.world.storage.get_state(STATE_CHANNEL_ID), FakeSlack.CHANNEL)
        self.assertIsNone(self.world.storage.get_state(STATE_READY))
        self.slack.calls.clear()
        self.bot().poll_once()
        self.assertEqual(self.slack.methods(), ["invite", "post", "history"])

    def test_owner_already_in_channel_counts_as_invited(self):
        self.slack.fail_next["invite"] = SlackError("conversations.invite", "already_in_channel")
        self.bot().poll_once()
        self.assertEqual(self.world.storage.get_state(STATE_READY), "1")

    def test_name_taken_stops_the_transport_with_a_clear_log(self):
        self.slack.taken_names.add("ecobox-sarj")
        loop = SlackLoop(self.bot(), 3, Backoff(5, 900))
        logging.disable(logging.NOTSET)
        with self.assertLogs("ecobox.service", "ERROR") as logs:
            self.assertIsNone(loop.step())
        self.assertIn("'ecobox-sarj' is already taken", logs.output[0])
        self.assertEqual(self.slack.methods(), ["create"])
        self.assertIsNone(self.world.storage.get_state(STATE_CHANNEL_ID))

    def test_only_the_owners_plain_messages_are_answered(self):
        bot = self.bot()
        bot.poll_once()
        posted_before = len(self.slack.posted)
        self.slack.say(OTHER_SLACK, "durum")
        self.slack.say(OWNER_SLACK, "durum", subtype="channel_join")
        self.slack.say(OWNER_SLACK, "durum", bot_id="B0OTHER")
        self.slack.say(OWNER_SLACK, "   ")
        self.assertEqual(bot.poll_once(), 0)
        self.assertEqual(len(self.slack.posted), posted_before)

        self.slack.say(OWNER_SLACK, "durum")
        self.slack.say(OWNER_SLACK, "yardım")
        self.assertEqual(bot.poll_once(), 2)
        replies = [text for _, text in self.slack.posted[posted_before:]]
        self.assertIn("Boş AC priz: 1/12", replies[0])
        self.assertIn("haberver ego", replies[1])
        self.assertEqual(bot.poll_once(), 0)  # its own replies are not commands

    def test_restart_does_not_replay_old_messages(self):
        bot = self.bot()
        bot.poll_once()
        command_ts = self.slack.say(OWNER_SLACK, "haberver ego")
        self.assertEqual(bot.poll_once(), 1)
        self.assertEqual(self.world.storage.get_state(STATE_LAST_TS), command_ts)

        posted_before = len(self.slack.posted)
        restarted = self.bot()
        self.assertEqual(restarted.poll_once(), 0)
        self.assertEqual(len(self.slack.posted), posted_before)
        self.assertEqual(self.slack.calls[-1][2], command_ts)  # history asked only for newer
        reply_ts = self.slack.history_messages[-1]["ts"]
        self.assertEqual(self.world.storage.get_state(STATE_LAST_TS), reply_ts)
        self.assertEqual(self.bot().poll_once(), 0)

    def test_escaped_text_is_unescaped(self):
        bot = self.bot()
        bot.poll_once()
        self.slack.say(OWNER_SLACK, "istatistik f &amp; blok")
        bot.poll_once()
        self.assertIn("'f & blok' ile eşleşen AC priz yok", self.slack.posted[-1][1])

    def test_failing_command_does_not_block_later_messages(self):
        bot = self.bot()
        bot.poll_once()
        self.slack.say(OWNER_SLACK, "durum")
        self.slack.say(OWNER_SLACK, "yardim")
        with mock.patch.object(self.commands, "reply",
                               side_effect=[RuntimeError("boom"), "ok"]):
            self.assertEqual(bot.poll_once(), 1)
        self.assertEqual(self.slack.posted[-1][1], "ok")


class SlackLoopTest(unittest.TestCase):
    def setUp(self):
        quiet(self)
        self.world = World()
        self.addCleanup(self.world.storage.close)
        self.slack = FakeSlack()
        self.loop = SlackLoop(SlackBot(self.slack, make_commands(self.world), self.world.storage,
                                       self.world.clock, OWNER_SLACK, "ecobox-sarj"),
                              3, Backoff(5, 900))

    def test_rate_limit_waits_retry_after(self):
        self.slack.fail_next["history"] = SlackRateLimited("conversations.history", 42)
        self.assertEqual(self.loop.step(), 42)
        self.assertEqual(self.loop.step(), 3)

    def test_transient_error_backs_off_then_recovers(self):
        self.assertEqual(self.loop.step(), 3)
        for expected in (5, 10):
            self.slack.fail_next["history"] = SlackError("conversations.history",
                                                         "internal_error")
            self.assertEqual(self.loop.step(), expected)
        self.assertEqual(self.loop.step(), 3)

    def test_fatal_error_stops(self):
        self.slack.fail_next["create"] = SlackError("conversations.create", "invalid_auth")
        self.assertIsNone(self.loop.step())

    def test_slack_failure_does_not_touch_the_recorder(self):
        self.slack.fail_next["create"] = SlackError("conversations.create", "internal_error")
        self.loop.step()
        self.world.poll()
        self.assertEqual(self.world.storage.poll_counts(), (1, 0))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HttpSlackClientTest(unittest.TestCase):
    TOKEN = "xoxb-SECRET-TOKEN"

    def setUp(self):
        self.client = HttpSlackClient(self.TOKEN, http_timeout_s=9)
        self.requests: list[urllib.request.Request] = []

    def serve(self, *payloads):
        responses = list(payloads)

        def urlopen(request, timeout):
            self.assertEqual(timeout, 9)
            self.requests.append(request)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return FakeResponse(json.dumps(response).encode())

        return mock.patch.object(urllib.request, "urlopen", urlopen)

    def form(self, index: int) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(self.requests[index].data.decode()))

    def test_request_shape_and_token_only_in_header(self):
        with self.serve({"ok": True, "channel": {"id": "G1"}}):
            self.assertEqual(self.client.create_private_channel("ecobox-sarj"), "G1")
        request = self.requests[0]
        self.assertEqual(request.full_url, "https://slack.com/api/conversations.create")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {self.TOKEN}")
        self.assertEqual(self.form(0), {"name": "ecobox-sarj", "is_private": "true"})
        self.assertNotIn("SECRET", request.full_url + request.data.decode())

    def test_ok_false_raises_with_the_error_code(self):
        with self.serve({"ok": False, "error": "name_taken"}):
            with self.assertRaises(SlackError) as caught:
                self.client.create_private_channel("ecobox-sarj")
        self.assertEqual(caught.exception.error, "name_taken")
        self.assertTrue(caught.exception.fatal)
        with self.serve({"ok": False, "error": "internal_error"}):
            with self.assertRaises(SlackError) as caught:
                self.client.post_message("G1", "x")
        self.assertFalse(caught.exception.fatal)

    def test_429_honours_retry_after(self):
        error = urllib.error.HTTPError("https://slack.com/api/conversations.history", 429,
                                       "Too Many Requests", {"Retry-After": "17"},
                                       io.BytesIO(b""))
        with self.serve(error):
            with self.assertRaises(SlackRateLimited) as caught:
                self.client.history("G1", "1.000000")
        self.assertEqual(caught.exception.retry_after_s, 17)

    def test_errors_never_contain_the_token(self):
        failure = urllib.error.URLError(f"failed with Bearer {self.TOKEN}")
        with self.serve(failure):
            with self.assertRaises(SlackError) as caught:
                self.client.post_message("G1", "x")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_history_paginates_and_sorts_oldest_first(self):
        with self.serve({"ok": True, "has_more": True, "messages": [{"ts": "3.000002"},
                                                                    {"ts": "3.000001"}],
                         "response_metadata": {"next_cursor": "c2"}},
                        {"ok": True, "has_more": False, "messages": [{"ts": "2.900000"}]}):
            messages = self.client.history("G1", "1.500000")
        self.assertEqual([m["ts"] for m in messages], ["2.900000", "3.000001", "3.000002"])
        self.assertEqual(self.form(0), {"channel": "G1", "oldest": "1.500000",
                                        "inclusive": "false", "limit": "200"})
        self.assertEqual(self.form(1)["cursor"], "c2")

    def test_post_message_escapes_and_returns_ts(self):
        with self.serve({"ok": True, "ts": "5.000001"}):
            ts = self.client.post_message("G1", "tahmin <başlangıç %> & <hedef %>")
        self.assertEqual(ts, "5.000001")
        form = self.form(0)
        self.assertEqual(form["text"], "tahmin &lt;başlangıç %&gt; &amp; &lt;hedef %&gt;")
        self.assertEqual(form["mrkdwn"], "false")


class PerTransportRoutingTest(unittest.TestCase):
    def setUp(self):
        quiet(self)
        self.world = World(datetime(2026, 11, 10, 6, 0, tzinfo=UTC))
        self.addCleanup(self.world.storage.close)
        commands = make_commands(self.world)
        self.telegram = FakeTelegram()
        self.slack = FakeSlack()
        self.telegram_bot = TelegramBot(self.telegram, commands, OWNER_TELEGRAM)
        self.slack_bot = SlackBot(self.slack, commands, self.world.storage, self.world.clock,
                                  OWNER_SLACK, "ecobox-sarj")
        self.slack_bot.poll_once()  # bootstrap
        self.notifier = SubscriptionNotifier(self.world.storage, {
            TELEGRAM: TelegramOutbox(self.telegram), SLACK: SlackOutbox(self.slack)})
        self.world.recorder.add_listener(self.notifier.on_transitions)
        self.world.poll()
        self.world.clock.advance(60)

    def telegram_says(self, text):
        self.telegram.sent.clear()
        self.telegram_bot.handle_update(message(OWNER_TELEGRAM, text))
        return self.telegram.sent[-1][1]

    def slack_says(self, text):
        self.slack.say(OWNER_SLACK, text)
        self.slack_bot.poll_once()
        return self.slack.posted[-1][1]

    def slack_notifications(self):
        return [text for _, text in self.slack.posted if text.startswith("Boşaldı")]

    def test_both_transports_fire_on_their_own_side(self):
        self.assertIn("haber vereceğim", self.telegram_says("/haberver ego"))
        self.assertIn("haber vereceğim", self.slack_says("haberver f blok"))
        self.assertEqual({(s.transport, s.chat_id) for s in self.world.storage.subscriptions()},
                         {(TELEGRAM, OWNER_TELEGRAM), (SLACK, FakeSlack.CHANNEL)})
        self.telegram.sent.clear()

        self.world.poll({F_BLOK_2: "available"})  # F Blok: Slack fires, Telegram waits
        [slack_note] = self.slack_notifications()
        self.assertIn("Boşaldı: Hacettepe - F Blok 2", slack_note)
        self.assertIn("tekrar için haberver.", slack_note)
        self.assertEqual(self.telegram.sent, [])

        self.world.clock.advance(POLL_S)
        self.world.poll({F_BLOK_2: "available", EGO[0]: "available"})  # Ego: Telegram fires
        [(chat, telegram_note)] = self.telegram.sent
        self.assertEqual(chat, OWNER_TELEGRAM)
        self.assertIn("Boşaldı: Hacettepe Ego - 1 Ac", telegram_note)
        self.assertIn("tekrar için /haberver.", telegram_note)
        self.assertEqual(len(self.slack_notifications()), 1)
        self.assertEqual(self.world.storage.subscriptions(), [])

    def test_same_socket_notifies_both_when_both_subscribed(self):
        self.telegram_says("/haberver")
        self.slack_says("haberver")
        self.world.poll({EGO[0]: "available"})
        self.assertEqual(len(self.telegram.sent), 2)  # the subscribe reply + the notification
        self.assertEqual(len(self.slack_notifications()), 1)

    def test_cancel_only_affects_its_own_transport(self):
        self.telegram_says("/haberver")
        self.slack_says("haberver")
        self.assertEqual(self.slack_says("iptal"), "Abonelik iptal edildi.")
        self.assertEqual([s.transport for s in self.world.storage.subscriptions()], [TELEGRAM])

    def test_subscription_waits_while_its_transport_is_not_running(self):
        self.slack_says("haberver")
        telegram_only = SubscriptionNotifier(self.world.storage,
                                             {TELEGRAM: TelegramOutbox(self.telegram)})
        self.world.recorder._listeners = [telegram_only.on_transitions]
        self.telegram.sent.clear()
        self.world.poll({EGO[0]: "available"})
        self.assertEqual(self.telegram.sent, [])
        self.assertEqual([s.transport for s in self.world.storage.subscriptions()], [SLACK])


class MigrationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "old.sqlite3"
        conn = sqlite3.connect(self.path)
        conn.executescript((FIXTURES / "schema_v1.sql").read_text(encoding="utf-8"))
        conn.execute("INSERT INTO poll_log(ts_utc, kind, ok, rows_total, rows_in_scope, "
                     "duration_ms) VALUES ('2026-11-12T08:00:00Z', 'status', 1, 168, 14, 90)")
        conn.execute("INSERT INTO subscriptions(chat_id, location_term, created_utc) "
                     "VALUES (1111, 'ego', '2026-11-12T08:01:00Z')")
        conn.commit()
        conn.close()

    def columns(self) -> list[str]:
        conn = sqlite3.connect(self.path)
        try:
            return [r[1] for r in conn.execute("PRAGMA table_info(subscriptions)")]
        finally:
            conn.close()

    def test_old_database_is_migrated_in_place(self):
        self.assertNotIn("transport", self.columns())
        storage = Storage(self.path)
        try:
            [old] = storage.subscriptions()
            self.assertEqual((old.transport, old.chat_id, old.location_term),
                             (TELEGRAM, 1111, "ego"))
            self.assertEqual(storage.poll_counts(), (1, 0))
            storage.add_subscription(SLACK, "G0ECOBOX", None,
                                     datetime(2026, 11, 13, tzinfo=UTC))
            storage.set_state(STATE_LAST_TS, "1.000001", datetime(2026, 11, 13, tzinfo=UTC))
        finally:
            storage.close()
        self.assertEqual(self.columns(),
                         ["id", "chat_id", "location_term", "created_utc", "transport"])

        reopened = Storage(self.path)  # idempotent: a second open changes nothing
        try:
            self.assertEqual([(s.transport, s.chat_id) for s in reopened.subscriptions()],
                             [(TELEGRAM, 1111), (SLACK, "G0ECOBOX")])
            self.assertEqual(reopened.get_state(STATE_LAST_TS), "1.000001")
            self.assertEqual(reopened.poll_counts(), (1, 0))
        finally:
            reopened.close()
        self.assertEqual(self.columns().count("transport"), 1)

    def test_old_code_style_insert_still_defaults_to_telegram(self):
        Storage(self.path).close()
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO subscriptions(chat_id, location_term, created_utc) "
                     "VALUES (2222, NULL, '2026-11-12T09:00:00Z')")
        conn.commit()
        conn.close()
        storage = Storage(self.path)
        try:
            self.assertEqual([s.transport for s in storage.subscriptions()],
                             [TELEGRAM, TELEGRAM])
        finally:
            storage.close()


class SlackConfigTest(unittest.TestCase):
    def test_defaults(self):
        config = Config.load(Path("/nonexistent/.env"), environ={})
        self.assertFalse(config.slack_enabled)
        self.assertEqual(config.slack_channel_name, "ecobox-sarj")
        self.assertEqual(config.slack_poll_interval_s, 3.0)

    def test_values(self):
        config = Config.load(Path("/nonexistent/.env"), environ={
            "SLACK_BOT_TOKEN": "xoxb-1", "SLACK_USER_ID": "U1", "SLACK_CHANNEL_NAME": "sarj",
            "SLACK_POLL_INTERVAL_S": "5"})
        self.assertTrue(config.slack_enabled)
        self.assertEqual((config.slack_channel_name, config.slack_poll_interval_s), ("sarj", 5))
        self.assertFalse(Config.load(Path("/nonexistent"), {"SLACK_BOT_TOKEN": "x"})
                         .slack_enabled)


if __name__ == "__main__":
    unittest.main()
