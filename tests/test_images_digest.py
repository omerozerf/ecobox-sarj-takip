import io
import json
import logging
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from unittest import mock

from helpers import (PNG_SIGNATURE, POLL_S, FakeRenderer, FakeSlack, FakeTelegram, World,
                     make_commands, message)

from ecobox.bot import TelegramBot, TelegramOutbox
from ecobox.commands import SLACK, TELEGRAM
from ecobox.digest import STATE_PREFIX, DigestSchedule, DigestTarget, WeeklyDigest
from ecobox.replies import Image, Reply
from ecobox.slack import HttpSlackClient, SlackError
from ecobox.slack_bot import SlackBot, SlackOutbox
from ecobox.telegram import HttpTelegramClient, TelegramError

OWNER_TELEGRAM = 1111
OWNER_SLACK = "U0OWNER"
PNG = PNG_SIGNATURE + b"chart"
REPLY = Reply("📈 başlık", (Image(PNG, "a.png", "ilk"), Image(PNG, "b.png", "ikinci")))


def quiet(test: unittest.TestCase) -> None:
    logging.disable(logging.CRITICAL)
    test.addCleanup(logging.disable, logging.NOTSET)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(requests: list, *responses):
    queue = list(responses)

    def urlopen(request, timeout):
        requests.append(request)
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response if isinstance(response, bytes)
                            else json.dumps(response).encode())

    return mock.patch.object(urllib.request, "urlopen", urlopen)


class TelegramPhotoTest(unittest.TestCase):
    TOKEN = "123456:SECRET-TOKEN"

    def test_send_photo_is_multipart_with_the_png(self):
        requests = []
        with serve(requests, {"ok": True, "result": {}}):
            HttpTelegramClient(self.TOKEN).send_photo(42, PNG, "isi.png", "Isı haritası")
        [request] = requests
        self.assertTrue(request.full_url.endswith("/sendPhoto"))
        content_type = request.get_header("Content-type")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        boundary = content_type.split("boundary=")[1].encode()
        parts = request.data.split(b"--" + boundary)
        self.assertEqual(len(parts), 5)  # preamble, chat_id, caption, photo, closing
        self.assertIn(b'name="chat_id"\r\n\r\n42\r\n', parts[1])
        self.assertIn('name="caption"\r\n\r\nIsı haritası\r\n'.encode(), parts[2])
        self.assertIn(b'name="photo"; filename="isi.png"\r\nContent-Type: image/png\r\n\r\n'
                      + PNG + b"\r\n", parts[3])
        self.assertEqual(parts[4], b"--\r\n")

    def test_photo_errors_never_contain_the_token(self):
        failure = urllib.error.URLError(f"failed for /bot{self.TOKEN}/sendPhoto")
        with serve([], failure):
            with self.assertRaises(TelegramError) as caught:
                HttpTelegramClient(self.TOKEN).send_photo(1, PNG, "a.png", "x")
        self.assertNotIn("SECRET", str(caught.exception))


class SlackUploadTest(unittest.TestCase):
    TOKEN = "xoxb-SECRET"

    def form(self, request) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(request.data.decode()))

    def test_external_upload_flow(self):
        requests = []
        upload_url = "https://files.slack.com/upload/v1/ABC"
        with serve(requests, {"ok": True, "upload_url": upload_url, "file_id": "F123"},
                   b"OK - 13", {"ok": True, "files": [{"id": "F123"}]}):
            HttpSlackClient(self.TOKEN).upload_file("G1", PNG, "isi.png", "Isı haritası")
        get_url, upload, complete = requests
        self.assertEqual(get_url.full_url, "https://slack.com/api/files.getUploadURLExternal")
        self.assertEqual(self.form(get_url), {"filename": "isi.png", "length": str(len(PNG))})
        self.assertEqual(get_url.get_header("Authorization"), f"Bearer {self.TOKEN}")
        self.assertEqual(upload.full_url, upload_url)
        self.assertEqual(upload.data, PNG)
        self.assertIsNone(upload.get_header("Authorization"))  # the URL is pre-signed
        self.assertEqual(complete.full_url, "https://slack.com/api/files.completeUploadExternal")
        form = self.form(complete)
        self.assertEqual(form["channel_id"], "G1")
        self.assertEqual(json.loads(form["files"]), [{"id": "F123", "title": "Isı haritası"}])

    def test_missing_scope_surfaces_as_a_slack_error(self):
        with serve([], {"ok": False, "error": "missing_scope"}):
            with self.assertRaises(SlackError) as caught:
                HttpSlackClient(self.TOKEN).upload_file("G1", PNG, "a.png", "x")
        self.assertEqual(caught.exception.error, "missing_scope")

    def test_failed_byte_upload_is_a_slack_error_without_the_token(self):
        error = urllib.error.HTTPError("https://files.slack.com/upload/v1/ABC", 500, "boom", {},
                                       io.BytesIO(b""))
        with serve([], {"ok": True, "upload_url": "https://files.slack.com/u", "file_id": "F"},
                   error):
            with self.assertRaises(SlackError) as caught:
                HttpSlackClient(self.TOKEN).upload_file("G1", PNG, "a.png", "x")
        self.assertEqual(str(caught.exception), "files.upload: HTTP 500")


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        quiet(self)
        self.world = World(datetime(2026, 11, 10, 6, 0, tzinfo=UTC))
        self.addCleanup(self.world.storage.close)
        for _ in range(3):
            self.world.poll()
            self.world.clock.advance(POLL_S)
        self.commands = make_commands(self.world, FakeRenderer())

    def test_telegram_sends_text_then_each_photo(self):
        telegram = FakeTelegram()
        TelegramBot(telegram, self.commands, OWNER_TELEGRAM).handle_update(
            message(OWNER_TELEGRAM, "/grafik zaman"))
        [(chat, text)] = telegram.sent
        self.assertEqual(chat, OWNER_TELEGRAM)
        self.assertTrue(text.startswith("📈 Hacettepe: priz zaman çizelgesi"))
        self.assertEqual([(c, name) for c, _, name, _ in telegram.photos],
                         [(OWNER_TELEGRAM, "priz-zaman-cizelgesi-24s.png"),
                          (OWNER_TELEGRAM, "priz-zaman-cizelgesi-7g.png")])
        self.assertTrue(all(data.startswith(PNG_SIGNATURE) for _, data, _, _ in telegram.photos))

    def test_telegram_photo_failure_is_reported_in_chat(self):
        telegram = FakeTelegram()
        telegram.fail_photo = True
        TelegramOutbox(telegram).send_reply(OWNER_TELEGRAM, REPLY)
        self.assertEqual([text for _, text in telegram.sent],
                         ["📈 başlık", "Grafik gönderilemedi; biraz sonra tekrar deneyin."])

    def test_slack_uploads_into_its_channel(self):
        slack = FakeSlack()
        bot = SlackBot(slack, self.commands, self.world.storage, self.world.clock, OWNER_SLACK,
                       "ecobox-sarj")
        bot.poll_once()
        slack.say(OWNER_SLACK, "grafik")
        self.assertEqual(bot.poll_once(), 1)
        self.assertTrue(slack.posted[-1][1].startswith("📈 Hacettepe: gün × saat"))
        self.assertIn("(grafik <tür>)", slack.posted[-1][1])  # Slack writes it without "/"
        [(channel, data, filename, title)] = slack.uploads
        self.assertEqual((channel, filename), (FakeSlack.CHANNEL, "bos-priz-isi-haritasi.png"))
        self.assertTrue(data.startswith(PNG_SIGNATURE))
        self.assertEqual(bot.poll_once(), 0)  # the bot's own file post is not a command

    def test_slack_missing_scope_keeps_the_transport_running(self):
        slack = FakeSlack()
        slack.fail_next["upload"] = SlackError("files.completeUploadExternal", "missing_scope")
        SlackOutbox(slack).send_reply("G1", REPLY)
        self.assertEqual([text for _, text in slack.posted],
                         ["📈 başlık", "Grafik gönderilemedi: Slack uygulamasına files:write "
                                       "izni eklenmeli (README, Slack botunu kurma)."])


class RecordingOutbox:
    command_prefix = ""

    def __init__(self, fail: bool = False):
        self.replies: list[tuple] = []
        self.fail = fail

    def send(self, chat_id, text):
        raise AssertionError("the digest goes through send_reply")

    def send_reply(self, chat_id, reply):
        if self.fail:
            raise ConnectionError("down")
        self.replies.append((chat_id, reply))


class WeeklyDigestTest(unittest.TestCase):
    # Monday 09:00 local (06:00 UTC) on 16 November 2026, ISO week 47
    MONDAY_0900 = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)

    def setUp(self):
        quiet(self)
        self.world = World(self.MONDAY_0900 - timedelta(minutes=1))
        self.addCleanup(self.world.storage.close)
        self.days = 5.0
        self.composed: list[datetime] = []
        self.telegram, self.slack = RecordingOutbox(), RecordingOutbox()
        self.slack_channel = "G0ECOBOX"

    def digest(self, min_days: float = 3.0) -> WeeklyDigest:
        def compose(now):
            self.composed.append(now)
            return Reply("📬 Haftalık özet", (Image(PNG, "a.png", "x"),))
        return WeeklyDigest(
            self.world.storage, self.world.clock, DigestSchedule(0, 9, min_days), compose,
            lambda: self.days,
            [DigestTarget(TELEGRAM, self.telegram, lambda: OWNER_TELEGRAM),
             DigestTarget(SLACK, self.slack, lambda: self.slack_channel)])

    def test_fires_once_per_week_at_the_configured_moment(self):
        digest = self.digest()
        self.assertEqual(digest.step(), [])  # 08:59
        self.world.clock.advance(60)
        self.assertEqual(digest.step(), [TELEGRAM, SLACK])
        self.assertEqual(self.telegram.replies[0][0], OWNER_TELEGRAM)
        self.assertEqual(self.slack.replies[0][0], "G0ECOBOX")
        self.assertEqual(len(self.telegram.replies[0][1].images), 1)
        for hours in (1, 24, 6 * 24):  # later the same week: nothing
            self.world.clock.current = self.MONDAY_0900 + timedelta(hours=hours)
            self.assertEqual(self.digest().step(), [])  # a restart remembers it too
        self.assertEqual(self.world.storage.get_state(STATE_PREFIX + TELEGRAM), "2026-W47")
        self.world.clock.current = self.MONDAY_0900 + timedelta(days=7)
        self.assertEqual(digest.step(), [TELEGRAM, SLACK])
        self.assertEqual(len(self.composed), 2)

    def test_a_sleeping_mac_catches_up_later_that_week(self):
        self.world.clock.current = self.MONDAY_0900 + timedelta(days=2, hours=3)
        self.assertEqual(self.digest().step(), [TELEGRAM, SLACK])

    def test_never_while_data_is_short(self):
        self.days = 2.9
        self.world.clock.current = self.MONDAY_0900
        digest = self.digest()
        self.assertEqual(digest.step(), [])
        self.assertEqual(self.composed, [])
        self.assertIsNone(self.world.storage.get_state(STATE_PREFIX + TELEGRAM))
        self.days = 3.0  # enough data later in the week: it goes out then
        self.world.clock.advance(3600)
        self.assertEqual(digest.step(), [TELEGRAM, SLACK])

    def test_slack_waits_for_its_channel_and_failures_retry(self):
        self.slack_channel = None
        self.telegram.fail = True
        self.world.clock.current = self.MONDAY_0900
        digest = self.digest()
        self.assertEqual(digest.step(), [])  # Telegram failed, Slack has no channel yet
        self.telegram.fail = False
        self.slack_channel = "G0ECOBOX"
        self.world.clock.advance(60)
        self.assertEqual(digest.step(), [TELEGRAM, SLACK])

    def test_schedule_slot(self):
        schedule = DigestSchedule(weekday=6, hour=20, min_days=3)
        week, moment = schedule.slot(datetime(2026, 11, 13, 12, 0, tzinfo=UTC))  # Friday
        self.assertEqual(week, "2026-W46")
        self.assertEqual(moment.isoformat(), "2026-11-15T20:00:00+03:00")  # Sunday 20:00


class DigestComposerTest(unittest.TestCase):
    def test_week_findings_plus_heatmap(self):
        import synthetic
        from helpers import GAP_S, display_scope
        from ecobox.charts import ChartBook
        from ecobox.digest import DigestComposer
        from ecobox.insights import InsightEngine
        from ecobox.stats import Statistics
        world = synthetic.two_office_weeks()
        self.addCleanup(world.storage.close)
        stats = Statistics(world.storage, GAP_S)
        renderer = FakeRenderer()
        reply = DigestComposer(InsightEngine(stats), ChartBook(stats, renderer),
                               display_scope(world))(world.clock.now())
        self.assertTrue(reply.text.startswith("📬 Haftalık özet · 9 Kasım – 15 Kasım\n"
                                              "Hacettepe · 12 AC priz · 2,8 gün gözlem\n\n"),
                        reply.text)
        self.assertIn("📈 Bu hafta boş priz bulma şansı %100", reply.text)
        self.assertEqual([i.filename for i in reply.images], ["bos-priz-isi-haritasi.png"])

        without = DigestComposer(InsightEngine(stats),
                                 ChartBook(stats, FakeRenderer(unavailable=True)),
                                 display_scope(world))(world.clock.now())
        self.assertEqual(without.images, ())
        self.assertIn("matplotlib kurulu değil", without.text)


if __name__ == "__main__":
    unittest.main()
