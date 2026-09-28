"""`parse_grants`: what the manager accepts from the platform before any of it reaches Docker."""

import pytest

from app.grants import GrantsUnavailable, Mount, parse_grants

USER = "00000000-0000-4000-8000-00000000000a"


def _payload(*mounts: dict) -> dict:
    return {"uid": 20001, "gid": 30001, "gids": [30001], "mounts": list(mounts)}


def _m(target: str, read_only: bool = True, host: str = "/srv/homeai/spaces/x/apps/y/ro") -> dict:
    return {"host_path": host, "container_path": target, "read_only": read_only}


@pytest.mark.parametrize(
    "target",
    [
        "/app-data/personal/grocery-list",
        "/app-data/spaces/family/grocery-list",
        "/app-data/spaces/family/grocery-list-0123abcd",
    ],
)
def test_read_only_app_data_mounts_are_accepted(target: str) -> None:
    grants = parse_grants(USER, _payload(_m(target)))
    assert grants.mounts == (Mount("/srv/homeai/spaces/x/apps/y/ro", target, True),)


def test_a_writable_app_data_mount_is_refused() -> None:
    with pytest.raises(GrantsUnavailable, match="read-only"):
        parse_grants(USER, _payload(_m("/app-data/spaces/family/grocery-list", read_only=False)))


@pytest.mark.parametrize(
    "target",
    [
        "/app-data",
        "/app-data/personal",
        "/app-data/spaces/family",
        "/app-data/spaces/family/a/b",
        "/app-data/spaces/../x/y",
        "/app-data/other/grocery-list",
        "/app-data/personal/Grocery",
        "/files/personal/grocery-list",
    ],
)
def test_other_paths_are_refused(target: str) -> None:
    with pytest.raises(GrantsUnavailable, match="container path"):
        parse_grants(USER, _payload(_m(target)))
