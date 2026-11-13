"""Everything the `istatistik` reply shows, gathered once so the text only formats it."""

from dataclasses import dataclass
from datetime import datetime

from ecobox.insights import Thresholds
from ecobox.labels import ShortNames
from ecobox.stats import (ConnectorShare, Coverage, DurationSummary, FreeGrid, TimeDistribution,
                          WaitSummary)


@dataclass(frozen=True)
class NowCount:
    """Free and total AC sockets of the selection at the newest reading."""
    free: int
    total: int

    @property
    def all_busy(self) -> bool:
        return self.total > 0 and self.free == 0


@dataclass(frozen=True)
class StatsView:
    label: str
    now: datetime
    current: NowCount | None
    """None when the selection has no current reading."""
    wait: WaitSummary | None
    """Only when every socket is busy right now."""
    grid: FreeGrid
    plug_in: TimeDistribution
    unplug: TimeDistribution
    durations: DurationSummary
    shares: list[ConnectorShare]
    coverage: Coverage
    names: ShortNames
    thresholds: Thresholds = Thresholds()
