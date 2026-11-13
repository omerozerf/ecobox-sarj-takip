"""Weekly digest: the week's findings plus the heatmap, sent on its own to every transport.

It goes out at the first check on or after the configured local weekday and hour of each ISO
week (so a Mac asleep at that moment sends it when it wakes, still once that week), and only
once the display scope has at least `min_days` of observation. The ISO week last sent is kept
per transport in `service_state`, so a restart never sends it twice.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from ecobox import texts
from ecobox.charts import ChartBook, ChartRequest, ChartsUnavailable
from ecobox.clock import Clock, LOCAL_TZ, to_local
from ecobox.insights import InsightEngine
from ecobox.notifier import Outbox
from ecobox.replies import Reply
from ecobox.storage import Storage
from ecobox.view import DisplayScope

log = logging.getLogger(__name__)

STATE_PREFIX = "digest.last_week."
WEEK = timedelta(days=7)


@dataclass(frozen=True)
class DigestSchedule:
    weekday: int
    """Local weekday, 0 = Monday."""
    hour: int
    min_days: float

    def slot(self, now: datetime) -> tuple[str, datetime]:
        """The ISO week `now` falls in, and that week's send moment (aware, local)."""
        local = to_local(now)
        year, week, _ = local.isocalendar()
        monday = (local - timedelta(days=local.weekday())).date()
        day = monday + timedelta(days=self.weekday)
        moment = datetime(day.year, day.month, day.day, self.hour, tzinfo=LOCAL_TZ)
        return f"{year}-W{week:02d}", moment


@dataclass(frozen=True)
class DigestTarget:
    transport: str
    outbox: Outbox
    chat_id: Callable[[], int | str | None]
    """Where to send; None while the transport has no conversation yet (Slack before setup)."""


class WeeklyDigest:
    def __init__(self, storage: Storage, clock: Clock, schedule: DigestSchedule,
                 compose: Callable[[datetime], Reply], observed_days: Callable[[], float],
                 targets: list[DigestTarget]):
        self._storage = storage
        self._clock = clock
        self._schedule = schedule
        self._compose = compose
        self._observed_days = observed_days
        self._targets = targets

    def step(self) -> list[str]:
        """Sends the digest where it is due; returns the transports it went to."""
        now = self._clock.now()
        week, moment = self._schedule.slot(now)
        if now < moment:
            return []
        pending = [(t, chat) for t in self._targets
                   if self._storage.get_state(STATE_PREFIX + t.transport) != week
                   and (chat := t.chat_id()) is not None]
        if not pending:
            return []
        days = self._observed_days()
        if days < self._schedule.min_days:
            log.debug("weekly digest held back: %.1f observed days < %.1f", days,
                      self._schedule.min_days)
            return []
        reply = self._compose(now)
        sent = []
        for target, chat_id in pending:
            try:
                target.outbox.send_reply(chat_id, reply)
            except Exception:  # retried at the next step; the other transports still get it
                log.exception("weekly digest to %s failed", target.transport)
                continue
            self._storage.set_state(STATE_PREFIX + target.transport, week, now)
            sent.append(target.transport)
        return sent


class DigestComposer:
    """Builds the digest reply: the last seven days' findings and the heatmap."""

    def __init__(self, insights: InsightEngine, charts: ChartBook, scope: DisplayScope):
        self._insights = insights
        self._charts = charts
        self._scope = scope

    def __call__(self, now: datetime) -> Reply:
        since = now - WEEK
        selection, names = self._scope.selection(), self._scope.names()
        analysis = self._insights.analyse(selection, self._scope.label, names, now, since)
        text = texts.digest_text(analysis, since, now)
        request = ChartRequest(selection, self._scope.label, names, now)
        try:
            images = self._charts.build(self._charts.kinds()[0], request)
        except ChartsUnavailable:
            return Reply(text + "\n\n" + texts.CHARTS_UNAVAILABLE)
        return Reply(text, tuple(images))
