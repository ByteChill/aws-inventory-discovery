import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from sqlalchemy import Engine, event, func, text
from sqlmodel import Session, select

from app.aws.account_registry import RegistryAccount
from app.core.config import Settings
from app.models import Account, AccountRegion, Resource, ScanRequest, ScanShard, ScanTask
from app.worker import cleanup, leases, queue, requests
from app.worker.scheduler import Scheduler, sync_accounts
from tests.worker.conftest import (
    IMAGES,
    PAYMENTS_DEV,
    PAYMENTS_PROD,
    REGION,
    TYPE,
    RegistryServer,
    claim_one,
    lease_scope,
    on_demand_shard,
    refresh,
)

# 03:10 UTC: inside the hour in which the scheduler runs the cleanup.
TODAY = datetime(2026, 11, 8, 3, 10, tzinfo=UTC)


def count(session: Session, model: type) -> int:
    session.expire_all()
    return session.exec(select(func.count()).select_from(model)).one()


def run_one_refresh(session: Session, resource_id: str, account_id: str, at: datetime) -> None:
    """Create a refresh at `at` and run it to completion at `at`."""
    refresh(session, resource_id, account_id, now=at)
    task = claim_one(session, at)
    queue.complete_task(session, task.id or 0, task.claim_token or "", now=at)


def test_the_scheduler_purges_finished_tasks_and_requests_after_their_retention(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
    registry: RegistryServer,
    accounts: list[RegistryAccount],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cleanup, "BATCH_SIZE", 1)  # force the batch loop to run several times
    run_one_refresh(session, "queue-old", PAYMENTS_DEV, TODAY - timedelta(days=100))
    run_one_refresh(session, "queue-mid", IMAGES, TODAY - timedelta(days=20))
    run_one_refresh(session, "queue-new", PAYMENTS_PROD, TODAY - timedelta(days=1))
    scheduler = Scheduler(engine, make_settings(SWEEP_ENABLED=False), registry.client())

    # Only the 03:00 hour triggers the cleanup, and only once a day.
    assert scheduler.tick(TODAY.replace(hour=12))
    assert (count(session, ScanTask), count(session, ScanRequest)) == (3, 3)
    assert scheduler.tick(TODAY)
    scheduler.lock.release()

    # Tasks go after 14 days, their requests after 90.
    assert [t.resource_id for t in session.exec(select(ScanTask))] == ["queue-new"]
    assert sorted((r.target or {})["resource_id"] for r in session.exec(select(ScanRequest))) == [
        "queue-mid",
        "queue-new",
    ]


@pytest.mark.parametrize("claim_again", [False, True])
def test_cleanup_keeps_a_task_retried_after_candidate_selection(
    engine: Engine,
    session: Session,
    accounts: list[RegistryAccount],
    claim_again: bool,
) -> None:
    old = TODAY - timedelta(days=20)
    refresh(session, "queue-retry", PAYMENTS_DEV, now=old)
    task = claim_one(session, old)
    queue.fail_task(session, task, "access_denied", "no access", retryable=False, now=old)
    retried = False
    connection = session.connection()

    def retry_after_selection(conn, cursor, statement, parameters, context, executemany):
        nonlocal retried
        if retried or not statement.startswith(
            "SELECT id FROM t_inv_aws_scan_task WHERE finished_at"
        ):
            return
        retried = True
        with Session(engine) as other:
            assert requests.retry_failed(other, task.scan_request_id, TODAY) == 1
            if claim_again:
                claimed = claim_one(other, TODAY)
                assert claimed.id == task.id

    event.listen(connection, "after_cursor_execute", retry_after_selection)
    try:
        assert cleanup._purge_tasks(session, TODAY - timedelta(days=14)) == 0
    finally:
        event.remove(connection, "after_cursor_execute", retry_after_selection)

    assert retried
    session.expire_all()
    kept = session.get_one(ScanTask, task.id)
    assert kept.status == ("running" if claim_again else "queued")
    assert kept.finished_at is None
    assert session.get_one(ScanRequest, task.scan_request_id).status == "running"


def resource(account_id: str, resource_id: str, status: str, deleted_at: datetime | None):
    # Only the retention-relevant columns matter here; the rows carry no AWS payload.
    return Resource(
        arn=f"arn:aws:sqs:{REGION}:{account_id}:{resource_id}",
        account_id=account_id,
        aws_region=REGION,
        resource_type=TYPE,
        resource_id=resource_id,
        observed_at=TODAY,
        observed_source="cloudcontrol",
        content_hash="-",
        status=status,
        deleted_at=deleted_at,
        first_seen_at=TODAY,
        last_changed_at=TODAY,
    )


def test_cleanup_purges_old_deleted_resources_and_long_inactive_accounts(
    session: Session,
    settings: Settings,
    registry: RegistryServer,
    accounts: list[RegistryAccount],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cleanup, "BATCH_SIZE", 2)
    session.add_all(
        [
            resource(IMAGES, "old-1", "deleted", TODAY - timedelta(days=40)),
            resource(IMAGES, "old-2", "deleted", TODAY - timedelta(days=31)),
            resource(IMAGES, "old-3", "deleted", TODAY - timedelta(days=45)),
            resource(IMAGES, "recent", "deleted", TODAY - timedelta(days=2)),
            resource(IMAGES, "alive", "active", None),
            resource(PAYMENTS_DEV, "orphan-1", "active", None),
            resource(PAYMENTS_DEV, "orphan-2", "active", None),
            resource(PAYMENTS_DEV, "orphan-3", "missing", None),
            AccountRegion(
                account_id=PAYMENTS_DEV,
                region=REGION,
                opt_in_status="opt-in-not-required",
                reachable=True,
                checked_at=TODAY,
            ),
        ]
    )
    session.commit()
    # PAYMENTS_DEV left the registry 40 days ago, IMAGES 5 days ago.
    without_dev = [a for a in accounts if a.account_id != PAYMENTS_DEV]
    sync_accounts(session, without_dev, TODAY - timedelta(days=40))
    sync_accounts(
        session, [a for a in without_dev if a.account_id != IMAGES], TODAY - timedelta(days=5)
    )
    on_demand_shard(session, TODAY)  # a shard task holds a lease on the account's scope
    task = claim_one(session, TODAY)
    assert leases.try_acquire(session, task, lease_scope(), TODAY) is leases.LeaseState.ACQUIRED

    result = cleanup.run_cleanup(session, settings, TODAY)

    assert (result.resources, result.accounts) == (3, 1)
    session.expire_all()
    assert sorted(r.resource_id for r in session.exec(select(Resource))) == ["alive", "recent"]
    assert sorted(a.account_id for a in session.exec(select(Account))) == sorted(
        [IMAGES, PAYMENTS_PROD]
    )
    assert count(session, AccountRegion) == 0
    assert count(session, ScanShard) == 0


def deactivate_account(session: Session, accounts: list[RegistryAccount]) -> None:
    """PAYMENTS_DEV left the registry 40 days ago; its active resources are still in the DB."""
    session.add(resource(PAYMENTS_DEV, "orphan-1", "active", None))
    session.commit()
    sync_accounts(
        session,
        [a for a in accounts if a.account_id != PAYMENTS_DEV],
        TODAY - timedelta(days=40),
    )


def wait_for_a_lock(db: psycopg.Connection, *markers: str, timeout: float = 10) -> None:
    """Wait until some backend is blocked on one of the statements whose text contains a marker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiting = [
            row[0] or ""
            for row in db.execute(
                "SELECT query FROM pg_stat_activity"
                " WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            ).fetchall()
        ]
        if any(marker in query for query in waiting for marker in markers):
            return
        time.sleep(0.01)
    pytest.fail(f"no backend waited for {markers}")


def test_reactivation_after_selection_keeps_the_account_and_its_resources(
    engine: Engine,
    session: Session,
    accounts: list[RegistryAccount],
) -> None:
    deactivate_account(session, accounts)
    reactivated = False

    def reactivate_after_selection(conn, cursor, statement, parameters, context, executemany):
        nonlocal reactivated
        if reactivated or not statement.lstrip().startswith(
            "SELECT account_id FROM t_inv_aws_account"
        ):
            return
        reactivated = True
        # The API's account sync commits while cleanup is between selecting the expired
        # accounts and deleting their data.
        with Session(engine) as other:
            sync_accounts(other, accounts, TODAY)

    connection = session.connection()
    event.listen(connection, "after_cursor_execute", reactivate_after_selection)
    try:
        purged = cleanup._purge_inactive_accounts(
            session,
            TODAY - timedelta(days=30),
        )
    finally:
        event.remove(connection, "after_cursor_execute", reactivate_after_selection)

    assert reactivated
    assert purged == 0
    session.expire_all()
    account = session.get_one(Account, PAYMENTS_DEV)
    assert account.active and account.inactive_since is None
    assert [r.resource_id for r in session.exec(select(Resource))] == ["orphan-1"]


def test_account_sync_holds_the_purge_off_a_reactivating_account(
    engine: Engine,
    session: Session,
    accounts: list[RegistryAccount],
) -> None:
    deactivate_account(session, accounts)
    syncing, resume = threading.Event(), threading.Event()
    synced = threading.Event()
    purged: list[int] = []
    errors: list[BaseException] = []

    def run_sync() -> None:
        try:
            with Session(engine) as own:
                connection = own.connection()

                def pause_after_upsert(conn, cursor, statement, parameters, context, executemany):
                    if "INSERT INTO t_inv_aws_account" in statement:
                        syncing.set()
                        resume.wait(10)

                event.listen(connection, "after_cursor_execute", pause_after_upsert)
                try:
                    sync_accounts(own, accounts, TODAY)
                finally:
                    event.remove(connection, "after_cursor_execute", pause_after_upsert)
            synced.set()
        except BaseException as exc:
            errors.append(exc)

    def run_cleanup() -> None:
        try:
            with Session(engine) as own:
                purged.append(
                    cleanup._purge_inactive_accounts(
                        own,
                        TODAY - timedelta(days=30),
                    )
                )
        except BaseException as exc:
            errors.append(exc)

    sync_thread = threading.Thread(target=run_sync)
    sync_thread.start()
    assert syncing.wait(10), "sync did not reach the upsert"
    cleanup_thread = threading.Thread(target=run_cleanup)
    cleanup_thread.start()
    # The sync holds the account lock, so cleanup leaves the account for the next run
    # instead of waiting for it or deleting it.
    cleanup_thread.join(10)
    assert not cleanup_thread.is_alive()
    resume.set()
    sync_thread.join(10)

    assert not sync_thread.is_alive()
    assert not errors
    assert synced.is_set()
    assert purged == [0]
    session.expire_all()
    account = session.get_one(Account, PAYMENTS_DEV)
    assert account.active and account.inactive_since is None
    assert [r.resource_id for r in session.exec(select(Resource))] == ["orphan-1"]


def test_cleanup_keeps_a_deleted_resource_restored_while_its_batch_waits(
    engine: Engine,
    session: Session,
    accounts: list[RegistryAccount],
    db: psycopg.Connection,
) -> None:
    session.add(resource(IMAGES, "restored", "deleted", TODAY - timedelta(days=40)))
    session.commit()
    purged: list[int] = []
    errors: list[BaseException] = []

    def run_cleanup() -> None:
        try:
            with Session(engine) as own:
                purged.append(cleanup._purge_deleted_resources(own, TODAY - timedelta(days=30)))
        except BaseException as exc:
            errors.append(exc)

    with Session(engine) as writer:
        # A later observation restores the resource, uncommitted: cleanup's batch has already
        # selected it as deleted and now waits for this row.
        writer.connection().execute(
            text(
                "UPDATE t_inv_aws_resource SET status = 'active', deleted_at = NULL"
                " WHERE resource_id = 'restored'"
            )
        )
        cleanup_thread = threading.Thread(target=run_cleanup)
        cleanup_thread.start()
        wait_for_a_lock(db, "DELETE FROM t_inv_aws_resource")
        writer.commit()
    cleanup_thread.join(10)

    assert not cleanup_thread.is_alive()
    assert not errors
    assert purged == [0]
    session.expire_all()
    restored = session.exec(select(Resource).where(Resource.resource_id == "restored")).one()
    assert restored.status == "active" and restored.deleted_at is None
