"""Plain records read back from storage and passed between modules."""

from dataclasses import dataclass
from datetime import datetime

from ecobox.models import AC_TYPE, Tariff


@dataclass(frozen=True)
class CatalogEntry:
    connector_id: str
    charge_point_id: str
    display_name: str
    location_name: str
    connector_order: int
    type: int
    power_kw: float

    @property
    def is_ac(self) -> bool:
        return self.type == AC_TYPE

    @property
    def label(self) -> str:
        """Display name, plus the socket number on multi-socket charge points."""
        if self.connector_order == 1:
            return self.display_name
        return f"{self.display_name} #{self.connector_order}"


@dataclass(frozen=True)
class Sample:
    poll_id: int
    connector_id: str
    ts: datetime
    state: str


@dataclass(frozen=True)
class Transition:
    connector_id: str
    ts: datetime
    prev_state: str | None
    new_state: str
    prev_ts: datetime | None
    uncertain: bool
    """True when the true change time is unknown (first observation or a gap before it)."""


@dataclass(frozen=True)
class Gap:
    connector_id: str
    from_ts: datetime
    to_ts: datetime


@dataclass(frozen=True)
class CurrentStatus:
    entry: CatalogEntry
    state: str
    sampled_at: datetime
    since: datetime | None
    since_uncertain: bool
    tariff: Tariff | None


@dataclass(frozen=True)
class Subscription:
    id: int
    transport: str
    """Front-end the subscription was made on ("telegram" | "slack"); it fires there."""
    chat_id: int | str
    """Transport-specific conversation id: Telegram chat id (int), Slack channel id (str)."""
    location_term: str | None
    created_at: datetime
