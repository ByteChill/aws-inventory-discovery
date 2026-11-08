"""The worker process: claim threads, a heartbeat thread and the leader-elected scheduler.

Shutdown follows scanner.md §11: on SIGTERM stop claiming; running handlers stop at their
next checkpoint and their tasks go back to the queue without an attempt bump; the scheduler
gives up the advisory lock; threads still running after 60 seconds are abandoned to the reaper.
"""

import logging
import os
import random
import signal
import socket
import threading
import time
import uuid
from collections.abc import Callable
from types import FrameType

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.db import make_engine
from app.core.logging import log_context
from app.models import ScanTask
from app.worker import leases, queue
from app.worker.errors import TaskCancelled, TaskError, TaskLost, TaskStopped
from app.worker.handlers import HANDLERS, Handler, TaskContext
from app.worker.scheduler import Scheduler

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30
POLL_INTERVAL_SECONDS = 2.0
SHUTDOWN_GRACE_SECONDS = 60


class Worker:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        *,
        handlers: dict[str, Handler] | None = None,
        scheduler: Scheduler | None = None,
        worker_id: str | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.handlers = HANDLERS if handlers is None else handlers
        self.scheduler = scheduler
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.stop_event = threading.Event()
        self.context = TaskContext(self.new_session, self.worker_id, self.stop_event)
        self._running: dict[int, ScanTask] = {}
        self._running_lock = threading.Lock()
        self._heartbeat_done = threading.Event()
        self._claimers: list[threading.Thread] = []
        self._helpers: list[threading.Thread] = []

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Worker:
        settings = settings or get_settings()
        engine = make_engine(
            settings.DATABASE_URL,
            settings.DATABASE_SCHEMA,
            pool_size=settings.WORKER_THREADS + 4,
            max_overflow=4,
        )
        return cls(engine, settings, scheduler=Scheduler.from_settings(engine, settings))

    def new_session(self) -> Session:
        # expire_on_commit=False: a claimed task must stay readable after its transaction ends.
        return Session(self.engine, expire_on_commit=False)

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        threads = max(self.settings.WORKER_THREADS, 1)
        on_demand = min(self.settings.WORKER_ONDEMAND_THREADS, threads)
        for index in range(threads):
            max_priority = 0 if index < on_demand else 1
            thread = threading.Thread(
                target=self._claim_loop,
                args=(max_priority,),
                name=f"claim-{index}",
                daemon=True,
            )
            self._claimers.append(thread)
        self._helpers.append(
            threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True)
        )
        if self.scheduler is not None:
            self._helpers.append(
                threading.Thread(
                    target=self.scheduler.run,
                    args=(self.stop_event,),
                    name="scheduler",
                    daemon=True,
                )
            )
        for thread in [*self._claimers, *self._helpers]:
            thread.start()
        logger.info(
            "worker started",
            extra={"worker_id": self.worker_id, "threads": threads, "on_demand_threads": on_demand},
        )

    def stop(self) -> None:
        """Stop claiming and ask running handlers to stop at their next checkpoint."""
        self.stop_event.set()

    def join(self, timeout: float = SHUTDOWN_GRACE_SECONDS) -> bool:
        """Wait for the threads to finish; True when they all did within `timeout` seconds."""
        deadline = time.monotonic() + timeout
        for thread in self._claimers:
            thread.join(max(deadline - time.monotonic(), 0))
        self._heartbeat_done.set()  # keep heartbeating until the last handler is done
        for thread in self._helpers:
            thread.join(max(deadline - time.monotonic(), 0))
        stragglers = [t.name for t in [*self._claimers, *self._helpers] if t.is_alive()]
        if stragglers:
            logger.warning("gave up on threads at shutdown", extra={"threads": stragglers})
        return not stragglers

    def run(self) -> int:
        """Run until SIGTERM/SIGINT, then shut down gracefully. Main thread only."""

        def on_signal(signum: int, _frame: FrameType | None) -> None:
            logger.info("received %s, shutting down", signal.Signals(signum).name)
            self.stop()

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        self.start()
        self.stop_event.wait()
        clean = self.join()
        self.engine.dispose()
        return 0 if clean else 1

    # -- threads -----------------------------------------------------------------------------

    def _claim_loop(self, max_priority: int) -> None:
        while not self.stop_event.is_set():
            try:
                with self.new_session() as session:
                    task = queue.claim_task(
                        session,
                        self.worker_id,
                        per_account_concurrency=self.settings.PER_ACCOUNT_CONCURRENCY,
                        max_priority=max_priority,
                    )
            except Exception:  # e.g. the database is briefly unreachable
                logger.exception("claiming a task failed")
                task = None
            if task is None:
                self.stop_event.wait(self.poll_interval * random.uniform(0.5, 1.5))
                continue
            self._run_task(task)

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_done.wait(self.heartbeat_interval):
            with self._running_lock:
                claims = {i: t.claim_token or "" for i, t in self._running.items()}
            if not claims:
                continue
            task_ids = list(claims)
            try:
                with self.new_session() as session:
                    owned = queue.heartbeat(session, claims)
                    leases.renew_leases(session, {i: claims[i] for i in owned})
            except Exception:
                logger.exception("heartbeat failed")
                continue
            if lost := set(task_ids) - owned:
                logger.warning(
                    "tasks no longer owned by this worker", extra={"tasks": sorted(lost)}
                )

    # -- one task ----------------------------------------------------------------------------

    def _run_task(self, task: ScanTask) -> None:
        assert task.id is not None
        with self._running_lock:
            self._running[task.id] = task
        try:
            with log_context(
                task_id=task.id,
                scan_request_id=task.scan_request_id,
                account_id=task.account_id,
                region=task.region,
                resource_type=task.resource_type,
            ):
                self._execute(task)
        except Exception:
            # The task stays `running` until the reaper finds its heartbeat gone.
            logger.exception("could not settle task")
        finally:
            with self._running_lock:
                self._running.pop(task.id, None)

    def _execute(self, task: ScanTask) -> None:
        assert task.id is not None
        task_id = task.id
        handler = self.handlers.get(task.kind)
        try:
            if handler is None:
                raise TaskError(
                    "internal", f"no handler for task kind {task.kind!r}", retryable=False
                )
            stats = handler(task, self.context)
        except TaskLost:
            # Reaped and claimed again: the new owner settles the task and its leases. Any
            # write from here would only get in its way.
            logger.warning("task ownership lost, abandoning the run")
        except TaskStopped:
            logger.info("task interrupted by shutdown, requeueing")
            self._release(task, success=None)
            self._settle(lambda s: queue.requeue_task(s, task))
        except TaskCancelled:
            logger.info("task stopped, scan request cancelled")
            self._release(task, success=None)
            self._settle(
                lambda s: queue.finish_task(
                    s, task_id, "cancelled", claim_token=task.claim_token or ""
                )
            )
        except TaskError as exc:
            logger.warning("task failed: %s", exc.message, extra={"error_code": exc.error_code})
            self._release(task, success=False, error_code=exc.error_code)
            self._fail(task, exc.error_code, exc.message, retryable=exc.retryable)
        except NotImplementedError as exc:
            logger.error("task kind not implemented: %s", exc)
            self._release(task, success=None)
            self._fail(task, "internal", str(exc), retryable=False)
        except Exception as exc:
            logger.exception("task crashed")
            self._release(task, success=False, error_code="internal")
            self._fail(task, "internal", f"{type(exc).__name__}: {exc}", retryable=True)
        else:
            self._release(task, success=True)
            self._settle(lambda s: queue.complete_task(s, task_id, task.claim_token or "", stats))

    def _release(
        self, task: ScanTask, *, success: bool | None, error_code: str | None = None
    ) -> None:
        """Safety net: free whatever leases the handler left behind."""
        assert task.id is not None
        with self.new_session() as session:
            leases.release_task_leases(session, task, success=success, error_code=error_code)

    def _fail(self, task: ScanTask, error_code: str, message: str, *, retryable: bool) -> None:
        self._settle(lambda s: queue.fail_task(s, task, error_code, message, retryable=retryable))

    def _settle(self, action: Callable[[Session], object]) -> None:
        with self.new_session() as session:
            action(session)
