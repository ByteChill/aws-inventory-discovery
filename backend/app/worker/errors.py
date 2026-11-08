"""Errors that task handlers raise to tell the worker loop how a task ended (scanner.md §9)."""

# error_code -> retryable. `worker_lost` is only ever set by the reaper.
ERROR_CODES: dict[str, bool] = {
    "access_denied": False,
    "credential_error": True,
    "throttled": True,
    "endpoint_unreachable": True,
    "region_disabled": False,
    "unsupported": False,
    "registry_error": True,
    "worker_lost": False,
    "internal": True,
}


class TaskError(Exception):
    """A task failed. `error_code` decides whether it is worth retrying."""

    def __init__(self, error_code: str, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = ERROR_CODES[error_code] if retryable is None else retryable


class TaskStopped(Exception):
    """The worker is shutting down; the task goes back to the queue without an attempt bump."""


class TaskCancelled(Exception):
    """The scan request was cancelled; stop without any deletion judgement."""
