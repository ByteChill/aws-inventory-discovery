import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.deps import get_registry
from app.core.db import make_engine
from app.types.registry import TypeRegistry
from tests.conftest import TEST_DATABASE_URL
from tests.support.seed import Seeded, seed

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/0")


@pytest.fixture(scope="session")
def registry() -> TypeRegistry:
    return get_registry()


@pytest.fixture(scope="session")
def seeded(schema_name: str, registry: TypeRegistry) -> Iterator[Seeded]:
    """The official fixtures loaded into the test schema, once for the whole session."""
    engine = make_engine(TEST_DATABASE_URL, schema_name)
    try:
        yield seed(engine, registry)
    finally:
        engine.dispose()


@pytest.fixture
def db_engine(schema_name: str) -> Iterator[Engine]:
    """An engine for the session's migrated schema, for tests that insert their own rows."""
    engine = make_engine(TEST_DATABASE_URL, schema_name)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def client(make_client: Callable[..., TestClient], seeded: Seeded) -> TestClient:
    del seeded  # only needed for its side effect: the data is loaded
    return make_client()


@pytest.fixture
def get_json(client: TestClient) -> Callable[..., Any]:
    """GET a path and return the JSON body, asserting a 200."""

    def get(path: str, **kwargs: Any) -> Any:
        response = client.get(path, **kwargs)
        assert response.status_code == 200, response.text
        return response.json()

    return get
