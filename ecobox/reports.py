"""Assembles the replies shared by the bot and the CLI."""

from datetime import datetime

from ecobox import texts
from ecobox.fmt import capitalised
from ecobox.state import AVAILABLE
from ecobox.stats import Selection, Statistics, coverage, free_grid
from ecobox.stats_view import NowCount, StatsView
from ecobox.view import DisplayScope


def scope_label(scope: DisplayScope, term: str | None) -> str:
    """How a reply names what it covers: the location typed, else the display scope."""
    return capitalised(term) if term else scope.label


def _now_count(statistics: Statistics, scope: DisplayScope,
               selection: Selection) -> NowCount | None:
    ids = {e.connector_id for e in statistics.entries(selection)}
    current = [s for s in scope.current_status() if s.entry.connector_id in ids]
    if not current:
        return None
    return NowCount(sum(s.state == AVAILABLE for s in current), len(current))


def stats_report(statistics: Statistics, scope: DisplayScope, selection: Selection,
                 label: str, now: datetime) -> str:
    current = _now_count(statistics, scope, selection)
    polls = statistics.polls(selection)
    return texts.stats_text(StatsView(
        label=label,
        now=now,
        current=current,
        wait=(statistics.wait_until_free(selection, now)
              if current is not None and current.all_busy else None),
        grid=free_grid(polls),
        plug_in=statistics.plug_in(selection),
        unplug=statistics.unplug(selection),
        durations=statistics.durations(selection),
        shares=statistics.availability(selection),
        coverage=coverage([p.ts for p in polls], statistics.gap_threshold),
        names=scope.names()))
