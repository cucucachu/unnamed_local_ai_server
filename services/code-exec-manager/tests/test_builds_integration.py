"""`app.builds.BuildRunner` against a REAL Docker daemon and the REAL
`homeai-app-builder:latest` image: the hardening flags are accepted, the
toolchain works with no network, and the phases see the mounts they should.

Skipped when the daemon, the image, or the builder's fixture app (only in a
repo checkout) is unavailable. Run for real:

    uv run pytest -m integration tests/test_builds_integration.py
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import docker
import docker.errors
import pytest
from fastapi import HTTPException

from app.builds import BuildRunner
from app.core.config import Settings

BUILDER_IMAGE = "homeai-app-builder:latest"
FIXTURE = Path(__file__).resolve().parents[2] / "app-builder" / "tests" / "fixtures" / "groceries"


def _probe() -> tuple[docker.DockerClient | None, str]:
    if not FIXTURE.is_dir():
        return None, f"builder fixture not found at {FIXTURE}"
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure just means "skip"
        return None, f"Docker daemon unreachable: {exc}"
    try:
        client.images.get(BUILDER_IMAGE)
    except docker.errors.ImageNotFound:
        return None, f"{BUILDER_IMAGE} not built - run services/app-builder/build-builder-image.sh"
    return client, ""


_real_client, _skip_reason = _probe()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_real_client is None, reason=_skip_reason),
]


def _stage(root: Path, build_id: str) -> Path:
    build = root / build_id
    shutil.copytree(FIXTURE, build / "src")
    for out in ("bundle", "smoke"):
        (build / out).mkdir()
        (build / out).chmod(0o777)
    return build


def _runner(root: Path, **overrides: object) -> BuildRunner:
    settings = Settings(app_builds_host_dir=str(root), _env_file=None).model_copy(update=overrides)
    return BuildRunner(_real_client, settings)


async def test_a_real_build_compiles_then_smoke_renders(tmp_path: Path) -> None:
    build_id = uuid.uuid4().hex
    build = _stage(tmp_path, build_id)
    runner = _runner(tmp_path)

    compiled = await runner.run(build_id, "compile")
    smoked = await runner.run(build_id, "smoke")

    assert (compiled.exit_code, compiled.timed_out) == (0, False), compiled.stderr
    assert json.loads((build / "bundle" / "result.json").read_text())["ok"] is True
    assert (build / "bundle" / "app.js").read_text().startswith("__homeai_define(")
    assert (smoked.exit_code, smoked.timed_out) == (0, False), smoked.stderr
    smoke = json.loads((build / "smoke" / "result.json").read_text())
    assert smoke["ok"] is True and [r["path"] for r in smoke["routes"]] == ["/", "/item/1"]
    assert not _real_client.containers.list(all=True, filters={"label": f"homeai.build={build_id}"})


async def test_an_unstaged_build_is_refused_without_creating_host_dirs(tmp_path: Path) -> None:
    build_id = uuid.uuid4().hex

    with pytest.raises(HTTPException) as err:
        await _runner(tmp_path).run(build_id, "compile")

    assert err.value.status_code == 404
    assert not (tmp_path / build_id).exists()


async def test_a_phase_past_its_timeout_is_killed(tmp_path: Path) -> None:
    build_id = uuid.uuid4().hex
    _stage(tmp_path, build_id)

    result = await _runner(tmp_path, build_timeout_s=1).run(build_id, "compile")

    assert result.timed_out is True
    assert not _real_client.containers.list(all=True, filters={"label": f"homeai.build={build_id}"})
