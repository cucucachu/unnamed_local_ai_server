"""The agent file tools: `app.core.agentfs` and `/api/platform/files/{read,write,edit,grep,glob}`.

Expected values are `deepagents==0.7.11` `FilesystemBackend` outputs
(captured by running it on the same inputs), so M11-02's backend can pass
them through unchanged. Known differences are listed in `agentfs`'s docstring.
"""

from __future__ import annotations

import pytest

from app.core import agentfs
from app.core.agentfs import EMPTY_CONTENT_WARNING, AgentFsError
from tests.files_world import FILES, World

TEN = "".join(f"line {i} hello\n" for i in range(1, 11))


# --- read ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "limit", "window", "next_offset"),
    [(0, 2000, (1, 10), None), (3, 4, (4, 7), 7), (8, 100, (9, 10), None), (-3, 2, (1, 2), 2)],
)
def test_slice_windows(offset, limit, window, next_offset) -> None:
    out = agentfs.slice_text(TEN, offset, limit)
    start, end = window
    assert out == {
        "content": "".join(f"line {i} hello\n" for i in range(start, end + 1)),
        "encoding": "utf-8",
        "total_lines": 10,
        "start_line": start,
        "end_line": end,
        "next_offset": next_offset,
    }


def test_slice_edges() -> None:
    assert agentfs.slice_text(TEN, 0, 0) == {
        "content": "", "encoding": "utf-8", "no_lines_requested": True,
    }  # fmt: skip
    for empty in ("", "   \n\n"):
        assert agentfs.slice_text(empty, 5, 10)["content"] == EMPTY_CONTENT_WARNING
    with pytest.raises(AgentFsError) as exc:
        agentfs.slice_text(TEN, 10, 5)
    assert (exc.value.code, exc.value.message, exc.value.extra) == (
        "offset_out_of_range", "Line offset 10 exceeds file length (10 lines)", {"total_lines": 10},
    )  # fmt: skip


def test_read_file_crlf_binary_and_undecodable(tmp_path) -> None:
    (tmp_path / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    assert agentfs.read_file(tmp_path / "crlf.txt", "/p/crlf.txt", 0, 10)["content"] == "one\ntwo\n"
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    assert agentfs.read_file(tmp_path / "img.png", "/p/img.png", 0, 10) == {
        "content": "iVBORw0KGgpmYWtl", "encoding": "base64",
    }  # fmt: skip
    (tmp_path / "bad.txt").write_bytes(b"hello\n\xff\xfe bad\n")
    with pytest.raises(AgentFsError) as exc:
        agentfs.read_file(tmp_path / "bad.txt", "/p/bad.txt", 0, 10)
    assert exc.value.message.startswith(
        "Error reading file '/p/bad.txt': 'utf-8' codec can't decode byte 0xff in position 6"
    )


def test_read_types() -> None:
    assert [agentfs.read_type(n) for n in ("a.PNG", "b.mkv", "c.mp3", "d.pdf", "e.md")] == [
        "image", "video", "audio", "file", "text",
    ]  # fmt: skip


# --- edit ---------------------------------------------------------------------------


def test_edit_semantics() -> None:
    text = "alpha beta\nbeta gamma\n"
    assert agentfs.edit_text(text, "alpha", "ALPHA", False) == ("ALPHA beta\nbeta gamma\n", 1)
    assert agentfs.edit_text(text, "beta", "B", True) == ("alpha B\nB gamma\n", 2)
    assert agentfs.edit_text("a\nb\n", "a\r\nb", "c\r\nd", False) == ("c\nd\n", 1)


@pytest.mark.parametrize(
    ("content", "old", "code", "message"),
    [
        ("alpha beta\nbeta gamma\n", "nope", "string_not_found",
         "Error: String not found in file: 'nope'"),
        ("alpha beta\nbeta gamma\n", "beta", "string_not_unique",
         ("Error: String 'beta' appears 2 times in file. Use replace_all=True to replace all "
          "instances, or provide a more specific string with surrounding context.")),
        ("one\ntwo", "two\n", "trailing_newline_mismatch",
         ("Error: old_string ends with a newline, but the file does not end with a newline. "
          "Retry with the trailing newline removed from old_string (and from new_string if it "
          "also ends with a newline).")),
        ("x-x", "x\n", "trailing_newline_mismatch",
         ("Error: old_string ends with a newline, but the file does not end with a newline. "
          "With the trailing newline removed, old_string would appear 2 times in the file. "
          "Retry with the trailing newline removed and add surrounding context so the match is "
          "unique.")),
    ],
)  # fmt: skip
def test_edit_errors_match_deepagents(content, old, code, message) -> None:
    with pytest.raises(AgentFsError) as exc:
        agentfs.edit_text(content, old, "x", False)
    assert (exc.value.code, exc.value.message) == (code, message)


# --- glob patterns ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "rel", "matches"),
    [
        ("*.txt", "a.txt", True), ("*.txt", "sub/d.txt", True),  # unanchored: basename
        ("sub/*.txt", "sub/d.txt", True), ("sub/*.txt", "sub/deep/e.txt", False),
        ("**/*.txt", "a.txt", True), ("**/*.txt", "sub/deep/e.txt", True),
        ("{a,b}.txt", "b.txt", True), ("/sub/*", "sub/c.py", True),
    ],
)  # fmt: skip
def test_compile_glob(pattern, rel, matches) -> None:
    assert agentfs.compile_glob(pattern)(rel) is matches


def test_compile_glob_refuses_traversal() -> None:
    with pytest.raises(AgentFsError) as exc:
        agentfs.compile_glob("../*")
    assert exc.value.message == "Path traversal not allowed in glob pattern '../*'"


# --- over HTTP ----------------------------------------------------------------------


def _tree(world: World) -> None:
    home = world.home("bob")
    (home / "sub" / "deep").mkdir(parents=True)
    (home / "a.txt").write_text(TEN)
    (home / "b.txt").write_text("hello world\nno match\nhello again")
    (home / "sub" / "c.py").write_text("print('hello')\n")
    (home / "sub" / "deep" / "e.txt").write_text("deep hello\n")
    (home / "out").symlink_to(world.family_root)
    (world.family_root / "f.txt").write_text("family hello\n")


async def _call(world: World, route: str, user: str = "bob", **body):
    return await world.client.post(f"{FILES}/{route}", json=body, headers=await world.agent(user))


async def test_read_over_http(world: World) -> None:
    _tree(world)
    r = await _call(world, "read", path="/personal/a.txt", offset=3, limit=2)
    assert r.status_code == 200
    assert r.json() == {
        "path": "/personal/a.txt", "content": "line 4 hello\nline 5 hello\n", "encoding": "utf-8",
        "total_lines": 10, "start_line": 4, "end_line": 5, "next_offset": 5,
        "no_lines_requested": False,
    }  # fmt: skip
    r = await _call(world, "read", path="/personal/a.txt", offset=99)
    assert (r.status_code, r.json()) == (422, {
        "detail": "offset_out_of_range", "total_lines": 10,
        "message": "Line offset 99 exceeds file length (10 lines)",
    })  # fmt: skip
    for missing in ("/personal/nope.txt", "/personal/sub"):
        assert (await _call(world, "read", path=missing)).json() == {"detail": "not_found"}


async def test_write_creates_parents_and_overwrites(world: World) -> None:
    r = await _call(world, "write", path="/personal/new/dir/n.md", content="# hi\n")
    assert (r.status_code, r.json()) == (200, {"path": "/personal/new/dir/n.md"})
    assert (world.home("bob") / "new" / "dir" / "n.md").read_text() == "# hi\n"
    r = await _call(world, "write", path="/personal/new/dir/n.md", content="again")
    assert (world.home("bob") / "new" / "dir" / "n.md").read_text() == "again"
    r = await _call(world, "write", path="/personal/new", content="x")
    assert (r.status_code, r.json()) == (409, {"detail": "is_a_directory"})


async def test_edit_over_http(world: World) -> None:
    _tree(world)
    r = await _call(world, "edit", path="/personal/b.txt", old_string="hello", new_string="bye")
    assert (r.status_code, r.json()["detail"], r.json()["occurrences"]) == (
        422, "string_not_unique", 2,
    )  # fmt: skip
    assert r.json()["message"].startswith("Error: String 'hello' appears 2 times")
    r = await _call(
        world, "edit", path="/personal/b.txt", old_string="hello", new_string="bye",
        replace_all=True,
    )  # fmt: skip
    assert r.json() == {"path": "/personal/b.txt", "occurrences": 2}
    assert (world.home("bob") / "b.txt").read_text() == "bye world\nno match\nbye again"


async def test_grep_over_http(world: World) -> None:
    _tree(world)
    r = await _call(world, "grep", pattern="hello", path="/personal", glob="*.txt")
    assert r.json() == {
        "matches": [
            *(
                {"path": "/personal/a.txt", "line": i, "text": f"line {i} hello"}
                for i in range(1, 11)
            ),
            {"path": "/personal/b.txt", "line": 1, "text": "hello world"},
            {"path": "/personal/b.txt", "line": 3, "text": "hello again"},
            {"path": "/personal/sub/deep/e.txt", "line": 1, "text": "deep hello"},
        ],
        "truncated": False,
        "error": None,
    }  # the symlink into /spaces/family is never followed
    r = await _call(world, "grep", pattern="hello", path="/personal", max_count=2)
    assert (len(r.json()["matches"]), r.json()["truncated"]) == (2, True)
    r = await _call(world, "grep", pattern="hello", path="/personal/nope")
    assert r.json() == {"matches": [], "truncated": False, "error": None}
    r = await _call(world, "grep", pattern="x", glob="../*")
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_glob")


async def test_grep_and_glob_from_the_root_span_readable_spaces(world: World) -> None:
    _tree(world)
    (world.home("dave") / "dave.txt").write_text("hello from dave\n")
    for path in (None, "/"):
        r = await _call(world, "grep", pattern="family hello", **({"path": path} if path else {}))
        assert r.json()["matches"] == [
            {"path": "/spaces/family/f.txt", "line": 1, "text": "family hello"}
        ]
    r = await _call(world, "glob", pattern="**/*.txt", path="/spaces")
    assert [m["path"] for m in r.json()["matches"]] == ["/spaces/family/f.txt"]
    r = await _call(world, "grep", pattern="hello", path="/", user="dave")
    assert [m["path"] for m in r.json()["matches"]] == ["/personal/dave.txt"]


async def test_glob_over_http(world: World) -> None:
    _tree(world)
    r = await _call(world, "glob", pattern="**/*.txt", path="/personal")
    body = r.json()
    assert [m["path"] for m in body["matches"]] == [
        "/personal/a.txt", "/personal/b.txt", "/personal/sub/deep/e.txt",
    ]  # fmt: skip
    assert body["matches"][0]["is_dir"] is False and body["matches"][0]["size"] == len(TEN)
    assert (body["truncated"], body["truncation_reason"]) == (False, None)
    r = await _call(world, "glob", pattern="deep/*", path="/personal/sub")
    assert [m["path"] for m in r.json()["matches"]] == ["/personal/sub/deep/e.txt"]
