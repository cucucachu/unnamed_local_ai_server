"""A user's tool policy (#342) that a test can change between turns, as Settings would."""

from __future__ import annotations

from typing import Any

from app.agent.approvals import DEFAULT_POLICY, ToolPolicy


class FakeToolPolicies:
    def __init__(self, policy: ToolPolicy = DEFAULT_POLICY) -> None:
        self.policy = policy

    async def for_turn(self, delegation: Any) -> ToolPolicy:
        return self.policy

    def allow(self, *names: str) -> None:
        self.policy = ToolPolicy(self.policy.available, self.policy.allowed | set(names))

    def turn_off(self, *names: str) -> None:
        self.policy = ToolPolicy(self.policy.available - set(names), self.policy.allowed)
