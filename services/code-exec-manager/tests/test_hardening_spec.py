"""Field-by-field assertion of `app.sessions.build_run_kwargs` against
docs/ARCHITECTURE.md's "Contracts" section's exec-container hardening
spec - the security test for this module. Every value here is either a
hardcoded constant or derived purely from `Settings`; none is
caller-controlled.
"""

from __future__ import annotations

from app.core.config import Settings
from app.sessions import build_run_kwargs


def test_hardening_spec_matches_reference_exactly() -> None:
    settings = Settings(
        workspace_host_dir="/srv/homeai/workspace",
        homeai_uid=1000,
        homeai_gid=1000,
        toolbox_image="homeai-exec-toolbox:latest",
        _env_file=None,
    )

    spec = build_run_kwargs("thread-abc123", settings)

    assert spec == {
        "image": "homeai-exec-toolbox:latest",
        "name": "homeai-exec-thread-abc123",
        "command": ["sleep", "infinity"],
        "detach": True,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": True,
        "tmpfs": {"/tmp": "size=512m", "/home/homeai": "size=64m,uid=1000,gid=1000,mode=0700"},
        "mem_limit": "4g",
        "nano_cpus": 4_000_000_000,
        "user": "1000:1000",
        "pids_limit": 512,
        "volumes": {"/srv/homeai/workspace": {"bind": "/workspace", "mode": "rw"}},
        "labels": {"homeai.exec": "1", "homeai.session": "thread-abc123"},
    }


def test_hardening_spec_uses_host_path_not_container_path() -> None:
    # The bind-mount SOURCE must be whatever `dockerd` (not this process)
    # resolves on the host - `workspace_host_dir`, never a hardcoded
    # in-container path like agent-server's own `/data/workspace`.
    settings = Settings(workspace_host_dir="/srv/homeai/workspace", _env_file=None)

    spec = build_run_kwargs("s1", settings)

    assert list(spec["volumes"].keys()) == ["/srv/homeai/workspace"]
    assert spec["volumes"]["/srv/homeai/workspace"]["bind"] == "/workspace"


def test_hardening_spec_reflects_configured_uid_gid() -> None:
    settings = Settings(homeai_uid=2000, homeai_gid=2001, _env_file=None)

    spec = build_run_kwargs("s1", settings)

    assert spec["user"] == "2000:2001"


def test_hardening_spec_labels_carry_session_id_for_reaper_and_listing() -> None:
    settings = Settings(_env_file=None)

    spec = build_run_kwargs("my-session", settings)

    assert spec["labels"] == {"homeai.exec": "1", "homeai.session": "my-session"}


def test_container_name_matches_naming_convention() -> None:
    from app.sessions import container_name

    assert container_name("abc") == "homeai-exec-abc"
