"""Long-running service: the recorder loop plus optional chat front-end loops.

Each loop runs in its own thread with its own backoff, so a failing front-end never stops the
recorder or another front-end, and vice versa.
"""

import logging
import signal
import threading
from datetime import datetime, timedelta
from typing import Protocol

from ecobox.bot import TelegramBot
from ecobox.clock import Clock
from ecobox.recorder import Recorder
from ecobox.slack import SlackError, SlackRateLimited
from ecobox.slack_bot import SlackBot, SlackSetupError
from ecobox.storage import Storage
from ecobox.telegram import TelegramClient

log = logging.getLogger(__name__)


class Backoff:
    """Exponential delay after consecutive failures: base, 2*base, 4*base ... capped."""

    def __init__(self, base_s: float, cap_s: float):
        self._base = base_s
        self._cap = cap_s
        self.failures = 0

    def success(self) -> None:
        self.failures = 0

    def failure(self) -> float:
        self.failures += 1
        return min(self._base * 2 ** (self.failures - 1), self._cap)


class RecorderLoop:
    """One `step()` = at most one catalog refresh and one status poll; returns the wait."""

    def __init__(self, recorder: Recorder, storage: Storage, clock: Clock,
                 poll_interval_s: float, station_refresh_s: float, backoff: Backoff):
        self._recorder = recorder
        self._storage = storage
        self._clock = clock
        self._interval = poll_interval_s
        self._refresh_every = timedelta(seconds=station_refresh_s)
        self._backoff = backoff
        self._last_refresh: datetime | None = None

    def step(self) -> float:
        if self._refresh_due():
            try:
                count = self._recorder.refresh_catalog()
                self._last_refresh = self._clock.now()
                log.info("station list refreshed: %d connectors in scope", count)
            except Exception as exc:
                log.warning("station list refresh failed: %s", exc)
                if not self._storage.has_catalog():
                    return self._backoff.failure()
                # Keep polling with the stored catalog; retry the refresh next step.
        try:
            result = self._recorder.poll_once()
        except Exception as exc:
            delay = self._backoff.failure()
            log.warning("status poll failed (%d in a row), retry in %.0fs: %s",
                        self._backoff.failures, delay, exc)
            return delay
        self._backoff.success()
        log.info("poll ok: %d samples, %d transitions, %d gaps%s", result.sample_count,
                 len(result.transitions), len(result.gaps),
                 f", missing {len(result.missing_connectors)}" if result.missing_connectors
                 else "")
        return self._interval

    def _refresh_due(self) -> bool:
        return (self._last_refresh is None
                or self._clock.now() - self._last_refresh >= self._refresh_every)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                delay = self.step()
            except Exception:  # storage trouble etc.: log, wait, keep the process alive
                log.exception("recorder step crashed")
                delay = self._backoff.failure()
            stop.wait(delay)


class BotLoop:
    def __init__(self, telegram: TelegramClient, bot: TelegramBot, backoff: Backoff,
                 long_poll_s: int = 30):
        self._telegram = telegram
        self._bot = bot
        self._backoff = backoff
        self._long_poll_s = long_poll_s
        self._offset: int | None = None

    def step(self) -> None:
        updates = self._telegram.get_updates(self._offset, self._long_poll_s)
        for update in updates:
            self._offset = update["update_id"] + 1
            try:
                self._bot.handle_update(update)
            except Exception:
                log.exception("handling update %s failed", update["update_id"])

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.step()
                self._backoff.success()
            except Exception as exc:
                delay = self._backoff.failure()
                log.warning("telegram polling failed, retry in %.0fs: %s", delay, exc)
                stop.wait(delay)


class SlackLoop:
    """Polls the Slack channel every `interval_s`; stops for good on unfixable errors."""

    def __init__(self, bot: SlackBot, interval_s: float, backoff: Backoff):
        self._bot = bot
        self._interval = interval_s
        self._backoff = backoff

    def step(self) -> float | None:
        """Returns the wait before the next step, or None when the transport must stop."""
        try:
            self._bot.poll_once()
        except SlackRateLimited as exc:
            log.warning("slack rate limited, waiting %.0fs", exc.retry_after_s)
            return max(exc.retry_after_s, self._interval)
        except SlackSetupError as exc:
            log.error("slack transport stopped: %s", exc)
            return None
        except SlackError as exc:
            if exc.fatal:
                log.error("slack transport stopped, needs a config or Slack-side fix: %s", exc)
                return None
            delay = self._backoff.failure()
            log.warning("slack polling failed (%d in a row), retry in %.0fs: %s",
                        self._backoff.failures, delay, exc)
            return delay
        except Exception:  # storage trouble etc.: log, wait, keep the thread alive
            delay = self._backoff.failure()
            log.exception("slack step crashed, retry in %.0fs", delay)
            return delay
        self._backoff.success()
        return self._interval

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            delay = self.step()
            if delay is None:
                return
            stop.wait(delay)


class StoppableLoop(Protocol):
    def run(self, stop: threading.Event) -> None: ...


def run_service(recorder_loop: RecorderLoop, front_ends: dict[str, StoppableLoop]) -> None:
    """Runs until SIGTERM/SIGINT. `front_ends` maps a thread name to a chat front-end loop."""
    stop = threading.Event()

    def request_stop(signum, _frame):
        log.info("signal %s received, stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    recorder = threading.Thread(target=recorder_loop.run, args=(stop,), name="recorder")
    # Front-ends may sit in a network call at shutdown; daemon threads do not hold the exit.
    threads = [recorder] + [threading.Thread(target=loop.run, args=(stop,), name=name,
                                             daemon=True)
                            for name, loop in front_ends.items()]
    if not front_ends:
        log.info("no chat front-end configured: running the recorder only")
    for thread in threads:
        thread.start()
    while not stop.is_set():
        stop.wait(1)
    recorder.join()
