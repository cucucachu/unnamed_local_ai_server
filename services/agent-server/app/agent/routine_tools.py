"""The agent's routine tools (M17-07): make and manage routines from a chat.

`current_time`, `list_routines`, `create_routine`, `update_routine` and
`delete_routine`, saving through `app.routines.service` exactly as
`/api/routines` does, but on the chat turn's delegation (`Caller`) rather
than an identity token: the platform checks the space and issues the
routine's grant for the user behind it.

Creating, changing (other than just turning a routine off) and deleting a
routine always ask the user first, whatever the HITL setting, with the
schedule in words and the prompt on the card (the same tool-raised
`interrupt` as `app_tools`, so everything before it is a read). A routine
run can't use any of them but `current_time`: an unattended run making
more unattended runs would outlive whatever the user approved it for (the
platform refuses a routine run's delegation too).

Times are local to the routine's timezone; the default is the user's
(`SettingsDocument.timezone`, kept in step with their device by the app),
else UTC. The model learns "now" from `current_time` rather than from the
system prompt, which stays the same every turn for the model server's
prompt cache.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from pydantic import ValidationError

from app.agent import approvals
from app.agent.app_tools import _ask_user, _delegation_token
from app.core.delegation import Caller
from app.db.routines import ApprovalMode, RoutineRecord
from app.routines import schedule as sched
from app.routines import service

ROUTINE_TOOL_NAMES: tuple[str, ...] = (
    "current_time",
    "list_routines",
    "create_routine",
    "update_routine",
    "delete_routine",
)

Repeat = Literal["once", "daily", "weekdays", "weekly", "monthly"]

APPROVAL_MODE_WORDS: dict[str, str] = {
    "ask": "asks before changing anything",
    "allow_writes": "may change files without asking (still asks before deleting)",
    "read_only": "read-only: can't change anything",
}

_NO_DELEGATION = "Error: routines are unavailable for this run (no delegation)."
_IN_ROUTINE = (
    "Error: a routine run can't create or change routines. Don't retry; "
    "say in your reply what you would have set up."
)
_MAX_PROMPT_SHOWN = 300


def _user_id(config: RunnableConfig) -> str | None:
    user_id = (config.get("configurable") or {}).get("user_id")
    return user_id if isinstance(user_id, str) and user_id else None


async def _user_timezone(state: Any, user_id: str) -> str:
    document = await state.settings_store.get_document(user_id)
    zone = document.timezone
    if zone:
        try:
            sched.zone(zone)
        except ValueError:
            return "UTC"
        return zone
    return "UTC"


def _build_schedule(
    repeat: str,
    time_: str | None,
    date_: str | None,
    days: list[str] | None,
    day_of_month: int | None,
) -> sched.Schedule | str:
    """A schedule from the tools' flat arguments, or an error for the model."""
    if not time_:
        return "Error: give `time` as HH:MM (24-hour, the routine's local time)."
    raw: dict[str, Any] = {"kind": repeat, "time": time_}
    if repeat == "once":
        if not date_:
            return "Error: a once routine needs `date` (YYYY-MM-DD)."
        raw = {"kind": "once", "at": f"{date_}T{time_}"}
    elif repeat == "weekly":
        if not days:
            return 'Error: a weekly routine needs `days`, e.g. ["mon", "thu"].'
        raw["days"] = days
    elif repeat == "monthly":
        if day_of_month is None:
            return "Error: a monthly routine needs `day_of_month` (1-31)."
        raw["day"] = day_of_month
    try:
        return sched.parse_schedule(raw)
    except ValidationError as exc:
        problems = "; ".join(e["msg"] for e in exc.errors())
        return f"Error: that schedule isn't valid ({problems})."


def _local(at: datetime | None, timezone: str) -> str:
    if at is None:
        return "none (it's off)"
    local = at.astimezone(sched.zone(timezone))
    return f"{local:%a} {local:%b} {local.day}, {local.year} at {local.hour}:{local.minute:02d}"


def _when(schedule: dict | sched.Schedule, timezone: str) -> str:
    parsed = sched.parse_schedule(schedule) if isinstance(schedule, dict) else schedule
    return f"{sched.describe(parsed)} ({timezone})"


def _shorten(text: str, limit: int = _MAX_PROMPT_SHOWN) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _summary(record: RoutineRecord) -> str:
    state = "on" if record.enabled else "off"
    return (
        f'- "{record.name}" (id {record.id}), {state}: {_when(record.schedule, record.timezone)}; '
        f"next run {_local(record.next_run_at, record.timezone)}; space {record.space}; "
        f"{APPROVAL_MODE_WORDS.get(record.approval_mode, record.approval_mode)}.\n"
        f"  Prompt: {_shorten(record.prompt)}"
    )


def _error(exc: service.RoutineError) -> str:
    return f"Error: {exc.detail}."


def _find(routines: list[RoutineRecord], routine: str) -> RoutineRecord | str:
    """By id, else by exact name (case-insensitive)."""
    wanted = routine.strip()
    for record in routines:
        if record.id == wanted:
            return record
    named = [r for r in routines if r.name.casefold() == wanted.casefold()]
    if len(named) == 1:
        return named[0]
    if named:
        return f'Error: more than one routine is called "{wanted}"; use its id from list_routines.'
    return f'Error: no routine "{wanted}". Call list_routines for the ids.'


def make_routine_tools(state: Any) -> list[BaseTool]:
    """The routine tools, bound to the app's state (stores, delegation client)."""

    def _context(config: RunnableConfig) -> tuple[Caller, str] | str:
        if approvals.approval_mode(config) is not None:
            return _IN_ROUTINE
        token = _delegation_token(config)
        user_id = _user_id(config)
        if token is None or user_id is None:
            return _NO_DELEGATION
        return Caller(delegation_token=token), user_id

    @tool
    async def current_time(config: RunnableConfig) -> str:
        """The current date and time in the user's timezone.

        Call it before working out a relative time ("tomorrow", "in an hour",
        "next Friday") for a routine or anything else.
        """
        user_id = _user_id(config)
        timezone = await _user_timezone(state, user_id) if user_id else "UTC"
        now = _now().astimezone(sched.zone(timezone))
        return (
            f"It's {now:%A}, {now:%B} {now.day}, {now.year}, {now.hour}:{now.minute:02d} "
            f"({now:%Y-%m-%dT%H:%M}) in {timezone}, UTC{now:%z}."
        )

    @tool
    async def list_routines(config: RunnableConfig) -> str:
        """The user's routines: prompts the assistant runs on a schedule."""
        context = _context(config)
        if isinstance(context, str):
            return context
        _caller, user_id = context
        routines = await state.routine_store.list_for_owner(user_id)
        if not routines:
            return "The user has no routines."
        return "\n".join(_summary(r) for r in routines)

    @tool
    async def create_routine(
        name: str,
        prompt: str,
        repeat: Repeat,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
        time: str | None = None,
        date: str | None = None,
        days: list[sched.Weekday] | None = None,
        day_of_month: int | None = None,
        space: str = "/personal",
        timezone: str | None = None,
        approval_mode: ApprovalMode = "ask",
    ) -> str:
        """Schedule a prompt for the assistant to run by itself later (a routine).

        Each run is a new chat in the user's chats list. name: short title. prompt:
        what to do, written as the user's own request, complete on its own.
        repeat: "once" (with date YYYY-MM-DD), "daily", "weekdays", "weekly"
        (with days, e.g. ["mon", "thu"]) or "monthly" (with day_of_month 1-31;
        a short month uses its last day). time: HH:MM, 24-hour, local to
        timezone (an IANA name; leave it out for the user's own). space: where
        the run works ("/personal" or "/spaces/<slug>"). approval_mode: "ask"
        (default: the run stops for approval before changing anything),
        "allow_writes" (changes files and app data without asking; deleting still asks)
        or "read_only". When its job is to change something, ask the user which they
        want unless they said. The user approves the routine first.
        For "tomorrow" and the like, call current_time first.
        """
        context = _context(config)
        if isinstance(context, str):
            return context
        caller, user_id = context
        schedule = _build_schedule(repeat, time, date, days, day_of_month)
        if isinstance(schedule, str):
            return schedule
        zone = timezone or await _user_timezone(state, user_id)
        now = _now()
        try:
            service.check_timezone(zone)
            first = service.next_run_at(sched.dump_schedule(schedule), zone, True, now)
            canonical = await service.editable_space(state, caller, space)
        except service.RoutineError as exc:
            return _error(exc)
        description = (
            f'Create the routine "{name}": {_when(schedule, zone)}, '
            f"first run {_local(first, zone)}.\n"
            f"Space: {canonical}. This routine {APPROVAL_MODE_WORDS[approval_mode]}.\n\n"
            f"Prompt:\n{prompt}"
        )
        args = {
            "name": name,
            "prompt": prompt,
            "schedule": sched.dump_schedule(schedule),
            "timezone": zone,
            "space": canonical,
            "approval_mode": approval_mode,
        }
        approved, reason = _ask_user("create_routine", args, description, tool_call_id)
        if not approved:
            return f"The user rejected this routine; it wasn't created. {reason}"
        draft = service.Draft(
            name=name,
            prompt=prompt,
            space=canonical,
            schedule=schedule,
            timezone=zone,
            approval_mode=approval_mode,
        )
        try:
            record = await service.create(state, caller, user_id, draft, _now())
        except service.RoutineError as exc:
            return _error(exc)
        return f"Created. Runs appear in the user's chats list.\n{_summary(record)}"

    @tool
    async def update_routine(
        routine: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
        name: str | None = None,
        prompt: str | None = None,
        repeat: Repeat | None = None,
        time: str | None = None,
        date: str | None = None,
        days: list[sched.Weekday] | None = None,
        day_of_month: int | None = None,
        space: str | None = None,
        timezone: str | None = None,
        enabled: bool | None = None,
        approval_mode: ApprovalMode | None = None,
    ) -> str:
        """Change one of the user's routines; only what you pass changes.

        routine: its id (or exact name) from list_routines. To change when it
        runs, pass the whole new schedule (repeat and time, plus date / days /
        day_of_month as for create_routine). enabled: false pauses it, true
        turns it back on. Anything but pausing needs the user's approval.
        """
        context = _context(config)
        if isinstance(context, str):
            return context
        caller, user_id = context
        current = _find(await state.routine_store.list_for_owner(user_id), routine)
        if isinstance(current, str):
            return current
        changes: dict[str, Any] = {
            key: value
            for key, value in {
                "name": name,
                "prompt": prompt,
                "timezone": timezone,
                "enabled": enabled,
                "approval_mode": approval_mode,
            }.items()
            if value is not None
        }
        if repeat is not None:
            schedule = _build_schedule(repeat, time, date, days, day_of_month)
            if isinstance(schedule, str):
                return schedule
            changes["schedule"] = schedule
        elif time or date or days or day_of_month is not None:
            return "Error: to change when it runs, pass `repeat` with the whole new schedule."
        if not changes and space is None:
            return "Nothing to change."
        try:
            if timezone is not None:
                service.check_timezone(timezone)
            if space is not None:
                changes["space"] = await service.editable_space(state, caller, space)
            zone = changes.get("timezone", current.timezone)
            schedule = changes.get("schedule") or sched.parse_schedule(current.schedule)
            on = changes.get("enabled", current.enabled)
            next_at = service.next_run_at(sched.dump_schedule(schedule), zone, on, _now())
        except service.RoutineError as exc:
            return _error(exc)
        if changes != {"enabled": False}:
            description = "\n".join(
                [f'Change the routine "{current.name}":', *_change_lines(current, changes)]
                + [f"Next run: {_local(next_at, zone)}."]
            )
            args = {"routine_id": current.id, **_jsonable(changes)}
            approved, reason = _ask_user("update_routine", args, description, tool_call_id)
            if not approved:
                return f"The user rejected this change; the routine is as it was. {reason}"
        try:
            record = await service.update(state, caller, current, changes, _now())
        except service.RoutineError as exc:
            return _error(exc)
        return f"Updated.\n{_summary(record)}"

    @tool
    async def delete_routine(
        routine: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """Delete one of the user's routines for good (its past runs stay in the chats list).

        routine: its id (or exact name) from list_routines. The user approves it
        first; to stop it for now, update_routine with enabled false instead.
        """
        context = _context(config)
        if isinstance(context, str):
            return context
        _caller, user_id = context
        current = _find(await state.routine_store.list_for_owner(user_id), routine)
        if isinstance(current, str):
            return current
        description = (
            f'Delete the routine "{current.name}" ({_when(current.schedule, current.timezone)}).'
            f"\n\nPrompt:\n{_shorten(current.prompt)}"
        )
        args = {"routine_id": current.id, "name": current.name}
        approved, reason = _ask_user("delete_routine", args, description, tool_call_id)
        if not approved:
            return f"The user rejected this; the routine wasn't deleted. {reason}"
        try:
            await service.delete(state, current)
        except service.RoutineError as exc:
            return _error(exc)
        return f'Deleted the routine "{current.name}".'

    return [current_time, list_routines, create_routine, update_routine, delete_routine]


def _now() -> datetime:
    return datetime.now(UTC)


def _change_lines(current: RoutineRecord, changes: dict) -> list[str]:
    lines = []
    if "name" in changes:
        lines.append(f'Name: "{changes["name"]}"')
    if "schedule" in changes or "timezone" in changes:
        zone = changes.get("timezone", current.timezone)
        lines.append(f"When: {_when(changes.get('schedule', current.schedule), zone)}")
    if "space" in changes:
        lines.append(f"Space: {changes['space']}")
    if "enabled" in changes:
        lines.append("Turn it on" if changes["enabled"] else "Turn it off")
    if "approval_mode" in changes:
        lines.append(f"This routine {APPROVAL_MODE_WORDS[changes['approval_mode']]}.")
    if "prompt" in changes:
        lines.append(f"Prompt:\n{changes['prompt']}")
    return lines


def _jsonable(changes: dict) -> dict:
    out = dict(changes)
    if "schedule" in out:
        out["schedule"] = sched.dump_schedule(out["schedule"])
    return out
