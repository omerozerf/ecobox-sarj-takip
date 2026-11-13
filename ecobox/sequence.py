"""The one rule that turns a connector's sample sequence into transitions and gaps.

The recorder applies it online, one poll at a time; the snapshot importer replays it over a
stretch of history after inserting samples into the past. Both must agree exactly, so the rule
lives here and nowhere else.

Honesty rule: when two consecutive samples of a connector are further apart than the gap
threshold, the time between them was not observed. A gap is recorded, and a state change found
across it is marked uncertain because its true time lies somewhere inside the gap.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from ecobox.records import Gap, Transition

Observation = tuple[datetime, str]
"""(sample time, normalised state)."""


def step(connector_id: str, prior: Observation | None, ts: datetime, state: str,
         gap_threshold: timedelta) -> tuple[Transition | None, Gap | None]:
    """What one new sample adds after `prior`, the connector's previous sample (or None)."""
    if prior is None:
        return Transition(connector_id, ts, None, state, None, True), None
    prior_ts, prior_state = prior
    gap = ts - prior_ts > gap_threshold
    transition = None
    if state != prior_state:
        transition = Transition(connector_id, ts, prior_state, state, prior_ts, gap)
    return transition, Gap(connector_id, prior_ts, ts) if gap else None


def replay(connector_id: str, anchor: Observation | None, samples: Iterable[Observation],
           gap_threshold: timedelta) -> tuple[list[Transition], list[Gap]]:
    """Every transition and gap of `samples` (oldest first) following the sample `anchor`."""
    transitions: list[Transition] = []
    gaps: list[Gap] = []
    prior = anchor
    for ts, state in samples:
        transition, gap = step(connector_id, prior, ts, state, gap_threshold)
        if transition is not None:
            transitions.append(transition)
        if gap is not None:
            gaps.append(gap)
        prior = (ts, state)
    return transitions, gaps
