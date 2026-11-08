"""`GET /resources`: filters, type columns, sorting and paging, against the seeded fixtures."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.types.columns import column_values
from app.types.registry import TypeRegistry
from tests.support.seed import Seeded

LIVE = ("active", "missing")


def account_of(seeded: Seeded, row: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in seeded.accounts if a["account_id"] == row["account_id"])


def attrs_of(registry: TypeRegistry, row: dict[str, Any]) -> dict[str, Any]:
    return column_values(registry.get(row["resource_type"]), row["configuration"])


def listing(get_json: Callable[..., Any], query: str) -> dict[str, Any]:
    return get_json(f"/api/v1/resources?pageSize=200&{query}")


def ids(body: dict[str, Any]) -> set[int]:
    return {item["id"] for item in body["items"]}


def sample_tag(seeded: Seeded) -> dict[str, str]:
    return next(tag for r in seeded.resources if r["tags"] for tag in r["tags"])


def tag_pairs(row: dict[str, Any]) -> set[tuple[str, str]]:
    return {(tag["key"], tag["value"]) for tag in row["tags"]}


def test_default_list_hides_deleted_and_matches_the_seeded_rows(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    body = listing(get_json, "")

    expected = [r for r in seeded.resources if r["status"] in LIVE]
    assert body["total"] == len(expected) == len(body["items"])
    assert ids(body) == {r["id"] for r in expected}
    assert {item["status"] for item in body["items"]} == {"active", "missing"}


def filter_cases(seeded: Seeded) -> list[Any]:
    tag = sample_tag(seeded)
    tag_key, tag_value = tag["key"], tag["value"]
    return [
        ("account", "accountId=111122223333", lambda r, a: r["account_id"] == "111122223333"),
        (
            "two accounts",
            "accountId=111122223333&accountId=444455556666",
            lambda r, a: r["account_id"] in ("111122223333", "444455556666"),
        ),
        ("eim", "eimId=10000001", lambda r, a: a["eim_id"] == "10000001"),
        ("ba", "baId=BA0002", lambda r, a: a["ba_id"] == "BA0002"),
        ("account type", "accountType=dev", lambda r, a: a["account_type"] == "dev"),
        ("region", "awsRegion=eu-west-1", lambda r, a: r["aws_region"] == "eu-west-1"),
        (
            "two types",
            "resourceType=AWS::EC2::VPC&resourceType=AWS::EC2::Subnet",
            lambda r, a: r["resource_type"] in ("AWS::EC2::VPC", "AWS::EC2::Subnet"),
        ),
        ("status missing", "status=missing", lambda r, a: r["status"] == "missing"),
        ("status deleted", "status=deleted", lambda r, a: r["status"] == "deleted"),
        (
            "all statuses",
            "status=active&status=missing&status=deleted",
            lambda r, a: True,
        ),
        (
            "q matches name, arn or id, case-insensitively",
            "q=VPC",
            lambda r, a: any(
                "vpc" in (r[f] or "").lower() for f in ("resource_name", "arn", "resource_id")
            ),
        ),
        (
            "q takes % and _ literally",
            "q=_",
            lambda r, a: any("_" in (r[f] or "") for f in ("resource_name", "arn", "resource_id")),
        ),
        (
            "tag key:value",
            f"tag={tag_key}:{tag_value}",
            lambda r, a: (tag_key, tag_value) in tag_pairs(r),
        ),
        ("tag key", f"tag={tag_key}", lambda r, a: any(k == tag_key for k, _ in tag_pairs(r))),
        (
            "tag missing",
            f"tagMissing={tag_key}",
            lambda r, a: not any(k == tag_key for k, _ in tag_pairs(r)),
        ),
        (
            "first seen from",
            "firstSeenAt.gte=2026-11-05T00:00:00Z",
            lambda r, a: r["first_seen_at"] >= datetime.fromisoformat("2026-11-05T00:00:00Z"),
        ),
        (
            "first seen to",
            "firstSeenAt.lte=2026-11-05T00:00:00Z",
            lambda r, a: r["first_seen_at"] <= datetime.fromisoformat("2026-11-05T00:00:00Z"),
        ),
    ]


def test_filters_return_the_rows_a_python_filter_returns(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    for name, query, predicate in filter_cases(seeded):
        explicit_status = "status=" in query
        body = listing(get_json, query)

        expected = {
            r["id"]
            for r in seeded.resources
            if predicate(r, account_of(seeded, r)) and (explicit_status or r["status"] in LIVE)
        }
        assert ids(body) == expected, name
        assert body["total"] == len(expected), name
        assert expected, f"{name}: the fixtures should have a match"


@pytest.mark.parametrize(
    ("param", "field", "status"),
    [("missingSince.gte", "missing_since", "missing"), ("deletedAt.gte", "deleted_at", "deleted")],
)
def test_state_time_ranges_select_the_scan_page_counts(
    get_json: Callable[..., Any], seeded: Seeded, param: str, field: str, status: str
) -> None:
    row = next(r for r in seeded.resources if r["status"] == status)
    just_before = row[field].isoformat().replace("+00:00", "Z")

    hit = listing(get_json, f"status={status}&{param}={just_before}")
    after = listing(get_json, f"status={status}&{param}=2999-01-01T00:00:00Z")

    assert ids(hit) == {row["id"]}
    assert after["total"] == 0


def test_stale_means_the_scope_was_not_scanned_for_twelve_hours(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    body = listing(get_json, "")

    stale = {item["id"] for item in body["items"] if item["stale"]}
    vpc = seeded.one(account_id="111122223333", resource_type="AWS::EC2::VPC")
    unscanned = {  # no scope of these accounts has ever completed a scan
        r["id"]
        for r in seeded.resources
        if r["account_id"] != "111122223333" and r["status"] in LIVE
    }
    assert stale == {vpc["id"]} | unscanned


def test_list_item_shape(get_json: Callable[..., Any], seeded: Seeded) -> None:
    row = seeded.one(account_id="111122223333", resource_type="AWS::EC2::Instance")

    item = next(i for i in listing(get_json, "").get("items") if i["id"] == row["id"])

    assert item["arn"] == row["arn"]
    assert item["resourceType"] == "AWS::EC2::Instance"
    assert item["account"] == {
        "accountId": "111122223333",
        "accountName": "payments-prod",
        "accountType": "prod",
        "eimId": "10000001",
        "eimApp": "payments",
        "baId": "BA0001",
    }
    assert item["tags"] == row["tags"]
    assert item["relationshipCount"] == len(row["relationships"])
    assert item["attrs"] == {}
    assert "configuration" not in item and "live" not in item
    assert item["configurationItemCaptureTime"].startswith("2026-11-06T09:15:22")


def test_attrs_follow_the_type_spec_for_every_type(
    get_json: Callable[..., Any], seeded: Seeded, registry: TypeRegistry
) -> None:
    for spec in registry:
        rows = [r for r in seeded.where(resource_type=spec.type_name) if r["status"] in LIVE]
        body = listing(get_json, f"resourceType={spec.type_name}")

        assert body["total"] == len(rows), spec.type_name
        for item in body["items"]:
            row = next(r for r in rows if r["id"] == item["id"])
            assert item["attrs"] == attrs_of(registry, row), spec.type_name


def attr_cases(seeded: Seeded, registry: TypeRegistry) -> list[Any]:
    lambdas = [attrs_of(registry, r) for r in seeded.where(resource_type="AWS::Lambda::Function")]
    queues = [attrs_of(registry, r) for r in seeded.where(resource_type="AWS::SQS::Queue")]
    timeouts = sorted(a["visibilityTimeout"] for a in queues if a["visibilityTimeout"] is not None)
    return [
        (  # enum
            "AWS::Lambda::Function",
            f"attr.runtime={lambdas[0]['runtime']}",
            lambda a: a["runtime"] == lambdas[0]["runtime"],
        ),
        (  # number range, both ends
            "AWS::SQS::Queue",
            f"attr.visibilityTimeout.gte={timeouts[0]}&attr.visibilityTimeout.lte={timeouts[0]}",
            lambda a: a["visibilityTimeout"] == timeouts[0],
        ),
        (  # number range, open end
            "AWS::ECS::Service",
            "attr.desiredCount.gte=3",
            lambda a: a["desiredCount"] is not None and a["desiredCount"] >= 3,
        ),
        (  # bool
            "AWS::EC2::VPC",
            "attr.isDefault=false",
            lambda a: a["isDefault"] is False,
        ),
        (  # datetime
            "AWS::EC2::Image",
            "attr.creationDate.gte=2019-01-01T00:00:00Z&attr.creationDate.lte=2020-01-01T00:00:00Z",
            lambda a: a["creationDate"].startswith("2019-"),
        ),
        (  # count column
            "AWS::ECS::Cluster",
            "attr.capacityProviderCount.lte=0",
            lambda a: a["capacityProviderCount"] == 0,
        ),
        (  # enum, several values mean "or"
            "AWS::EC2::Instance",
            "attr.state=running&attr.state=stopped",
            lambda a: a["state"] in ("running", "stopped"),
        ),
    ]


def test_attr_filters_match_the_type_columns(
    get_json: Callable[..., Any], seeded: Seeded, registry: TypeRegistry
) -> None:
    for type_name, query, predicate in attr_cases(seeded, registry):
        body = listing(get_json, f"resourceType={type_name}&{query}")

        expected = {
            r["id"]
            for r in seeded.where(resource_type=type_name)
            if r["status"] in LIVE and predicate(attrs_of(registry, r))
        }
        assert ids(body) == expected, query
        assert body["total"] == len(expected), query
        assert expected, f"{query}: the fixtures should have a match"


@pytest.mark.parametrize(
    "query",
    [
        "attr.runtime=python3.12",  # no resource type
        "resourceType=AWS::Lambda::Function&resourceType=AWS::SQS::Queue&attr.runtime=x",
        "resourceType=AWS::Lambda::Function&attr.nope=x",
        "resourceType=AWS::Lambda::Function&attr.memorySize.gte=lots",
        "resourceType=AWS::Lambda::Function&attr.runtime.gte=python3",
        "resourceType=AWS::EC2::Subnet&attr.mapPublicIpOnLaunch=maybe",
        "resourceType=AWS::Lambda::Function&nonsense=1",
    ],
)
def test_invalid_filters_are_a_400(client: TestClient, query: str) -> None:
    response = client.get(f"/api/v1/resources?{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_filter"
    assert response.json()["message"]


def test_sorting_by_common_and_type_columns(get_json: Callable[..., Any]) -> None:
    newest = listing(get_json, "sort=-firstSeenAt,resourceId")["items"]
    by_count = listing(get_json, "resourceType=AWS::ECS::Cluster&sort=-capacityProviderCount")
    by_flag = listing(get_json, "resourceType=AWS::EC2::VPC&sort=-isDefault")["items"]
    by_prefixed = listing(
        get_json, "resourceType=AWS::ECS::Cluster&sort=attr.capacityProviderCount"
    )

    times = [i["firstSeenAt"] for i in newest]
    assert times == sorted(times, reverse=True)
    counts = [i["attrs"]["capacityProviderCount"] for i in by_count["items"]]
    assert counts == sorted(counts, reverse=True)
    assert [i["attrs"]["capacityProviderCount"] for i in by_prefixed["items"]] == sorted(counts)
    flags = [i["attrs"]["isDefault"] for i in by_flag]
    assert flags[0] is False and flags[1:] == [None] * (len(flags) - 1)  # NULLs sort last


def test_default_sort_is_last_changed_descending(get_json: Callable[..., Any]) -> None:
    items = listing(get_json, "")["items"]

    changed = [i["lastChangedAt"] for i in items]
    assert changed == sorted(changed, reverse=True)


@pytest.mark.parametrize(
    "query",
    [
        "sort=configuration",
        "sort=tags",
        "sort=visibilityTimeout",  # a type column, but no single type is given
        "resourceType=AWS::SQS::Queue&sort=-nope",
    ],
)
def test_sort_outside_the_whitelist_is_a_400(client: TestClient, query: str) -> None:
    response = client.get(f"/api/v1/resources?{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_sort"


def test_pagination_pages_are_disjoint_and_total_is_stable(
    get_json: Callable[..., Any],
) -> None:
    first = get_json("/api/v1/resources?pageSize=5&page=1")
    second = get_json("/api/v1/resources?pageSize=5&page=2")
    beyond = get_json("/api/v1/resources?pageSize=5&page=99")

    assert first["page"] == 1 and second["page"] == 2 and first["pageSize"] == 5
    assert len(first["items"]) == 5
    assert not ids(first) & ids(second)
    assert first["total"] == second["total"] == beyond["total"]
    assert beyond["items"] == []


def test_page_size_above_200_is_a_validation_error(client: TestClient) -> None:
    response = client.get("/api/v1/resources?pageSize=201")

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
