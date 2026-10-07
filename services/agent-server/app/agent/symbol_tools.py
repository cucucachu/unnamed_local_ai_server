"""`outline` and `replace_symbol`: edit a TS/TSX file one top-level declaration at a time (#232).

App screens are single 150-250 line TSX files. Asked to change one section,
a small model tends to rewrite the whole file or retype a long `old_string`;
rewriting one component it can see whole is what it does best. `outline`
lists the declarations with line ranges, and `replace_symbol` swaps one of
them for new code, which must parse before anything is written.
"""

from __future__ import annotations

from dataclasses import dataclass

from deepagents.backends.protocol import BackendProtocol
from deepagents.backends.utils import validate_path
from langchain_core.tools import tool
from tree_sitter import Node

from app.agent.syntax_check import parse, syntax_report

SYMBOL_TOOL_NAMES: tuple[str, ...] = ("outline", "replace_symbol")
_READ_LIMIT = 100_000
_FUNCTIONS = ("arrow_function", "function_expression", "function")


@dataclass(frozen=True)
class Symbol:
    name: str | None
    kind: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    # The `export ` / `export default ` before the declaration, if any.
    exported: bytes = b""


def _text(node: Node | None) -> str:
    return (node.text or b"").decode(errors="replace") if node is not None else ""


def _function_kind(name: str) -> str:
    return "component" if name[:1].isupper() else "function"


def _const_kind(declarator: Node) -> str:
    value = declarator.child_by_field_name("value")
    if value is None:
        return "const"
    if value.type in _FUNCTIONS:
        return _function_kind(_text(declarator.child_by_field_name("name")))
    if value.type == "call_expression" and _text(value.child_by_field_name("function")).endswith(
        "StyleSheet.create"
    ):
        return "styles"
    return "const"


def _declaration(node: Node) -> tuple[str | None, str]:
    kind = node.type
    if kind in ("function_declaration", "generator_function_declaration"):
        name = _text(node.child_by_field_name("name"))
        return name, _function_kind(name)
    if kind in ("class_declaration", "abstract_class_declaration"):
        return _text(node.child_by_field_name("name")), "class"
    if kind == "interface_declaration":
        return _text(node.child_by_field_name("name")), "interface"
    if kind == "type_alias_declaration":
        return _text(node.child_by_field_name("name")), "type"
    if kind == "enum_declaration":
        return _text(node.child_by_field_name("name")), "enum"
    if kind == "lexical_declaration":
        declarators = [d for d in node.named_children if d.type == "variable_declarator"]
        if declarators:
            return _text(declarators[0].child_by_field_name("name")), _const_kind(declarators[0])
    return None, "statement"


def _symbol(node: Node) -> Symbol | None:
    if node.type == "comment":
        return None
    lines = (node.start_point[0] + 1, node.end_point[0] + 1)
    span = (node.start_byte, node.end_byte)
    if node.type == "import_statement":
        return Symbol(
            _text(node.child_by_field_name("source")).strip("'\""), "import", *lines, *span
        )
    if node.type == "export_statement":
        inner = node.child_by_field_name("declaration")
        prefix = node.text[: inner.start_byte - node.start_byte] if inner is not None else b""
        if inner is not None:
            name, kind = _declaration(inner)
            return Symbol(name, kind, *lines, *span, exported=prefix)
        value = node.child_by_field_name("value")
        if value is not None:
            name = _text(value) if value.type == "identifier" else "default"
            return Symbol(name if name == "default" else None, "export default", *lines, *span)
        return Symbol(None, "export", *lines, *span)
    name, kind = _declaration(node)
    return Symbol(name, kind, *lines, *span)


def symbols(source: str, path: str = "x.tsx") -> list[Symbol]:
    return [s for n in parse(source, path).named_children if (s := _symbol(n)) is not None]


def _label(s: Symbol) -> str:
    export = s.exported.decode().strip()
    name = f" {s.name}" if s.name else ""
    what = f"const{name} (StyleSheet)" if s.kind == "styles" else f"{s.kind}{name}"
    return f"{export + ' ' if export else ''}{what}"


def outline_text(path: str, source: str) -> str:
    rows: list[tuple[str, str]] = []
    imports: list[Symbol] = []
    for s in symbols(source, path):
        if s.kind == "import":
            imports.append(s)
            continue
        if imports:
            rows.append(_imports_row(imports))
            imports = []
        rows.append((_range(s.start_line, s.end_line), _label(s)))
    if imports:
        rows.append(_imports_row(imports))
    total = len(source.removesuffix("\n").split("\n"))
    width = max((len(r) for r, _ in rows), default=0)
    body = "\n".join(f"  {r.ljust(width)}  {label}" for r, label in rows)
    return f"{path} ({total} lines)\n{body}" if rows else f"{path} ({total} lines): no declarations"


def _range(start: int, end: int) -> str:
    return f"{start}" if start == end else f"{start}-{end}"


def _imports_row(imports: list[Symbol]) -> tuple[str, str]:
    modules = ", ".join(dict.fromkeys(s.name for s in imports if s.name))
    return _range(imports[0].start_line, imports[-1].end_line), f"imports: {modules}"


class SymbolEditError(Exception):
    pass


def _available(found: list[Symbol]) -> str:
    names = [f"{s.name} ({_range(s.start_line, s.end_line)})" for s in found if s.name]
    return ", ".join(names) or "none"


def replace(path: str, source: str, name: str, new_code: str) -> tuple[str, str]:
    """`source` with the declaration `name` replaced by `new_code`, and what changed."""
    found = [s for s in symbols(source, path) if s.kind != "import"]
    targets = [s for s in found if s.name == name]
    if not targets:
        raise SymbolEditError(
            f"Error: no top-level declaration named '{name}' in {path}. "
            f"Declarations: {_available(found)}."
        )
    if len(targets) > 1:
        lines = ", ".join(_range(s.start_line, s.end_line) for s in targets)
        raise SymbolEditError(
            f"Error: '{name}' is declared more than once in {path} (lines {lines}); "
            "use edit_file for this change."
        )
    [target] = targets
    report = syntax_report(path, new_code)
    if report is not None and report.startswith("Syntax error"):
        detail = report.split("\n", 1)[1].rsplit("\n", 1)[0]
        raise SymbolEditError(f"Error: new_code doesn't parse, so nothing was written:\n{detail}")
    added = symbols(new_code, path)
    if any(s.kind in ("import", "export") or s.name is None for s in added):
        raise SymbolEditError(
            "Error: new_code may contain only declarations (no imports or other "
            "statements); add imports with edit_file."
        )
    primary = [s for s in added if s.name == name]
    if len(primary) != 1:
        raise SymbolEditError(
            f"Error: new_code must contain exactly one declaration named '{name}' "
            f"(it declares: {_available(added)})."
        )
    others = {s.name for s in found if s is not target}
    clash = [s.name for s in added if s.name != name and s.name in others]
    if clash:
        raise SymbolEditError(
            f"Error: new_code declares {', '.join(clash)}, which the file already declares; "
            "replace those with their own replace_symbol call."
        )
    code = new_code.strip().encode()
    kept = ""
    if target.exported and not primary[0].exported:
        at = primary[0].start_byte - (len(new_code.encode()) - len(new_code.lstrip().encode()))
        code = code[:at] + target.exported + code[at:]
        kept = f" (kept `{target.exported.decode().strip()}`)"
    raw = source.encode()
    updated = (raw[: target.start_byte] + code + raw[target.end_byte :]).decode()
    end = target.start_line + code.count(b"\n")
    new_names = [s.name for s in added if s.name != name]
    also = f"; also added {', '.join(new_names)}" if new_names else ""
    summary = (
        f"Replaced {target.kind} {name}{kept} in {path}: lines "
        f"{_range(target.start_line, target.end_line)} are now {_range(target.start_line, end)}"
        f"{also}. The file is {len(updated.removesuffix(chr(10)).split(chr(10)))} lines."
    )
    return updated, summary


def _ts_path(file_path: str) -> str:
    path = validate_path(file_path)
    if not path.endswith((".ts", ".tsx")):
        raise SymbolEditError(f"Error: {path} is not a .ts or .tsx file.")
    return path


async def _read(backend: BackendProtocol, path: str) -> str:
    result = await backend.aread(path, 0, _READ_LIMIT)
    if result.error or result.file_data is None:
        raise SymbolEditError(result.error or f"Error: could not read {path}")
    return result.file_data["content"]


def make_symbol_tools(backend: BackendProtocol) -> list:
    @tool
    async def outline(file_path: str) -> str:
        """List the top-level declarations of a .ts/.tsx file with their line ranges.

        Shows imports, types, components and functions, consts (such as a StyleSheet's
        styles) and the default export, e.g. `14-88  component FoodSection`. Use it on a
        long screen file before changing one part of it, then read_file just those lines.
        """
        try:
            path = _ts_path(file_path)
            return outline_text(path, await _read(backend, path))
        except (SymbolEditError, ValueError) as exc:
            return str(exc)

    @tool
    async def replace_symbol(file_path: str, name: str, new_code: str) -> str:
        """Replace one whole top-level declaration in a .ts/.tsx file with new_code.

        `name` is a component, function, type or const as listed by outline. new_code is
        the complete new declaration with the same name (the whole
        `function FoodSection(...) { ... }`); it may also define new helper components or
        consts, which are added next to it. `export`/`export default` is kept if new_code
        leaves it off. Nothing is written if new_code doesn't parse. Use it instead of
        edit_file when changing most of one component, and instead of write_file when the
        rest of the file stays as it is.
        """
        try:
            path = _ts_path(file_path)
            updated, summary = replace(path, await _read(backend, path), name, new_code)
        except (SymbolEditError, ValueError) as exc:
            return str(exc)
        written = await backend.awrite(path, updated)
        if written.error:
            return written.error
        if await _read(backend, path) != updated:
            return f"Error: {path} changed while it was being edited; read it and try again."
        report = syntax_report(path, updated)
        return summary if report is None else f"{summary}\n{report}"

    return [outline, replace_symbol]
