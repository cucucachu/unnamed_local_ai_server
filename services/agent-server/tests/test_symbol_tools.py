from pathlib import Path

import pytest

from app.agent.symbol_tools import SymbolEditError, outline_text, replace, symbols

TEMPLATES = Path(__file__).resolve().parents[3] / "examples" / "apps"
SCREENS = sorted(TEMPLATES.glob("*/app/**/*.tsx"))
PATH = "/personal/Apps/a/app/index.tsx"

SOURCE = """import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

type Item = { id: number; name: string };

function FoodSection({ items }: { items: Item[] }) {
  return (
    <View>
      {items.map((i) => (
        <Text key={i.id}>{i.name}</Text>
      ))}
    </View>
  );
}

export default function Home() {
  const [items] = useState<Item[]>([]);
  return <FoodSection items={items} />;
}

const styles = StyleSheet.create({ row: { padding: 8 } });
"""


def test_outline_lists_every_declaration_with_its_lines() -> None:
    assert outline_text(PATH, SOURCE) == (
        f"{PATH} (21 lines)\n"
        "  1-2    imports: react, react-native\n"
        "  4      type Item\n"
        "  6-14   component FoodSection\n"
        "  16-19  export default component Home\n"
        "  21     const styles (StyleSheet)"
    )


@pytest.mark.parametrize("path", SCREENS, ids=lambda p: str(p.relative_to(TEMPLATES)))
def test_outline_ranges_cover_each_template_declaration(path: Path) -> None:
    source = path.read_text()
    lines = source.split("\n")
    found = symbols(source, str(path))
    assert any(
        s.kind == "export default" or s.exported.startswith(b"export default") for s in found
    )
    for s in found:
        text = "\n".join(lines[s.start_line - 1 : s.end_line])
        assert source.encode()[s.start_byte : s.end_byte].decode() in text


@pytest.mark.parametrize("path", SCREENS, ids=lambda p: str(p.relative_to(TEMPLATES)))
def test_replacing_a_template_symbol_keeps_the_rest_byte_identical(path: Path) -> None:
    source = path.read_text()
    target = next(s for s in symbols(source, str(path)) if s.exported.startswith(b"export default"))
    new_code = f"function {target.name}() {{\n  return null;\n}}"
    updated, _ = replace(str(path), source, target.name, new_code)
    raw = source.encode()
    expected = (
        raw[: target.start_byte] + b"export default " + new_code.encode() + raw[target.end_byte :]
    )
    assert updated.encode() == expected


def test_replace_reports_the_new_lines_and_added_helpers() -> None:
    new_code = (
        "function FoodSection({ items }: { items: Item[] }) {\n"
        "  return <View>{items.map((i) => <Row key={i.id} item={i} />)}</View>;\n"
        "}\n\n"
        "function Row({ item }: { item: Item }) {\n"
        "  return <Text>{item.name}</Text>;\n"
        "}\n"
    )
    updated, summary = replace(PATH, SOURCE, "FoodSection", new_code)
    assert summary == (
        f"Replaced component FoodSection in {PATH}: lines 6-14 are now 6-12; also added Row. "
        "The file is 19 lines."
    )
    assert updated.startswith(SOURCE[: SOURCE.index("function FoodSection")])
    assert updated.endswith(SOURCE[SOURCE.index("\n\nexport default") :])


def test_replace_keeps_export_default_when_new_code_drops_it() -> None:
    updated, summary = replace(PATH, SOURCE, "Home", "function Home() {\n  return null;\n}")
    assert "export default function Home() {\n  return null;\n}" in updated
    assert "(kept `export default`)" in summary


@pytest.mark.parametrize(
    ("name", "new_code", "message"),
    [
        ("Missing", "function Missing() {}",
         (f"Error: no top-level declaration named 'Missing' in {PATH}. Declarations: Item (4), "
          "FoodSection (6-14), Home (16-19), styles (21).")),
        ("FoodSection", "function FoodSection() {\n  return (<View>;\n}",
         "Error: new_code doesn't parse, so nothing was written:\n  line 2:"),
        ("FoodSection", "function Other() {}",
         ("Error: new_code must contain exactly one declaration named 'FoodSection' "
          "(it declares: Other (1)).")),
        ("FoodSection", "import x from 'y';\nfunction FoodSection() {}",
         "Error: new_code may contain only declarations"),
        ("FoodSection", "function FoodSection() {}\nfunction Home() {}",
         "Error: new_code declares Home, which the file already declares"),
    ],
)  # fmt: skip
def test_bad_replacements_are_refused(name, new_code, message) -> None:
    with pytest.raises(SymbolEditError) as exc:
        replace(PATH, SOURCE, name, new_code)
    assert str(exc.value).startswith(message)


def test_a_duplicated_name_is_refused() -> None:
    with pytest.raises(SymbolEditError) as exc:
        replace(PATH, SOURCE + "function Home() {}\n", "Home", "function Home() {}")
    assert str(exc.value) == (
        f"Error: 'Home' is declared more than once in {PATH} (lines 16-19, 22); "
        "use edit_file for this change."
    )
