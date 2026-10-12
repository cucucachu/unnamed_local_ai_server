"""MCP results as text the model reads, trimmed to a cap (head and tail kept)."""

from __future__ import annotations

import json
from typing import Any

import mcp_types as types


def trim(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit * 2 // 3
    tail = limit - head
    cut = len(text) - head - tail
    note = f"\n\n[… {cut} characters trimmed: the result was too large …]\n\n"
    return text[:head] + note + text[-tail:], True


def _resource_contents(c: types.TextResourceContents | types.BlobResourceContents) -> str:
    if isinstance(c, types.TextResourceContents):
        return c.text
    size = len(c.blob) * 3 // 4
    return f"[binary content: {c.mime_type or 'unknown type'}, about {size} bytes, {c.uri}]"


def block_text(block: Any) -> str:
    if isinstance(block, types.TextContent):
        return block.text
    if isinstance(block, types.ImageContent):
        return f"[image: {block.mime_type}, about {len(block.data) * 3 // 4} bytes]"
    if isinstance(block, types.AudioContent):
        return f"[audio: {block.mime_type}, about {len(block.data) * 3 // 4} bytes]"
    if isinstance(block, types.ResourceLink):
        return f"[resource: {block.name} {block.uri}]"
    if isinstance(block, types.EmbeddedResource):
        return _resource_contents(block.resource)
    return f"[{getattr(block, 'type', 'unknown')} content]"


def tool_text(result: types.CallToolResult) -> str:
    parts = [block_text(b) for b in result.content]
    if result.structured_content is not None and not any(
        isinstance(b, types.TextContent) for b in result.content
    ):
        parts.append(json.dumps(result.structured_content, ensure_ascii=False, indent=2))
    return "\n\n".join(p for p in parts if p)


def resource_text(result: types.ReadResourceResult) -> str:
    return "\n\n".join(_resource_contents(c) for c in result.contents)


def prompt_text(result: types.GetPromptResult) -> str:
    lines = [f"{m.role}: {block_text(m.content)}" for m in result.messages]
    return "\n\n".join(lines)
