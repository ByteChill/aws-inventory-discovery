import json

import pytest

from app.aws.account_registry import RegistryError
from tests.worker.conftest import FIXTURES, RegistryServer


def test_client_parses_the_registry_response_and_retries_throttling_and_server_errors(
    registry: RegistryServer,
) -> None:
    registry.failures = [429, 503]

    accounts = registry.client().fetch_accounts()

    assert registry.request_count == 3
    expected = json.loads((FIXTURES / "accounts.json").read_text())
    assert [a.model_dump() for a in accounts] == expected


def test_client_gives_up_after_its_retries(registry: RegistryServer) -> None:
    registry.always_fail = 503

    with pytest.raises(RegistryError):
        registry.client().fetch_accounts()

    assert registry.request_count == 4  # first try + 3 retries


def test_client_does_not_retry_client_errors(registry: RegistryServer) -> None:
    registry.always_fail = 404

    with pytest.raises(RegistryError):
        registry.client().fetch_accounts()

    assert registry.request_count == 1
