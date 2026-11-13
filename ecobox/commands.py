"""Transport-agnostic command handling: text in, Turkish reply out.

Front-ends (Telegram, Slack) only decide who may talk and how a reply is delivered; every
command behaves identically on each of them. The one per-transport difference is how a
command is written (`/durum` in Telegram, `durum` in Slack), carried by `Conversation`.
"""

import re
from dataclasses import dataclass
from typing import Callable

from ecobox import texts
from ecobox.clock import Clock
from ecobox.estimate import ChargeEstimator
from ecobox.records import CatalogEntry
from ecobox.reports import stats_report
from ecobox.scope import fold
from ecobox.state import AVAILABLE
from ecobox.stats import Selection, Statistics
from ecobox.storage import Storage

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
                 clock: Clock, price_includes_vat: bool):
        self._storage = storage
        self._stats = statistics
        self._estimator = estimator
        self._clock = clock
        self._price_includes_vat = price_includes_vat
        self._commands: dict[str, Callable[[str, Conversation], str]] = {
            "start": self._help,
            "yardim": self._help,
            "help": self._help,
            "durum": self._status,
            "haberver": self._subscribe,
            "iptal": self._unsubscribe,
            "istatistik": self._statistics,
            "tahmin": self._estimate,
        }

    def reply(self, text: str, conversation: Conversation) -> str:
        name, args = parse_command(text)
        handler = self._commands.get(name)
        if handler is None:
            return texts.unknown_command(conversation.command_prefix)
        return handler(args, conversation)

    def _help(self, _: str, conversation: Conversation) -> str:
        return texts.help_text(conversation.command_prefix)

    def _status(self, _: str, __: Conversation) -> str:
        return texts.status_text(self._storage.current_status(), self._clock.now())

    def _subscribe(self, args: str, conversation: Conversation) -> str:
        term = args or None
        entries = Selection(location_term=term).apply(self._storage.catalog())
        if not entries:
            return self._unknown_location(term)
        self._storage.add_subscription(conversation.transport, conversation.chat_id, term,
                                       self._clock.now())
        where = f"'{term}' konumunda " if term else ""
        reply = f"Tamam: {where}bir AC priz boşalınca bir kez haber vereceğim."
        ids = {e.connector_id for e in entries}
        free = [s for s in self._storage.current_status()
                if s.entry.connector_id in ids and s.state == AVAILABLE]
        if free:
            reply += "\nŞu an boş olan(lar): " + ", ".join(s.entry.label for s in free)
        return reply

    def _unsubscribe(self, _: str, conversation: Conversation) -> str:
        removed = self._storage.remove_subscriptions(conversation.transport,
                                                     conversation.chat_id)
        return "Abonelik iptal edildi." if removed else "Aktif abonelik yok."

    def _statistics(self, args: str, _: Conversation) -> str:
        term = args or None
        selection = Selection(location_term=term)
        entries = self._stats.entries(selection)
        if not entries:
            return self._unknown_location(term)
        return stats_report(self._stats, self._storage, selection,
                            f"{term} (AC)" if term else "tüm AC prizler", self._clock.now())

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
        estimate = self._estimator.estimate(start, target, self._clock.now(), entry.power_kw,
                                            self._storage.current_tariff(entry.connector_id))
        return texts.estimate_text(estimate, entry.label, self._price_includes_vat)

    def _pick_connector(self, term: str | None) -> CatalogEntry | None:
        entries = Selection(location_term=term).apply(self._storage.catalog())
        free = {s.entry.connector_id for s in self._storage.current_status()
                if s.state == AVAILABLE}
        entries.sort(key=lambda e: e.connector_id not in free)
        return entries[0] if entries else None

    def _unknown_location(self, term: str | None) -> str:
        locations = sorted({e.location_name for e in Selection().apply(self._storage.catalog())})
        if not locations:
            return "Henüz istasyon listesi kaydedilmedi."
        return (f"'{term}' ile eşleşen AC priz yok. Konumlar:\n"
                + "\n".join(f"  {name}" for name in locations))
