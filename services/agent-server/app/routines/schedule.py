"""When a routine runs (M17-02): a one-shot time, or a small RRULE-like repeat.

Shapes (stored as JSON on the routine, times local to its IANA timezone):

    {"kind": "once", "at": "2026-11-02T08:30"}
    {"kind": "daily", "time": "08:30"}                          # FREQ=DAILY
    {"kind": "weekdays", "time": "08:30"}                       # FREQ=WEEKLY;BYDAY=MO..FR
    {"kind": "weekly", "days": ["mon", "thu"], "time": "08:30"} # FREQ=WEEKLY;BYDAY=...
    {"kind": "monthly", "day": 31, "time": "08:30"}             # FREQ=MONTHLY;BYMONTHDAY=...

`monthly` on a day a month doesn't have runs on its last day instead.

DST: a local time that doesn't exist that day (spring forward) runs as
many minutes after the jump as it would have been before it (02:30 becomes
03:30); a time that happens twice (fall back) runs at its first occurrence.
`next_run_at` is always UTC.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Every rule matches at least once a month, so this always finds the next run.
_SEARCH_DAYS = 62


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Once(_Rule):
    kind: Literal["once"]
    at: datetime

    @field_validator("at")
    @classmethod
    def _local(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("`at` is local to the routine's timezone: no UTC offset")
        return value.replace(second=0, microsecond=0)


class _Repeating(_Rule):
    time: time

    @field_validator("time")
    @classmethod
    def _minutes(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("`time` is local to the routine's timezone: no UTC offset")
        return value.replace(second=0, microsecond=0)


class Daily(_Repeating):
    kind: Literal["daily"]


class Weekdays(_Repeating):
    kind: Literal["weekdays"]


class Weekly(_Repeating):
    kind: Literal["weekly"]
    days: Annotated[list[Weekday], Field(min_length=1)]

    @field_validator("days")
    @classmethod
    def _ordered(cls, value: list[Weekday]) -> list[Weekday]:
        return sorted(set(value), key=WEEKDAYS.index)


class Monthly(_Repeating):
    kind: Literal["monthly"]
    day: Annotated[int, Field(ge=1, le=31)]


Schedule = Annotated[Once | Daily | Weekdays | Weekly | Monthly, Field(discriminator="kind")]
schedule_adapter: TypeAdapter[Schedule] = TypeAdapter(Schedule)


def parse_schedule(raw: object) -> Schedule:
    """Raises `pydantic.ValidationError` for a malformed schedule."""
    return schedule_adapter.validate_python(raw)


def dump_schedule(schedule: Schedule) -> dict:
    return schedule_adapter.dump_python(schedule, mode="json")


_DAY_NAMES = dict(zip(WEEKDAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), strict=True))
_DAY_PLURALS = dict(
    zip(WEEKDAYS, ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays",
                   "Sundays"), strict=True)
)  # fmt: skip


def _clock(value: time) -> str:
    return f"{value.hour}:{value.minute:02d}"


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def describe(schedule: Schedule) -> str:
    """The schedule in words, as the app's `scheduleSummary` puts it."""
    match schedule:
        case Once(at=at):
            return f"Once on {at:%b} {at.day}, {at.year} at {_clock(at.time())}"
        case Daily():
            return f"Every day at {_clock(schedule.time)}"
        case Weekdays():
            return f"Weekdays at {_clock(schedule.time)}"
        case Weekly(days=days):
            at = _clock(schedule.time)
            if len(days) == 7:
                return f"Every day at {at}"
            if days == list(WEEKDAYS[:5]):
                return f"Weekdays at {at}"
            if days == ["sat", "sun"]:
                return f"Weekends at {at}"
            if len(days) == 1:
                return f"{_DAY_PLURALS[days[0]]} at {at}"
            return f"{', '.join(_DAY_NAMES[d] for d in days)} at {at}"
        case Monthly(day=day):
            last = " (or the last day)" if day > 28 else ""
            return f"Monthly on the {_ordinal(day)}{last} at {_clock(schedule.time)}"
    raise AssertionError(schedule)


def zone(name: str) -> ZoneInfo:
    """Raises `ValueError` for anything but a known IANA zone name."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {name!r}") from exc


def _to_utc(local: datetime, tz: ZoneInfo) -> datetime:
    """Wall time in `tz` to UTC, per the module docstring's DST rules.

    PEP 495's `fold=0` gives exactly those: a repeated time's first
    occurrence, and a skipped one read with the offset from before the jump.
    """
    return local.replace(tzinfo=tz, fold=0).astimezone(UTC)


def _matches(rule: Daily | Weekdays | Weekly | Monthly, day: date) -> bool:
    if isinstance(rule, Daily):
        return True
    if isinstance(rule, Weekdays):
        return day.weekday() < 5
    if isinstance(rule, Weekly):
        return WEEKDAYS[day.weekday()] in rule.days
    last = calendar.monthrange(day.year, day.month)[1]
    return day.day == min(rule.day, last)


def next_run(schedule: Schedule, timezone: str, after: datetime) -> datetime | None:
    """The first run strictly after `after` (aware), in UTC; None if there is none."""
    tz = zone(timezone)
    if isinstance(schedule, Once):
        at = _to_utc(schedule.at, tz)
        return at if at > after else None
    start = after.astimezone(tz).date()
    for offset in range(_SEARCH_DAYS):
        day = start + timedelta(days=offset)
        if not _matches(schedule, day):
            continue
        candidate = _to_utc(datetime.combine(day, schedule.time), tz)
        if candidate > after:
            return candidate
    return None
