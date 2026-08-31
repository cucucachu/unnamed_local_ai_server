from httpx import AsyncClient


async def test_health_returns_200_with_exact_body(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
