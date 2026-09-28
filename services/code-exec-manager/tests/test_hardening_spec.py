"""Field-by-field assertion of `app.sessions.build_run_kwargs` against
docs/ARCHITECTURE.md's "Contracts" section's exec-container hardening
spec - the security test for this module. Every value here is either a
hardcoded constant, derived from `Settings`, or taken from the session's
`Grants`; none is caller-controlled.
"""

from __future__ import annotations

from docker.types import Mount as DockerMount

from app.core.config import Settings
from app.grants import Grants, Mount
from app.sessions import build_run_kwargs, container_name
from tests.conftest import USER_A, grants_for


def test_hardening_spec_matches_reference_exactly() -> None:
    settings = Settings(toolbox_image="homeai-exec-toolbox:latest", _env_file=None)
    grants = Grants(
        user_id=USER_A,
        uid=20001,
        gid=30001,
        gids=(30001, 30050, 30051),
        mounts=(
            Mount("/srv/homeai/spaces/a/files", "/files/personal", False),
            Mount("/srv/homeai/spaces/f/files", "/files/spaces/family", False),
            Mount("/srv/homeai/spaces/w/files", "/files/spaces/work", True),
        ),
    )

    spec = build_run_kwargs("thread-abc123", settings, grants)

    assert spec == {
        "image": "homeai-exec-toolbox:latest",
        "name": "homeai-exec-thread-abc123",
        "command": ["sleep", "infinity"],
        "detach": True,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": True,
        "tmpfs": {"/tmp": "size=512m", "/home/homeai": "size=64m,uid=20001,gid=30001,mode=0700"},
        "mem_limit": "4g",
        "nano_cpus": 4_000_000_000,
        "user": "20001:30001",
        "group_add": ["30050", "30051"],
        "pids_limit": 512,
        "mounts": [
            {
                "Target": "/files/personal",
                "Source": "/srv/homeai/spaces/a/files",
                "Type": "bind",
                "ReadOnly": False,
            },
            {
                "Target": "/files/spaces/family",
                "Source": "/srv/homeai/spaces/f/files",
                "Type": "bind",
                "ReadOnly": False,
            },
            {
                "Target": "/files/spaces/work",
                "Source": "/srv/homeai/spaces/w/files",
                "Type": "bind",
                "ReadOnly": True,
            },
        ],
        "labels": {
            "homeai.exec": "1",
            "homeai.session": "thread-abc123",
            "homeai.user": USER_A,
            "homeai.grants": grants.digest,
        },
    }
    assert all(isinstance(m, DockerMount) for m in spec["mounts"])


def test_no_volumes_privileged_or_extra_fields() -> None:
    spec = build_run_kwargs("s1", Settings(_env_file=None), grants_for(USER_A))

    for forbidden in ("volumes", "privileged", "cap_add", "devices", "network", "pid_mode"):
        assert forbidden not in spec


def test_personal_only_user_gets_no_supplementary_groups() -> None:
    grants = grants_for(USER_A, uid=20009, gid=30009, shared=False)

    spec = build_run_kwargs("s1", Settings(_env_file=None), grants)

    assert spec["user"] == "20009:30009"
    assert spec["group_add"] == []
    assert [m["Target"] for m in spec["mounts"]] == ["/files/personal"]


def test_grants_digest_changes_with_any_grant_field() -> None:
    base = grants_for(USER_A)
    variants = [
        Grants(base.user_id, base.uid + 1, base.gid, base.gids, base.mounts),
        Grants(base.user_id, base.uid, base.gid, base.gids + (30099,), base.mounts),
        Grants(base.user_id, base.uid, base.gid, base.gids, base.mounts[:1]),
        Grants(
            base.user_id,
            base.uid,
            base.gid,
            base.gids,
            (base.mounts[0], Mount(base.mounts[1].host_path, base.mounts[1].container_path, False)),
        ),
        Grants("22222222-2222-4222-8222-222222222222", base.uid, base.gid, base.gids, base.mounts),
    ]

    digests = {v.digest for v in variants}

    assert base.digest not in digests
    assert len(digests) == len(variants)
    assert grants_for(USER_A).digest == base.digest


def test_container_name_matches_naming_convention() -> None:
    assert container_name("abc") == "homeai-exec-abc"
