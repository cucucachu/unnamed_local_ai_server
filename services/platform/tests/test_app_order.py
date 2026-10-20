"""A user's arrangement of a space's home page (#465): `/spaces/{id}/app-order`."""

from __future__ import annotations

from tests.files_world import API, World


def _url(world: World, space_id: str | None = None) -> str:
    return f"{API}/spaces/{space_id or world.family['id']}/app-order"


async def test_order_is_per_user(world: World) -> None:
    alice, bob = world.headers["alice"], world.headers["bob"]
    assert (await world.client.get(_url(world), headers=alice)).json() == {"items": []}

    response = await world.client.put(
        _url(world), json={"items": ["b", "files", "a", "b"]}, headers=alice
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"items": ["b", "files", "a"]}
    await world.client.put(_url(world), json={"items": ["a"]}, headers=bob)

    assert (await world.client.get(_url(world), headers=alice)).json() == {
        "items": ["b", "files", "a"]
    }
    assert (await world.client.get(_url(world), headers=bob)).json() == {"items": ["a"]}

    await world.client.put(_url(world), json={"items": ["files"]}, headers=alice)
    assert (await world.client.get(_url(world), headers=alice)).json() == {"items": ["files"]}


async def test_viewers_arrange_their_own_page(world: World) -> None:
    carol = world.headers["carol"]
    response = await world.client.put(_url(world), json={"items": ["x"]}, headers=carol)
    assert response.status_code == 200
    assert (await world.client.get(_url(world), headers=carol)).json() == {"items": ["x"]}


async def test_non_members_get_not_found(world: World) -> None:
    dave = world.headers["dave"]
    assert (await world.client.get(_url(world), headers=dave)).status_code == 404
    assert (
        await world.client.put(_url(world), json={"items": []}, headers=dave)
    ).status_code == 404
    assert (await world.client.get(_url(world))).status_code == 401


async def test_items_are_bounded(world: World) -> None:
    alice = world.headers["alice"]
    too_long = await world.client.put(_url(world), json={"items": ["x" * 65]}, headers=alice)
    too_many = await world.client.put(
        _url(world), json={"items": [str(i) for i in range(501)]}, headers=alice
    )
    assert too_long.status_code == 422
    assert too_many.status_code == 422
