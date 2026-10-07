"""Syntax errors in app source, reported on the file tool's result (#230).

After a successful `write_file`/`edit_file` on a `.ts`/`.tsx` file in an app
source folder, the new contents are parsed with tree-sitter's TSX grammar and
the first few errors are appended to the tool result. The write itself stands:
a multi-step edit may pass through broken states. Type checking stays in
`build_app`.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Awaitable, Callable
from typing import Any

import tree_sitter_typescript
from deepagents.backends.protocol import BackendProtocol
from deepagents.backends.utils import validate_path
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from tree_sitter import Language, Node, Parser

_APP_SOURCE = re.compile(r"\A/(personal|spaces/[^/]+)/Apps/[^/]+/.+\.tsx?\Z")
_TOOLS = ("write_file", "edit_file")
_MAX_ERRORS = 3
_CONTEXT = 120
_READ_LIMIT = 100_000


@functools.cache
def _parser(tsx: bool) -> Parser:
    grammar = (
        tree_sitter_typescript.language_tsx()
        if tsx
        else tree_sitter_typescript.language_typescript()
    )
    return Parser(Language(grammar))


def parse(source: str, path: str = "x.tsx") -> Node:
    return _parser(path.endswith(".tsx")).parse(source.encode()).root_node


def is_app_source(path: Any) -> bool:
    return isinstance(path, str) and bool(_APP_SOURCE.match(path))


def _error_nodes(node: Node, out: list[Node]) -> None:
    if node.is_error or node.is_missing:
        out.append(node)
        return
    if node.has_error:
        for child in node.children:
            _error_nodes(child, out)


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _CONTEXT else text[:_CONTEXT] + "…"


def _describe(node: Node, lines: list[str]) -> str:
    row, col = node.start_point
    if node.is_missing:
        what = f'missing "{node.type}"'
    else:
        first = (node.text or b"").decode(errors="replace").strip().split("\n", 1)[0]
        what = f'unexpected "{_clip(first)[:40]}"' if first else "unexpected input"
    near = _clip(lines[row]) if row < len(lines) else ""
    return f'line {row + 1}:{col + 1} {what} (near: "{near}")'


def _declared_names(node: Node) -> list[tuple[str, int]]:
    """Top-level names a duplicate of which TypeScript rejects: imports, functions, consts."""
    if node.type == "export_statement":
        declaration = node.child_by_field_name("declaration")
        return _declared_names(declaration) if declaration is not None else []
    row = node.start_point[0] + 1
    if node.type == "import_statement":
        return [
            (n.text.decode(), row)
            for n in _walk(node)
            if n.type == "identifier"
            and n.parent is not None
            and n.parent.type in ("import_clause", "import_specifier", "namespace_import")
            and (n.parent.type != "import_specifier" or n == _local_name(n.parent))
        ]
    if node.type in (
        "function_declaration",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    ):
        name = node.child_by_field_name("name")
        return [(name.text.decode(), row)] if name is not None else []
    if node.type == "lexical_declaration":
        return [
            (name.text.decode(), row)
            for d in node.named_children
            if d.type == "variable_declarator"
            and (name := d.child_by_field_name("name")) is not None
            and name.type == "identifier"
        ]
    return []


def _local_name(specifier: Node) -> Node | None:
    return specifier.child_by_field_name("alias") or specifier.child_by_field_name("name")


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _duplicates(root: Node) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for child in root.named_children:
        for name, row in _declared_names(child):
            if name in seen:
                out.append(f'line {row}: "{name}" is already declared on line {seen[name]}')
            else:
                seen[name] = row
    return out


def syntax_report(path: str, source: str) -> str | None:
    """The tool-result lines for `source`'s problems, or None if it parses cleanly."""
    root = parse(source, path)
    lines = source.split("\n")
    problems: list[str] = []
    if root.has_error:
        nodes: list[Node] = []
        _error_nodes(root, nodes)
        problems = [_describe(n, lines) for n in nodes]
    else:
        problems = _duplicates(root)
    if not problems:
        return None
    shown = problems[:_MAX_ERRORS]
    more = len(problems) - len(shown)
    header = "Syntax error after this edit:" if root.has_error else "Error after this edit:"
    body = "\n".join(f"  {p}" for p in shown)
    tail = f"\n  (+{more} more)" if more else ""
    return f"{header}\n{body}{tail}\nFix it before build_app."


def _target(request: ToolCallRequest) -> str | None:
    call = request.tool_call
    if call["name"] not in _TOOLS:
        return None
    path = (call.get("args") or {}).get("file_path")
    if not isinstance(path, str):
        return None
    try:
        path = validate_path(path)
    except ValueError:
        return None
    return path if is_app_source(path) else None


def _succeeded(result: Any) -> bool:
    return (
        isinstance(result, ToolMessage)
        and result.status != "error"
        and isinstance(result.content, str)
        and not result.content.startswith("Error")
    )


def _append(result: ToolMessage, report: str | None) -> ToolMessage:
    if report is None:
        return result
    return result.model_copy(update={"content": f"{result.content}\n{report}"})


class SyntaxCheckMiddleware(AgentMiddleware):
    def __init__(self, backend: BackendProtocol) -> None:
        super().__init__()
        self._backend = backend

    def _source(self, request: ToolCallRequest, path: str) -> str | None:
        if request.tool_call["name"] == "write_file":
            return request.tool_call["args"].get("content")
        read = self._backend.read(path, 0, _READ_LIMIT)
        return None if read.error or read.file_data is None else read.file_data["content"]

    async def _asource(self, request: ToolCallRequest, path: str) -> str | None:
        if request.tool_call["name"] == "write_file":
            return request.tool_call["args"].get("content")
        read = await self._backend.aread(path, 0, _READ_LIMIT)
        return None if read.error or read.file_data is None else read.file_data["content"]

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]
    ) -> Any:
        result = handler(request)
        path = _target(request)
        if path is None or not _succeeded(result):
            return result
        source = self._source(request, path)
        return result if source is None else _append(result, syntax_report(path, source))

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ) -> Any:
        result = await handler(request)
        path = _target(request)
        if path is None or not _succeeded(result):
            return result
        source = await self._asource(request, path)
        return result if source is None else _append(result, syntax_report(path, source))
