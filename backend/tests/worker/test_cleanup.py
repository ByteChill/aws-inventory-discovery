from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.aws.account_registry import RegistryAccount
from app.core.config import Settings
from app.models import Account, AccountRegion, Resource, ScanRequest, ScanShard, ScanTask
from app.worker import cleanup, leases, queue
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
