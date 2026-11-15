"""The scheduler: runs in every worker, active in exactly one (DESIGN 4.4, scanner.md §5).

Leader election uses a Postgres session-level advisory lock held on a dedicated connection.
If that connection dies, the lock dies with it and another worker takes over on its next tick.
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import Connection, Engine, func, text
from sqlmodel import Session, col, select

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


class Scheduler:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        lock: AdvisoryLock | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.lock = lock or AdvisoryLock(
            engine, advisory_lock_key(SCHEDULER_LOCK_NAME, settings.DATABASE_SCHEMA)
        )
        self._last_cleanup_on = None

    @classmethod
    def from_settings(cls, engine: Engine, settings: Settings) -> Scheduler:
        return cls(engine, settings)

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
        """Create one sweep from the local account master data (§5.1)."""
        request = self._start_sweep(session, now, only_if_due=False)
        assert request is not None
        return request

    def _start_sweep(
        self, session: Session, now: datetime, *, only_if_due: bool
    ) -> ScanRequest | None:
        # Full sweeps are created one at a time (shared with POST /scans all=true); look again
        # inside the lock, since another instance may have started one meanwhile.
        lock_full_sweeps(session, self.settings.DATABASE_SCHEMA)
        if only_if_due and not self._sweep_due(session, now):
            session.rollback()
            return None
        account_ids = list(
            session.exec(
                select(Account.account_id).where(col(Account.active), col(Account.scan_enabled))
            )
        )
        request = requests.create_sweep_request(
            session,
            account_ids,
            trigger="schedule",
            priority=1,
            scope={"all": True},
            stagger_window=requests.stagger_window_seconds(self.settings),
            skip_in_flight=True,
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
