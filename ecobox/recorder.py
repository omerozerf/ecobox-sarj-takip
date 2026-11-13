"""Polls the status endpoint and turns it into samples, transitions and gaps."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from ecobox.api import EcoboxApi
from ecobox.clock import Clock
from ecobox.records import Gap, Transition
from ecobox.scope import ScopeFilter
from ecobox.state import normalise
from ecobox.storage import SampleWrite, Storage

log = logging.getLogger(__name__)

TransitionListener = Callable[[list[Transition]], None]


@dataclass(frozen=True)
class PollResult:
    poll_id: int
    ts: datetime
    sample_count: int
    transitions: list[Transition]
    gaps: list[Gap]
    missing_connectors: set[str]
    """In-scope connectors absent from this status payload."""


class Recorder:
    def __init__(self, api: EcoboxApi, storage: Storage, scope: ScopeFilter, clock: Clock,
                 gap_threshold_s: float, listeners: list[TransitionListener] | None = None):
        self._api = api
        self._storage = storage
        self._scope = scope
        self._clock = clock
        self._gap_threshold = timedelta(seconds=gap_threshold_s)
        self._listeners = list(listeners or [])

    def add_listener(self, listener: TransitionListener) -> None:
        self._listeners.append(listener)

    def refresh_catalog(self) -> int:
        """Fetch the station list and store the in-scope part. Returns connector count."""
        started, ts = time.monotonic(), self._clock.now()
        try:
            charge_points = self._api.fetch_stations()
        except Exception as exc:
            self._storage.log_failure(ts, "stations", _describe(exc), _elapsed_ms(started))
            raise
        selected = self._scope.select(charge_points)
        self._storage.upsert_catalog(selected, ts, rows_total=len(charge_points),
                                     duration_ms=_elapsed_ms(started))
        return sum(len(cp.connectors) for cp in selected)

    def poll_once(self) -> PollResult:
        started, ts = time.monotonic(), self._clock.now()
        try:
            statuses = self._api.fetch_status()
        except Exception as exc:
            self._storage.log_failure(ts, "status", _describe(exc), _elapsed_ms(started))
            raise

        in_scope = {entry.connector_id for entry in self._storage.catalog()}
        previous = self._storage.latest_samples()
        samples: list[SampleWrite] = []
        transitions: list[Transition] = []
        gaps: list[Gap] = []
        for status in statuses:
            if status.connector_id not in in_scope:
                continue
            state = normalise(status.availability, status.is_under_maintenance,
                              status.charging_state)
            samples.append(SampleWrite(status, state))
            prior = previous.get(status.connector_id)
            if prior is None:
                transitions.append(Transition(status.connector_id, ts, None, state, None, True))
                continue
            prior_ts, prior_state = prior
            gap = ts - prior_ts > self._gap_threshold
            if gap:
                gaps.append(Gap(status.connector_id, prior_ts, ts))
            if state != prior_state:
                transitions.append(Transition(status.connector_id, ts, prior_state, state,
                                              prior_ts, gap))

        poll_id = self._storage.record_status_poll(ts, len(statuses), _elapsed_ms(started),
                                                   samples, transitions, gaps)
        seen = {s.status.connector_id for s in samples}
        result = PollResult(poll_id, ts, len(samples), transitions, gaps, in_scope - seen)
        self._notify(transitions)
        return result

    def _notify(self, transitions: list[Transition]) -> None:
        if not transitions:
            return
        for listener in self._listeners:
            try:
                listener(transitions)
            except Exception:  # a listener (e.g. the bot) must never stop recording
                log.exception("transition listener failed")


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]
