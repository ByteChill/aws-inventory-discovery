"""Task handlers: one function per task kind, registered in the explicit `HANDLERS` dict.

A handler takes the scope lease, drives `Discovery` into `ResourceWriter` inside it, and
returns its stats (or None). It signals the outcome by returning or raising:

- `TaskError(code, message)`: the task failed; the code decides whether it is retried
- `TaskStopped` / `TaskCancelled`: raised by `TaskContext.checkpoint`
- any other exception: failed with `internal`

A handler that settles the task itself (`leases.acquire_or_settle` skipped or parked it)
simply returns; the worker's own completion then finds the task no longer running.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from app.models import ScanTask
from app.worker import queue
from app.worker.errors import TaskCancelled, TaskStopped


@dataclass(frozen=True)
class TaskContext:
    """What a handler may use besides the task itself."""

    new_session: Callable[[], Session]
    worker_id: str
    stop: threading.Event

    def checkpoint(self, task: ScanTask) -> None:
        """Call after every page of work. Raises when the worker is shutting down
        (`TaskStopped`) or when the scan request was cancelled (`TaskCancelled`); the handler
        must then stop without any deletion judgement and let the exception propagate."""
        if self.stop.is_set():
            raise TaskStopped
        with self.new_session() as session:
            if queue.is_cancelled(session, task.scan_request_id):
                raise TaskCancelled


type Handler = Callable[[ScanTask, TaskContext], dict[str, Any] | None]


def handle_sweep_account(task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
    raise NotImplementedError("sweep_account needs credentials and Discovery: implemented in M4")


def handle_config_group(task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
    raise NotImplementedError("config_group needs Discovery and the Writer: implemented in M4")


def handle_shard(task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
    raise NotImplementedError("shard needs Discovery and the Writer: implemented in M4")


def handle_resource(task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
    raise NotImplementedError("resource needs Discovery and the Writer: implemented in M4")


def handle_confirm_missing(task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
    raise NotImplementedError("confirm_missing needs Discovery and the Writer: implemented in M4")


HANDLERS: dict[str, Handler] = {
    "sweep_account": handle_sweep_account,
    "config_group": handle_config_group,
    "shard": handle_shard,
    "resource": handle_resource,
    "confirm_missing": handle_confirm_missing,
}
