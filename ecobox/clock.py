"""Injectable clock and the time helpers shared by storage and statistics."""

from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Istanbul")


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as a timezone-aware UTC datetime."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def to_db(moment: datetime) -> str:
    """Serialise an aware datetime as a sortable UTC ISO string."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_db(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def to_local(moment: datetime) -> datetime:
    return moment.astimezone(LOCAL_TZ)


def local_bucket(moment: datetime) -> tuple[int, int]:
    """(weekday, hour) in Europe/Istanbul; weekday 0 is Monday."""
    local = to_local(moment)
    return local.weekday(), local.hour
