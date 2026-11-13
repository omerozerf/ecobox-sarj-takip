"""Assembles the statistics report shared by the bot and the CLI."""

from datetime import datetime

from ecobox import texts
from ecobox.state import AVAILABLE
from ecobox.stats import Selection, Statistics
from ecobox.storage import Storage


def stats_report(statistics: Statistics, storage: Storage, selection: Selection,
                 label: str, now: datetime) -> str:
    ids = {e.connector_id for e in statistics.entries(selection)}
    current = [s for s in storage.current_status() if s.entry.connector_id in ids]
    all_busy = bool(current) and not any(s.state == AVAILABLE for s in current)
    return texts.stats_text(
        scope_label=label,
        polls=storage.poll_counts(),
        plug_in=statistics.plug_in(selection),
        unplug=statistics.unplug(selection),
        durations=statistics.durations(selection),
        shares=statistics.availability(selection),
        grid=statistics.free_probability(selection),
        now=now,
        wait=statistics.wait_until_free(selection, now) if all_busy else None)
