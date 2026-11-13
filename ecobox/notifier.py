"""Fires "tell me when a socket frees up" subscriptions on the transport they were made on."""

import logging
from typing import Protocol

from ecobox import texts
from ecobox.records import Transition
from ecobox.state import AVAILABLE
from ecobox.stats import Selection
from ecobox.storage import Storage

log = logging.getLogger(__name__)


class Outbox(Protocol):
    """One transport's way of sending a message to one of its conversations."""
    command_prefix: str

    def send(self, chat_id: int | str, text: str) -> None: ...


class SubscriptionNotifier:
    def __init__(self, storage: Storage, outboxes: dict[str, Outbox]):
        self._storage = storage
        self._outboxes = outboxes

    def on_transitions(self, transitions: list[Transition]) -> None:
        """Recorder listener: fire and clear subscriptions when an AC socket frees up."""
        freed_ids = {t.connector_id for t in transitions
                     if t.prev_state is not None and t.prev_state != AVAILABLE
                     and t.new_state == AVAILABLE}
        if not freed_ids:
            return
        catalog = self._storage.catalog()
        for subscription in self._storage.subscriptions():
            outbox = self._outboxes.get(subscription.transport)
            if outbox is None:  # that front-end is not configured in this run: keep it
                log.info("subscription %s waits for transport %s, which is not running",
                         subscription.id, subscription.transport)
                continue
            entries = [e for e in Selection(location_term=subscription.location_term)
                       .apply(catalog) if e.connector_id in freed_ids]
            if not entries:
                continue
            try:
                outbox.send(subscription.chat_id,
                            texts.notification_text(entries, outbox.command_prefix))
            except Exception:  # keep the subscription; the next freeing retries it
                log.exception("notification to subscription %s failed", subscription.id)
                continue
            self._storage.remove_subscription(subscription.id)
