import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import REPO_ROOT

PREFIX = "/aws-inventory"
HTML = {"accept": "text/html"}


@pytest.fixture
def client(make_client: Callable[..., TestClient], tmp_path: Path) -> TestClient:
    """The app as deployed behind the shared ALB, serving the dashboard's real index.html."""
    shutil.copy(REPO_ROOT / "frontend" / "index.html", tmp_path / "index.html")
    return make_client(BASE_PATH=PREFIX, FRONTEND_DIST=str(tmp_path))


@pytest.mark.parametrize(
    "path",
    [f"{PREFIX}/api/v1/health", f"{PREFIX}/api/v1/openapi.json", f"{PREFIX}/docs"],
)
def test_api_and_docs_are_served_under_the_prefix(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_docs_load_the_prefixed_schema(client: TestClient) -> None:
    assert f"{PREFIX}/api/v1/openapi.json" in client.get(f"{PREFIX}/docs").text


def test_dashboard_deep_link_is_served_under_the_prefix(client: TestClient) -> None:
    # A browser navigation: the frontend fallback answers only requests that accept HTML.
    response = client.get(f"{PREFIX}/resources/42", headers=HTML)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_bare_prefix_redirects_to_the_trailing_slash(client: TestClient) -> None:
    response = client.get(PREFIX, headers=HTML, follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith(f"{PREFIX}/")


@pytest.mark.parametrize("path", ["/api/v1/health", "/docs", "/resources/42"])
def test_nothing_is_served_outside_the_prefix(client: TestClient, path: str) -> None:
    assert client.get(path, headers=HTML).status_code == 404


def test_unknown_api_path_under_the_prefix_is_a_json_404(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/api/v1/nope", headers={"accept": "application/json"})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
