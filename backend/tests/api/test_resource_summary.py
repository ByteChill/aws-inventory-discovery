"""`GET /resources/summary`: distinct counts under a filter, computed in SQL.

Facets cap their buckets at 200, so the overview must not derive organization-wide
application/account counts from them.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert

from app.query.tables import ACCOUNT, RESOURCE
from tests.api.conftest import TEST_REDIS_URL
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
        "applications": len(
            {
                accounts[row["account_id"]]["eim_id"]
                for row in rows
                if accounts[row["account_id"]]["eim_id"] is not None
            }
        ),
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


@pytest.mark.usefixtures("seeded")
def test_the_list_total_and_the_summary_share_one_cached_count(
    make_client: Callable[..., TestClient],
    redis_client: redis.Redis,
    schema_name: str,
) -> None:
    client = make_client(REDIS_URL=TEST_REDIS_URL)
    query = "awsRegion=us-east-1"

    counts = client.get(f"/api/v1/resources/summary?{query}").json()
    # Paging and sort do not change the count, so this reuses the entry the summary wrote.
    page = client.get(f"/api/v1/resources?pageSize=2&page=2&sort=resourceName&{query}").json()
    keys = list(redis_client.scan_iter(f"aws-inventory-discovery:{schema_name}:resources:count:*"))

    assert len(keys) == 1
    assert counts["resources"] > 0
    assert counts["resources"] == page["total"]


@pytest.mark.usefixtures("seeded")
def test_an_empty_attribute_value_is_a_filter_not_an_absent_parameter(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client(REDIS_URL=TEST_REDIS_URL)

    every = client.get("/api/v1/resources?resourceType=AWS::EC2::Instance").json()
    # `attr.state=` filters on the empty value (`state.name IN ('')`); it must not reuse the
    # cached count of the unfiltered request.
    empty = client.get("/api/v1/resources?resourceType=AWS::EC2::Instance&attr.state=").json()

    assert every["total"] > 0
    assert empty["total"] == 0


BULK_REGION = "ap-south-1"
BULK_ACCOUNTS = 202


@pytest.fixture
def bulk_rows(db_engine: Engine, seeded: Seeded) -> Iterator[None]:
    """202 accounts (one without an EIM) with one resource each, in a region the fixtures do not
    use. Only the account, region and resource IDs differ from the fixture rows, the same way the
    demo seed copies them. They are removed afterwards so the other tests' counts do not change.
    """
    template_account = seeded.accounts[0]
    template = next(
        row
        for row in seeded.resources
        if row["resource_type"] == "AWS::SQS::Queue" and row["status"] == "active"
    )
    ids = [f"9{index:011d}" for index in range(BULK_ACCOUNTS)]
    accounts = [
        {
            **template_account,
            "account_id": account_id,
            "account_name": f"bulk-account-{index:03d}",
            "eim_id": None if index == 0 else f"8{index:07d}",
            "eim_app": None if index == 0 else f"bulk-app-{index:03d}",
        }
        for index, account_id in enumerate(ids)
    ]
    resources = [
        {
            **{key: value for key, value in template.items() if key != "id"},
            "account_id": account_id,
            "aws_region": BULK_REGION,
            "resource_id": f"https://sqs.{BULK_REGION}.amazonaws.com/{account_id}/bulk-{index:03d}",
            "arn": f"arn:aws:sqs:{BULK_REGION}:{account_id}:bulk-{index:03d}",
        }
        for index, account_id in enumerate(ids)
    ]
    try:
        with db_engine.begin() as connection:
            connection.execute(insert(ACCOUNT), accounts)
            connection.execute(insert(RESOURCE), resources)
        yield
    finally:
        with db_engine.begin() as connection:
            connection.execute(delete(RESOURCE).where(RESOURCE.c.account_id.in_(ids)))
            connection.execute(delete(ACCOUNT).where(ACCOUNT.c.account_id.in_(ids)))


def test_more_than_200_accounts_and_applications_are_counted(
    get_json: Callable[..., Any], bulk_rows: None
) -> None:
    del bulk_rows  # only needed for its rows
    counts = summary(get_json, f"awsRegion={BULK_REGION}")
    facet = get_json(f"/api/v1/resources/facets?field=accountId&awsRegion={BULK_REGION}")

    assert counts == {
        "resources": 202,
        "accounts": 202,
        "applications": 201,
        "resourceTypes": 1,
        "regions": 1,
    }
    # The same filter through facets stops at the 200-bucket cap, which is why the
    # overview may not use bucket counts.
    assert len(facet["accountId"]) == 200
