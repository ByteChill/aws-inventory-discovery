from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.conftest import NOWHERE


def test_health_reports_database_ok_and_redis_unavailable(
    make_client: Callable[..., TestClient],
) -> None:
    response = make_client().get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"database": "ok", "redis": "unavailable"}


def test_health_is_503_when_database_is_down(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client(DATABASE_READ_URL=f"postgresql+psycopg://x:x@{NOWHERE}/x")

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"
