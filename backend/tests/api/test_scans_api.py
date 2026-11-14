"""The write side of the API: error codes and status codes of refresh, scans, tasks and scopes.

No worker runs here: requests stay queued, which is exactly what the contract is about.
The end-to-end behaviour is in tests/worker/test_end_to_end.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert, text

from app.core.db import make_engine
from app.query.tables import RESOURCE
from app.types.registry import TypeRegistry
from tests.api.conftest import TEST_REDIS_URL
from tests.conftest import NOWHERE, TEST_DATABASE_URL
from tests.support.seed import (
    AWS_FIXTURES,
    DEV_ACCOUNT,
    IMAGES_ACCOUNT,
    MAIN_ACCOUNT,
    Seeded,
    cloudcontrol_rows,
    load_json,
)

API = "/api/v1"
SCOPE = {"accountId": MAIN_ACCOUNT, "awsRegion": "eu-west-1", "resourceType": "AWS::SNS::Topic"}


@pytest.fixture
def db_engine(schema_name: str) -> Iterator[Engine]:
    engine = make_engine(TEST_DATABASE_URL, schema_name)
    yield engine
    with engine.begin() as connection:  # leave the shared seed as it was
        connection.execute(text("TRUNCATE t_inv_aws_scan_task, t_inv_aws_scan_request CASCADE"))
        connection.execute(
            text(
                "UPDATE t_inv_aws_scan_shard SET guard_state = 'normal', candidate_count = NULL,"
                " candidate_ids = NULL, guard_detected_at = NULL, guard_confirmed_by = NULL"
            )
        )
    engine.dispose()


@pytest.fixture
def api(client: TestClient, db_engine: Engine) -> TestClient:
    del db_engine  # only needed for its clean-up
    return client


def error_of(response: Any, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return response.json()


def test_create_scan_validates_the_body_and_the_scope(
    api: TestClient, make_client: Callable[..., TestClient], seeded: Seeded
) -> None:
    for body in ({}, {"accountIds": [MAIN_ACCOUNT], "all": True}, {"accountIds": ["12"]}):
        assert error_of(api.post(f"{API}/scans", json=body), 422)["code"] == "validation_error"
    unknown_type = {"accountIds": [MAIN_ACCOUNT], "resourceTypes": ["AWS::Nope::Thing"]}
    assert error_of(api.post(f"{API}/scans", json=unknown_type), 422)["code"] == (
        "validation_error"
    )
    missing = error_of(api.post(f"{API}/scans", json={"accountIds": ["999999999999"]}), 404)
    assert missing["code"] == "account_not_found"
    assert missing["details"] == {"accountIds": ["999999999999"]}

    small = make_client(MAX_SCAN_ACCOUNTS="1")
    too_large = error_of(
        small.post(f"{API}/scans", json={"accountIds": [MAIN_ACCOUNT, IMAGES_ACCOUNT]}), 422
    )
    assert too_large["code"] == "scope_too_large"
    assert too_large["details"] == {"limit": 1, "requested": 2}


def test_create_scan_status_codes_and_idempotency(api: TestClient) -> None:
    body = {"accountIds": [MAIN_ACCOUNT], "awsRegions": ["us-east-1"]}
    headers = {"Idempotency-Key": "key-1", "X-Caller-Id": "dashboard"}

    created = api.post(f"{API}/scans", json=body, headers=headers)
    again = api.post(f"{API}/scans", json=body, headers=headers)
    different = api.post(
        f"{API}/scans", json={"accountIds": [IMAGES_ACCOUNT]}, headers={"Idempotency-Key": "key-1"}
    )

    assert created.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == created.json()["id"]
    assert created.json()["callerId"] == "dashboard" and created.json()["status"] == "queued"
    assert created.json()["scope"] == {
        "accountIds": [MAIN_ACCOUNT],
        "awsRegions": ["us-east-1"],
        "resourceTypes": None,
        "all": None,
    }
    assert error_of(different, 409)["code"] == "idempotency_conflict"

    everything = api.post(f"{API}/scans", json={"all": True})
    running = api.post(f"{API}/scans", json={"all": True})
    assert (everything.status_code, running.status_code) == (201, 200)
    assert running.json()["id"] == everything.json()["id"]


def test_scan_listing_detail_tasks_and_cancel(api: TestClient) -> None:
    created = api.post(f"{API}/scans", json={"accountIds": [MAIN_ACCOUNT, IMAGES_ACCOUNT]}).json()
    scan_id = created["id"]

    listed = api.get(f"{API}/scans", params={"status": "queued", "trigger": "api"}).json()
    assert [s["id"] for s in listed["items"]] == [scan_id] and listed["total"] == 1
    assert api.get(f"{API}/scans", params={"trigger": "schedule"}).json()["total"] == 0
    detail = api.get(f"{API}/scans/{scan_id}").json()
    assert detail["failuresByCode"] == [] and detail["counts"]["resources"]["new"] == 0
    tasks = api.get(f"{API}/scans/{scan_id}/tasks", params={"kind": "sweep_account"}).json()
    assert tasks["total"] == 2 and {t["accountId"] for t in tasks["items"]} == {
        MAIN_ACCOUNT,
        IMAGES_ACCOUNT,
    }
    assert api.get(f"{API}/scans/{scan_id}/tasks", params={"status": "failed"}).json()["total"] == 0
    task = api.get(f"{API}/tasks/{tasks['items'][0]['id']}").json()
    assert task["status"] == "queued" and task["attempt"] == 0 and task["maxAttempts"] == 3

    cancelled = api.post(f"{API}/scans/{scan_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert error_of(api.post(f"{API}/scans/{scan_id}/cancel"), 409)["code"] == (
        "scan_not_cancellable"
    )
    assert error_of(api.post(f"{API}/scans/{scan_id}/retry-failed"), 409)["code"] == (
        "scan_cancelled"
    )
    cancelled_tasks = api.get(f"{API}/scans/{scan_id}/tasks").json()["items"]
    assert {t["status"] for t in cancelled_tasks} == {"cancelled"}


def test_unknown_scans_and_tasks_are_404(api: TestClient) -> None:
    for method, path, code in (
        ("get", "/scans/999999", "scan_not_found"),
        ("get", "/scans/999999/tasks", "scan_not_found"),
        ("post", "/scans/999999/cancel", "scan_not_found"),
        ("post", "/scans/999999/retry-failed", "scan_not_found"),
        ("get", "/tasks/999999", "task_not_found"),
        ("get", "/tasks/999999/logs", "task_not_found"),
    ):
        assert error_of(getattr(api, method)(f"{API}{path}"), 404)["code"] == code, path


def test_retry_failed_requeues_only_failed_tasks(api: TestClient, db_engine: Engine) -> None:
    scan_id = api.post(f"{API}/scans", json={"accountIds": [MAIN_ACCOUNT]}).json()["id"]
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE t_inv_aws_scan_task SET status = 'failed', attempt = 3,"
                " error_code = 'throttled', finished_at = now() WHERE scan_request_id = :id"
            ),
            {"id": scan_id},
        )

    assert api.post(f"{API}/scans/{scan_id}/retry-failed").json() == {"requeued": 1}

    (task,) = api.get(f"{API}/scans/{scan_id}/tasks").json()["items"]
    assert (task["status"], task["attempt"], task["errorCode"]) == ("queued", 0, None)
    assert api.post(f"{API}/scans/{scan_id}/retry-failed").json() == {"requeued": 0}


def test_summary_reports_the_backlog(api: TestClient) -> None:
    api.post(f"{API}/scans", json={"accountIds": [MAIN_ACCOUNT, IMAGES_ACCOUNT]})

    summary = api.get(f"{API}/scans/summary").json()

    assert summary["backlog"]["onDemand"]["queued"] == 2
    assert summary["backlog"]["scheduled"] == {"queued": 0, "oldestWaitSeconds": 0}
    assert summary["sweep"] is None and summary["failures24h"] == []
    assert summary["workers"] == {"activeTasks": 0, "lastHeartbeatAt": None}
    assert summary["abnormalScopeCount"] == 0


def test_refresh_without_waiting_is_202_and_shares_the_inflight_task(
    api: TestClient, seeded: Seeded
) -> None:
    row = seeded.where(account_id=DEV_ACCOUNT, resource_type="AWS::SQS::Queue")[0]
    body = {
        "accountId": row["account_id"],
        "awsRegion": row["aws_region"],
        "resourceType": row["resource_type"],
        "resourceId": row["resource_id"],
    }

    first = api.post(f"{API}/resources/refresh", json=body)
    waited = api.post(f"{API}/resources/refresh?wait=1", json=body)  # nobody works the queue

    assert first.status_code == 202 and waited.status_code == 202
    assert first.json() == waited.json() and first.json()["taskId"] > 0
    task = api.get(f"{API}/tasks/{first.json()['taskId']}").json()
    assert (task["kind"], task["resourceId"]) == ("resource", row["resource_id"])


def test_refresh_rejects_bad_requests(api: TestClient, seeded: Seeded) -> None:
    key = {"accountId": MAIN_ACCOUNT, "awsRegion": "us-east-1", "resourceId": "x"}
    for path, body in (
        ("/resources/refresh?wait=21", {"arn": "arn:aws:x"}),
        ("/resources/refresh", {}),
        ("/resources/refresh", {**key, "arn": "arn:aws:x"}),
        ("/resources/refresh", {**key, "resourceType": "AWS::Nope::Thing"}),
    ):
        assert error_of(api.post(f"{API}{path}", json=body), 422)["code"] == "validation_error"
    unknown_account = {**key, "accountId": "999999999999", "resourceType": "AWS::SQS::Queue"}
    assert error_of(api.post(f"{API}/resources/refresh", json=unknown_account), 404)["code"] == (
        "account_not_found"
    )
    missing_arn = error_of(api.post(f"{API}/resources/refresh", json={"arn": "arn:aws:nope"}), 404)
    assert missing_arn["code"] == "resource_not_found"


def test_refresh_by_arn_that_is_in_two_accounts_is_ambiguous(
    api: TestClient, seeded: Seeded
) -> None:
    arn = seeded.one(account_id=IMAGES_ACCOUNT, resource_type="AWS::EC2::Image")["arn"]

    body = error_of(api.post(f"{API}/resources/refresh", json={"arn": arn}), 409)

    assert body["code"] == "ambiguous_arn"
    assert IMAGES_ACCOUNT in body["details"]["accountIds"]
    assert len(body["details"]["accountIds"]) > 1


def test_scan_scopes_and_confirming_an_abnormal_one(api: TestClient, db_engine: Engine) -> None:
    assert api.get(f"{API}/scan-scopes").json()["total"] == 0
    assert error_of(api.post(f"{API}/scan-scopes/confirm-change", json=SCOPE), 409)["code"] == (
        "scope_not_abnormal"
    )
    nothing = {**SCOPE, "awsRegion": "mars-1"}
    assert error_of(api.post(f"{API}/scan-scopes/confirm-change", json=nothing), 404)["code"] == (
        "scope_not_found"
    )
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE t_inv_aws_scan_shard SET guard_state = 'abnormal', candidate_count = 30,"
                " guard_detected_at = now() WHERE account_id = :a AND region = :r"
                " AND resource_type = :t"
            ),
            {"a": SCOPE["accountId"], "r": SCOPE["awsRegion"], "t": SCOPE["resourceType"]},
        )
    (abnormal,) = api.get(f"{API}/scan-scopes").json()["items"]
    assert abnormal["candidateCount"] == 30 and abnormal["resourceType"] == SCOPE["resourceType"]
    assert isinstance(abnormal["scanShardId"], int)
    # A direct link reads one scope by id, independent of its page in the list.
    assert api.get(f"{API}/scan-scopes/{abnormal['scanShardId']}").json() == abnormal
    assert error_of(api.get(f"{API}/scan-scopes/999999999"), 404)["code"] == "scope_not_found"
    assert api.get(f"{API}/scans/summary").json()["abnormalScopeCount"] == 1

    confirmed = api.post(
        f"{API}/scan-scopes/confirm-change", json=SCOPE, headers={"X-Caller-Id": "alice"}
    )

    assert confirmed.status_code == 200
    scan = api.get(f"{API}/scans/{confirmed.json()['scanRequestId']}").json()
    assert scan["target"] == {k: SCOPE[k] for k in SCOPE} and scan["callerId"] == "alice"
    (task,) = api.get(f"{API}/scans/{scan['id']}/tasks").json()["items"]
    assert (task["kind"], task["source"], task["resourceType"]) == (
        "shard",
        "cloudcontrol",
        SCOPE["resourceType"],
    )
    with db_engine.connect() as connection:
        shard = connection.execute(
            text(
                "SELECT guard_state, guard_confirmed_by FROM t_inv_aws_scan_shard"
                " WHERE account_id = :a AND region = :r AND resource_type = :t"
            ),
            {"a": SCOPE["accountId"], "r": SCOPE["awsRegion"], "t": SCOPE["resourceType"]},
        ).one()
    assert tuple(shard) == ("confirmed", "alice")


def test_scope_candidates_come_from_the_candidates_saved_with_the_guard(
    api: TestClient, db_engine: Engine, seeded: Seeded, registry: TypeRegistry
) -> None:
    vpc = seeded.one(account_id=MAIN_ACCOUNT, aws_region="us-east-1", resource_type="AWS::EC2::VPC")
    # A second VPC of the same scope, from the Cloud Control fixture: a candidate when the
    # guard fired, removed by the retention cleanup afterwards.
    listing = load_json(AWS_FIXTURES / "aws.ec2.vpc" / "cloudcontrol_list.json")
    purged = next(
        row
        for row in cloudcontrol_rows(
            registry, listing, account_id=vpc["account_id"], region=vpc["aws_region"]
        )
        if row["resource_id"] != vpc["resource_id"]
    )
    stored = [{"resourceId": r["resource_id"], "arn": r["arn"]} for r in (vpc, purged)]
    scope = {"accountId": vpc["account_id"], "awsRegion": vpc["aws_region"]}
    scope["resourceType"] = vpc["resource_type"]
    with db_engine.begin() as connection:
        purged_id = connection.execute(
            insert(RESOURCE).values(**purged).returning(RESOURCE.c.id)
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE t_inv_aws_scan_shard SET guard_state = 'abnormal', candidate_count = 2,"
                " candidate_ids = CAST(:ids AS jsonb), guard_detected_at = now()"
                " WHERE account_id = :a AND region = :r AND resource_type = :t"
            ),
            {
                "ids": json.dumps(stored),
                "a": scope["accountId"],
                "r": scope["awsRegion"],
                "t": scope["resourceType"],
            },
        )
        connection.execute(delete(RESOURCE).where(RESOURCE.c.id == purged_id))
    (item,) = api.get(f"{API}/scan-scopes", params={"guardState": "abnormal"}).json()["items"]
    url = f"{API}/scan-scopes/{item['scanShardId']}/candidates"

    body = api.get(url).json()

    assert body["total"] == 2
    resource_ids = [c["resourceId"] for c in body["items"]]
    assert resource_ids == sorted(resource_ids)
    found = next(c for c in body["items"] if c["resourceId"] == vpc["resource_id"])
    assert (found["id"], found["resourceName"], found["arn"]) == (
        vpc["id"],
        vpc["resource_name"],
        vpc["arn"],
    )
    assert found["inventoryStatus"] == vpc["status"]
    gone = next(c for c in body["items"] if c["resourceId"] == purged["resource_id"])
    assert (gone["id"], gone["resourceName"], gone["inventoryStatus"]) == (
        None,
        None,
        "purged",
    )
    assert gone["arn"] == purged["arn"]

    for candidate in (vpc, purged):
        assert api.get(url, params={"q": candidate["resource_id"]}).json()["total"] == 1
    first = api.get(url, params={"pageSize": 1}).json()
    assert first["total"] == 2 and len(first["items"]) == 1

    # Flagged before candidates were saved (SQL NULL), or cleared since (JSON null): no list.
    for unsaved in ("NULL", "'null'::jsonb"):
        with db_engine.begin() as connection:
            connection.execute(
                text(f"UPDATE t_inv_aws_scan_shard SET candidate_ids = {unsaved} WHERE id = :id"),
                {"id": item["scanShardId"]},
            )
        assert api.get(url).json()["total"] == 0

    with db_engine.begin() as connection:
        connection.execute(text("UPDATE t_inv_aws_scan_shard SET guard_state = 'normal'"))
    normal = api.get(url).json()
    assert normal["total"] == 0 and normal["items"] == []

    missing = api.get(f"{API}/scan-scopes/999999999/candidates")
    assert error_of(missing, 404)["code"] == "scope_not_found"


def test_task_logs_are_503_when_cloudwatch_cannot_be_reached(
    make_client: Callable[..., TestClient],
    seeded: Seeded,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    client = make_client(AWS_ENDPOINT_URL=f"http://{NOWHERE}")
    scan = client.post(f"{API}/scans", json={"accountIds": [MAIN_ACCOUNT]}).json()
    (task,) = client.get(f"{API}/scans/{scan['id']}/tasks").json()["items"]

    response = client.get(f"{API}/tasks/{task['id']}/logs")

    assert error_of(response, 503)["code"] == "dependency_unavailable"
    assert error_of(client.get(f"{API}/tasks/{task['id']}/logs?limit=1001"), 422)["code"] == (
        "validation_error"
    )


@pytest.mark.usefixtures("seeded")
def test_the_scan_summary_is_cached_for_ten_seconds(
    make_client: Callable[..., TestClient], redis_client: redis.Redis, schema_name: str
) -> None:
    client = make_client(REDIS_URL=TEST_REDIS_URL)
    key = f"aws-inventory-discovery:{schema_name}:scans:summary"

    first = client.get(f"{API}/scans/summary").json()
    second = client.get(f"{API}/scans/summary").json()

    assert first == second
    assert 0 < redis_client.ttl(key) <= 10


@pytest.mark.usefixtures("seeded")
def test_a_cached_value_of_the_wrong_shape_is_a_miss(
    make_client: Callable[..., TestClient], redis_client: redis.Redis, schema_name: str
) -> None:
    client = make_client(REDIS_URL=TEST_REDIS_URL)
    redis_client.set(f"aws-inventory-discovery:{schema_name}:scans:summary", '{"nonsense": true}')

    response = client.get(f"{API}/scans/summary")

    assert response.status_code == 200
    assert response.json()["missingCount"] >= 0
