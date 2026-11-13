"""Synthetic histories with known answers, recorded through the real recorder.

`two_office_weeks` polls Mon 2 – Sun 15 November 2026 (local time), 07:00–19:00 on weekdays
and 10:00–14:00 at weekends; the Mac sleeps otherwise, so every night is a recording gap.

Weekday pattern of the 12 Hacettepe AC sockets:

- Ego 1      charging 08:00–13:00 (5 h)
- Ego 2      charging 09:00–10:00, first week only
- Ego 3      charging 09:00–11:00
- Ego 4      faulted the whole time
- F Blok 2   charging 08:30–10:30
- the other 7 ("REST") charging 07:30–12:30 and 13:30–18:30 (5 h each)

So in the first week no socket is free 09:00–10:00, and in the second week Ego 2 is.
At weekends every socket but Ego 4 is free.
"""

from datetime import UTC, date, datetime, timedelta

from helpers import EGO, F_BLOK_2, World, connector_id
from ecobox.clock import LOCAL_TZ

EGO_1, EGO_2, EGO_3, EGO_4 = EGO
REST = [connector_id(name, order) for name, order in (
    ("Hacettepe 3. Arge Otopark - 1", 1), ("Hacettepe 3. Arge Otopark - 2", 1),
    ("Hacettepe 3. Arge Otopark - 3", 1), ("Hacettepe-Arge", 1), ("Hacettepe-F", 1),
    ("Hacettepe - F Blok 3", 1), ("Hacettepe - F Blok 4", 1))]
FIRST_DAY = date(2026, 11, 2)  # a Monday
END = datetime(2026, 11, 16, tzinfo=LOCAL_TZ)  # Monday 00:00 local, after the fortnight
STEP_S = 180


def _minutes(local: datetime) -> int:
    return local.hour * 60 + local.minute


def office_states(ts: datetime) -> dict[str, str]:
    local = ts.astimezone(LOCAL_TZ)
    minute = _minutes(local)
    states = {cid: "available" for cid in [*EGO, F_BLOK_2, *REST]}
    states[EGO_4] = "faulted"
    if local.weekday() >= 5:
        return states
    second_week = local.date() >= FIRST_DAY + timedelta(days=7)
    if 8 * 60 <= minute < 13 * 60:
        states[EGO_1] = "charging"
    if not second_week and 9 * 60 <= minute < 10 * 60:
        states[EGO_2] = "charging"
    if 9 * 60 <= minute < 11 * 60:
        states[EGO_3] = "charging"
    if 8 * 60 + 30 <= minute < 10 * 60 + 30:
        states[F_BLOK_2] = "charging"
    if 7 * 60 + 30 <= minute < 12 * 60 + 30 or 13 * 60 + 30 <= minute < 18 * 60 + 30:
        for cid in REST:
            states[cid] = "charging"
    return states


def polling_windows(first_day: date = FIRST_DAY, days: int = 14):
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        start, end = (7, 19) if day.weekday() < 5 else (10, 14)
        yield (datetime(day.year, day.month, day.day, start, tzinfo=LOCAL_TZ),
               datetime(day.year, day.month, day.day, end, tzinfo=LOCAL_TZ))


def two_office_weeks(world: World | None = None) -> World:
    world = world or World(datetime(2026, 11, 2, 4, 0, tzinfo=UTC))
    for start, end in polling_windows():
        world.clock.current = start.astimezone(UTC)
        world.run(end.astimezone(UTC), office_states, STEP_S)
    world.clock.current = END.astimezone(UTC)
    return world
