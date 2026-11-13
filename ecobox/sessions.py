"""Sessions (occupancy of a socket) derived from transitions.

"Charging" is the API's busy state: it does not tell charging from a finished car left
plugged in, so a session's length is occupancy time, never charge time.

A session is one connector's run of the "charging" state: it opens at the transition into
charging and closes at the next transition out. Times are the first sample that showed the new
state, so they are late by at most one poll interval.

A session is *clean* (usable for timing statistics) only if both of its transitions were
observed without a gap before them and no recording gap falls inside it: a gap in the middle
could hide an unplug and a new plug-in.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ecobox.records import Gap, Transition
from ecobox.state import CHARGING


@dataclass(frozen=True)
class Session:
    connector_id: str
    start: datetime
    end: datetime | None
    start_uncertain: bool
    end_uncertain: bool
    contains_gap: bool

    @property
    def is_open(self) -> bool:
        return self.end is None

    @property
    def is_clean(self) -> bool:
        return (self.end is not None and not self.start_uncertain and not self.end_uncertain
                and not self.contains_gap)

    @property
    def duration(self) -> timedelta | None:
        return None if self.end is None else self.end - self.start


def derive_sessions(transitions: list[Transition], gaps: list[Gap]) -> list[Session]:
    by_connector: dict[str, list[Transition]] = {}
    for t in transitions:
        by_connector.setdefault(t.connector_id, []).append(t)
    gaps_by_connector: dict[str, list[Gap]] = {}
    for g in gaps:
        gaps_by_connector.setdefault(g.connector_id, []).append(g)

    sessions: list[Session] = []
    for connector_id, items in by_connector.items():
        items.sort(key=lambda t: t.ts)
        connector_gaps = gaps_by_connector.get(connector_id, [])
        opening: Transition | None = None
        for t in items:
            if opening is None and t.new_state == CHARGING:
                opening = t
            elif opening is not None and t.new_state != CHARGING:
                sessions.append(_session(opening, t, connector_gaps))
                opening = None
        if opening is not None:
            sessions.append(_session(opening, None, connector_gaps))
    sessions.sort(key=lambda s: (s.start, s.connector_id))
    return sessions


def _session(opening: Transition, closing: Transition | None, gaps: list[Gap]) -> Session:
    end = None if closing is None else closing.ts
    inside = any(g.to_ts > opening.ts and (end is None or g.from_ts < end) for g in gaps)
    return Session(
        connector_id=opening.connector_id,
        start=opening.ts,
        end=end,
        start_uncertain=opening.uncertain,
        end_uncertain=closing is not None and closing.uncertain,
        contains_gap=inside,
    )
