import threading
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, event, text
from sqlmodel import Session, select

from app.aws.account_registry import RegistryAccount
from app.core.config import Settings
from app.models import ScanRequest, ScanTask
from app.worker import queue, requests
from app.worker.scheduler import Scheduler
from tests.worker.conftest import (
    IMAGES,
    NOW,
    PAYMENTS_DEV,
    PAYMENTS_PROD,
    REGION,
    TYPE,
    claim_one,
    refresh,
    task_state,
)


def tasks_of(session: Session, request: ScanRequest) -> list[ScanTask]:
    return list(session.exec(select(ScanTask).where(ScanTask.scan_request_id == request.id)))


def test_on_demand_scan_creates_one_priority_zero_sweep_per_account(
    session: Session, settings: Settings, accounts: list[RegistryAccount]
) -> None:
    spec = requests.ScanSpec(
        account_ids=[PAYMENTS_DEV, IMAGES],
        aws_regions=[REGION],
        resource_types=[TYPE],
    )

    created = requests.create_scan(session, spec, settings, caller_id="dashboard", now=NOW)

    assert created.created
    assert created.request.trigger == "api"
    assert created.request.priority == 0
    assert created.request.caller_id == "dashboard"
    assert created.request.scope == {
        "all": False,
        "accountIds": sorted([PAYMENTS_DEV, IMAGES]),
        "awsRegions": [REGION],
        "resourceTypes": [TYPE],
    }
    tasks = tasks_of(session, created.request)
    assert {t.account_id for t in tasks} == {PAYMENTS_DEV, IMAGES}
    assert {(t.kind, t.priority, t.status, t.not_before) for t in tasks} == {
        ("sweep_account", 0, "queued", NOW)
    }


def test_scan_of_too_many_or_unknown_accounts_is_rejected(
    session: Session,
    make_settings: Callable[..., Settings],
    accounts: list[RegistryAccount],
) -> None:
    ids = [a.account_id for a in accounts]
    with pytest.raises(requests.ScopeTooLarge):
        requests.create_scan(
            session,
            requests.ScanSpec(account_ids=ids[:3]),
            make_settings(MAX_SCAN_ACCOUNTS=2),
        )
    with pytest.raises(requests.UnknownAccounts) as unknown:
        requests.create_scan(
            session,
            requests.ScanSpec(account_ids=[ids[0], "999999999999"]),
            make_settings(),
        )

    assert unknown.value.account_ids == ["999999999999"]
    assert session.exec(select(ScanRequest)).all() == []


def test_idempotency_key_returns_the_original_request_or_conflicts(
    session: Session, settings: Settings, accounts: list[RegistryAccount]
) -> None:
    first = requests.create_scan(
        session,
        requests.ScanSpec(account_ids=[PAYMENTS_DEV, IMAGES]),
        settings,
        idempotency_key="key-1",
        now=NOW,
    )
    # Same body, different order and duplicates: still the same body.
    again = requests.create_scan(
        session,
        requests.ScanSpec(account_ids=[IMAGES, PAYMENTS_DEV, IMAGES]),
        settings,
        idempotency_key="key-1",
        now=NOW,
    )

    assert again.request.id == first.request.id
    assert not again.created
    with pytest.raises(requests.IdempotencyConflict):
        requests.create_scan(
            session,
            requests.ScanSpec(account_ids=[PAYMENTS_DEV]),
            settings,
            idempotency_key="key-1",
        )
    assert len(session.exec(select(ScanRequest)).all()) == 1


def test_scan_of_all_accounts_joins_a_sweep_that_is_already_running(
    session: Session,
    settings: Settings,
    scheduler: Scheduler,
    accounts: list[RegistryAccount],
) -> None:
    scheduled = scheduler.start_sweep(session, NOW)

    joined = requests.create_scan(session, requests.ScanSpec(all_accounts=True), settings, now=NOW)

    assert joined.request.id == scheduled.id
    assert not joined.created


def test_scan_of_all_accounts_starts_at_once_and_is_shared_while_it_runs(
    session: Session, settings: Settings, accounts: list[RegistryAccount]
) -> None:
    first = requests.create_scan(session, requests.ScanSpec(all_accounts=True), settings, now=NOW)
    second = requests.create_scan(session, requests.ScanSpec(all_accounts=True), settings, now=NOW)

    assert first.created
    assert first.request.priority == 0
    assert {t.not_before for t in tasks_of(session, first.request)} == {NOW}
    assert len(tasks_of(session, first.request)) == len(accounts)
    assert second.request.id == first.request.id

    queue.cancel_request(session, first.request.id or 0, NOW)
    third = requests.create_scan(session, requests.ScanSpec(all_accounts=True), settings, now=NOW)
    assert third.created
    assert third.request.id != first.request.id


def test_refreshing_a_resource_shares_the_in_flight_task(session: Session) -> None:
    first = requests.request_resource_refresh(
        session, requests.ResourceKey(PAYMENTS_DEV, REGION, TYPE, "queue-1"), now=NOW
    )
    second = requests.request_resource_refresh(
        session, requests.ResourceKey(PAYMENTS_DEV, REGION, TYPE, "queue-1"), now=NOW
    )
    other = refresh(session, "queue-2")

    assert first.created
    assert (first.task.priority, first.task.kind) == (0, "resource")
    assert not second.created
    assert second.task.id == first.task.id
    assert other.id != first.task.id
    assert len(session.exec(select(ScanRequest)).all()) == 2  # the shared call made no request

    # Once the refresh has finished, a new call starts a new one.
    for _ in range(2):
        running = claim_one(session)
        queue.complete_task(session, running.id or 0, running.claim_token or "", now=NOW)
    again = requests.request_resource_refresh(
        session, requests.ResourceKey(PAYMENTS_DEV, REGION, TYPE, "queue-1"), now=NOW
    )
    assert again.created
    assert again.task.id != first.task.id


def test_retry_failed_requeues_only_failed_tasks_of_an_unfinished_request(
    session: Session, settings: Settings, accounts: list[RegistryAccount]
) -> None:
    created = requests.create_scan(
        session,
        requests.ScanSpec(account_ids=[PAYMENTS_DEV, PAYMENTS_PROD]),
        settings,
        now=NOW,
    )
    bad, good = claim_one(session), claim_one(session)
    queue.fail_task(session, bad, "access_denied", "no access", retryable=False, now=NOW)
    queue.complete_task(session, good.id or 0, good.claim_token or "", now=NOW)
    session.refresh(created.request)
    assert created.request.status == "partial"

    requeued = requests.retry_failed(session, created.request.id or 0, NOW)

    assert requeued == 1
    retried = task_state(session, bad)
    assert (retried.status, retried.attempt, retried.error_code) == ("queued", 0, None)
    assert task_state(session, good).status == "succeeded"
    session.refresh(created.request)
    assert created.request.status == "running"
    assert created.request.finished_at is None

    queue.cancel_request(session, created.request.id or 0, NOW)
    with pytest.raises(requests.InvalidRequestState):
        requests.retry_failed(session, created.request.id or 0, NOW)


def test_cancellation_winning_during_retry_rolls_back_requeued_tasks(
    engine: Engine,
    session: Session,
    settings: Settings,
    accounts: list[RegistryAccount],
) -> None:
    created = requests.create_scan(
        session,
        requests.ScanSpec(account_ids=[PAYMENTS_DEV, PAYMENTS_PROD]),
        settings,
        now=NOW,
    )
    failed = claim_one(session)
    queue.fail_task(session, failed, "access_denied", "no access", retryable=False, now=NOW)
    request_id = created.request.id or 0
    connection = session.connection()
    cancelled = False

    def cancel_after_requeue(conn, cursor, statement, parameters, context, executemany):
        nonlocal cancelled
        if cancelled or not statement.startswith("UPDATE t_inv_aws_scan_task SET"):
            return
        cancelled = True
        with Session(engine) as other:
            other.connection().execute(text("SET LOCAL lock_timeout = '2s'"))
            result = queue.cancel_request(other, request_id, NOW)
            assert result is not None and result.status == "cancelled"

    event.listen(connection, "after_cursor_execute", cancel_after_requeue)
    try:
        with pytest.raises(requests.InvalidRequestState, match="cancelled"):
            requests.retry_failed(session, request_id, NOW)
    finally:
        event.remove(connection, "after_cursor_execute", cancel_after_requeue)

    assert cancelled
    session.expire_all()
    assert session.get_one(ScanRequest, request_id).status == "cancelled"
    assert task_state(session, failed).status == "failed"
    assert not session.exec(
        select(ScanTask).where(ScanTask.scan_request_id == request_id, ScanTask.status == "queued")
    ).all()


def test_concurrent_all_account_scans_share_one_full_sweep(
    engine: Engine, settings: Settings, accounts: list[RegistryAccount]
) -> None:
    callers = 8
    barrier = threading.Barrier(callers)
    results: list[requests.CreatedScan] = []

    def call() -> None:
        with Session(engine, expire_on_commit=False) as own:
            barrier.wait()
            results.append(
                requests.create_scan(own, requests.ScanSpec(all_accounts=True), settings)
            )

    threads = [threading.Thread(target=call) for _ in range(callers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == callers
    assert sum(created.created for created in results) == 1
    assert len({created.request.id for created in results}) == 1
    with Session(engine) as check:
        sweeps = check.exec(select(ScanRequest).where(ScanRequest.trigger == "api")).all()
        assert len(sweeps) == 1
