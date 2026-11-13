"""Tells the owner when the remote collector has stopped writing snapshots, and when it is back.

The GitHub collector keeps itself alive by starting its own successor; nothing on GitHub's
side restarts a chain that died with its runner. This machine already fetches the snapshot
branch on every import run, so it compares the newest snapshot with the clock there: older
than `stale_after_s` means the chain is down. One message goes out when that is first seen and
one when snapshots flow again; the state is kept per transport in `service_state`, so a
restart never repeats an alert.
"""

import logging
from datetime import timedelta
from pathlib import Path

from ecobox import texts
from ecobox.clock import Clock
from ecobox.digest import DigestTarget
from ecobox.snapshot import newest_snapshot_time
from ecobox.storage import Storage

log = logging.getLogger(__name__)

STATE_PREFIX = "collector_watch.down."
DOWN = "1"
UP = "0"

class CollectorWatch:
    def __init__(self, storage: Storage, clock: Clock, stale_after_s: float,
                 targets: list[DigestTarget]):
        self._storage = storage
        self._clock = clock
        self._stale_after = timedelta(seconds=stale_after_s)
        self._targets = targets

    def check(self, directory: Path) -> list[str]:
        """Sends what is due for the snapshots in `directory`; returns the transports reached."""
        now = self._clock.now()
        newest = newest_snapshot_time(directory)
        down = now - newest > self._stale_after
        state, compose = (DOWN, texts.collector_stopped) if down else (UP, texts.collector_back)
        sent = []
        for target in self._targets:
            key = STATE_PREFIX + target.transport
            # Never alerted counts as up: a healthy collector is not announced at first start.
            if (self._storage.get_state(key) or UP) == state:
                continue
            chat_id = target.chat_id()
            if chat_id is None:
                continue
            try:
                target.outbox.send(chat_id, compose(newest, now))
            except Exception:  # retried at the next check; the other transports still get it
                log.exception("collector alert to %s failed", target.transport)
                continue
            self._storage.set_state(key, state, now)
            sent.append(target.transport)
        if down:
            log.warning("remote collector looks stopped: newest snapshot %s", newest.isoformat())
        return sent
