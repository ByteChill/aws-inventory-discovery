"""Resource types, columns, overview and accounts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient

from app.types.registry import TypeRegistry
from tests.api.conftest import TEST_REDIS_URL
from tests.support.seed import DEV_ACCOUNT, IMAGES_ACCOUNT, MAIN_ACCOUNT, Seeded

EN = {"Accept-Language": "en-US,en;q=0.9"}


def test_resource_types_are_localized(get_json: Callable[..., Any], registry: TypeRegistry) -> None:
    zh = get_json("/api/v1/resource-types")
    en = get_json("/api/v1/resource-types", headers=EN)

    assert [t["typeName"] for t in zh] == [s.type_name for s in registry]
    assert {t["typeName"]: t["label"] for t in zh}["AWS::EC2::Instance"] == "EC2 实例"
    assert {t["typeName"]: t["label"] for t in en}["AWS::EC2::Instance"] == "EC2 instance"
    assert [t["typeName"] for t in zh if t["global"]] == ["AWS::Route53::HostedZone"]


def test_columns_are_common_only_without_a_type_and_add_the_type_columns_with_one(
    get_json: Callable[..., Any], registry: TypeRegistry
) -> None:
    common = get_json("/api/v1/columns")
    zh = get_json("/api/v1/columns?resourceType=AWS::Lambda::Function")
    en = get_json("/api/v1/columns?resourceType=AWS::Lambda::Function", headers=EN)
    spec = registry.get("AWS::Lambda::Function")

    assert {c["scope"] for c in common} == {"common"}
    assert zh[: len(common)] == common
    assert [c["key"] for c in zh[len(common) :]] == [c.key for c in spec.columns]
    assert {c["scope"] for c in zh[len(common) :]} == {"type"}
    assert [c["label"] for c in zh[len(common) :]] == [c.label.zh_cn for c in spec.columns]
    assert [c["label"] for c in en[len(common) :]] == [c.label.en for c in spec.columns]
    by_key = {c["key"]: c for c in common}
    assert by_key["resourceName"]["label"] == "名称"
    assert by_key["tags"]["sortable"] is False
    assert by_key["arn"]["mono"] is True


def test_overview_counts_live_resources_and_reachable_accounts(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    live = [r for r in seeded.resources if r["status"] != "deleted"]

    body = get_json("/api/v1/stats/overview")

    assert body["resourceTotal"] == len(live)
    assert sum(t["count"] for t in body["byResourceType"]) == len(live)
    assert sum(t["count"] for t in body["byAccountType"]) == len(live)
    assert body["accountTotal"] == 2  # the dev account is inactive
    assert body["accountUnreachable"] == 1  # credential_error
    prod = next(t for t in body["byAccountType"] if t["accountType"] == "prod")
    assert prod["count"] == len([r for r in live if r["account_id"] != DEV_ACCOUNT])
    assert prod["label"] == "prod"
    assert datetime.fromisoformat(body["generatedAt"])


def test_account_list_defaults_to_active_and_filters_sorts_and_pages(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    active = get_json("/api/v1/accounts")
    inactive = get_json("/api/v1/accounts?active=false")
    by_count = get_json("/api/v1/accounts?sort=-resourceCount")
    searched = get_json("/api/v1/accounts?q=SHARED")
    prod = get_json("/api/v1/accounts?accountType=prod&eimId=10000001")

    assert {a["accountId"] for a in active["items"]} == {MAIN_ACCOUNT, IMAGES_ACCOUNT}
    assert active["total"] == 2
    assert [a["accountId"] for a in inactive["items"]] == [DEV_ACCOUNT]
    counts = [a["resourceCount"] for a in by_count["items"]]
    assert counts == sorted(counts, reverse=True)
    assert by_count["items"][0]["resourceCount"] == len(
        [r for r in seeded.where(account_id=MAIN_ACCOUNT) if r["status"] != "deleted"]
    )
    assert [a["accountId"] for a in searched["items"]] == [IMAGES_ACCOUNT]
    assert [a["accountId"] for a in prod["items"]] == [MAIN_ACCOUNT]
    main = next(a for a in active["items"] if a["accountId"] == MAIN_ACCOUNT)
    assert main["purgeAt"] is None and main["lastScanOkAt"] is not None
    purge_at = datetime.fromisoformat(inactive["items"][0]["purgeAt"])
    assert timedelta(days=24) < purge_at - datetime.now(UTC) < timedelta(days=26)  # 30 - 5


def test_account_detail_lists_regions(get_json: Callable[..., Any]) -> None:
    body = get_json(f"/api/v1/accounts/{MAIN_ACCOUNT}")

    assert body["accountName"] == "payments-prod"
    assert {r["region"]: (r["reachable"], r["configRecording"]) for r in body["regions"]} == {
        "ap-southeast-1": (False, None),
        "us-east-1": (True, True),
    }


def test_coverage_states(get_json: Callable[..., Any]) -> None:
    cells = get_json(f"/api/v1/accounts/{MAIN_ACCOUNT}/coverage")

    state = {(c["region"], c["resourceType"]): c["state"] for c in cells}
    assert state[("us-east-1", "AWS::EC2::VPC")] == "stale"
    assert state[("us-east-1", "AWS::EC2::Subnet")] == "ok"
    assert state[("eu-west-1", "AWS::EC2::VPC")] == "failed"
    assert state[("eu-west-1", "AWS::SQS::Queue")] == "access_denied"
    assert state[("eu-west-1", "AWS::SNS::Topic")] == "never"
    failed = next(c for c in cells if c["state"] == "failed")
    assert failed["lastErrorCode"] == "Throttling" and failed["lastSucceededAt"] is None
    assert get_json(f"/api/v1/accounts/{DEV_ACCOUNT}/coverage") == []


def test_unknown_account_is_a_404(client: TestClient) -> None:
    for path in ("", "/coverage"):
        response = client.get(f"/api/v1/accounts/000000000000{path}")

        assert response.status_code == 404
        assert response.json()["code"] == "account_not_found"


@pytest.mark.usefixtures("seeded")
def test_the_accounts_list_is_cached(
    make_client: Callable[..., TestClient], redis_client: redis.Redis, schema_name: str
) -> None:
    client = make_client(REDIS_URL=TEST_REDIS_URL)

    first = client.get("/api/v1/accounts").json()
    second = client.get("/api/v1/accounts").json()
    keys = list(redis_client.scan_iter(f"aws-inventory-discovery:{schema_name}:accounts:list:*"))

    assert first == second
    assert len(keys) == 1
    assert 0 < redis_client.ttl(keys[0]) <= 60
