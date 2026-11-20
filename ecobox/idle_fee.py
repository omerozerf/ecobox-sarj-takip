"""When a car has to leave the socket before Ecobox starts charging for standing there.

Ecobox's notice of 2025-03-12 (app, "Cezai İşlem Uygulaması Hakkında Bilgilendirme"): after a
charge completes the car has 30 minutes to leave; past that, the fee is 2 TL per minute
counted from the moment the charge completed. Between 00:00 and 08:30 no fee applies, but a
car whose charge completed in that window must be gone by 08:30: there is no extra 30
minutes, and the fee runs from 08:30.

The notice does not say what happens when the 30 minutes themselves cross midnight; the
deadline stays at completion plus the tolerance then, the cautious reading.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from ecobox.clock import to_local


@dataclass(frozen=True)
class IdleDeadline:
    leave_by: datetime
    per_minute: float
    night_free: bool
    """The charge completes inside the fee-free night window; `leave_by` is its end."""


@dataclass(frozen=True)
class IdleFeePolicy:
    tolerance: timedelta
    per_minute: float
    night_free_until: time
    """Local time the fee-free window, which starts at midnight, ends."""

    def deadline(self, finish_at: datetime) -> IdleDeadline:
        local = to_local(finish_at)
        if local.time() < self.night_free_until:
            window_end = datetime.combine(local.date(), self.night_free_until, local.tzinfo)
            return IdleDeadline(window_end, self.per_minute, night_free=True)
        return IdleDeadline(finish_at + self.tolerance, self.per_minute, night_free=False)
