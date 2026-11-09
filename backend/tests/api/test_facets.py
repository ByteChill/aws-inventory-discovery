"""`GET /resources/facets`, including the Redis cache."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient

from tests.api.conftest import TEST_REDIS_URL

COMMON_FIELDS = ["accountId", "eimId", "baId", "accountType", "awsRegion", "resourceType", "status"]
FILTER_PARAM = {field: field for field in COMMON_FIELDS}


def facets(get_json: Callable[..., Any], query: str, **kwargs: Any) -> dict[str, Any]:
    return get_json(f"/api/v1/resources/facets?{query}", **kwargs)


def label(body: dict[str, Any], field: str, value: str) -> str:
    return next(b["label"] for b in body[field] if b["value"] == value)


def total(get_json: Callable[..., Any], query: str) -> int:
    return get_json(f"/api/v1/resources?pageSize=1&{query}")["total"]


def test_every_bucket_count_equals_the_list_total_with_that_value_selected(
    get_json: Callable[..., Any],
) -> None:
    base = "awsRegion=us-east-1&awsRegion=eu-west-1&eimId=10000001"
    fields = "&".join(f"field={f}" for f in COMMON_FIELDS)

    body = facets(get_json, f"{fields}&{base}")

    assert set(body) == set(COMMON_FIELDS)
    for field, buckets in body.items():
        assert buckets, field
        assert [b["count"] for b in buckets] == sorted((b["count"] for b in buckets), reverse=True)
        for bucket in buckets:
            # Take the field's own filter out of `base`, then select just this bucket.
            others = "&".join(part for part in base.split("&") if not part.startswith(f"{field}="))
            selected = total(get_json, f"{others}&{field}={bucket['value']}")
            assert bucket["count"] == selected, (field, bucket)


def test_a_field_ignores_its_own_filter_but_not_the_others(
    get_json: Callable[..., Any],
) -> None:
    body = facets(
        get_json,
        "field=accountType&field=awsRegion&accountType=prod&awsRegion=eu-west-1",
    )

    # Only dev has resources in eu-west-1; only prod is selected: each sees the other's filter.
    assert {b["value"] for b in body["accountType"]} == {"dev"}
    # (prod's Route 53 zones are global, filed under the region `global`)
    assert {b["value"] for b in body["awsRegion"]} == {"us-east-1", "global"}


def test_status_facet_counts_every_status_unless_narrowed_by_others(
    get_json: Callable[..., Any],
) -> None:
    buckets = facets(get_json, "field=status")["status"]

    assert {b["value"]: b["count"] for b in buckets} == {
        status: total(get_json, f"status={status}") for status in ("active", "missing", "deleted")
    }


def test_labels_follow_accept_language(get_json: Callable[..., Any]) -> None:
    query = "field=resourceType&field=status&field=accountType&field=accountId&field=awsRegion"

    zh = facets(get_json, query)
    en = facets(get_json, query, headers={"Accept-Language": "en-US,en;q=0.9"})

    assert label(zh, "resourceType", "AWS::EC2::VPC") == "VPC"
    assert label(zh, "resourceType", "AWS::EC2::Instance") == "EC2 实例"
    assert label(en, "resourceType", "AWS::EC2::Instance") == "EC2 instance"
    assert label(zh, "status", "missing") != label(en, "status", "missing")
    assert label(zh, "accountType", "prod") == "生产"
    assert label(en, "accountType", "prod") == "Prod"
    assert label(zh, "accountId", "111122223333") == "payments-prod"
    assert label(zh, "awsRegion", "us-east-1") == "us-east-1"


def test_type_columns_can_be_faceted_when_enum_or_bool(get_json: Callable[..., Any]) -> None:
    both = facets(get_json, "field=attr.state&field=attr.isDefault&resourceType=AWS::EC2::VPC")
    narrowed = facets(
        get_json, "field=attr.state&field=attr.isDefault&resourceType=AWS::EC2::VPC&attr.state=nope"
    )

    assert [b["value"] for b in both["attr.state"]] == ["available"]
    assert {b["value"]: b["label"] for b in both["attr.isDefault"]} == {"false": "否"}
    assert [b["value"] for b in narrowed["attr.state"]] == [
        "available"
    ]  # its own filter is ignored
    assert narrowed["attr.isDefault"] == []  # the other fields' filters still apply


@pytest.mark.parametrize(
    "query",
    [
        "field=attr.state",  # no single type
        "field=attr.cidrBlock&resourceType=AWS::EC2::VPC",  # text column
        "field=attr.nope&resourceType=AWS::EC2::VPC",
        "field=eimApp",
    ],
)
def test_facet_field_not_allowed_is_a_400(client: TestClient, query: str) -> None:
    response = client.get(f"/api/v1/resources/facets?{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_filter"


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(TEST_REDIS_URL)
    for key in client.scan_iter("aws-inventory-discovery:*"):
        client.delete(key)
    yield client
    client.close()


@pytest.mark.usefixtures("seeded")
def test_facets_and_overview_are_cached_in_redis_for_60_seconds(
    make_client: Callable[..., TestClient], redis_client: redis.Redis
) -> None:
    client = make_client(REDIS_URL=TEST_REDIS_URL)
    url = "/api/v1/resources/facets?field=awsRegion"

    first = client.get(url).json()
    keys = [k.decode() for k in redis_client.scan_iter("aws-inventory-discovery:facets:*")]
    second = client.get(url).json()

    assert len(keys) == 1
    assert 0 < redis_client.ttl(keys[0]) <= 60
    assert first == second
    stamp = client.get("/api/v1/stats/overview").json()["generatedAt"]
    time.sleep(0.01)
    assert client.get("/api/v1/stats/overview").json()["generatedAt"] == stamp
    assert redis_client.ttl("aws-inventory-discovery:stats:overview:zh-CN") > 0
