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
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent.approvals import ToolPolicy
from app.agent.tool_catalog import CATALOG, NAMES
from app.core.delegation import Caller
from app.db.routines import RoutineRecord
from app.routines import schedule as sched
from app.routines import service

logger = logging.getLogger(__name__)

SPACE = "/personal"
FIRST_SINCE = timedelta(days=14)
MAX_OFFSET_MINUTES = 120
NO_GRANT = "open HomeAI to keep this routine running"


@dataclass(frozen=True)
class Definition:
    key: str
    name: str
    # Local time of the default daily run, before the per-user offset.
    time: str
    # The mechanics, with `{since}` and `{now}`; users can't change it.
    fixed_prompt: str
    # What the user may change; `''` in their row means this.
    default_prompt: str
    unavailable_tools: frozenset[str] = frozenset()
    quiet: bool = False


# key -> definition. M21-10 adds `memory`.
DEFINITIONS: dict[str, Definition] = {}


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
    return sched.parse_schedule({"kind": "daily", "time": f"{total // 60:02d}:{total % 60:02d}"})


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
    fixed = definition.fixed_prompt.format(
        since=_local(await since(app_state, routine, now), routine.timezone),
        now=_local(now, routine.timezone),
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


class DeliveredRoutines:
    def __init__(self, app_state: Any) -> None:
        self._state = app_state
        self._done: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}

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
