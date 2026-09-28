"""`POST /api/platform/apps/{id}/build` (`app.core.appbuild`).

The builder is a fake that plays the builder container's part on the staging
dir the platform prepared: it reads `src/` and writes `bundle/` and
`smoke/`, including the hostile outputs a compromised build could leave.
`tests/test_builds_integration.py` in code-exec-manager and
`scripts/e2e/app_build_smoke.sh` run the real builder image.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.core import appbuild
from app.core.appbuild import Builds, ExecManagerBuilder, PhaseRun
from app.core.errors import Unavailable
from tests.app_packages import manifest, write_package
from tests.files_world import API, World

APPS = f"{API}/apps"
BUNDLE = b"__homeai_define(function(){});\n"

Script = Callable[[str, Path], PhaseRun | None]


def ok_result(out: Path, **extra) -> None:
    (out / "result.json").write_text(json.dumps({"ok": True, "diagnostics": []} | extra))


def succeed(phase: str, build: Path) -> None:
    if phase == "compile":
        ok_result(build / "bundle", routes=[])
        (build / "bundle" / "app.js").write_bytes(BUNDLE)
        (build / "bundle" / "app.js.map").write_text('{"version":3}')
    else:
        ok_result(build / "smoke")


def fail_with(phase_to_fail: str, *diagnostics: dict) -> Script:
    def script(phase: str, build: Path) -> None:
        if phase != phase_to_fail:
            return succeed(phase, build)
        out = build / ("bundle" if phase == "compile" else "smoke")
        (out / "result.json").write_text(json.dumps({"ok": False, "diagnostics": diagnostics}))

    return script


class FakeBuilder:
    def __init__(self, root: Path, script: Script = succeed) -> None:
        self.root = root
        self.script = script
        self.calls: list[tuple[str, str]] = []
        self.staged: dict[str, bytes] = {}
        self.error: Exception | None = None

    async def run(self, build_id: str, phase: str) -> PhaseRun:
        self.calls.append((build_id, phase))
        if self.error is not None:
            raise self.error
        build = self.root / build_id
        if phase == "compile":
            src = build / "src"
            self.staged = {
                str(p.relative_to(src)): p.read_bytes() for p in src.rglob("*") if p.is_file()
            }
        return self.script(phase, build) or PhaseRun(0, False)


@pytest.fixture
def builder(world: World) -> FakeBuilder:
    fake = FakeBuilder(world.platform.app.state.builds.root)
    world.platform.app.state.builder = fake
    return fake


def _pkg(world: World, slug: str = "hello", **kw) -> Path:
    return write_package(world.family_root / "Apps" / slug, **kw)


async def _registered(world: World, slug: str = "hello") -> dict:
    _pkg(world, slug)
    response = await world.client.post(
        APPS, json={"source_path": f"/spaces/family/Apps/{slug}"}, headers=world.headers["alice"]
    )
    assert response.status_code == 201, response.text
    return response.json()["app"]


async def _build(world: World, app_id: str, headers: dict | None = None) -> httpx.Response:
    return await world.client.post(
        f"{APPS}/{app_id}/build", headers=headers or world.headers["alice"]
    )


def _data(world: World) -> Path:
    return world.platform.app.state.settings.platform_data_dir


def _staging(world: World) -> list[str]:
    return os.listdir(world.platform.app.state.builds.root)


# --- a good build ------------------------------------------------------------------------


async def test_a_build_stores_the_bundle_as_the_working_version(world, builder) -> None:
    app = await _registered(world)

    response = await _build(world, app["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["ok"], body["diagnostics"]) == (True, [])
    build = body["build"]
    bundle_path = f"app-bundles/{app['id']}/{build['id']}/app.js"
    assert build["bundle_path"] == bundle_path and build["bundle_bytes"] == len(BUNDLE)
    assert body["app"]["working_version"]["bundle_path"] == bundle_path
    assert (_data(world) / bundle_path).read_bytes() == BUNDLE
    assert (_data(world) / bundle_path).with_suffix(".js.map").read_text() == '{"version":3}'
    assert [phase for _, phase in builder.calls] == ["compile", "smoke"]
    assert _staging(world) == []


async def test_the_builder_gets_a_plain_copy_it_can_read_and_two_dirs_it_can_write(
    world, builder, chowns
) -> None:
    app = await _registered(world)
    pkg = world.family_root / "Apps" / "hello"
    (pkg / ".git").mkdir()
    (pkg / ".git" / "config").write_text("[core]\n")
    (pkg / "lib").mkdir()
    (pkg / "lib" / "util.ts").write_text("export const one = 1;\n")
    seen: dict[str, int] = {}

    def inspect(phase: str, build: Path) -> None:
        if phase == "compile":
            seen["build"] = stat.S_IMODE((build).stat().st_mode)
            seen["src"] = stat.S_IMODE((build / "src").stat().st_mode)
            seen["file"] = stat.S_IMODE((build / "src" / "lib" / "util.ts").stat().st_mode)
            seen["bundle"] = stat.S_IMODE((build / "bundle").stat().st_mode)
            seen["owner"] = chowns[(build / "bundle").resolve()]
            seen["smoke_owner"] = chowns[(build / "smoke").resolve()]
        succeed(phase, build)

    builder.script = inspect

    assert (await _build(world, app["id"])).json()["ok"] is True
    assert "lib/util.ts" in builder.staged and "app.json" in builder.staged
    assert not any(name.startswith(".git") for name in builder.staged)
    assert seen == {
        "build": 0o700,
        "src": 0o755,
        "file": 0o644,
        "bundle": 0o700,
        "owner": (19999, 19999),
        "smoke_owner": (19999, 19999),
    }


async def test_a_rebuild_replaces_the_bundle_and_drops_the_old_one(world, builder) -> None:
    app = await _registered(world)
    first = (await _build(world, app["id"])).json()["build"]["bundle_path"]

    second = (await _build(world, app["id"])).json()["build"]["bundle_path"]

    assert second != first
    assert (_data(world) / second).is_file()
    assert not (_data(world) / first).parent.exists()


async def test_a_build_takes_the_manifest_like_validate_does(world, builder) -> None:
    app = await _registered(world)
    doc = manifest("hello", name="Hello Again", version="1.2.0")
    (world.family_root / "Apps" / "hello" / "app.json").write_text(json.dumps(doc))

    body = (await _build(world, app["id"])).json()

    assert body["app"]["name"] == "Hello Again"
    assert body["app"]["working_version"]["version"] == "1.2.0"


# --- failures ---------------------------------------------------------------------------


async def test_a_bad_app_json_is_a_manifest_diagnostic_and_never_reaches_the_builder(
    world, builder
) -> None:
    app = await _registered(world)
    doc = manifest("hello")
    doc["homeai"]["sdk"] = "2"
    (world.family_root / "Apps" / "hello" / "app.json").write_text(json.dumps(doc))

    body = (await _build(world, app["id"])).json()

    assert (body["ok"], body["build"]) == (False, None)
    assert body["diagnostics"] == [
        {
            "step": "manifest",
            "file": "app.json",
            "path": "/homeai/sdk",
            "line": None,
            "column": None,
            "message": 'sdk must be one of: "1"',
            "source": None,
        }
    ]
    assert builder.calls == [] and _staging(world) == []


async def test_symlinks_are_refused_not_followed(world, builder, tmp_path) -> None:
    app = await _registered(world)
    secret = tmp_path / "secret.ts"
    secret.write_text("export const key = 'hunter2';\n")
    (world.family_root / "Apps" / "hello" / "lib").mkdir()
    (world.family_root / "Apps" / "hello" / "lib" / "key.ts").symlink_to(secret)

    body = (await _build(world, app["id"])).json()

    assert body["ok"] is False and builder.calls == []
    assert body["diagnostics"][0] | {"source": None} == {
        "step": "files",
        "file": "lib/key.ts",
        "path": "",
        "line": None,
        "column": None,
        "message": "lib/key.ts is a symlink; apps can't use symlinks",
        "source": None,
    }
    assert _staging(world) == []


async def test_a_missing_app_folder_is_a_manifest_diagnostic(world, builder) -> None:
    app = await _registered(world)
    (world.family_root / "Apps" / "hello").rename(world.family_root / "Apps" / "moved")

    body = (await _build(world, app["id"])).json()

    assert body["ok"] is False and builder.calls == []
    assert body["diagnostics"][0]["step"] == "manifest"
    assert "doesn't exist" in body["diagnostics"][0]["message"]


async def test_oversized_files_are_refused(world, builder, monkeypatch) -> None:
    monkeypatch.setattr(appbuild, "MAX_FILE_BYTES", 10)
    app = await _registered(world)

    body = (await _build(world, app["id"])).json()

    assert body["ok"] is False and builder.calls == []
    assert {d["step"] for d in body["diagnostics"]} == {"files"}
    assert "AGENT.md is larger than" in body["diagnostics"][0]["message"]


async def test_builder_diagnostics_are_passed_on_and_the_old_bundle_kept(world, builder) -> None:
    app = await _registered(world)
    good = (await _build(world, app["id"])).json()["build"]["bundle_path"]
    type_error = {
        "step": "type",
        "file": "app/index.tsx",
        "path": "",
        "line": 3,
        "column": 9,
        "message": "TS2322: Type 'number' is not assignable to type 'string'.",
        "source": "const x: string = 1;",
    }
    builder.script = fail_with("compile", type_error)
    builder.calls.clear()

    body = (await _build(world, app["id"])).json()

    assert body["ok"] is False and body["diagnostics"] == [type_error]
    assert body["build"]["bundle_path"] is None
    assert [phase for _, phase in builder.calls] == ["compile"]
    assert body["app"]["working_version"]["bundle_path"] == good
    assert (_data(world) / good).is_file()
    assert _staging(world) == []


async def test_a_render_failure_is_reported_from_the_smoke_step(world, builder) -> None:
    app = await _registered(world)
    render = {"step": "render", "file": "app/index.tsx", "line": 5, "column": 11,
              "message": "boom (on screen /)"}  # fmt: skip
    builder.script = fail_with("smoke", render)

    body = (await _build(world, app["id"])).json()

    assert body["ok"] is False
    assert body["diagnostics"] == [render | {"path": "", "source": None}]
    assert body["app"]["working_version"]["bundle_path"] is None


async def test_builder_diagnostics_are_normalized_and_capped(world, builder) -> None:
    app = await _registered(world)
    hostile = [
        {"step": "rm -rf", "message": "x" * 5000, "line": True, "column": -1, "extra": "?"},
        {"step": "import", "file": 7, "message": "fine", "source": "s" * 999},
        "not an object",
        {"step": "type"},
    ] + [{"step": "type", "message": f"m{i}"} for i in range(100)]
    builder.script = fail_with("compile", *hostile)

    found = (await _build(world, app["id"])).json()["diagnostics"]

    assert found[0] == {"step": "build", "file": "", "path": "", "line": None, "column": None,
                        "message": "x" * 2000, "source": None}  # fmt: skip
    assert found[1]["file"] == "" and len(found[1]["source"]) == 200
    assert len(found) <= 50


def _hostile(write: Callable[[Path], None], phase_to_break: str = "compile") -> Script:
    def script(phase: str, build: Path) -> None:
        succeed(phase, build)
        if phase == phase_to_break:
            write(build / ("bundle" if phase == "compile" else "smoke"))

    return script


def _link(name: str, target: Path) -> Callable[[Path], None]:
    def write(out: Path) -> None:
        (out / name).unlink()
        (out / name).symlink_to(target)

    return write


@pytest.mark.parametrize(
    ("write", "phase", "message"),
    [
        (lambda out: (out / "result.json").write_text("{not json"), "compile",
         "the compile step failed without a result"),
        (lambda out: (out / "result.json").unlink(), "smoke",
         "the smoke step failed without a result"),
        (lambda out: (out / "result.json").write_text(" " * (1024 * 1024 + 1)), "compile",
         "the compile step failed without a result"),
        (lambda out: (out / "result.json").write_text('{"ok": false, "diagnostics": []}'),
         "smoke", "the smoke step failed"),
        (lambda out: (out / "app.js").write_text("fetch('https://evil')"), "compile",
         "the build produced no usable bundle"),
        (lambda out: (out / "app.js.map").unlink(), "compile",
         "the build produced no usable bundle"),
    ],
)  # fmt: skip
async def test_untrusted_outputs_are_refused(world, builder, write, phase, message) -> None:
    app = await _registered(world)
    builder.script = _hostile(write, phase)

    body = (await _build(world, app["id"])).json()

    assert body["ok"] is False
    assert [d["message"] for d in body["diagnostics"]] == [message]
    assert body["app"]["working_version"]["bundle_path"] is None
    assert _staging(world) == []


@pytest.mark.parametrize("name", ["result.json", "app.js"])
async def test_output_symlinks_are_never_followed(world, builder, name) -> None:
    app = await _registered(world)
    planted = _data(world) / "planted.js"
    planted.write_bytes(BUNDLE)
    builder.script = _hostile(_link(name, planted))

    body = (await _build(world, app["id"])).json()

    assert body["ok"] is False
    assert body["app"]["working_version"]["bundle_path"] is None
    assert planted.read_bytes() == BUNDLE


async def test_a_phase_that_timed_out_says_so(world, builder) -> None:
    app = await _registered(world)

    def slow(phase: str, build: Path) -> PhaseRun:
        succeed(phase, build)
        return PhaseRun(-1, True) if phase == "smoke" else None

    builder.script = slow

    body = (await _build(world, app["id"])).json()

    assert body["ok"] is False
    assert body["diagnostics"][0]["message"] == "the smoke step took too long and was stopped"


# --- who may build ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user", "agent", "status"),
    [
        ("alice", False, 200),
        ("bob", False, 200),
        ("bob", True, 200),
        ("carol", False, 403),
        ("carol", True, 403),
        ("dave", False, 404),
    ],
)
async def test_building_needs_write_on_the_source_space(
    world, builder, user, agent, status
) -> None:
    app = await _registered(world)
    headers = await world.agent(user) if agent else world.headers[user]

    response = await _build(world, app["id"], headers)

    assert response.status_code == status, response.text
    if status != 200:
        assert builder.calls == [] and _staging(world) == []


async def test_building_needs_a_session(world, builder) -> None:
    app = await _registered(world)
    assert (await world.client.post(f"{APPS}/{app['id']}/build")).status_code == 401


# --- the builder being unavailable -------------------------------------------------------


async def test_an_unreachable_builder_is_a_503_and_the_staging_dir_is_removed(
    world, builder
) -> None:
    app = await _registered(world)
    builder.error = Unavailable("builder_unavailable")

    response = await _build(world, app["id"])

    assert response.status_code == 503
    assert response.json() == {"detail": "builder_unavailable"}
    assert _staging(world) == []


async def test_builds_are_503_when_the_staging_root_is_unusable(world, builder) -> None:
    app = await _registered(world)
    world.platform.app.state.builds = None

    response = await _build(world, app["id"])

    assert response.status_code == 503


def test_prepare_makes_the_root_private_and_removes_stale_builds(tmp_path) -> None:
    root = tmp_path / "builds"
    (root / "stale" / "bundle").mkdir(parents=True)
    (root / "stale" / "bundle" / "app.js").write_text("x")
    (root / "stale" / "link").symlink_to(tmp_path)
    root.chmod(0o755)

    Builds(root).prepare()

    assert os.listdir(root) == [] and stat.S_IMODE(root.stat().st_mode) == 0o700
    assert tmp_path.is_dir()


# --- ExecManagerBuilder ------------------------------------------------------------------


async def test_the_manager_is_called_with_the_service_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"exit_code": 0, "timed_out": False, "stderr": ""})

    builder = ExecManagerBuilder(
        "http://manager:8090/", "tok", 5, transport=httpx.MockTransport(handler)
    )

    assert await builder.run("ab" * 16, "smoke") == PhaseRun(0, False)
    assert str(seen[0].url) == f"http://manager:8090/builds/{'ab' * 16}/smoke"
    assert seen[0].headers["authorization"] == "Bearer tok"


@pytest.mark.parametrize(
    "respond",
    [
        lambda request: httpx.Response(401, json={"detail": "unauthenticated"}),
        lambda request: httpx.Response(200, text="not json"),
        lambda request: httpx.Response(200, json={"exit_code": 0}),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused")),
    ],
)
async def test_manager_failures_are_unavailable(respond) -> None:
    builder = ExecManagerBuilder("http://m", "tok", 5, transport=httpx.MockTransport(respond))
    with pytest.raises(Unavailable):
        await builder.run("ab" * 16, "compile")


async def test_no_token_means_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("called")

    builder = ExecManagerBuilder("http://m", "", 5, transport=httpx.MockTransport(handler))
    with pytest.raises(Unavailable):
        await builder.run("ab" * 16, "compile")
