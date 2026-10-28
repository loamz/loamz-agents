"""When a schedule fires: cron in the schedule's own time zone, across the clock change, and in plain words."""

from datetime import UTC, datetime

import pytest

from loamz.engine.errors import InvalidRequestError
from loamz.server.agents.features.automation.schedules.cron import (
    describe,
    next_cron,
    next_run,
    parse_cron,
)


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def iso(epoch: float, tz: str = "UTC") -> str:
    from zoneinfo import ZoneInfo

    return datetime.fromtimestamp(epoch, ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")


def test_a_weekday_morning_skips_the_weekend():
    friday = at("2026-10-30 09:00")  # a Friday, after 08:00
    assert iso(next_cron("0 8 * * 1-5", "UTC", friday)) == "2026-11-02 08:00"  # the Monday


def test_it_is_the_first_moment_strictly_after():
    assert iso(next_cron("30 9 * * *", "UTC", at("2026-10-28 09:30"))) == "2026-10-29 09:30"
    assert iso(next_cron("*/15 * * * *", "UTC", at("2026-10-28 09:07"))) == "2026-10-28 09:15"


def test_eight_in_london_stays_eight_in_london_across_the_clock_change():
    # London left summer time on Sunday 2026-10-25: 08:00 is 07:00 UTC before it and 08:00 UTC after.
    before = next_cron("0 8 * * *", "Europe/London", at("2026-10-23 12:00"))
    after = next_cron("0 8 * * *", "Europe/London", at("2026-10-26 00:00"))
    assert iso(before, "Europe/London") == "2026-10-24 08:00" and iso(before) == "2026-10-24 07:00"
    assert iso(after, "Europe/London") == "2026-10-26 08:00" and iso(after) == "2026-10-26 08:00"


def test_a_time_the_clock_skips_does_not_fire_that_day():
    # New York springs forward on 2026-03-08: 02:30 does not exist that day.
    first = next_cron("30 2 * * *", "America/New_York", at("2026-03-07 00:00"))
    assert iso(first, "America/New_York") == "2026-03-07 02:30"
    second = next_cron("30 2 * * *", "America/New_York", first)
    assert iso(second, "America/New_York") == "2026-03-09 02:30"


def test_day_of_month_and_day_of_week_either_may_match_as_in_cron():
    both = next_cron(
        "0 9 13 * 5", "UTC", at("2026-10-28 00:00")
    )  # the 13th or a Friday: the Friday comes first
    assert iso(both) == "2026-10-30 09:00"
    assert iso(next_cron("0 9 29 2 *", "UTC", at("2026-10-28 00:00"))) == "2028-02-29 09:00"


def test_bad_timings_and_zones_are_refused_kindly():
    for bad in (
        "",
        "* * * *",
        "61 * * * *",
        "* 24 * * *",
        "a * * * *",
        "*/0 * * * *",
        "5-1 * * * *",
    ):
        with pytest.raises(InvalidRequestError):
            parse_cron(bad)
    with pytest.raises(InvalidRequestError, match="time zone"):
        next_cron("0 8 * * *", "Mars/Olympus", 0)


def test_once_and_interval():
    assert next_run("once", run_at=2000.0, after=1000.0) == 2000.0
    assert next_run("once", run_at=2000.0, after=3000.0) is None
    assert next_run("interval", interval_seconds=3600, after=1000.0) == 4600.0
    with pytest.raises(InvalidRequestError, match="15 minutes"):
        next_run("interval", interval_seconds=60, after=0.0)


def test_the_timing_is_put_in_plain_words_for_approval():
    assert (
        describe("recurring", cron="0 8 * * 1-5", tz="Europe/London")
        == "Every weekday at 08:00 (Europe/London)"
    )
    assert describe("recurring", cron="30 18 * * 5", tz="UTC") == "Every Friday at 18:30 (UTC)"
    assert describe("interval", interval_seconds=3600) == "Every 1 hour"
    assert "Once, on" in describe("once", run_at=at("2026-11-03 15:00"), tz="UTC")
