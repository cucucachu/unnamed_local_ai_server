"""A small world for the files tests: one shared space and a member of every role.

alice  owner of /spaces/family (and of her own /personal)
bob    editor of /spaces/family
carol  viewer of /spaces/family
dave   not a member
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tests.conftest import Platform
from tests.helpers import create_user, delegation, identity, login, sql

API = "/api/platform"
FILES = f"{API}/files"
ROLES = {"alice": "owner", "bob": "editor", "carol": "viewer", "dave": None}


@dataclass
class World:
    platform: Platform
    headers: dict[str, dict[str, str]] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    users: dict[str, dict] = field(default_factory=dict)
    family: dict = field(default_factory=dict)

    @property
    def client(self):
        return self.platform.client

    def personal(self, username: str) -> dict:
        (row,) = sql(
            self.platform,
            "SELECT s.* FROM spaces s WHERE s.kind = 'personal' AND s.owner_user_id = %s",
            (self.users[username]["id"],),
        )
        return row

    def root(self, space: dict) -> Path:
        return self.platform.app.state.storage.space_dir(space["id"]).resolve() / "files"

    def home(self, username: str) -> Path:
        return self.root(self.personal(username))

    @property
    def family_root(self) -> Path:
        return self.root(self.family)

    async def agent(self, username: str) -> dict[str, str]:
        return await delegation(self.platform, self.tokens[username])


async def build_world(platform: Platform) -> World:
    world = World(platform)
    for username in ROLES:
        world.users[username] = await create_user(platform, username)
        world.tokens[username] = await login(platform, username)
        world.headers[username] = await identity(platform, world.tokens[username])
    response = await platform.client.post(
        f"{API}/spaces", json={"slug": "family", "name": "Family"}, headers=world.headers["alice"]
    )
    assert response.status_code == 201, response.text
    world.family = response.json()
    for username, role in ROLES.items():
        if role in ("editor", "viewer"):
            response = await platform.client.post(
                f"{API}/spaces/{world.family['id']}/members",
                json={"user_id": str(world.users[username]["id"]), "role": role},
                headers=world.headers["alice"],
            )
            assert response.status_code == 201, response.text
    return world
