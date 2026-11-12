"""`scanRequestId` on the resource list and facets: the resolved scope of a scan (DESIGN 8.2).

The request rows are inserted directly (the API's creation path needs the registry and AWS);
its tasks go through the worker's own `add_tasks`, like the handler's children do. A request
older than the task retention stands for one whose tasks the cleanup has started to purge.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, text
from sqlmodel import Session

from app.core.db import make_engine
from app.query.tables import SCAN_REQUEST
from app.types.registry import TypeRegistry
from app.worker import requests
from tests.conftest import TEST_DATABASE_URL
from tests.support.seed import DEV_ACCOUNT, MAIN_ACCOUNT, Seeded

API = "/api/v1"
LIVE = ("active", "missing")
PAST_TASK_RETENTION = timedelta(days=15)  # TASK_RETENTION_DAYS is 14
Task = tuple[str, str, str | None, str | None, str | None]  # kind, account, region, type, id


@pytest.fixture
def db_engine(schema_name: str) -> Iterator[Engine]:
    engine = make_engine(TEST_DATABASE_URL, schema_name)
    yield engine
    with engine.begin() as connection:  # leave the shared seed as it was
        connection.execute(text("TRUNCATE t_inv_aws_scan_task, t_inv_aws_scan_request CASCADE"))
    engine.dispose()


def create_request(
    engine: Engine,
    scope: dict[str, Any] | None,
    tasks: list[Task],
    *,
    target: dict[str, Any] | None = None,
    age: timedelta = timedelta(0),
) -> int:
    with engine.begin() as connection:
        request_id = connection.execute(
            insert(SCAN_REQUEST)
            .values(
                trigger="api",
                scope=scope,
                target=target,
                priority=0,
                status="queued",
                created_at=datetime.now(UTC) - age,
            )
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
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine, registry: TypeRegistry
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

    in_reach = [
        r
        for r in seeded.where(account_id=MAIN_ACCOUNT)
        if r["aws_region"] in ("us-east-1", "global") and r["status"] in LIVE
    ]
    by_config = {r["id"] for r in in_reach if registry.get(r["resource_type"]).sources.config}
    # Config cannot serve the AMI in the same region: only its own shard would cover it.
    ami = seeded.one(account_id=MAIN_ACCOUNT, resource_type="AWS::EC2::Image")
    assert ami["aws_region"] == "us-east-1" and ami["id"] not in by_config
    assert ids(listing(get_json, everything)) == by_config
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


def test_past_the_task_retention_the_scope_is_the_one_the_request_recorded(
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine
) -> None:
    # A leftover task no longer decides anything: some of the others may be purged already.
    leftover: list[Task] = [("shard", MAIN_ACCOUNT, "us-east-1", "AWS::EC2::VPC", None)]
    by_account = create_request(
        db_engine,
        {"accountIds": [MAIN_ACCOUNT], "awsRegions": [], "resourceTypes": [], "all": False},
        leftover,
        age=PAST_TASK_RETENTION,
    )
    one_type = create_request(
        db_engine,
        {
            "accountIds": [MAIN_ACCOUNT],
            "awsRegions": ["us-east-1"],
            "resourceTypes": ["AWS::SQS::Queue"],
            "all": False,
        },
        leftover,
        age=PAST_TASK_RETENTION,
    )
    # us-east-1 is where global types are read, so naming it includes them.
    global_home = create_request(
        db_engine,
        {"accountIds": [DEV_ACCOUNT], "awsRegions": ["us-east-1"], "all": False},
        [],
        age=PAST_TASK_RETENTION,
    )
    sweep = create_request(db_engine, {"all": True}, [], age=PAST_TASK_RETENTION)

    assert ids(listing(get_json, by_account)) == expected(seeded, account_id=MAIN_ACCOUNT)
    queues = expected(
        seeded, account_id=MAIN_ACCOUNT, aws_region="us-east-1", resource_type="AWS::SQS::Queue"
    )
    assert queues and ids(listing(get_json, one_type)) == queues
    global_rows = expected(seeded, account_id=DEV_ACCOUNT, aws_region="global")
    assert global_rows and ids(listing(get_json, global_home)) == global_rows
    everything = {r["id"] for r in seeded.resources if r["status"] in LIVE}
    assert ids(listing(get_json, sweep)) == everything


def test_past_the_task_retention_a_target_request_covers_its_resource_or_scope(
    get_json: Callable[..., Any], seeded: Seeded, db_engine: Engine
) -> None:
    vpc = seeded.one(account_id=MAIN_ACCOUNT, aws_region="us-east-1", resource_type="AWS::EC2::VPC")
    key = requests.ResourceKey(
        vpc["account_id"], vpc["aws_region"], vpc["resource_type"], vpc["resource_id"]
    )
    refresh = create_request(db_engine, None, [], target=key._asdict(), age=PAST_TASK_RETENTION)
    confirmed_scope = create_request(  # the target `create_scope_scan` stores
        db_engine,
        None,
        [],
        target={
            "accountId": MAIN_ACCOUNT,
            "awsRegion": "us-east-1",
            "resourceType": "AWS::SQS::Queue",
        },
        age=PAST_TASK_RETENTION,
    )

    assert ids(listing(get_json, refresh)) == {vpc["id"]}
    assert ids(listing(get_json, confirmed_scope)) == expected(
        seeded, account_id=MAIN_ACCOUNT, aws_region="us-east-1", resource_type="AWS::SQS::Queue"
    )


def test_scan_request_id_must_be_a_number(client: TestClient) -> None:
    assert client.get(f"{API}/resources?scanRequestId=abc").status_code == 422
