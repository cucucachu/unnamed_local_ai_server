"""Delivered routines (M21-07, docs/MEMORY.md §4): routines defined in code, one copy per user.

A `Definition` is shipped in the image the way system apps are. Each user's
copy is an ordinary `routines` row with `system_key` set, so it runs,
waits for approvals and lists runs like any routine; what differs:

- `prompt` holds only the user's editable part (`''`: the definition's
  default). The fixed part, `{since}` and `{now}` filled in, comes first in
  each run's message, and the unavailable tools, are never stored per user.
- It can't be deleted, renamed or moved out of `/personal`
  (`app.routines.service`).
- Every turn in one of its threads loses the definition's
  `unavailable_tools` (`without_tools`, applied in `TurnRunner._stream`).
- A `quiet` one's successful runs aren't unread and stay out of the main
  chats list.

`DeliveredRoutines.ensure` creates a user's missing copies on a request
they make in person (`GET /api/threads`, `GET /api/routines`), since only
then can the platform issue the routine grant scheduled runs act with. It
waits until the user's timezone is known, and runs at most once per user
per process, unless `forget` says a grant was lost: the next request then
issues a new one. It never fails the request.

The Memory reviews (M22-14, `review_prompts`) are three more: weekly,
monthly and yearly, each with a `min_period` its schedule can't beat (a
weekly review run daily would rewrite the same week) and its `review` kind,
which fills in the periods a run covers.

The admin can turn each definition off for the whole household (M21-08):
the platform keeps the switch, agent-server registers its definitions there
at startup (`register`) and reads the switches with its service token
(`admin_off`), since the scheduler has no user to ask as. While one is off
nothing runs it; each user's own `enabled` is kept.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.agent.approvals import ToolPolicy
from app.agent.tool_catalog import CATALOG, NAMES
from app.core.delegation import Caller
from app.db.routines import RoutineRecord
from app.routines import memory_prompt, review_prompts, service
from app.routines import schedule as sched

logger = logging.getLogger(__name__)

SPACE = "/personal"
MEMORY_KEY = "memory"
REVIEW_KEYS = ("memory_weekly", "memory_monthly", "memory_yearly")
REGISTER_RETRY_S = 15.0
_SWITCHES_TIMEOUT_S = 10.0
FIRST_SINCE = timedelta(days=14)
MAX_OFFSET_MINUTES = 120
NO_GRANT = "open HomeAI to keep this routine running"


@dataclass(frozen=True)
class Definition:
    key: str
    name: str
    # One line for the admin's switch in Settings.
    description: str
    # Local time of the default run, before the per-user offset (which never
    # takes it past midnight: every `time` here is before 22:00).
    time: str
    # The mechanics, with `{since}` and `{now}`; users can't change it.
    fixed_prompt: str
    # What the user may change; `''` in their row means this.
    default_prompt: str
    unavailable_tools: frozenset[str] = frozenset()
    quiet: bool = False
    # The default schedule but its time, e.g. {"kind": "weekly", "days": ["mon"]}.
    repeat: dict[str, Any] = field(default_factory=lambda: {"kind": "daily"})
    # Fewest days between runs (`schedule.period_days`) a schedule may have.
    min_period: int | None = None
    review: review_prompts.Review | None = None


MEMORY = Definition(
    key=MEMORY_KEY,
    name="Memory",
    description="Each night, keeps notes on what the user's chats say about them, for every new chat.",
    time="03:00",
    fixed_prompt=memory_prompt.FIXED,
    default_prompt=memory_prompt.DEFAULT,
    unavailable_tools=frozenset(
        {
            "web_search",
            "web_fetch",
            "open_integration",
            "use_integration",
            "read_integration_resource",
            "get_integration_prompt",
        }
    ),
    quiet=True,
)

_REVIEW_OFF = MEMORY.unavailable_tools | {"list_chats", "read_chat", "list_app_changes"}


def _review(
    key: str, kind: review_prompts.Review, name: str, about: str, time: str, repeat: dict, days: int
) -> Definition:
    return Definition(
        key=key,
        name=name,
        description=about,
        time=time,
        fixed_prompt=review_prompts.FIXED[kind],
        default_prompt=review_prompts.DEFAULT[kind],
        unavailable_tools=_REVIEW_OFF,
        quiet=True,
        repeat=repeat,
        min_period=days,
        review=kind,
    )


WEEKLY = _review(
    "memory_weekly",
    "week",
    "Memory: weekly review",
    "Each Monday, sums up the past week from the Memory notes and tidies the core notes.",
    "06:00",
    {"kind": "weekly", "days": ["mon"]},
    7,
)
MONTHLY = _review(
    "memory_monthly",
    "month",
    "Memory: monthly review",
    "On the 1st, sums up the past month, tidies the core notes and the pages.",
    "07:00",
    {"kind": "monthly", "day": 1},
    28,
)
YEARLY = _review(
    "memory_yearly",
    "year",
    "Memory: yearly review",
    "On January 1, sums up the past year, tidies the core notes and the pages.",
    "08:00",
    {"kind": "yearly", "month": 1, "day": 1},
    365,
)

DEFINITIONS: dict[str, Definition] = {d.key: d for d in (MEMORY, WEEKLY, MONTHLY, YEARLY)}


def definition_of(routine: RoutineRecord | None) -> Definition | None:
    if routine is None or routine.system_key is None:
        return None
    return DEFINITIONS.get(routine.system_key)


def offset_minutes(user_id: str) -> int:
    """A fixed 0-119 minutes per user, so a household's runs don't all start at once."""
    digest = hashlib.sha256(user_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") % MAX_OFFSET_MINUTES


def default_schedule(definition: Definition, user_id: str) -> sched.Schedule:
    hour, minute = map(int, definition.time.split(":"))
    total = (hour * 60 + minute + offset_minutes(user_id)) % (24 * 60)
    return sched.parse_schedule(
        {**definition.repeat, "time": f"{total // 60:02d}:{total % 60:02d}"}
    )


def check_schedule(definition: Definition, schedule: sched.Schedule) -> str | None:
    """Why `schedule` is too frequent for `definition`, or None if it isn't."""
    if definition.min_period is None:
        return None
    period = sched.period_days(schedule)
    if period is None or period < definition.min_period:
        every = {7: "a week", 28: "a month", 365: "a year"}.get(
            definition.min_period, f"{definition.min_period} days"
        )
        return f'"{definition.name}" covers {every}: it can\'t run more often than once {every}'
    return None


def editable_prompt(routine: RoutineRecord, definition: Definition) -> str:
    return routine.prompt or definition.default_prompt


def stored_prompt(prompt: str, definition: Definition) -> str:
    """What to save for a new editable part: `''` while it's the default."""
    return "" if prompt.strip() == definition.default_prompt.strip() else prompt


async def since(app_state: Any, routine: RoutineRecord, now: datetime) -> datetime:
    """When the last successful run started; for the first run, `FIRST_SINCE` back."""
    for run in await app_state.routine_store.list_runs(routine.id, routine.owner_user_id):
        if run.status == "succeeded" and run.started_at is not None:
            return run.started_at
    return now - FIRST_SINCE


def _local(at: datetime, timezone: str) -> str:
    return at.astimezone(sched.zone(timezone)).isoformat(timespec="minutes")


async def run_body(
    app_state: Any, routine: RoutineRecord, definition: Definition, now: datetime
) -> str:
    """The fixed part filled in, then the editable part: what follows the run framing."""
    start = await since(app_state, routine, now)
    zone = sched.zone(routine.timezone)
    periods = ""
    if definition.review is not None:
        periods = review_prompts.periods(
            definition.review, start.astimezone(zone), now.astimezone(zone)
        )
    fixed = definition.fixed_prompt.format(
        since=_local(start, routine.timezone),
        now=_local(now, routine.timezone),
        periods=periods,
    )
    return f"{fixed}\n\n{editable_prompt(routine, definition)}"


def without_tools(policy: ToolPolicy | None, names: frozenset[str]) -> ToolPolicy:
    """`policy` with `names` unavailable. No policy means every tool, every call asking."""
    if policy is None:
        return ToolPolicy(NAMES - names, frozenset())
    return ToolPolicy(policy.available - names, policy.allowed - names)


async def definition_for_thread(app_state: Any, thread_id: str, user_id: str) -> Definition | None:
    """The delivered routine a thread is a run of, if any."""
    thread = await app_state.thread_store.get(thread_id, user_id)
    if thread is None or thread.routine_id is None:
        return None
    return definition_of(await app_state.routine_store.get(thread.routine_id, user_id))


# A quiet routine's run in one of these is out of the way; any other needs the user.
QUIET_STATUSES = frozenset({"queued", "running", "succeeded"})


async def quiet_thread_ids(app_state: Any, user_id: str) -> set[str]:
    """Threads of the user's quiet routines' runs that don't need them."""
    store = app_state.routine_store
    quiet = set()
    for routine in await store.list_for_owner(user_id):
        definition = definition_of(routine)
        if definition is None or not definition.quiet:
            continue
        for run in await store.list_runs(routine.id, user_id):
            if run.thread_id is not None and run.status in QUIET_STATUSES:
                quiet.add(run.thread_id)
    return quiet


def unavailable_tool_labels(definition: Definition) -> list[dict[str, str]]:
    """The definition's unavailable tools in catalog order, as the UI names them."""
    return [
        {"name": t.name, "label": t.label, "group": t.group}
        for t in CATALOG
        if t.name in definition.unavailable_tools
    ]


def _now() -> datetime:
    return datetime.now(UTC)


def catalog_payload() -> dict[str, Any]:
    return {
        "routines": [
            {"key": d.key, "name": d.name, "description": d.description}
            for d in DEFINITIONS.values()
        ]
    }


async def register(platform_url: str, agent_token: str) -> None:
    """Send the definitions to the platform (for the admin's switches), retrying until it answers."""
    if not agent_token:
        return
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.put(
                    f"{platform_url}/internal/delivered-routines",
                    json=catalog_payload(),
                    headers={"Authorization": f"Bearer {agent_token}"},
                )
            if response.status_code == 204:
                logger.info("delivered routines registered (%d)", len(DEFINITIONS))
                return
            logger.warning(
                "delivered routines: HTTP %s %s", response.status_code, response.text[:200]
            )
        except httpx.HTTPError as exc:
            logger.warning("delivered routines: platform unreachable (%r); retrying", exc)
        await asyncio.sleep(REGISTER_RETRY_S)


class DeliveredRoutines:
    def __init__(self, app_state: Any) -> None:
        self._state = app_state
        self._done: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    async def admin_off(self) -> frozenset[str] | None:
        """The keys the admin turned off; None if the platform can't say."""
        settings = self._state.settings
        if not settings.platform_agent_token:
            return frozenset()  # no platform to ask (dev, tests): everything is on
        try:
            async with httpx.AsyncClient(timeout=_SWITCHES_TIMEOUT_S) as client:
                response = await client.get(
                    f"{settings.platform_url}/internal/delivered-routines",
                    headers={"Authorization": f"Bearer {settings.platform_agent_token}"},
                )
            listed = response.json()["routines"] if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("delivered routines: switches unavailable: %r", exc)
            return None
        if not isinstance(listed, list):
            logger.warning("delivered routines: switches: HTTP %s", response.status_code)
            return None
        return frozenset(
            r["key"] for r in listed if isinstance(r, dict) and r.get("enabled_by_admin") is False
        )

    def forget(self, user_id: str) -> None:
        """Check this user again on their next request (a grant was lost)."""
        self._done.discard(user_id)

    async def ensure(self, caller: Caller, user_id: str) -> None:
        if not DEFINITIONS or user_id in self._done:
            return
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if user_id in self._done:
                return
            try:
                complete = await self._ensure(caller, user_id, _now())
            except Exception as exc:  # noqa: BLE001 - tried again on the next request
                logger.warning("delivered routines for %s: %r", user_id, exc)
                return
            if complete:
                self._done.add(user_id)

    async def _ensure(self, caller: Caller, user_id: str, now: datetime) -> bool:
        """True once every definition has a copy holding a grant if it's on."""
        state = self._state
        timezone = (await state.settings_store.get_document(user_id)).timezone
        try:
            service.check_timezone(timezone or "")
        except service.RoutineError:
            return False  # not known yet: wait for the app to send it
        existing = {
            r.system_key: r
            for r in await state.routine_store.list_for_owner(user_id)
            if r.system_key is not None
        }
        for key, definition in DEFINITIONS.items():
            current = existing.get(key)
            if current is None:
                draft = service.Draft(
                    name=definition.name,
                    prompt="",
                    space=SPACE,
                    schedule=default_schedule(definition, user_id),
                    timezone=timezone,
                    system_key=key,
                )
                await service.create(state, caller, user_id, draft, now)
                logger.info("delivered routine %r created for %s", key, user_id)
            elif current.enabled and not current.grant_token:
                await service.update(state, caller, current, {"enabled": True}, now)
                logger.info("delivered routine %r: new grant for %s", key, user_id)
        return True
