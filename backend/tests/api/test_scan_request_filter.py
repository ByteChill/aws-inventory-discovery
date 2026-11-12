"""`scanRequestId` on the resource list and facets: the resolved scope of a scan (DESIGN 8.2).

The request rows are inserted directly (the API's creation path needs the registry and AWS);
its tasks go through the worker's own `add_tasks`, like the handler's children do.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, text
from sqlmodel import Session

from app.core.db import make_engine
from app.query.tables import SCAN_REQUEST
from app.worker import requests
from tests.conftest import TEST_DATABASE_URL
from tests.support.seed import DEV_ACCOUNT, MAIN_ACCOUNT, Seeded

API = "/api/v1"
LIVE = ("active", "missing")
Task = tuple[str, str, str | None, str | None, str | None]  # kind, account, region, type, id


@pytest.fixture
def db_engine(schema_name: str) -> Iterator[Engine]:
    engine = make_engine(TEST_DATABASE_URL, schema_name)
    yield engine
    with engine.begin() as connection:  # leave the shared seed as it was
        connection.execute(text("TRUNCATE t_inv_aws_scan_task, t_inv_aws_scan_request CASCADE"))
    engine.dispose()


def create_request(engine: Engine, scope: dict[str, Any], tasks: list[Task]) -> int:
    with engine.begin() as connection:
        request_id = connection.execute(
            insert(SCAN_REQUEST)
            .values(trigger="api", scope=scope, priority=0, status="queued")
            .returning(SCAN_REQUEST.c.id)
        ).scalar_one()
    with Session(engine) as session:  # add_tasks commits its own transaction
        requests.add_tasks(session, request_id, [requests.NewTask(*task) for task in tasks])
    return request_id


def listing(get_json: Callable[..., Any], scan_request_id: int) -> dict[str, Any]:
    return get_json(f"{API}/resources?pageSize=200&scanRequestId={scan_request_id}")


def ids(body: dict[str, Any]) -> set[int]:
    return {item["id"] for item in body["items"]}


def expected(seeded: Seeded, **match: Any) -> set[int]:
    return {row["id"] for row in seeded.where(**match) if row["status"] in LIVE}


def test_shard_task_matches_its_exact_scope(
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine
) -> None:
    scan = create_request(
        db_engine,
        {
            "accountIds": [MAIN_ACCOUNT],
            "awsRegions": ["us-east-1"],
            "resourceTypes": ["AWS::EC2::VPC"],
            "all": False,
        },
        [("shard", MAIN_ACCOUNT, "us-east-1", "AWS::EC2::VPC", None)],
    )

    body = listing(get_json, scan)

    wanted = expected(
        seeded, account_id=MAIN_ACCOUNT, aws_region="us-east-1", resource_type="AWS::EC2::VPC"
    )
    assert wanted and ids(body) == wanted
    buckets = get_json(f"{API}/resources/facets?field=resourceType&scanRequestId={scan}")
    assert [(b["value"], b["count"]) for b in buckets["resourceType"]] == [
        ("AWS::EC2::VPC", len(wanted))
    ]


def test_confirm_missing_covers_its_scope_like_a_shard(
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine
) -> None:
    scan = create_request(
        db_engine,
        {
            "accountIds": [MAIN_ACCOUNT],
            "awsRegions": ["us-east-1"],
            "resourceTypes": ["AWS::EC2::VPC"],
            "all": False,
        },
        [("confirm_missing", MAIN_ACCOUNT, "us-east-1", "AWS::EC2::VPC", None)],
    )

    assert ids(listing(get_json, scan)) == expected(
        seeded, account_id=MAIN_ACCOUNT, aws_region="us-east-1", resource_type="AWS::EC2::VPC"
    )


def test_config_group_covers_every_config_type_unless_the_request_restricts_them(
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine
) -> None:
    everything = create_request(
        db_engine,
        {"accountIds": [MAIN_ACCOUNT], "all": False},
        [("config_group", MAIN_ACCOUNT, "us-east-1", None, None)],
    )
    only_vpcs = create_request(
        db_engine,
        {"accountIds": [MAIN_ACCOUNT], "resourceTypes": ["AWS::EC2::VPC"], "all": False},
        [("config_group", MAIN_ACCOUNT, "us-east-1", None, None)],
    )

    assert ids(listing(get_json, everything)) == expected(
        seeded, account_id=MAIN_ACCOUNT, aws_region="us-east-1"
    ) | expected(seeded, account_id=MAIN_ACCOUNT, aws_region="global")
    only_vpc = listing(get_json, only_vpcs)
    assert ids(only_vpc) == expected(
        seeded, account_id=MAIN_ACCOUNT, aws_region="us-east-1", resource_type="AWS::EC2::VPC"
    )
    # The two differ: the config group of the unrestricted request also covers other types.
    assert ids(only_vpc) < ids(listing(get_json, everything))


def test_global_resources_are_covered_by_the_config_group_that_reads_them(
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine
) -> None:
    global_rows = [
        r
        for r in seeded.resources
        if r["account_id"] == DEV_ACCOUNT and r["aws_region"] == "global"
    ]
    assert global_rows, "the fixtures must contain a global type"
    scan = create_request(
        db_engine,
        {"accountIds": [DEV_ACCOUNT], "all": False},
        [("config_group", DEV_ACCOUNT, "us-east-1", None, None)],
    )

    body = listing(get_json, scan)

    assert ids(body) == {r["id"] for r in global_rows if r["status"] in LIVE}
    assert {item["awsRegion"] for item in body["items"]} == {"global"}


def test_a_resource_task_matches_only_that_resource(
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine
) -> None:
    vpc = seeded.one(account_id=MAIN_ACCOUNT, aws_region="us-east-1", resource_type="AWS::EC2::VPC")
    scan = create_request(
        db_engine,
        {"accountIds": [MAIN_ACCOUNT], "all": False},
        [("resource", MAIN_ACCOUNT, "us-east-1", "AWS::EC2::VPC", vpc["resource_id"])],
    )

    assert ids(listing(get_json, scan)) == {vpc["id"]}


def test_a_sweep_account_task_alone_covers_nothing(
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine
) -> None:
    del seeded
    scan = create_request(
        db_engine,
        {"accountIds": [MAIN_ACCOUNT], "all": False},
        [("sweep_account", MAIN_ACCOUNT, None, None, None)],
    )

    assert listing(get_json, scan)["total"] == 0


def test_scan_request_id_must_be_a_number(client: TestClient) -> None:
    assert client.get(f"{API}/resources?scanRequestId=abc").status_code == 422
