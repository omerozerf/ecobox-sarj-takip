import io
import json
import logging
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from helpers import FIXTURES, GAP_S, POLL_S, FakeTelegram, World, message

from ecobox.api import HttpEcoboxApi
from ecobox.cli import build_parser
from ecobox.config import Config
from ecobox.service import Backoff, BotLoop, RecorderLoop
from ecobox.telegram import HttpTelegramClient, TelegramError


class BackoffTest(unittest.TestCase):
    def test_doubles_and_caps(self):
        backoff = Backoff(180, 900)
        self.assertEqual([backoff.failure() for _ in range(5)], [180, 360, 720, 900, 900])
        backoff.success()
        self.assertEqual(backoff.failure(), 180)


class RecorderLoopTest(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.world = World()
        self.addCleanup(self.world.storage.close)
        self.loop = RecorderLoop(self.world.recorder, self.world.storage, self.world.clock,
                                 POLL_S, 24 * 3600, Backoff(POLL_S, 900))

    def test_refreshes_catalog_daily_and_polls(self):
        calls_before = self.world.api.station_calls
        self.assertEqual(self.loop.step(), POLL_S)
        self.assertEqual(self.world.api.station_calls, calls_before + 1)
        self.world.clock.advance(POLL_S)
        self.loop.step()
        self.assertEqual(self.world.api.station_calls, calls_before + 1)
        self.world.clock.advance(24 * 3600)
        self.loop.step()
        self.assertEqual(self.world.api.station_calls, calls_before + 2)

    def test_errors_back_off_then_recover(self):
        self.world.api.fail_status = True
        self.assertEqual([self.loop.step() for _ in range(4)], [180, 360, 720, 900])
        self.world.api.fail_status = False
        self.assertEqual(self.loop.step(), POLL_S)
        self.assertEqual(self.world.storage.poll_counts(), (1, 4))

    def test_station_refresh_failure_uses_stored_catalog(self):
        self.world.api.fail_stations = True
        self.assertEqual(self.loop.step(), POLL_S)
        self.assertEqual(self.world.storage.poll_counts(), (1, 0))


class BotLoopTest(unittest.TestCase):
    def test_bad_update_does_not_stop_the_rest(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        telegram = FakeTelegram()
        handled = []

        class Handler:
            def handle_update(self, update):
                if update["update_id"] == 1:
                    raise RuntimeError("boom")
                handled.append(update["update_id"])

        telegram.updates = [[message(1, "/x", 1), message(1, "/y", 2)]]
        loop = BotLoop(telegram, Handler(), Backoff(5, 900))
        loop.step()
        self.assertEqual(handled, [2])
        self.assertEqual(loop._offset, 3)

    def test_run_registers_the_command_menu_first(self):
        import threading
        calls = []

        class Bot:
            def register_commands(self):
                calls.append("menu")

            def handle_update(self, update):
                calls.append("update")

        stop = threading.Event()
        telegram = FakeTelegram()
        telegram.updates = [[message(1, "/x", 1)]]
        loop = BotLoop(telegram, Bot(), Backoff(5, 900))
        original = loop.step

        def step_once():
            original()
            stop.set()
        loop.step = step_once
        loop.run(stop)
        self.assertEqual(calls, ["menu", "update"])


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HttpClientsTest(unittest.TestCase):
    def test_ecobox_request_shape(self):
        captured = []

        def urlopen(request, timeout):
            captured.append((request, timeout))
            name = "station_list.json" if request.get_method() == "POST" else \
                "connector_status.json"
            return FakeResponse((FIXTURES / name).read_bytes())

        api = HttpEcoboxApi(timeout_s=7)
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            self.assertEqual(len(api.fetch_status()), 168)
            self.assertEqual(len(api.fetch_stations()), 163)
        status_req, list_req = captured[0][0], captured[1][0]
        self.assertEqual(status_req.full_url,
                         "https://map-api.echargo.com/api/app/connector/status?includeNonPublic=true")
        self.assertEqual(status_req.get_method(), "GET")
        self.assertEqual(captured[0][1], 7)
        headers = {k.lower(): v for k, v in status_req.header_items()}
        self.assertEqual(headers["origin"], "http://harita.ecoboxsarj.com")
        self.assertEqual(headers["referer"], "http://harita.ecoboxsarj.com/")
        self.assertEqual(headers["x-requested-with"], "XMLHttpRequest")
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(list_req.get_method(), "POST")
        self.assertEqual(json.loads(list_req.data),
                         {"pageNumber": 1, "count": 10000, "direction": 0})

    def test_ecobox_empty_body_is_an_error(self):
        with mock.patch.object(urllib.request, "urlopen", lambda r, timeout: FakeResponse(b"")):
            with self.assertRaises(RuntimeError):
                HttpEcoboxApi(5).fetch_status()

    def test_telegram_errors_never_contain_the_token(self):
        token = "123456:SECRET-TOKEN"
        client = HttpTelegramClient(token)

        def urlopen(request, timeout):
            raise urllib.error.URLError(f"failed for {request.full_url}")

        with mock.patch.object(urllib.request, "urlopen", urlopen):
            with self.assertRaises(TelegramError) as caught:
                client.send_message(1, "x")
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

        def http_error(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b""))

        with mock.patch.object(urllib.request, "urlopen", http_error):
            with self.assertRaises(TelegramError) as caught:
                client.get_updates(None, 30)
        self.assertEqual(str(caught.exception), "getUpdates: HTTP 401")


class TelegramCommandMenuTest(unittest.TestCase):
    def test_set_my_commands_request(self):
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return FakeResponse(json.dumps({"ok": True, "result": True}).encode())

        with mock.patch.object(urllib.request, "urlopen", urlopen):
            HttpTelegramClient("1:T").set_my_commands([("durum", "[konum] – şu an hangi priz boş")])
        [request] = requests
        self.assertTrue(request.full_url.endswith("/setMyCommands"))
        self.assertEqual(json.loads(request.data), {"commands": [
            {"command": "durum", "description": "[konum] – şu an hangi priz boş"}]})


class ConfigCliTest(unittest.TestCase):
    def test_env_file_and_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("# comment\nTELEGRAM_BOT_TOKEN='abc'\nexport TELEGRAM_CHAT_ID=42\n"
                           "ECOBOX_SCOPE=hacettepe, ego\nECOBOX_POLL_INTERVAL_S=60\n")
            config = Config.load(env, environ={"ECOBOX_POLL_INTERVAL_S": "120"})
        self.assertEqual(config.telegram_token, "abc")
        self.assertEqual(config.telegram_chat_id, 42)
        self.assertEqual(config.scope_terms, ("hacettepe", "ego"))
        self.assertEqual(config.poll_interval_s, 120)
        self.assertEqual(config.gap_threshold_s, 300)

    def test_defaults_without_env(self):
        config = Config.load(Path("/nonexistent/.env"), environ={})
        self.assertIsNone(config.telegram_token)
        self.assertIsNone(config.telegram_chat_id)
        self.assertEqual(config.scope_terms, ("hacettepe",))
        self.assertEqual(config.poll_interval_s, 180)
        self.assertEqual(config.gap_threshold_s, GAP_S)
        self.assertTrue(str(config.db_path).endswith("data/ecobox.sqlite3"))

    def test_empty_scope_means_everything(self):
        self.assertEqual(Config.load(Path("/nonexistent"), {"ECOBOX_SCOPE": ""}).scope_terms, ())

    def test_cli_parser(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["estimate", "20", "80"]).target, 80)
        self.assertTrue(parser.parse_args(["poll-once", "--no-store"]).no_store)
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
            parser.parse_args([])


if __name__ == "__main__":
    unittest.main()
