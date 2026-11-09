import threading
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.config import Settings
from app.models import Account, ScanRequest, ScanTask
from app.worker import queue, requests
from app.worker.locks import FULL_SWEEP_LOCK_NAME, SCHEDULER_LOCK_NAME, advisory_lock_key
from app.worker.scheduler import Scheduler
from tests.worker.conftest import (
    IMAGES,
    NOW,
    PAYMENTS_DEV,
    PAYMENTS_PROD,
    RegistryServer,
    claim_one,
)

NEW_ACCOUNT = "999900001111"
PINNED_SCHEDULER_KEY = -7187737924124084656
PINNED_FULL_SWEEP_KEY = -4756006676216944716


def sweeps(session: Session) -> list[ScanRequest]:
    session.expire_all()
    return list(session.exec(select(ScanRequest).where(ScanRequest.trigger == "schedule")))


def sweep_tasks(session: Session, request: ScanRequest) -> list[ScanTask]:
    return list(session.exec(select(ScanTask).where(ScanTask.scan_request_id == request.id)))


def test_sweep_syncs_accounts_and_staggers_one_task_per_account(
    session: Session, scheduler: Scheduler
) -> None:
    assert scheduler.tick(NOW)

    (request,) = sweeps(session)
    assert (request.priority, request.status, request.scope) == (1, "queued", {"all": True})
    accounts = {a.account_id: a for a in session.exec(select(Account))}
    assert set(accounts) == {PAYMENTS_PROD, IMAGES, PAYMENTS_DEV}
    assert accounts[IMAGES].eim_app == "golden-images"
    assert accounts[PAYMENTS_DEV].active

    window = requests.stagger_window_seconds(scheduler.settings)
    assert window == 6 * 3600 - 30 * 60
    tasks = sweep_tasks(session, request)
    assert {t.account_id for t in tasks} == set(accounts)
    for task in tasks:
        offset = task.not_before - NOW
        assert offset == requests.stagger_offset(task.account_id, window)
        assert timedelta(0) <= offset < timedelta(seconds=window)

    # Not due again until the interval has passed; then busy accounts are skipped.
    assert scheduler.tick(NOW + timedelta(minutes=1))
    assert len(sweeps(session)) == 1
    for _ in range(2):
        task = claim_one(session, NOW + timedelta(hours=5, minutes=31))
        queue.complete_task(session, task.id or 0, task.claim_token or "", now=NOW)
    assert scheduler.tick(NOW + timedelta(hours=6))

    _, second = sweeps(session)
    assert len(sweep_tasks(session, second)) == 2
    assert second.counts == {"accounts": 2, "skippedAccounts": 1}


def test_only_one_scheduler_leads_at_a_time(
    engine: Engine, session: Session, settings: Settings, registry: RegistryServer
) -> None:
    # The key is derived from the name and the schema, so every instance of one deployment
    # computes the same one while deployments sharing a cluster do not collide.
    assert advisory_lock_key(SCHEDULER_LOCK_NAME, "inventory") == PINNED_SCHEDULER_KEY
    assert advisory_lock_key(FULL_SWEEP_LOCK_NAME, "inventory") == PINNED_FULL_SWEEP_KEY
    assert advisory_lock_key(SCHEDULER_LOCK_NAME, "other") != PINNED_SCHEDULER_KEY
    assert Scheduler(engine, settings, None).lock.key == advisory_lock_key(
        SCHEDULER_LOCK_NAME, settings.DATABASE_SCHEMA
    )
    leader = Scheduler(engine, settings, registry.client())
    standby = Scheduler(engine, settings, registry.client())

    assert leader.tick(NOW)
    assert not standby.tick(NOW + timedelta(hours=7))
    assert len(sweeps(session)) == 1

    leader.lock.release()
    assert standby.tick(NOW + timedelta(hours=7))
    assert len(sweeps(session)) == 2
    standby.lock.release()


def test_disabled_sweeps_create_nothing(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
    registry: RegistryServer,
) -> None:
    scheduler = Scheduler(engine, make_settings(SWEEP_ENABLED=False), registry.client())

    assert scheduler.tick(NOW)

    assert sweeps(session) == []
    scheduler.lock.release()


def test_accounts_missing_from_the_registry_become_inactive_and_return_active(
    session: Session, scheduler: Scheduler, registry: RegistryServer
) -> None:
    scheduler.tick(NOW)
    queue.cancel_request(session, sweeps(session)[0].id or 0, NOW)

    registry.file = "accounts_changed.json"
    changed_at = NOW + timedelta(hours=6)
    scheduler.tick(changed_at)

    session.expire_all()
    accounts = {a.account_id: a for a in session.exec(select(Account))}
    gone = accounts[PAYMENTS_DEV]
    assert (gone.active, gone.inactive_since) == (False, changed_at)
    assert accounts[PAYMENTS_PROD].account_name == "payments-prod-renamed"
    assert accounts[NEW_ACCOUNT].active
    request = sweeps(session)[1]
    assert {t.account_id for t in sweep_tasks(session, request)} == {
        PAYMENTS_PROD,
        IMAGES,
        NEW_ACCOUNT,
    }
    queue.cancel_request(session, request.id or 0, changed_at)

    registry.file = "accounts.json"
    scheduler.tick(NOW + timedelta(hours=12))

    session.expire_all()
    back = session.get_one(Account, PAYMENTS_DEV)
    assert (back.active, back.inactive_since) == (True, None)
    assert not session.get_one(Account, NEW_ACCOUNT).active


def test_registry_failure_sweeps_the_existing_snapshot_and_marks_nobody_inactive(
    session: Session, scheduler: Scheduler, registry: RegistryServer
) -> None:
    scheduler.tick(NOW)
    queue.cancel_request(session, sweeps(session)[0].id or 0, NOW)
    registry.always_fail = 503
    registry.request_count = 0

    scheduler.tick(NOW + timedelta(hours=6))

    assert registry.request_count == 4  # first try + 3 retries with backoff
    request = sweeps(session)[1]
    assert "registryError" in (request.counts or {})
    assert {t.account_id for t in sweep_tasks(session, request)} == {
        PAYMENTS_PROD,
        IMAGES,
        PAYMENTS_DEV,
    }
    session.expire_all()
    assert all(a.active for a in session.exec(select(Account)))


def test_concurrent_scheduler_instances_start_only_one_sweep(
    engine: Engine, settings: Settings, registry: RegistryServer, session: Session
) -> None:
    schedulers = [Scheduler(engine, settings, registry.client()) for _ in range(4)]
    barrier = threading.Barrier(len(schedulers))

    def start(scheduler: Scheduler) -> None:
        with Session(engine) as own:
            barrier.wait()
            scheduler.start_sweep_if_due(own, NOW)

    threads = [threading.Thread(target=start, args=(s,)) for s in schedulers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(sweeps(session)) == 1
