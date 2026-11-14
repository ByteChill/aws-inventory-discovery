"""The scheduler: runs in every worker, active in exactly one (DESIGN 4.4, scanner.md §5).

Leader election uses a Postgres session-level advisory lock held on a dedicated connection.
If that connection dies, the lock dies with it and another worker takes over on its next tick.
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import Connection, Engine, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.aws.account_registry import AccountRegistryClient, RegistryAccount, RegistryError
from app.core.config import Settings
from app.models import Account, ScanRequest
from app.models.scan import utcnow
from app.worker import cleanup, queue, requests
from app.worker.locks import SCHEDULER_LOCK_NAME, advisory_lock_key, lock_full_sweeps

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 60
CLEANUP_HOUR_UTC = 3


class AdvisoryLock:
    """A session-level advisory lock on its own connection. Not thread-safe: one owner."""

    def __init__(self, engine: Engine, key: int) -> None:
        self.engine = engine
        self.key = key
        self._connection: Connection | None = None

    @property
    def held(self) -> bool:
        return self._connection is not None

    def ensure(self) -> bool:
        """Return True while this instance holds the lock, acquiring it if it is free."""
        if self._connection is not None:
            try:
                self._connection.execute(text("SELECT 1"))
                return True
            except Exception:  # noqa: BLE001 - a dead connection means the lock is gone
                logger.warning("scheduler lock connection lost")
                self._drop()
        connection = self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": self.key}
        ).scalar_one()
        if acquired:
            self._connection = connection
            return True
        connection.close()
        return False

    def release(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
        except Exception:  # noqa: BLE001 - closing the connection releases the lock anyway
            logger.warning("could not unlock scheduler lock explicitly", exc_info=True)
        self._drop()

    def _drop(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                logger.debug("closing a dead lock connection failed", exc_info=True)


def sync_accounts(session: Session, accounts: list[RegistryAccount], now: datetime) -> None:
    """Make `t_inv_aws_account` match the registry (scanner.md §5.1 step 1): insert new
    accounts, refresh known ones (re-activating the returning), mark the absent inactive.

    Take the per-account purge lock before touching the snapshot, in key order. A cleanup
    that is already deleting an account therefore finishes first (and the insert below brings
    the account back if the registry still lists it); if the account is already active again,
    cleanup rechecks under the same lock and leaves its resources alone.
    """
    if not accounts:
        raise ValueError("refusing to sync an empty account list")
    schema = cleanup.schema_of(session)
    keys = sorted({cleanup.account_sync_key(schema, a.account_id) for a in accounts})
    # One round trip; the subquery keeps the ascending order the lock protocol relies on.
    session.connection().execute(
        text(
            "SELECT pg_advisory_xact_lock(k) FROM"
            " (SELECT unnest(CAST(:keys AS bigint[])) AS k ORDER BY 1) AS ordered"
        ),
        {"keys": keys},
    )
    fields = [
        {
            "account_id": a.account_id,
            "account_name": a.account_name,
            "account_type": a.account_type,
            "eim_id": a.eim_id,
            "eim_app": a.eim_app,
            "ba_id": a.ba_id,
            "active": True,
            "inactive_since": None,
            "registry_fetched_at": now,
        }
        for a in accounts
    ]
    statement = insert(Account.__table__).values(fields)  # ty: ignore[unresolved-attribute]
    session.connection().execute(
        statement.on_conflict_do_update(
            index_elements=["account_id"],
            set_={name: statement.excluded[name] for name in fields[0] if name != "account_id"},
        )
    )
    session.connection().execute(
        text("""
        UPDATE t_inv_aws_account SET active = false, inactive_since = :now
         WHERE active AND NOT (account_id = ANY(:ids))
        """),
        {"now": now, "ids": [a.account_id for a in accounts]},
    )
    session.commit()


class Scheduler:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        registry: AccountRegistryClient | None,
        lock: AdvisoryLock | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.registry = registry
        self.lock = lock or AdvisoryLock(
            engine, advisory_lock_key(SCHEDULER_LOCK_NAME, settings.DATABASE_SCHEMA)
        )
        self._last_cleanup_on = None

    @classmethod
    def from_settings(cls, engine: Engine, settings: Settings) -> Scheduler:
        registry = (
            AccountRegistryClient.from_settings(settings) if settings.ACCOUNT_REGISTRY_URL else None
        )
        return cls(engine, settings, registry)

    def tick(self, now: datetime | None = None) -> bool:
        """One scheduler pass. Returns False when another instance is the leader."""
        now = now or utcnow()
        if not self.lock.ensure():
            return False
        with Session(self.engine) as session:
            if self.settings.SWEEP_ENABLED:
                self._guarded("sweep", session, lambda: self.start_sweep_if_due(session, now))
            self._guarded("reaper", session, lambda: self.reap(session, now))
            if now.hour == CLEANUP_HOUR_UTC and self._last_cleanup_on != now.date():
                self._guarded("cleanup", session, lambda: self.purge(session, now))
        return True

    def run(self, stop: threading.Event, interval: float = TICK_INTERVAL_SECONDS) -> None:
        """Tick until `stop` is set, then give up leadership so another worker can take over."""
        try:
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:  # e.g. the database is briefly unreachable: try again next tick
                    logger.exception("scheduler tick failed")
                stop.wait(interval)
        finally:
            self.lock.release()

    def _sweep_due(self, session: Session, now: datetime) -> bool:
        last = session.exec(
            select(func.max(ScanRequest.created_at)).where(ScanRequest.trigger == "schedule")
        ).one()
        interval = timedelta(hours=self.settings.SWEEP_INTERVAL_HOURS)
        return last is None or now - last >= interval

    def start_sweep_if_due(self, session: Session, now: datetime) -> ScanRequest | None:
        if not self._sweep_due(session, now):
            return None
        return self._start_sweep(session, now, only_if_due=True)

    def start_sweep(self, session: Session, now: datetime) -> ScanRequest:
        """Refresh the account snapshot from the registry and create one sweep (§5.1).

        When the registry can't be read, the sweep runs on the existing snapshot, nobody is
        marked inactive, and the failure is recorded in the request's counts.
        """
        request = self._start_sweep(session, now, only_if_due=False)
        assert request is not None
        return request

    def _start_sweep(
        self, session: Session, now: datetime, *, only_if_due: bool
    ) -> ScanRequest | None:
        counts: dict[str, str] = {}
        try:
            if self.registry is None:
                raise RegistryError("ACCOUNT_REGISTRY_URL is not set")
            fetched = self.registry.fetch_accounts()
            if not fetched:
                # An empty list would mark every account inactive; treat it as a failure.
                raise RegistryError("registry returned no accounts")
        except RegistryError as exc:
            logger.error("account registry unavailable, using the existing snapshot: %s", exc)
            counts["registryError"] = str(exc)
        else:
            sync_accounts(session, fetched, now)
        # Full sweeps are created one at a time (shared with POST /scans all=true); look again
        # inside the lock, since another instance may have started one while we fetched.
        lock_full_sweeps(session, self.settings.DATABASE_SCHEMA)
        if only_if_due and not self._sweep_due(session, now):
            session.rollback()
            return None
        account_ids = list(session.exec(select(Account.account_id).where(col(Account.active))))
        request = requests.create_sweep_request(
            session,
            account_ids,
            trigger="schedule",
            priority=1,
            scope={"all": True},
            stagger_window=requests.stagger_window_seconds(self.settings),
            skip_in_flight=True,
            counts=counts,
            now=now,
        )
        session.commit()
        logger.info("sweep started", extra={"scan_request_id": request.id})
        return request

    def reap(self, session: Session, now: datetime) -> queue.ReapResult:
        result = queue.reap_lost_tasks(session, now)
        if result.requeued or result.failed:
            logger.warning(
                "reaped tasks of lost workers",
                extra={"requeued": result.requeued, "failed": result.failed},
            )
        return result

    def purge(self, session: Session, now: datetime) -> cleanup.CleanupResult:
        result = cleanup.run_cleanup(session, self.settings, now)
        self._last_cleanup_on = now.date()
        return result

    @staticmethod
    def _guarded(name: str, session: Session, step: Callable[[], object]) -> None:
        # One failing step (say, the registry or a cleanup batch) must not stop the others.
        try:
            step()
        except Exception:
            session.rollback()
            logger.exception("scheduler step %s failed", name)
