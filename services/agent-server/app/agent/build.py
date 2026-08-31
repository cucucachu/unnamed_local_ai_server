"""Assembles the deep agent: local model client + real-disk filesystem backend.

NOTE on `deepagents==0.7.11` vs. the ticket's illustrative snippet: introspection
of the installed package (`inspect.signature(deepagents.create_deep_agent)`)
confirmed `create_deep_agent` accepts `checkpointer` directly as a keyword
argument (typed `Checkpointer | None`, i.e. `None | bool | BaseCheckpointSaver`)
and returns an already-compiled `CompiledStateGraph` wired up with that
checkpointer — no separate `.compile(checkpointer=...)` call is needed, unlike
some other langgraph-based builders. `deepagents.backends.FilesystemBackend`
also matches the ticket's snippet exactly, including the `virtual_mode` flag
name (confirmed via `inspect.signature(FilesystemBackend.__init__)`), which is
the real path-traversal guard: with `virtual_mode=True`, all paths are treated
as virtual paths anchored to `root_dir`, `..`/`~` traversal is blocked, and
every resolved path is verified to stay within `root_dir`. This is mandatory
for a workspace-rooted agent and is not skipped or swapped for another mode.
"""

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.graph.state import CompiledStateGraph

from app.agent.model_client import build_model
from app.agent.prompts import SYSTEM_PROMPT
from app.core.config import Settings


def build_agent(settings: Settings, checkpointer) -> CompiledStateGraph:
    return create_deep_agent(
        model=build_model(settings),
        backend=FilesystemBackend(root_dir=settings.workspace_root, virtual_mode=True),
        system_prompt=SYSTEM_PROMPT,
        tools=[],  # execute_code added in M4-04
        checkpointer=checkpointer,
    )
