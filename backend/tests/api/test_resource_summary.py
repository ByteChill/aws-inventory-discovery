"""`GET /resources/summary`: distinct counts under a filter, computed in SQL.

Facets cap their buckets at 200, so the overview must not derive organization-wide
application/account counts from them.
"""

from collections.abc import Callable
from typing import Any

from tests.support.seed import Seeded


def summary(get_json: Callable[..., Any], query: str = "") -> dict[str, Any]:
    suffix = f"?{query}" if query else ""
    return get_json(f"/api/v1/resources/summary{suffix}")


def expected(seeded: Seeded, **match: Any) -> dict[str, int]:
    """The counts of the seeded rows a filter leaves, statuses taken as the list takes them."""
    accounts = {account["account_id"]: account for account in seeded.accounts}
    statuses = [match["status"]] if "status" in match else ["active", "missing"]
    rows = [
        row
        for row in seeded.resources
        if row["status"] in statuses
        and all(row[key] == value for key, value in match.items() if key != "status")
    ]
    return {
        "resources": len(rows),
        "accounts": len({row["account_id"] for row in rows}),
        "applications": len({accounts[row["account_id"]]["eim_id"] for row in rows}),
        "resourceTypes": len({row["resource_type"] for row in rows}),
        "regions": len({row["aws_region"] for row in rows}),
    }


def test_counts_match_the_fixtures(get_json: Callable[..., Any], seeded: Seeded) -> None:
    assert summary(get_json) == expected(seeded)


def test_counts_follow_the_filter(get_json: Callable[..., Any], seeded: Seeded) -> None:
    counts = summary(get_json, "awsRegion=us-east-1")

    assert counts == expected(seeded, aws_region="us-east-1")
    assert (
        counts["resources"] == get_json("/api/v1/resources?pageSize=1&awsRegion=us-east-1")["total"]
    )


def test_deleted_resources_count_only_when_asked_for(
    get_json: Callable[..., Any], seeded: Seeded
) -> None:
    every = summary(get_json)
    deleted = summary(get_json, "status=deleted")

    assert deleted == expected(seeded, status="deleted")
    assert 0 < deleted["resources"] < every["resources"]


def test_a_type_column_filter_is_applied(get_json: Callable[..., Any], seeded: Seeded) -> None:
    query = "resourceType=AWS::EC2::VPC&attr.isDefault=false"

    counts = summary(get_json, query)

    assert counts["resources"] == get_json(f"/api/v1/resources?pageSize=1&{query}")["total"]
    assert counts["resourceTypes"] == 1
