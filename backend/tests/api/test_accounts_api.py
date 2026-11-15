"""Account master data, bulk scan policy, import and effective-scope preview."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert, update
from sqlmodel import col

from app.models import AccountRegion, AccountScanRegion
from app.models.account import utcnow
from app.query.tables import ACCOUNT
from app.types.registry import TypeRegistry
from tests.conftest import NOWHERE
from tests.support.seed import IMAGES_ACCOUNT, MAIN_ACCOUNT, Seeded
from tests.worker.conftest import RegistryServer

API = "/api/v1"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "registry"
CHANGED = json.loads((FIXTURES / "accounts_changed.json").read_text())
NEW_ACCOUNT = next(account for account in CHANGED if account["account_id"] == "999900001111")


@pytest.fixture
def registry_server() -> Iterator[RegistryServer]:
    server = RegistryServer()
    server.thread.start()
    yield server
    server.server.shutdown()
    server.server.server_close()


@pytest.fixture(autouse=True)
def restore_accounts(db_engine: Engine, seeded: Seeded) -> Iterator[None]:
    yield
    with db_engine.begin() as connection:
        connection.execute(delete(AccountScanRegion))
        connection.execute(
            delete(AccountRegion).where(col(AccountRegion.region).in_(["eu-west-1", "eu-west-2"]))
        )
        connection.execute(
            delete(ACCOUNT).where(
                ACCOUNT.c.account_id.not_in([account["account_id"] for account in seeded.accounts])
            )
        )
        for account in seeded.accounts:
            connection.execute(
                update(ACCOUNT)
                .where(ACCOUNT.c.account_id == account["account_id"])
                .values(
                    account_name=account["account_name"],
                    account_type=account["account_type"],
                    eim_id=account["eim_id"],
                    eim_app=account["eim_app"],
                    ba_id=account["ba_id"],
                    active=account.get("active", True),
                    inactive_since=account.get("inactive_since"),
                    source=account["source"],
                    scan_enabled=True,
                    scan_region_mode="all",
                )
            )


def test_create_read_update_soft_delete_and_restore(
    client: TestClient,
) -> None:
    created = client.post(
        f"{API}/accounts",
        json={
            **NEW_ACCOUNT,
            "scanEnabled": False,
            "scanRegionMode": "selected",
            "selectedRegions": ["us-east-1", "ap-southeast-1"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["accountId"] == NEW_ACCOUNT["account_id"]
    assert (body["source"], body["active"]) == ("manual", True)
    assert (body["scanEnabled"], body["scanRegionMode"]) == (False, "selected")
    assert {
        region["region"]: region["state"] for region in body["regions"] if region["selected"]
    } == {
        "us-east-1": "unknown",
        "ap-southeast-1": "unreachable",
    }

    duplicate = client.post(f"{API}/accounts", json=NEW_ACCOUNT)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "account_exists"

    changed = client.patch(
        f"{API}/accounts/{NEW_ACCOUNT['account_id']}",
        json={
            "accountName": "reporting-prod-local",
            "scanEnabled": True,
            "scanRegionMode": "all",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["accountName"] == "reporting-prod-local"
    assert changed.json()["scanEnabled"] is True

    assert client.delete(f"{API}/accounts/{NEW_ACCOUNT['account_id']}").status_code == 204
    removed = client.get(f"{API}/accounts/{NEW_ACCOUNT['account_id']}").json()
    assert removed["active"] is False and removed["purgeAt"] is not None
    assert removed["scanEnabled"] is True  # deletion is not "stop scanning"

    restored = client.post(f"{API}/accounts/{NEW_ACCOUNT['account_id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["active"] is True
    assert restored.json()["purgeAt"] is None


def test_patch_validates_and_does_not_change_the_account_id(client: TestClient) -> None:
    empty = client.patch(f"{API}/accounts/{MAIN_ACCOUNT}", json={})
    assert empty.status_code == 422
    changed_id = client.patch(
        f"{API}/accounts/{MAIN_ACCOUNT}",
        json={"accountId": "999900001111"},
    )
    assert changed_id.status_code == 422
    missing = client.patch(f"{API}/accounts/999999999999", json={"scanEnabled": False})
    assert missing.status_code == 404
    for null_field in (
        {"accountName": None},
        {"accountType": None},
        {"scanEnabled": None},
        {"scanRegionMode": None},
        {"selectedRegions": None},
    ):
        assert client.patch(f"{API}/accounts/{MAIN_ACCOUNT}", json=null_field).status_code == 422


def test_batch_policy_by_eim_and_environment_and_effective_preview(
    client: TestClient, registry: TypeRegistry
) -> None:
    by_eim = client.post(
        f"{API}/accounts/batch-policy",
        json={
            "selector": {"eimIds": ["10000001"]},
            "scanEnabled": False,
        },
    )
    assert by_eim.status_code == 200, by_eim.text
    assert by_eim.json() == {"updated": 1}  # the dev account is inactive and not selected
    assert client.get(f"{API}/accounts/{MAIN_ACCOUNT}").json()["scanEnabled"] is False
    assert client.get(f"{API}/accounts/{IMAGES_ACCOUNT}").json()["scanEnabled"] is True

    by_environment = client.post(
        f"{API}/accounts/batch-policy",
        json={
            "selector": {"accountTypes": ["prod"]},
            "scanEnabled": True,
            "scanRegionMode": "selected",
            "selectedRegions": ["ap-southeast-1"],
        },
    )
    assert by_environment.json() == {"updated": 2}

    preview = client.post(
        f"{API}/accounts/scan-preview",
        json={
            "selector": {"accountTypes": ["prod"]},
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "accounts": 2,
        "regions": 0,
        "estimatedTasks": 2,
        "accountsWithoutRegionData": 1,
        "skippedDisabledAccounts": 0,
    }

    all_regions = client.post(
        f"{API}/accounts/scan-preview",
        json={"selector": {"accountIds": [MAIN_ACCOUNT]}, "overrideScanPolicy": True},
    ).json()
    assert all_regions["accounts"] == 1
    assert all_regions["regions"] == 1
    assert all_regions["estimatedTasks"] == 1 + len(registry)


def test_effective_regions_intersect_policy_account_opt_in_and_network(
    client: TestClient, db_engine: Engine, registry: TypeRegistry
) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            insert(AccountRegion),
            [
                {
                    "account_id": MAIN_ACCOUNT,
                    "region": "eu-west-1",
                    "opt_in_status": "opt-in-not-required",
                    "reachable": True,
                    "config_recorder": None,
                    "checked_at": utcnow(),
                },
                {
                    "account_id": MAIN_ACCOUNT,
                    "region": "eu-west-2",
                    "opt_in_status": "not-opted-in",
                    "reachable": True,
                    "config_recorder": None,
                    "checked_at": utcnow(),
                },
            ],
        )
    changed = client.patch(
        f"{API}/accounts/{MAIN_ACCOUNT}",
        json={
            "scanRegionMode": "selected",
            "selectedRegions": ["eu-west-1", "eu-west-2", "ap-southeast-1"],
        },
    )
    assert changed.status_code == 200, changed.text
    detail = client.get(f"{API}/accounts/{MAIN_ACCOUNT}").json()
    states = {region["region"]: region for region in detail["regions"]}
    assert states["eu-west-1"]["state"] == "selected"
    assert states["eu-west-1"]["effective"] is True
    assert states["eu-west-2"]["state"] == "account_disabled"
    assert states["eu-west-2"]["effective"] is False
    assert states["ap-southeast-1"]["state"] == "unreachable"
    assert states["ap-southeast-1"]["effective"] is False
    assert states["ap-east-1"]["state"] == "not_selected"

    preview = client.post(
        f"{API}/accounts/scan-preview",
        json={"selector": {"accountIds": [MAIN_ACCOUNT]}},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["regions"] == 1
    assert preview.json()["estimatedTasks"] == 1 + len(registry)

    proposed_off = client.post(
        f"{API}/accounts/scan-preview",
        json={"selector": {"accountIds": [MAIN_ACCOUNT]}, "scanEnabled": False},
    )
    assert proposed_off.json()["accounts"] == 0
    assert proposed_off.json()["skippedDisabledAccounts"] == 1

    proposed_selected = client.post(
        f"{API}/accounts/scan-preview",
        json={
            "selector": {"accountIds": [MAIN_ACCOUNT]},
            "scanEnabled": True,
            "scanRegionMode": "selected",
            "selectedRegions": ["eu-west-2"],
        },
    )
    assert proposed_selected.json()["regions"] == 0


def test_batch_selector_scan_enabled_false_and_list_filter(
    client: TestClient,
) -> None:
    assert (
        client.patch(f"{API}/accounts/{MAIN_ACCOUNT}", json={"scanEnabled": False}).status_code
        == 200
    )
    changed = client.post(
        f"{API}/accounts/batch-policy",
        json={"selector": {"scanEnabled": False}, "scanRegionMode": "selected"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json() == {"updated": 1}

    stopped = client.get(f"{API}/accounts", params={"scanEnabled": "false"})
    assert stopped.status_code == 200, stopped.text
    assert [item["accountId"] for item in stopped.json()["items"]] == [MAIN_ACCOUNT]


def test_batch_selector_by_access_status_matches_the_list_filter(client: TestClient) -> None:
    changed = client.post(
        f"{API}/accounts/batch-policy",
        json={
            "selector": {"accessStatuses": ["credential_error"]},
            "scanEnabled": False,
        },
    )

    assert changed.status_code == 200, changed.text
    assert changed.json() == {"updated": 1}
    assert client.get(f"{API}/accounts/{IMAGES_ACCOUNT}").json()["scanEnabled"] is False
    assert client.get(f"{API}/accounts/{MAIN_ACCOUNT}").json()["scanEnabled"] is True


def test_import_only_inserts_unknown_accounts_and_never_overwrites_local_edits(
    make_client: Callable[..., TestClient], registry_server: RegistryServer, seeded: Seeded
) -> None:
    del seeded
    client = make_client(ACCOUNT_REGISTRY_URL=registry_server.url)
    registry_server.file = "accounts_changed.json"
    imported = client.post(f"{API}/accounts/import")
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"fetched": 3, "imported": 1, "skipped": 2}
    assert client.get(f"{API}/accounts/{MAIN_ACCOUNT}").json()["accountName"] == "payments-prod"
    new = client.get(f"{API}/accounts/{NEW_ACCOUNT['account_id']}").json()
    assert new["source"] == "imported"
    assert (new["scanEnabled"], new["scanRegionMode"]) == (True, "all")

    assert (
        client.patch(
            f"{API}/accounts/{NEW_ACCOUNT['account_id']}",
            json={"accountName": "locally-owned", "scanEnabled": False},
        ).status_code
        == 200
    )
    again = client.post(f"{API}/accounts/import")
    assert again.json() == {"fetched": 3, "imported": 0, "skipped": 3}
    local = client.get(f"{API}/accounts/{NEW_ACCOUNT['account_id']}").json()
    assert local["accountName"] == "locally-owned" and local["scanEnabled"] is False


def test_import_is_optional_and_scan_override_is_explicit(
    make_client: Callable[..., TestClient], seeded: Seeded
) -> None:
    del seeded
    client = make_client(ACCOUNT_REGISTRY_URL=f"http://{NOWHERE}/accounts")

    unavailable = client.post(f"{API}/accounts/import")
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "dependency_unavailable"
    assert client.get(f"{API}/accounts").status_code == 200
    assert client.post(f"{API}/scans", json={"all": True}).status_code == 201
    rejected = client.post(f"{API}/scans", json={"all": True, "overrideScanPolicy": True})
    assert rejected.status_code == 422

    assert (
        client.patch(f"{API}/accounts/{MAIN_ACCOUNT}", json={"scanEnabled": False}).status_code
        == 200
    )
    blocked = client.post(f"{API}/scans", json={"accountIds": [MAIN_ACCOUNT]})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "account_scan_disabled"
    allowed = client.post(
        f"{API}/scans",
        json={"accountIds": [MAIN_ACCOUNT], "overrideScanPolicy": True},
    )
    assert allowed.status_code == 201
    assert allowed.json()["scope"]["overrideScanPolicy"] is True


def test_post_accounts_validation(client: TestClient) -> None:
    bad_id = client.post(f"{API}/accounts", json={**NEW_ACCOUNT, "accountId": "123"})
    assert bad_id.status_code == 422
    bad_region = client.post(
        f"{API}/accounts",
        json={**NEW_ACCOUNT, "selectedRegions": ["not a region"]},
    )
    assert bad_region.status_code == 422
