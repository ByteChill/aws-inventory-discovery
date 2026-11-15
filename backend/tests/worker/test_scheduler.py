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
    claim_one,
)

PINNED_SCHEDULER_KEY = -7187737924124084656
PINNED_FULL_SWEEP_KEY = -4756006676216944716


def sweeps(session: Session) -> list[ScanRequest]:
    session.expire_all()
    return list(session.exec(select(ScanRequest).where(ScanRequest.trigger == "schedule")))


def sweep_tasks(session: Session, request: ScanRequest) -> list[ScanTask]:
    return list(session.exec(select(ScanTask).where(ScanTask.scan_request_id == request.id)))


def test_sweep_staggers_one_task_per_active_account(
    session: Session, scheduler: Scheduler, accounts
) -> None:
    assert scheduler.tick(NOW)

    (request,) = sweeps(session)
    assert (request.priority, request.status, request.scope) == (1, "queued", {"all": True})
    by_id = {a.account_id: a for a in session.exec(select(Account))}
    assert set(by_id) == {PAYMENTS_PROD, IMAGES, PAYMENTS_DEV}
    assert by_id[IMAGES].eim_app == "golden-images"
    assert by_id[PAYMENTS_DEV].active

    window = requests.stagger_window_seconds(scheduler.settings)
    assert window == 6 * 3600 - 30 * 60
    tasks = sweep_tasks(session, request)
    assert {t.account_id for t in tasks} == set(by_id)
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
    engine: Engine, session: Session, settings: Settings
) -> None:
    # The key is derived from the name and the schema, so every instance of one deployment
    # computes the same one while deployments sharing a cluster do not collide.
    assert advisory_lock_key(SCHEDULER_LOCK_NAME, "inventory") == PINNED_SCHEDULER_KEY
    assert advisory_lock_key(FULL_SWEEP_LOCK_NAME, "inventory") == PINNED_FULL_SWEEP_KEY
    assert advisory_lock_key(SCHEDULER_LOCK_NAME, "other") != PINNED_SCHEDULER_KEY
    assert Scheduler(engine, settings).lock.key == advisory_lock_key(
        SCHEDULER_LOCK_NAME, settings.DATABASE_SCHEMA
    )
    leader = Scheduler(engine, settings)
    standby = Scheduler(engine, settings)

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
) -> None:
    scheduler = Scheduler(engine, make_settings(SWEEP_ENABLED=False))

    assert scheduler.tick(NOW)

    assert sweeps(session) == []
    scheduler.lock.release()


def test_full_sweep_skips_scan_disabled_accounts(
    session: Session, scheduler: Scheduler, accounts
) -> None:
    disabled = session.get_one(Account, PAYMENTS_DEV)
    disabled.scan_enabled = False
    session.add(disabled)
    session.commit()

    assert scheduler.tick(NOW)

    request = sweeps(session)[0]
    assert {t.account_id for t in sweep_tasks(session, request)} == {
        PAYMENTS_PROD,
        IMAGES,
    }


def test_concurrent_scheduler_instances_start_only_one_sweep(
    engine: Engine, settings: Settings, session: Session, accounts
) -> None:
    schedulers = [Scheduler(engine, settings) for _ in range(4)]
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
