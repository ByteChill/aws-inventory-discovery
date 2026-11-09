"""The whole backend, in one process: fake-aws + moto (seeded from the official fixtures), the
worker loop on real threads, the real API on the same database.

Resource data only ever comes from the fixture files, through the simulated AWS services.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app import cli
from app.aws.account_registry import AccountRegistryClient
from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.worker.main import Worker
from app.worker.scheduler import Scheduler
from tests.conftest import AWS_REGIONS, TEST_DATABASE_URL, AwsEnv

REGION, OTHER_REGION = AWS_REGIONS
QUEUE = "AWS::SQS::Queue"
VPC = "AWS::EC2::VPC"
HOSTED_ZONE = "AWS::Route53::HostedZone"
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}


@dataclass
class Stack:
    aws: AwsEnv
    client: TestClient
    settings: Settings
    scheduler: Scheduler
    accounts: list[str]  # [Config on in every region, Config off in eu-west-2]
    db_engine: Engine

    # -- API helpers -------------------------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(f"/api/v1{path}", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def post(self, path: str, body: Any = None, expect: int = 200, **kwargs: Any) -> Any:
        response = self.client.post(f"/api/v1{path}", json=body, **kwargs)
        assert response.status_code == expect, response.text
        return response.json()

    def resources(self, **filters: Any) -> list[dict[str, Any]]:
        return self.get("/resources", pageSize=200, **filters)["items"]

    def wait_for_scan(self, scan_id: int, timeout: float = 90) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            scan = self.get(f"/scans/{scan_id}")
            if scan["status"] in TERMINAL:
                return scan
            assert time.monotonic() < deadline, f"scan {scan_id} still {scan['status']}"
            time.sleep(0.2)

    def scan(self, body: dict[str, Any]) -> dict[str, Any]:
        created = self.post("/scans", body, expect=201)
        return self.wait_for_scan(created["id"])

    def tasks(self, scan_id: int, **filters: Any) -> list[dict[str, Any]]:
        return self.get(f"/scans/{scan_id}/tasks", pageSize=200, **filters)["items"]

    # -- fake-aws helpers --------------------------------------------------------------------

    def cc_resources(self, account: str, region: str, type_name: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self.aws.dataset["resources"]
            if (r["account_id"], r["region"], r["type_name"]) == (account, region, type_name)
        ]

    def config_items(self, account: str, region: str, type_name: str) -> list[dict[str, Any]]:
        return [
            i
            for i in self.aws.dataset["config_items"]
            if (i["accountId"], i["awsRegion"], i["resourceType"]) == (account, region, type_name)
        ]

    def delete_in_aws(self, account: str, region: str, type_name: str, identifier: str) -> None:
        self.aws.admin(
            "DELETE",
            "/resources",
            params={
                "account_id": account,
                "region": region,
                "type_name": type_name,
                "identifier": identifier,
                "config_resource_id": identifier,
            },
        )

    def inject(self, error: str, **match: Any) -> None:
        self.aws.admin("POST", "/errors", json={"error": error, **match})

    def clear_errors(self) -> None:
        self.aws.admin("DELETE", "/errors")


@pytest.fixture
def stack(
    aws: AwsEnv,
    engine: Engine,
    make_settings: Callable[..., Settings],
    make_client: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Stack]:
    accounts = [aws.account_ids[0], aws.account_ids[2]]
    subset = {
        key: [r for r in rows if r.get("account_id", r.get("accountId")) in accounts]
        for key, rows in aws.dataset.items()
    }
    aws.dataset = subset
    aws.reload()
    registry_url = f"{aws.fake_url}/registry/accounts"
    settings = make_settings(
        REACHABLE_REGIONS=AWS_REGIONS,
        CREDENTIAL_SERVICE_URL=f"{aws.fake_url}/credentials",
        ACCOUNT_REGISTRY_URL=registry_url,
        WORKER_THREADS=6,
        WORKER_ONDEMAND_THREADS=2,
        SWEEP_INTERVAL_HOURS=0,  # no stagger: every account starts at once
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    client = make_client(ACCOUNT_REGISTRY_URL=registry_url)
    scheduler = Scheduler(engine, settings, AccountRegistryClient(registry_url))
    worker = Worker(engine, settings, poll_interval=0.05, heartbeat_interval=1)
    worker.start()
    try:
        yield Stack(aws, client, settings, scheduler, accounts, engine)
    finally:
        worker.stop()
        worker.join(timeout=20)
        scheduler.lock.release()
        aws.dataset = subset  # the next test reloads the full dataset itself


def full_sweep(stack: Stack) -> dict[str, Any]:
    from sqlmodel import Session

    with Session(stack.db_engine) as session:
        request = stack.scheduler.start_sweep(session, datetime.now(UTC))
        assert request.id is not None
        scan_id = request.id
    return stack.wait_for_scan(scan_id)


# --------------------------------------------------------------------------------------------


def test_scheduled_sweep_lands_every_account_and_region_and_rolls_up(stack: Stack) -> None:
    sweep = full_sweep(stack)

    assert sweep["status"] == "succeeded" and sweep["trigger"] == "schedule"
    total = stack.get("/resources", pageSize=1)["total"]
    assert total > 0 and sweep["counts"]["resources"]["new"] == total
    tasks = stack.tasks(sweep["id"])
    kinds = Counter(t["kind"] for t in tasks)
    assert kinds["sweep_account"] == 2 and kinds["config_group"] == 3  # Config off in one region
    assert {t["status"] for t in tasks} <= {"succeeded", "skipped"}
    assert sweep["counts"]["tasks"]["succeeded"] == len(tasks)

    on_account, off_account = stack.accounts
    # Config recording: what Config holds is what lands (global types: see the test below)
    regional = [i for i in stack.aws.dataset["config_items"] if i["awsRegion"] != "global"]
    for type_name in {i["resourceType"] for i in regional}:
        found = stack.resources(accountId=on_account, awsRegion=REGION, resourceType=type_name)
        assert len(found) == len(stack.config_items(on_account, REGION, type_name)), type_name
        assert {r["configurationItemCaptureTime"] is not None for r in found} == {True}
    # Config off: Cloud Control takes over, with the observation time of the call
    fallback = stack.resources(accountId=off_account, awsRegion=OTHER_REGION, resourceType=QUEUE)
    assert len(fallback) == len(stack.cc_resources(off_account, OTHER_REGION, QUEUE)) > 0
    detail = stack.get(f"/resources/{fallback[0]['id']}")
    assert detail["observedSource"] == "cloudcontrol" and detail["live"] is not None
    ami = stack.resources(accountId=on_account, awsRegion=REGION, resourceType="AWS::EC2::Image")
    assert ami and stack.get(f"/resources/{ami[0]['id']}")["observedSource"] == "native"

    for account in stack.accounts:
        info = stack.get(f"/accounts/{account}")
        assert info["accessStatus"] == "ok" and info["lastScanOkAt"] is not None
        recording = {r["region"]: r["configRecording"] for r in info["regions"] if r["reachable"]}
        assert recording[REGION] is True
        assert recording[OTHER_REGION] is (account == on_account)
    summary = stack.get("/scans/summary")
    assert summary["sweep"] is None and summary["failures24h"] == []
    assert summary["missingCount"] == 0 and summary["workers"]["activeTasks"] == 0


def test_a_global_type_is_read_once_per_account_and_filed_under_global(stack: Stack) -> None:
    sweep = full_sweep(stack)

    assert sweep["status"] == "succeeded"
    zones = stack.resources(resourceType=HOSTED_ZONE)
    for account in stack.accounts:
        recorded = stack.config_items(account, "global", HOSTED_ZONE)
        mine = [z for z in zones if z["accountId"] == account]
        assert len(mine) == len(recorded) > 0
        assert {z["awsRegion"] for z in mine} == {"global"}
        assert {z["resourceId"] for z in mine} == {i["resourceId"] for i in recorded}
        # only us-east-1 serves (and records) the global type
        assert stack.cc_resources(account, OTHER_REGION, HOSTED_ZONE) == []
        assert stack.get(f"/resources/{mine[0]['id']}")["observedSource"] == "config"
    for region in (REGION, OTHER_REGION):
        assert stack.resources(resourceType=HOSTED_ZONE, awsRegion=region) == []
    # no task of the sweep is planned for the global scope outside us-east-1
    assert Counter(t["kind"] for t in stack.tasks(sweep["id"]))["config_group"] == 3


def test_a_global_scope_is_planned_once_and_detects_deletion(stack: Stack, engine: Engine) -> None:
    account = stack.accounts[0]
    body = {"accountIds": [account], "resourceTypes": [HOSTED_ZONE]}  # both regions are scannable

    first = stack.scan(body)

    shards = [t for t in stack.tasks(first["id"]) if t["kind"] == "shard"]
    assert [(t["region"], t["source"]) for t in shards] == [("global", "cloudcontrol")]
    zones = stack.resources(accountId=account, resourceType=HOSTED_ZONE)
    served = stack.cc_resources(account, REGION, HOSTED_ZONE)
    assert len(zones) == len(served) > 1 and {z["awsRegion"] for z in zones} == {"global"}
    assert first["counts"]["resources"]["new"] == len(zones)
    with engine.begin() as connection:
        shard_rows = connection.execute(
            text("SELECT region FROM t_inv_aws_scan_shard WHERE resource_type = :t"),
            {"t": HOSTED_ZONE},
        ).all()
    assert [row.region for row in shard_rows] == ["global"]

    victim = zones[0]
    stack.delete_in_aws(account, REGION, HOSTED_ZONE, victim["resourceId"])
    with engine.begin() as connection:  # scanned moments ago (R3): age the scope
        connection.execute(
            text("UPDATE t_inv_aws_scan_shard SET last_succeeded_at = now() - interval '2 hours'")
        )
    second = stack.scan(body)

    # absent from the complete listing, and GetResource (read from us-east-1) agrees
    assert second["counts"]["resources"]["missing"] == 1
    assert second["counts"]["resources"]["deleted"] == 1
    gone = stack.get(f"/resources/{victim['id']}")
    assert gone["status"] == "deleted" and gone["awsRegion"] == "global"
    remaining = stack.resources(accountId=account, resourceType=HOSTED_ZONE)
    assert len(remaining) == len(zones) - 1 and victim["id"] not in {z["id"] for z in remaining}


def test_on_demand_scan_uses_live_sources_and_honours_its_scope(stack: Stack) -> None:
    account = stack.accounts[0]
    body = {"accountIds": [account], "awsRegions": [REGION], "resourceTypes": [QUEUE]}

    scan = stack.scan(body)

    assert scan["status"] == "succeeded" and scan["trigger"] == "api"
    assert [(t["kind"], t["source"], t["region"]) for t in stack.tasks(scan["id"])] == [
        ("sweep_account", None, None),
        ("shard", "cloudcontrol", REGION),
    ]
    stored = stack.resources()
    assert {(r["accountId"], r["awsRegion"], r["resourceType"]) for r in stored} == {
        (account, REGION, QUEUE)
    }
    assert len(stored) == len(stack.cc_resources(account, REGION, QUEUE))
    assert scan["counts"]["resources"]["new"] == len(stored)
    same = stack.post("/scans", body, expect=201, headers={"Idempotency-Key": "k1"})
    replay = stack.post("/scans", body, expect=200, headers={"Idempotency-Key": "k1"})
    assert replay["id"] == same["id"]
    stack.post("/scans", {"accountIds": ["999999999999"]}, expect=404)


def test_access_denied_marks_the_account_never_deletes_and_retry_failed_recovers(
    stack: Stack,
) -> None:
    denied, healthy = stack.accounts
    full_sweep(stack)
    before = stack.get("/resources", pageSize=1, accountId=denied)["total"]
    stack.inject("AccessDenied", account_id=denied)

    everything = stack.scan({"accountIds": [denied, healthy]})

    # AMIs are read through EC2, which the injection can't reach: some calls still work
    assert everything["status"] == "partial"
    assert stack.get(f"/accounts/{denied}")["accessStatus"] == "partial"
    narrow = stack.scan({"accountIds": [denied, healthy], "resourceTypes": [QUEUE, VPC]})
    failed = stack.tasks(narrow["id"], status="failed")
    assert failed and {t["errorCode"] for t in failed} == {"access_denied"}
    assert {t["attempt"] for t in failed} == {1}  # not retried
    assert stack.get(f"/accounts/{denied}")["accessStatus"] == "access_denied"
    assert stack.get(f"/accounts/{healthy}")["accessStatus"] == "ok"
    assert stack.get("/resources", pageSize=1, accountId=denied)["total"] == before
    assert stack.get("/resources", pageSize=1, status="deleted")["total"] == 0
    assert stack.get("/resources", pageSize=1, status="missing")["total"] == 0
    summary = stack.get("/scans/summary")
    assert {f["errorCode"] for f in summary["failures24h"]} == {"access_denied"}
    assert narrow["failuresByCode"] == [{"errorCode": "access_denied", "count": len(failed)}]

    stack.clear_errors()
    assert stack.post(f"/scans/{narrow['id']}/retry-failed")["requeued"] == len(failed)
    retried = stack.wait_for_scan(narrow["id"])

    assert retried["status"] == "succeeded" and retried["counts"]["tasks"].get("failed") is None
    assert stack.get(f"/accounts/{denied}")["accessStatus"] == "ok"


def test_a_deleted_resource_needs_both_evidences(stack: Stack, engine: Engine) -> None:
    account = stack.accounts[0]
    full_sweep(stack)
    queues = stack.resources(accountId=account, awsRegion=REGION, resourceType=QUEUE)
    victim = queues[0]
    detail = f"/resources/{victim['id']}"

    # Config stops reporting it (evidence 1) while Cloud Control still has it: no deletion
    stack.aws.admin(
        "DELETE",
        "/resources",
        params={
            "account_id": account,
            "region": REGION,
            "type_name": QUEUE,
            "config_resource_id": victim["resourceId"],
        },
    )
    with engine.begin() as connection:  # the scopes were scanned moments ago (R3): age them
        connection.execute(
            text("UPDATE t_inv_aws_scan_shard SET last_succeeded_at = now() - interval '2 hours'")
        )
    second = full_sweep(stack)

    assert second["counts"]["resources"]["missing"] == 1
    confirm = stack.tasks(second["id"], kind="confirm_missing")
    assert [t["stats"]["restored"] for t in confirm] == [1]
    assert stack.get(detail)["status"] == "active"

    # now AWS itself says it is gone: the next complete listing misses it and GetResource agrees
    stack.delete_in_aws(account, REGION, QUEUE, victim["resourceId"])
    third = stack.scan({"accountIds": [account], "awsRegions": [REGION], "resourceTypes": [QUEUE]})

    assert third["counts"]["resources"]["missing"] == 1
    assert third["counts"]["resources"]["deleted"] == 1
    gone = stack.get(detail)
    assert gone["status"] == "deleted" and gone["deletedAt"] is not None
    listed = stack.resources(accountId=account, awsRegion=REGION, resourceType=QUEUE)
    assert victim["id"] not in {r["id"] for r in listed}
    stack.post(f"/scans/{third['id']}/cancel", expect=409)


def test_refresh_returns_fresh_data_and_a_gone_resource_is_only_marked_missing(
    stack: Stack,
) -> None:
    account = stack.accounts[0]
    stack.scan({"accountIds": [account], "awsRegions": [REGION], "resourceTypes": [QUEUE]})
    first, second = stack.resources(accountId=account, awsRegion=REGION, resourceType=QUEUE)[:2]
    key = {"accountId": account, "awsRegion": REGION, "resourceType": QUEUE}

    changed = next(
        r
        for r in stack.cc_resources(account, REGION, QUEUE)
        if r["identifier"] == first["resourceId"]
    )
    stack.aws.admin(
        "PUT",
        "/resources",
        json={**changed, "properties": {**changed["properties"], "VisibilityTimeout": 99}},
    )
    refreshed = stack.post(
        "/resources/refresh?wait=10", {**key, "resourceId": first["resourceId"]}, expect=200
    )
    assert refreshed["configuration"]["VisibilityTimeout"] in (99, "99")
    assert refreshed["refreshError"] is None and refreshed["status"] == "active"
    # a refresh is a scan request too, listed with its roll-up like any other
    (refresh_scan,) = stack.get("/scans", trigger="api", pageSize=200)["items"][:1]
    assert refresh_scan["target"]["resourceId"] == first["resourceId"]
    assert refresh_scan["counts"]["tasks"] == {"succeeded": 1}
    by_arn = stack.post("/resources/refresh?wait=10", {"arn": first["arn"]})
    assert by_arn["id"] == first["id"]

    stack.delete_in_aws(account, REGION, QUEUE, second["resourceId"])
    for _ in range(2):  # a refresh alone never reaches `deleted`
        marked = stack.post(
            "/resources/refresh?wait=10", {**key, "resourceId": second["resourceId"]}
        )
        assert marked["id"] == second["id"] and marked["status"] == "missing"
    assert marked["deletedAt"] is None

    nothing = {**key, "resourceId": "https://sqs.us-east-1.amazonaws.com/1/never-existed"}
    stack.post("/resources/refresh?wait=10", nothing, expect=404)

    stack.inject("AccessDenied", account_id=account, operation="GetResource")
    failed = stack.post("/resources/refresh?wait=10", {**key, "resourceId": first["resourceId"]})
    assert failed["refreshError"]["code"] == "access_denied" and failed["status"] == "active"


def test_refreshing_an_ecs_service_that_is_not_in_the_inventory_derives_the_cluster(
    stack: Stack,
) -> None:
    account, ecs_service = stack.accounts[0], "AWS::ECS::Service"
    (service,) = stack.cc_resources(account, REGION, ecs_service)
    service_arn = service["identifier"].split("|")[0]
    key = {"accountId": account, "awsRegion": REGION, "resourceType": ecs_service}
    assert stack.resources(accountId=account, resourceType=ecs_service) == []

    refreshed = stack.post(
        "/resources/refresh?wait=10", {**key, "resourceId": service_arn}, expect=200
    )
    assert refreshed["resourceId"] == service_arn and refreshed["status"] == "active"
    assert refreshed["refreshError"] is None

    # the old short ARN format has no cluster in it: rejected before any task is queued
    short = service_arn.replace(service_arn.split(":service/")[1], "just-a-name")
    rejected = stack.post("/resources/refresh", {**key, "resourceId": short}, expect=422)
    assert rejected["code"] == "validation_error" and "short format" in rejected["message"]


def test_refreshing_an_ecs_cluster_that_is_not_in_the_inventory_derives_the_name(
    stack: Stack,
) -> None:
    account, ecs_cluster = stack.accounts[0], "AWS::ECS::Cluster"
    cluster = stack.cc_resources(account, REGION, ecs_cluster)[0]
    cluster_arn = cluster["properties"]["Arn"]  # the resource id; the identifier is the name
    key = {"accountId": account, "awsRegion": REGION, "resourceType": ecs_cluster}
    assert stack.resources(accountId=account, resourceType=ecs_cluster) == []

    refreshed = stack.post(
        "/resources/refresh?wait=10", {**key, "resourceId": cluster_arn}, expect=200
    )
    assert refreshed["resourceId"] == cluster_arn and refreshed["status"] == "active"
    assert refreshed["refreshError"] is None

    bad = stack.post("/resources/refresh", {**key, "resourceId": "telemetry"}, expect=422)
    assert bad["code"] == "validation_error" and "cluster ARN" in bad["message"]


def test_cancel_stops_a_queued_scan(stack: Stack, engine: Engine) -> None:
    # no sweep can start before the cancel: the worker's claim loop is not that fast, so the
    # scan is cancelled while its first tasks are queued or running
    created = stack.post("/scans", {"all": True}, expect=201)  # the registry fills the accounts

    cancelled = stack.post(f"/scans/{created['id']}/cancel")

    assert cancelled["status"] == "cancelled"
    stack.post(f"/scans/{created['id']}/cancel", expect=409)
    final = stack.get(f"/scans/{created['id']}")
    assert final["status"] == "cancelled" and final["finishedAt"] is not None
    deadline = time.monotonic() + 30  # whatever was running stops at its next checkpoint
    while {t["status"] for t in stack.tasks(created["id"])} & {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert stack.get(f"/scans/{created['id']}")["status"] == "cancelled"
    assert stack.get("/accounts", pageSize=1)["total"] == len(stack.accounts)


def test_task_logs_come_from_cloudwatch_and_carry_the_task_id(stack: Stack) -> None:
    lines: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = Collect()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    previous_level = root.level
    # setLevel (not assigning .level) clears every logger's cached isEnabledFor() answers;
    # an earlier in-process Alembic run leaves root at WARNING with those caches filled.
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        scan = stack.scan(
            {
                "accountIds": [stack.accounts[0]],
                "awsRegions": [REGION],
                "resourceTypes": [QUEUE],
            }
        )
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
    shard = next(t for t in stack.tasks(scan["id"]) if t["kind"] == "shard")
    sweep = next(t for t in stack.tasks(scan["id"]) if t["kind"] == "sweep_account")

    group = stack.settings.WORKER_LOG_GROUP
    logs = boto3.client("logs", endpoint_url=stack.aws.moto_url, region_name="us-east-1")
    logs.create_log_group(logGroupName=group)
    logs.create_log_stream(logGroupName=group, logStreamName="worker")
    events = sorted(
        (
            {
                "timestamp": int(
                    datetime.fromisoformat(json.loads(line)["time"]).timestamp() * 1000
                ),
                "message": line,
            }
            for line in lines
        ),
        key=lambda e: e["timestamp"],
    )
    logs.put_log_events(logGroupName=group, logStreamName="worker", logEvents=events)

    page = stack.get(f"/tasks/{shard['id']}/logs")

    assert page["events"], "the task logged nothing"
    assert {e["data"]["taskId"] for e in page["events"]} == {shard["id"]}
    assert {e["data"]["scanRequestId"] for e in page["events"]} == {scan["id"]}
    done = next(e for e in page["events"] if e["message"] == "task succeeded")
    assert done["level"] == "INFO" and done["data"]["stats"]["new"] == shard["stats"]["new"]
    other = stack.get(f"/tasks/{sweep['id']}/logs")
    assert {e["data"]["taskId"] for e in other["events"]} == {sweep["id"]}
    limited = stack.get(f"/tasks/{shard['id']}/logs", limit=2)
    assert len(limited["events"]) <= 2 and limited["nextToken"]


def test_doctor_passes_against_the_fakes_and_fails_when_a_dependency_is_down(
    stack: Stack, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.conftest import NOWHERE, clear_caches

    env = {
        "DATABASE_URL": TEST_DATABASE_URL,
        "DATABASE_SCHEMA": stack.settings.DATABASE_SCHEMA,
        "REDIS_URL": "redis://localhost:56379/0",
        "ACCOUNT_REGISTRY_URL": f"{stack.aws.fake_url}/registry/accounts",
        "CREDENTIAL_SERVICE_URL": f"{stack.aws.fake_url}/credentials",
        "REACHABLE_REGIONS": ",".join(AWS_REGIONS),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    clear_caches()

    assert cli.run_doctor() == 0
    table = capsys.readouterr().out
    assert table.count(" ok   ") == 4 + 3 * len(AWS_REGIONS) and "FAIL" not in table
    assert "migrations at head" in table

    monkeypatch.setenv("ACCOUNT_REGISTRY_URL", f"http://{NOWHERE}/accounts")
    clear_caches()
    assert cli.run_doctor() == 1
    table = capsys.readouterr().out
    assert table.count("FAIL") == 2  # the registry, and the credential check that needs it
    clear_caches()
