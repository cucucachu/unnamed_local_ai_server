"""`app.routines.schedule` (M17-02): schedule shapes and DST-safe next runs."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.routines.schedule import dump_schedule, next_run, parse_schedule, zone

LA = "America/Los_Angeles"


def _local(utc: datetime, tz: str) -> str:
    return utc.astimezone(ZoneInfo(tz)).strftime("%a %Y-%m-%d %H:%M %Z")


def _runs(raw: dict, tz: str, start: datetime, n: int) -> list[str]:
    schedule, out, after = parse_schedule(raw), [], start
    for _ in range(n):
        after = next_run(schedule, tz, after)
        assert after is not None and after.tzinfo is UTC
        out.append(_local(after, tz))
    return out


def test_daily_runs_today_if_still_ahead_then_every_day() -> None:
    start = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # 07:00 PDT
    assert _runs({"kind": "daily", "time": "08:30"}, LA, start, 2) == [
        "Mon 2026-10-05 08:30 PDT",
        "Tue 2026-10-06 08:30 PDT",
    ]


def test_weekdays_skip_the_weekend() -> None:
    start = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)  # Fri 11:00 PDT, after 09:00
    assert _runs({"kind": "weekdays", "time": "09:00"}, LA, start, 2) == [
        "Mon 2026-10-12 09:00 PDT",
        "Tue 2026-10-13 09:00 PDT",
    ]


def test_weekly_on_chosen_days() -> None:
    start = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    raw = {"kind": "weekly", "days": ["thu", "mon", "thu"], "time": "18:00"}
    assert parse_schedule(raw).days == ["mon", "thu"]
    assert _runs(raw, "Europe/Berlin", start, 3) == [
        "Mon 2026-10-05 18:00 CEST",
        "Thu 2026-10-08 18:00 CEST",
        "Mon 2026-10-12 18:00 CEST",
    ]


def test_monthly_on_a_missing_day_runs_on_the_last() -> None:
    start = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
    assert _runs({"kind": "monthly", "day": 31, "time": "09:00"}, "UTC", start, 3) == [
        "Sat 2026-02-28 09:00 UTC",
        "Tue 2026-03-31 09:00 UTC",
        "Thu 2026-04-30 09:00 UTC",
    ]


def test_once_runs_once() -> None:
    schedule = parse_schedule({"kind": "once", "at": "2026-11-02T08:30"})
    first = next_run(schedule, LA, datetime(2026, 10, 1, tzinfo=UTC))
    assert _local(first, LA) == "Mon 2026-11-02 08:30 PST"
    assert next_run(schedule, LA, first) is None


def test_spring_forward_shifts_a_skipped_time_past_the_jump() -> None:
    # 2026-03-08 02:00 PST jumps to 03:00 PDT: 02:30 doesn't exist that day.
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    assert _runs({"kind": "daily", "time": "02:30"}, LA, start, 3) == [
        "Sun 2026-03-08 03:30 PDT",
        "Mon 2026-03-09 02:30 PDT",
        "Tue 2026-03-10 02:30 PDT",
    ]


def test_fall_back_runs_a_repeated_time_once() -> None:
    # 2026-11-01 02:00 PDT falls back to 01:00 PST: 01:30 happens twice.
    start = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert _runs({"kind": "daily", "time": "01:30"}, LA, start, 2) == [
        "Sun 2026-11-01 01:30 PDT",
        "Mon 2026-11-02 01:30 PST",
    ]


def test_dst_keeps_local_time_across_the_change_in_europe() -> None:
    start = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)  # CEST ends Oct 25
    runs = _runs({"kind": "daily", "time": "07:00"}, "Europe/Berlin", start, 2)
    assert runs == ["Sun 2026-10-25 07:00 CET", "Mon 2026-10-26 07:00 CET"]


def test_dump_round_trips() -> None:
    raw = {"kind": "weekly", "days": ["fri"], "time": "07:05"}
    assert dump_schedule(parse_schedule(raw)) == {"kind": "weekly", "days": ["fri"], "time": "07:05:00"}


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "hourly", "time": "08:00"},
        {"kind": "daily"},
        {"kind": "daily", "time": "25:00"},
        {"kind": "daily", "time": "08:00", "extra": 1},
        {"kind": "weekly", "days": [], "time": "08:00"},
        {"kind": "weekly", "days": ["funday"], "time": "08:00"},
        {"kind": "monthly", "day": 0, "time": "08:00"},
        {"kind": "monthly", "day": 32, "time": "08:00"},
        {"kind": "once", "at": "2026-11-02T08:30:00+01:00"},
    ],
)
def test_malformed_schedules_are_refused(raw: dict) -> None:
    with pytest.raises(ValidationError):
        parse_schedule(raw)


@pytest.mark.parametrize("name", ["Mars/Olympus", "", "../etc/passwd"])
def test_unknown_timezones_are_refused(name: str) -> None:
    with pytest.raises(ValueError):
        zone(name)
