from pathlib import Path

import pytest

from app.agent.syntax_check import is_app_source, syntax_report

TEMPLATES = Path(__file__).resolve().parents[3] / "examples" / "apps"
SOURCES = sorted(TEMPLATES.glob("*/app/**/*.tsx"))

OK = """import { useState } from 'react';
import { Text, View } from 'react-native';

export default function Home() {
  const [count] = useState(0);
  return (
    <View>
      <Text>{count}</Text>
    </View>
  );
}
"""


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(TEMPLATES)))
def test_the_templates_parse_cleanly(path: Path) -> None:
    assert syntax_report(str(path), path.read_text()) is None


def test_valid_source_has_no_report() -> None:
    assert syntax_report("/personal/Apps/a/app/index.tsx", OK) is None


def test_a_duplicated_import_is_reported() -> None:
    source = OK.replace("import { Text", "import { useState } from 'react';\nimport { Text")
    assert syntax_report("/personal/Apps/a/app/index.tsx", source) == (
        "Error after this edit:\n"
        '  line 2: "useState" is already declared on line 1\n'
        "Fix it before build_app."
    )


def test_a_duplicated_component_is_reported() -> None:
    source = OK + "\nfunction Home() {\n  return null;\n}\n"
    report = syntax_report("/personal/Apps/a/app/index.tsx", source)
    assert '  line 13: "Home" is already declared on line 4' in report


def test_an_aliased_import_does_not_clash_with_its_original_name() -> None:
    source = "import { a as b } from 'x';\nconst a = 1;\nexport default () => b;\n"
    assert syntax_report("/personal/Apps/a/app/index.tsx", source) is None


def test_an_unclosed_jsx_tag_is_reported_at_its_line() -> None:
    source = OK.replace("    </View>\n", "")
    report = syntax_report("/personal/Apps/a/app/index.tsx", source)
    assert report.startswith("Syntax error after this edit:\n  line 6:")


def test_a_stray_brace_is_reported_at_its_line() -> None:
    report = syntax_report("/personal/Apps/a/app/index.tsx", OK + "}\n")
    assert report == (
        "Syntax error after this edit:\n"
        '  line 12:1 unexpected "}" (near: "}")\n'
        "Fix it before build_app."
    )


def test_at_most_three_errors_are_listed() -> None:
    report = syntax_report("/personal/Apps/a/app/index.tsx", OK + "}\n" * 5)
    assert report.count("unexpected") <= 3


@pytest.mark.parametrize(
    ("path", "checked"),
    [
        ("/personal/Apps/a/app/index.tsx", True),
        ("/personal/Apps/a/lib/util.ts", True),
        ("/spaces/fam/Apps/chat/app/[id].tsx", True),
        ("/personal/Apps/a/schema.sql", False),
        ("/personal/Apps/a/app.json", False),
        ("/personal/notes/index.tsx", False),
        ("/spaces/fam/index.tsx", False),
        (None, False),
    ],
)
def test_only_app_source_is_checked(path, checked) -> None:
    assert is_app_source(path) is checked
