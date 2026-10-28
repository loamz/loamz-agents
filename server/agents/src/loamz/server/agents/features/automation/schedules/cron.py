"""When a schedule fires next: a 5-field cron in the person's time zone, or an interval, or one date and time.

Cron is read in the zone the schedule was made in, so "weekdays at 08:00" stays 08:00 there across the clock change: a time that does
not exist that day (the spring gap) is skipped, one that happens twice (the autumn repeat) fires once, at its first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loamz.engine.errors import InvalidRequestError

MIN_INTERVAL_SECONDS = 15 * 60
LOOK_AHEAD_DAYS = 800  # far enough for "29 February"

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_NAMES = ("minute", "hour", "day of the month", "month", "day of the week")


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError, KeyError:
        raise InvalidRequestError(
            f"'{name}' is not a time zone. Use one like Europe/London or Asia/Kolkata."
        ) from None


def _field(text: str, low: int, high: int, name: str, *, weekday: bool = False) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, raw = part.partition("/")
            if not raw.isdigit() or int(raw) < 1:
                raise InvalidRequestError(f"The {name} step '{raw}' is not a positive number.")
            step = int(raw)
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit()):
                raise InvalidRequestError(f"The {name} range '{part}' is not numbers like 1-5.")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = int(part)
            end = high if step > 1 else start
        else:
            raise InvalidRequestError(f"The {name} '{part}' is not a number.")
        top = 7 if weekday else high
        if start < low or end > top or start > end:
            raise InvalidRequestError(f"The {name} must be between {low} and {top}.")
        values.update(range(start, end + 1, step))
    return {0 if weekday and v == 7 else v for v in values}


def parse_cron(
    expression: str,
) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    """The allowed minutes, hours, days of the month, months, days of the week, and whether the last two were restricted."""
    parts = expression.split()
    if len(parts) != 5:
        raise InvalidRequestError(
            "A cron timing has 5 parts: minute hour day-of-month month day-of-week, like '0 8 * * 1-5'."
        )
    sets = [
        _field(p, lo, hi, n, weekday=i == 4)
        for i, (p, (lo, hi), n) in enumerate(zip(parts, _RANGES, _NAMES, strict=True))
    ]
    return sets[0], sets[1], sets[2], sets[3], sets[4], parts[2] != "*", parts[4] != "*"


def next_cron(expression: str, tz: str, after: float) -> float:
    """The first moment strictly after `after` (epoch seconds) that the cron names, in zone `tz`."""
    minutes, hours, days, months, weekdays, dom_set, dow_set = parse_cron(expression)
    local_zone = zone(tz)
    start = datetime.fromtimestamp(after, UTC).astimezone(local_zone)
    day = start.date()
    for _ in range(LOOK_AHEAD_DAYS):
        cron_weekday = (day.weekday() + 1) % 7  # cron: Sunday is 0
        by_date = day.day in days
        by_week = cron_weekday in weekdays
        wanted = (by_date or by_week) if (dom_set and dow_set) else (by_date and by_week)
        if day.month in months and wanted:
            for hour in sorted(hours):
                for minute in sorted(minutes):
                    naive = datetime(day.year, day.month, day.day, hour, minute)
                    moment = naive.replace(tzinfo=local_zone, fold=0)
                    # A time the clock skipped does not exist: it comes back as a different wall time.
                    if moment.astimezone(UTC).astimezone(local_zone).replace(tzinfo=None) != naive:
                        continue
                    if moment.timestamp() > after:
                        return moment.timestamp()
        day += timedelta(days=1)
    raise InvalidRequestError("That timing never comes up.")


def next_run(
    kind: str,
    *,
    cron: str = "",
    interval_seconds: int = 0,
    run_at: float | None = None,
    tz: str = "UTC",
    after: float,
) -> float | None:
    """The next firing after `after`, or None for a one-off that is over."""
    if kind == "once":
        return run_at if run_at is not None and run_at > after else None
    if kind == "interval":
        if interval_seconds < MIN_INTERVAL_SECONDS:
            raise InvalidRequestError("Repeat at most every 15 minutes.")
        return after + interval_seconds
    return next_cron(cron, tz, after)


def describe(
    kind: str,
    *,
    cron: str = "",
    interval_seconds: int = 0,
    run_at: float | None = None,
    tz: str = "UTC",
) -> str:
    """The timing in plain words, for the person to approve."""
    if kind == "once":
        at = datetime.fromtimestamp(run_at or 0, zone(tz))
        return f"Once, on {at:%A %-d %B %Y at %H:%M} ({tz})"
    if kind == "interval":
        hours, minutes = divmod(interval_seconds // 60, 60)
        every = (
            f"{hours} hour{'s' if hours != 1 else ''}" + (f" {minutes} minutes" if minutes else "")
            if hours
            else f"{minutes} minutes"
        )
        return f"Every {every}"
    parts = cron.split()
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit():
        clock = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
        dow, dom = parts[4], parts[2]
        names = {
            "0": "Sunday",
            "1": "Monday",
            "2": "Tuesday",
            "3": "Wednesday",
            "4": "Thursday",
            "5": "Friday",
            "6": "Saturday",
            "7": "Sunday",
        }
        if dom == "*" and parts[3] == "*":
            if dow == "*":
                return f"Every day at {clock} ({tz})"
            if dow == "1-5":
                return f"Every weekday at {clock} ({tz})"
            if dow in names:
                return f"Every {names[dow]} at {clock} ({tz})"
        if dow == "*" and parts[3] == "*" and dom.isdigit():
            return f"On day {dom} of every month at {clock} ({tz})"
    return f"On the schedule “{cron}” ({tz})"
