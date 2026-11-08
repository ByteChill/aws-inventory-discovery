"""Detail, lookup, relationships and the unified error format."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.support.seed import DEV_ACCOUNT, IMAGES_ACCOUNT, MAIN_ACCOUNT, Seeded


def test_detail_has_configuration_relationships_and_no_live_for_config_data(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    row = seeded.one(account_id=MAIN_ACCOUNT, resource_type="AWS::EC2::Instance")

    body = get_json(f"/api/v1/resources/{row['id']}")

    assert body["configuration"] == row["configuration"]
    assert [r["resourceId"] for r in body["relationships"]] == [
        r["resourceId"] for r in row["relationships"]
    ]
    assert body["live"] is None
    assert body["observedSource"] == "config"
    assert body["attrs"]["instanceType"] == row["configuration"]["instanceType"]
    assert body["account"]["accountName"] == "payments-prod"


def test_detail_of_cloud_control_data_includes_the_live_properties(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    row = next(r for r in seeded.where(account_id=DEV_ACCOUNT) if r["live_raw"])

    body = get_json(f"/api/v1/resources/{row['id']}")

    assert body["live"]["properties"] == row["live_raw"]
    assert body["live"]["observedAt"].startswith("2026-11-07T10:00:00")
    assert body["observedSource"] == "cloudcontrol"


def test_lookup_returns_every_account_with_the_owner_of_a_shared_ami_first(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    arn = seeded.one(account_id=IMAGES_ACCOUNT, resource_type="AWS::EC2::Image")["arn"]

    body = get_json("/api/v1/resources/lookup", params={"arn": arn})

    assert [b["accountId"] for b in body] == [IMAGES_ACCOUNT, MAIN_ACCOUNT]
    assert [b["configuration"]["ownership"] for b in body] == ["self", "shared"]


def test_relationships_out_in_and_both(get_json: Callable[..., Any], seeded: Seeded) -> None:
    instance = seeded.one(account_id=MAIN_ACCOUNT, resource_type="AWS::EC2::Instance")
    subnet = seeded.one(account_id=MAIN_ACCOUNT, resource_type="AWS::EC2::Subnet")
    out_edges = [r for r in seeded.relations if r["from_resource_id"] == instance["id"]]
    base = f"/api/v1/resources/{instance['id']}/relationships"

    out = get_json(f"{base}?direction=out")
    both = get_json(base)
    into_subnet = get_json(f"/api/v1/resources/{subnet['id']}/relationships?direction=in")
    paged = get_json(f"{base}?direction=out&pageSize=2&page=2")

    assert out["total"] == len(out_edges)
    assert {e["direction"] for e in out["items"]} == {"out"}
    resolved = {e["resourceKey"]: e["resource"] for e in out["items"]}
    assert resolved[subnet["resource_id"]]["id"] == subnet["id"]
    assert None in resolved.values()  # targets the inventory does not hold
    in_edges = [r for r in seeded.relations if r["to_resource_id"] == instance["id"]]
    assert both["total"] == len(out_edges) + len(in_edges)
    assert instance["id"] in {e["resource"]["id"] for e in into_subnet["items"]}
    assert {e["direction"] for e in into_subnet["items"]} == {"in"}
    assert paged["total"] == out["total"] and len(paged["items"]) == len(out_edges) - 2


def test_errors_use_the_unified_body(client: TestClient) -> None:
    missing = client.get("/api/v1/resources/999999")
    bad_filter = client.get("/api/v1/resources?attr.x=1")
    invalid = client.get("/api/v1/resources/abc")
    route = client.get("/api/v1/nope")

    assert missing.status_code == 404
    assert missing.json()["code"] == "resource_not_found"
    assert bad_filter.status_code == 400
    assert set(bad_filter.json()) == {"code", "message"}
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"
    assert invalid.json()["details"]["errors"]
    assert route.status_code == 404 and route.json()["code"] == "not_found"
    assert client.get("/api/v1/resources/lookup?arn=arn:nothing").json()["code"] == (
        "resource_not_found"
    )
