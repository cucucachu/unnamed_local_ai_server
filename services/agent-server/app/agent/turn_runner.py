"""Detached turn runner (M17-01).

A turn runs in its own task, not the chat socket's: a client disconnect
only detaches the socket; the turn keeps going and checkpoints as usual.
Sockets attach to a running turn and get every frame it has sent so far
(from `turn_start`), then the live stream. A turn nobody is attached to is
cancelled after `detached_timeout_s` (`None`: never, e.g. a routine run);
an explicit `cancel()` stops it at once. Each turn holds its thread's lock
until it finishes, so a thread has at most one turn running.

It needs no socket: routine runs (M17-04) start a turn with a thread id, a
user message, the owner's delegation and settings, and read the outcome
from `ActiveTurn.wait()`.

While a turn runs, history readers use `base_checkpoint_id` (the tip the
turn started from) so a client hydrating mid-turn doesn't see the turn's
steps twice: once from the checkpoint, once from the replay.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.delegation import Delegation, DelegationDenied
from app.db.rls import bind_user

logger = logging.getLogger(__name__)

Status = Literal["completed", "awaiting_approval", "cancelled", "error"]

# Ends a subscriber's queue.
END: dict = {"type": "__end__"}


def _ws() -> Any:
    # `chat_ws` imports this module; its turn helpers are only needed at run time.
    from app.api import chat_ws

    return chat_ws


@dataclass
class TurnOutcome:
    status: Status
    pending_approval: dict | None = None
    error: BaseException | None = None


@dataclass
class TurnRequest:
    thread_id: str
    user_id: str
    run_input: Any
    hitl_enabled: bool
    thinking_enabled: bool
    checkpoint_id: str | None = None
    delegation: Delegation | None = None
    # The `user_message` behind a fresh turn, for clients attaching later.
    user_message: dict | None = None
    detached_timeout_s: float | None = None
    # A routine run's approval mode (M17-05, `app.agent.approvals`); None in a chat.
    approval_mode: str | None = None


@dataclass(eq=False)
class ActiveTurn:
    request: TurnRequest
    base_checkpoint_id: str | None
    frames: list[dict] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    started_mono: float = field(default_factory=time.monotonic)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    prior_assistant_id: str | None = None
    _timer: asyncio.TimerHandle | None = None
    _ended: bool = False
    _done: bool = False

    @property
    def thread_id(self) -> str:
        return self.request.thread_id

    def replay_start_frame(self) -> dict:
        frame: dict = {"type": "turn_start", "replay": True}
        if self.request.user_message is not None:
            frame["user_message"] = self.request.user_message
        return frame

    def attach(self, *, replay: bool = True) -> asyncio.Queue:
        """Subscribe to this turn's frames; `replay` sends what was missed first."""
        queue: asyncio.Queue = asyncio.Queue()
        if replay:
            queue.put_nowait(self.replay_start_frame())
            for frame in self.frames:
                queue.put_nowait(frame)
        if self._done:
            queue.put_nowait(END)
        else:
            self.subscribers.add(queue)
            self._disarm()
        return queue

    def detach(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)
        if not self.subscribers and not self._done:
            self._arm()

    def emit(self, frame: dict) -> None:
        if frame["type"] in ("turn_end", "error"):
            self._ended = True
        if frame["type"] != "turn_start":
            self.frames.append(frame)
        for queue in self.subscribers:
            queue.put_nowait(frame)

    def cancel(self) -> None:
        """Stop the turn, unless it already sent its `turn_end`."""
        if self._ended:
            return
        if self.task is not None and not self.task.done():
            self.task.cancel()

    async def wait(self) -> TurnOutcome:
        assert self.task is not None
        return await asyncio.shield(self.task)

    def _arm(self) -> None:
        timeout = self.request.detached_timeout_s
        if timeout is None or self._timer is not None:
            return
        self._timer = asyncio.get_running_loop().call_later(timeout, self._on_detached_timeout)

    def _disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_detached_timeout(self) -> None:
        self._timer = None
        if not self.subscribers:
            logger.info("turn on thread %s: no client for %ss, cancelling",
                        self.thread_id, self.request.detached_timeout_s)
            self.cancel()

    def _finish(self) -> None:
        self._done = True
        self._disarm()
        for queue in self.subscribers:
            queue.put_nowait(END)
        self.subscribers.clear()


class TurnRunner:
    """Owns the running turns of this process, one per thread at most."""

    def __init__(self, app_state: Any) -> None:
        self._state = app_state
        self._active: dict[str, ActiveTurn] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._finished = asyncio.Condition()

    def lock_for(self, thread_id: str) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())

    def active(self, thread_id: str) -> ActiveTurn | None:
        return self._active.get(thread_id)

    @property
    def running(self) -> int:
        return len(self._active)

    async def wait_below(self, limit: int) -> None:
        """Until fewer than `limit` turns run (the routine queue's GPU cap)."""
        async with self._finished:
            await self._finished.wait_for(lambda: self.running < limit)

    async def start(
        self, request: TurnRequest, *, lock_held: bool = False
    ) -> tuple[ActiveTurn, asyncio.Queue]:
        """Start a turn and subscribe the caller to it from its first frame.

        With `lock_held`, the caller already holds the thread's lock (it
        truncated history first); the turn takes it over and releases it
        when done. If starting fails, a held lock stays the caller's.
        """
        ws = _ws()
        lock = self.lock_for(request.thread_id)
        if not lock_held:
            await lock.acquire()
        try:
            start_state = await self._state.agent.aget_state(
                ws.graph_config(request.thread_id, request.checkpoint_id)
            )
            turn = ActiveTurn(
                request=request,
                base_checkpoint_id=ws.checkpoint_id_of(start_state),
                prior_assistant_id=ws._last_assistant_id(start_state),
            )
            self._active[request.thread_id] = turn
        except BaseException:
            if not lock_held:
                lock.release()
            raise
        queue = turn.attach(replay=False)
        turn.task = asyncio.create_task(self._run(turn, lock))
        return turn, queue

    async def shutdown(self) -> None:
        turns = list(self._active.values())
        for turn in turns:
            turn.cancel()
        for turn in turns:
            if turn.task is not None:
                with contextlib.suppress(BaseException):
                    await turn.task

    async def _run(self, turn: ActiveTurn, lock: asyncio.Lock) -> TurnOutcome:
        ws = _ws()
        request = turn.request
        state = self._state
        bind_user(request.user_id)
        keep_alive = (
            asyncio.create_task(request.delegation.keep_alive())
            if request.delegation is not None
            else None
        )
        try:
            try:
                outcome = await self._stream(turn)
            except asyncio.CancelledError:
                await self._turn_end(turn, "cancelled")
                outcome = TurnOutcome(
                    "cancelled", await ws.get_pending_approval(state.agent, request.thread_id)
                )
            except DelegationDenied as exc:
                return TurnOutcome("error", error=exc)
            except Exception as exc:
                logger.exception("turn on thread %s failed", request.thread_id)
                turn.emit({"type": "error", "message": str(exc)})
                return TurnOutcome("error", error=exc)
            await ws.persist_active_tip(state.thread_store, state.agent, request.thread_id)
            await state.thread_store.touch(request.thread_id)
            return outcome
        finally:
            if keep_alive is not None:
                keep_alive.cancel()
            self._active.pop(request.thread_id, None)
            turn._finish()
            lock.release()
            async with self._finished:
                self._finished.notify_all()

    async def _stream(self, turn: ActiveTurn) -> TurnOutcome:
        ws = _ws()
        request = turn.request
        agent = self._state.agent
        config = ws.graph_config(
            request.thread_id,
            request.checkpoint_id,
            hitl_enabled=request.hitl_enabled,
            thinking_enabled=request.thinking_enabled,
            delegation=request.delegation,
            approval_mode=request.approval_mode,
        )
        config["recursion_limit"] = self._state.settings.agent_recursion_limit

        turn.emit({"type": "turn_start"})
        async for event in agent.astream_events(request.run_input, config=config, version="v2"):
            for frame in ws._frames_for_event(event):
                turn.emit(frame)

        state = await agent.aget_state(ws.graph_config(request.thread_id))
        pending = ws._pending_approval_from_state(state)
        if pending is not None:
            turn.emit(ws.approval_request_frame(pending))
            await self._turn_end(turn, "awaiting_approval")
            return TurnOutcome("awaiting_approval", pending)
        await self._turn_end(turn, "completed")
        return TurnOutcome("completed")

    async def _turn_end(self, turn: ActiveTurn, status: str) -> None:
        turn.emit(await _ws().turn_end_frame(self._state, turn, status))

