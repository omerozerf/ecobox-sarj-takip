"""Fires "tell me when a socket frees up" subscriptions on the transport they were made on."""

import logging
from typing import Protocol

from ecobox import texts
from ecobox.records import Transition
from ecobox.replies import Reply
from ecobox.state import AVAILABLE
from ecobox.storage import Storage
from ecobox.view import DisplayScope

log = logging.getLogger(__name__)


class Outbox(Protocol):
    """One transport's way of sending a message to one of its conversations."""
    command_prefix: str

    def send(self, chat_id: int | str, text: str) -> None: ...

    def send_reply(self, chat_id: int | str, reply: Reply) -> None:
        """The text (a chart reply has none), then each image with its caption."""


class SubscriptionNotifier:
    """Only sockets in the display scope fire subscriptions, whatever else is recorded."""

    def __init__(self, storage: Storage, scope: DisplayScope, outboxes: dict[str, Outbox]):
        self._storage = storage
        self._scope = scope
        self._outboxes = outboxes

    def on_transitions(self, transitions: list[Transition]) -> None:
        """Recorder listener: fire and clear subscriptions when an AC socket frees up."""
        freed = [t for t in transitions
                 if t.prev_state is not None and t.prev_state != AVAILABLE
                 and t.new_state == AVAILABLE]
        if not freed:
            return
        freed_ids = {t.connector_id for t in freed}
        at = max(t.ts for t in freed)
        catalog = self._storage.catalog()
        names = self._scope.names()
        for subscription in self._storage.subscriptions():
            outbox = self._outboxes.get(subscription.transport)
            if outbox is None:  # that front-end is not configured in this run: keep it
                log.info("subscription %s waits for transport %s, which is not running",
                         subscription.id, subscription.transport)
                continue
            entries = [e for e in self._scope.selection(subscription.location_term)
                       .apply(catalog) if e.connector_id in freed_ids]
            if not entries:
                continue
            term = subscription.location_term
            ac_now = [st for st in self._scope.statuses(term) if st.entry.is_ac]
            free_now = sum(st.state == AVAILABLE for st in ac_now)
            try:
                outbox.send(subscription.chat_id, texts.notification_text(
                    entries, at, free_now, len(ac_now), outbox.command_prefix, names, term))
            except Exception:  # keep the subscription; the next freeing retries it
                log.exception("notification to subscription %s failed", subscription.id)
                continue
            self._storage.remove_subscription(subscription.id)
