"""`GET /applications`: active accounts grouped by EIM, against the seeded fixtures."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.support.seed import Seeded


def applications(get_json: Callable[..., Any], query: str = "") -> dict[str, Any]:
    return get_json(f"/api/v1/applications?{query}")


def by_eim(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["eimId"]: item for item in body["items"]}


def resource_count(seeded: Seeded, eim_id: str) -> int:
    """Non-deleted resources of the EIM's active accounts."""
    accounts = {a["account_id"] for a in seeded.accounts if a["active"] and a["eim_id"] == eim_id}
    return len(
        [r for r in seeded.resources if r["account_id"] in accounts and r["status"] != "deleted"]
    )


def test_applications_are_the_active_accounts_grouped_by_eim(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    body = applications(get_json)

    # payments-dev shares the payments EIM but is inactive, so the dev account counts nowhere.
    assert body["total"] == 2
    found = by_eim(body)
    assert set(found) == {"10000001", "10000002"}
    payments = found["10000001"]
    assert (payments["eimApp"], payments["baId"]) == ("payments", "BA2010001")
    assert payments["accountCount"] == 1
    assert payments["environments"] == ["prod"]
    assert payments["resourceCount"] == resource_count(seeded, "10000001")
    golden = found["10000002"]
    assert (golden["eimApp"], golden["baId"]) == ("golden-images", "BA2010002")
    assert golden["resourceCount"] == resource_count(seeded, "10000002")
    assert golden["resourceCount"] == len(
        [r for r in seeded.resources if r["account_id"] == "444455556666"]
    )
    # Default sort: application name.
    assert [item["eimApp"] for item in body["items"]] == ["golden-images", "payments"]


def test_application_search_covers_the_name_the_eim_and_the_ba(
    get_json: Callable[..., Any],
) -> None:
    for query, eim in (
        ("q=golden", "10000002"),
        ("q=payments", "10000001"),
        ("q=10000001", "10000001"),
        ("q=BA2010001", "10000001"),
        ("q=BA2010002", "10000002"),
    ):
        body = applications(get_json, query)
        assert [item["eimId"] for item in body["items"]] == [eim], query
    assert applications(get_json, "q=nothing-like-this")["total"] == 0


def test_application_filters_sorting_and_paging(get_json: Callable[..., Any]) -> None:
    assert applications(get_json, "accountType=dev")["total"] == 0  # only inactive dev accounts
    assert applications(get_json, "accountType=prod")["total"] == 2
    assert applications(get_json, "accountType=dev&accountType=preprod")["total"] == 0

    ascending = applications(get_json, "sort=resourceCount")["items"]
    descending = applications(get_json, "sort=-resourceCount")["items"]
    counts = [item["resourceCount"] for item in ascending]
    assert counts == sorted(counts)
    assert [item["resourceCount"] for item in descending] == list(reversed(counts))

    page = applications(get_json, "pageSize=1")
    assert page["total"] == 2 and len(page["items"]) == 1


def test_application_errors(get_json: Callable[..., Any], client: TestClient) -> None:
    del get_json
    assert client.get("/api/v1/applications?bogus=1").status_code == 422
    bad_sort = client.get("/api/v1/applications?sort=bogus")
    assert bad_sort.status_code == 400
    assert bad_sort.json()["code"] == "invalid_sort"
