"""Transport-agnostic command handling: text in, Turkish reply (text plus images) out.

Front-ends (Telegram, Slack) only decide who may talk and how a reply is delivered; every
command behaves identically on each of them. The one per-transport difference is how a
command is written (`/durum` in Telegram, `durum` in Slack), carried by `Conversation`.

Every command shows the display scope (Hacettepe by default). `analiz` and `grafik` also
accept a location outside it, since every Ecobox site is recorded: a term that matches
nothing in the display scope is looked up across the whole recording.

A message that is no command but names a location of the display scope as a whole word
("ego", "f blok boş mu") is answered as `durum <that location>`; anything else gets the short
"unknown command" reply.
"""

import re
from dataclasses import dataclass, replace
from typing import Callable

from ecobox import texts
from ecobox.charts import ChartBook, ChartRequest, ChartsUnavailable
from ecobox.clock import Clock
from ecobox.estimate import ChargeEstimator
from ecobox.fmt import capitalised
from ecobox.insights import InsightEngine
from ecobox.labels import ShortNames
from ecobox.records import CatalogEntry
from ecobox.replies import Image, Reply
from ecobox.reports import scope_label, stats_report
from ecobox.scope import fold
from ecobox.state import AVAILABLE
from ecobox.stats import Selection, Statistics
from ecobox.storage import Storage
from ecobox.view import DisplayScope

TELEGRAM = "telegram"
SLACK = "slack"

# Characters a user may put before a command word; Slack swallows a leading "/" itself, but
# it is accepted if it arrives, as is "!".
COMMAND_MARKERS = "/!"


@dataclass(frozen=True)
class Conversation:
    """Where a command came from, and therefore where its subscription fires."""
    transport: str
    chat_id: int | str
    command_prefix: str
    """How this transport's help text writes a command: "/" (Telegram) or "" (Slack)."""


@dataclass(frozen=True)
class Target:
    """What an analysis command looks at."""
    selection: Selection
    label: str
    names: ShortNames


def parse_command(text: str) -> tuple[str, str]:
    """`"/Haberver@bot F Blok"` -> `("haberver", "F Blok")`; the name is folded for lookup.

    Folding is case-insensitive and maps Turkish ı/İ to i, so `yardım` and `YARDIM` are
    `yardim`.
    """
    command, _, args = text.strip().partition(" ")
    name = command.lstrip(COMMAND_MARKERS).split("@", 1)[0]
    return fold(name), args.strip()


class CommandHandler:
    def __init__(self, storage: Storage, statistics: Statistics, estimator: ChargeEstimator,
                 clock: Clock, scope: DisplayScope, insights: InsightEngine, charts: ChartBook):
        self._storage = storage
        self._stats = statistics
        self._estimator = estimator
        self._clock = clock
        self._scope = scope
        self._insights = insights
        self._charts = charts
        self._commands: dict[str, Callable[[str, Conversation], Reply | str]] = {
            "start": self._help,
            "yardim": self._help,
            "help": self._help,
            "durum": self._status,
            "haberver": self._subscribe,
            "iptal": self._unsubscribe,
            "istatistik": self._statistics,
            "analiz": self._analysis,
            "grafik": self._chart,
            "tahmin": self._estimate,
        }

    def reply(self, text: str, conversation: Conversation) -> Reply:
        name, args = parse_command(text)
        handler = self._commands.get(name)
        if handler is None:
            term = self._scope.recognise(text)
            if term is None:
                return Reply(texts.unknown_command(conversation.command_prefix))
            handler, args = self._status, term
        answer = handler(args, conversation)
        return answer if isinstance(answer, Reply) else Reply(answer)

    def _help(self, _: str, conversation: Conversation) -> str:
        return texts.help_text(conversation.command_prefix)

    def _status(self, args: str, conversation: Conversation) -> str:
        term = args or None
        selection = self._scope.selection(term)
        if term is not None and not self._stats.entries(selection):
            return self._unknown_location(term)
        statuses = self._scope.statuses(term)
        now = self._clock.now()
        free_soon = None
        if statuses and not any(s.state == AVAILABLE for s in statuses if s.entry.is_ac):
            free_soon = texts.free_soon_text(self._stats.wait_until_free(selection, now),
                                             self._insights.thresholds.min_wait_days,
                                             conversation.command_prefix, term)
        return texts.status_text(statuses, now, self._scope.names(),
                                 capitalised(term) if term else None, free_soon)

    def _subscribe(self, args: str, conversation: Conversation) -> str:
        term = args or None
        entries = self._scope.selection(term).apply(self._storage.catalog())
        if not entries:
            return self._unknown_location(term)
        self._storage.add_subscription(conversation.transport, conversation.chat_id, term,
                                       self._clock.now())
        ids = {e.connector_id for e in entries}
        names = self._scope.names()
        free = [names.of(s.entry) for s in self._scope.current_status()
                if s.entry.connector_id in ids and s.state == AVAILABLE]
        return texts.subscribed_text(capitalised(term) if term else None, free)

    def _unsubscribe(self, _: str, conversation: Conversation) -> str:
        removed = self._storage.remove_subscriptions(conversation.transport,
                                                     conversation.chat_id)
        if removed:
            return texts.UNSUBSCRIBED
        return texts.no_subscription(conversation.command_prefix)

    def _statistics(self, args: str, _: Conversation) -> str:
        term = args or None
        selection = self._scope.selection(term)
        if not self._stats.entries(selection):
            return self._unknown_location(term)
        return stats_report(self._stats, self._scope, selection, scope_label(self._scope, term),
                            self._clock.now())

    def _analysis(self, args: str, _: Conversation) -> str:
        target = self._target(args or None)
        if target is None:
            return self._unknown_location(args)
        analysis = self._insights.analyse(target.selection, target.label, target.names,
                                          self._clock.now())
        return texts.analysis_text(analysis)

    def _chart(self, args: str, conversation: Conversation) -> Reply | str:
        first, _, rest = args.partition(" ")
        kind = self._charts.resolve(first) if first else None
        term = rest.strip() if kind is not None else args
        kind = kind or self._charts.kinds()[0]
        target = self._target(term or None)
        if target is None:
            return (self._unknown_location(term) + "\n\n" + texts.chart_usage(
                conversation.command_prefix,
                [(k.key, k.description) for k in self._charts.kinds()]))
        request = ChartRequest(target.selection, target.label, target.names, self._clock.now())
        try:
            images = self._charts.build(kind, request)
        except ChartsUnavailable:
            return texts.CHARTS_UNAVAILABLE
        others = texts.other_charts_line(conversation.command_prefix,
                                         [k.key for k in self._charts.kinds() if k is not kind])
        return Reply("", _with_footer(images, others))

    def _target(self, term: str | None) -> Target | None:
        """The display scope, narrowed by `term`; else `term` across every recorded site."""
        in_scope = self._scope.selection(term)
        if self._stats.entries(in_scope):
            label = self._scope.label if term is None else capitalised(term)
            return Target(in_scope, label, self._scope.names())
        if term is None:
            return None
        anywhere = Selection(location_term=term)
        if not self._stats.entries(anywhere):
            return None
        matching = Selection(location_term=term, ac_only=False).apply(self._storage.catalog())
        return Target(anywhere, capitalised(term), ShortNames(matching))

    def _estimate(self, args: str, conversation: Conversation) -> str:
        usage = texts.estimate_usage(conversation.command_prefix)
        parts = args.split(maxsplit=2)
        try:
            start, target = (float(re.sub(r"[%\s]", "", p).replace(",", ".")) for p in parts[:2])
        except ValueError:
            return usage
        if len(parts) < 2 or not 0 <= start < target <= 100:
            return usage
        term = parts[2] if len(parts) > 2 else None
        entry = self._pick_connector(term)
        if entry is None:
            return self._unknown_location(term)
        now = self._clock.now()
        estimate = self._estimator.estimate(start, target, now, entry.power_kw,
                                            self._storage.current_tariff(entry.connector_id))
        return texts.estimate_text(estimate, now)

    def _pick_connector(self, term: str | None) -> CatalogEntry | None:
        entries = self._scope.selection(term).apply(self._storage.catalog())
        free = {s.entry.connector_id for s in self._scope.current_status()
                if s.state == AVAILABLE}
        entries.sort(key=lambda e: e.connector_id not in free)
        return entries[0] if entries else None

    def _unknown_location(self, term: str | None) -> str:
        if not self._scope.selection().apply(self._storage.catalog()):
            return texts.NO_CATALOG
        return texts.unknown_location(term, self._scope.location_terms())


def _with_footer(images: list[Image], footer: str) -> tuple[Image, ...]:
    """The charts go out as photos alone: the first caption carries the pointer to the other
    chart kinds, so no separate text message repeats what the image already says."""
    first = images[0]
    return (replace(first, caption=f"{first.caption}\n{footer}"), *images[1:])
