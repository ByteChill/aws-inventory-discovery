"""Worker liveness without a database round trip: the heartbeat thread touches a file, and
`python -m app.cli healthcheck` (the container health check) looks at its age."""

import os
import time
from pathlib import Path

MAX_AGE_SECONDS = 60


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    os.utime(path)


def is_fresh(path: Path, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age < MAX_AGE_SECONDS
