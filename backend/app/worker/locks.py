"""Postgres advisory locks shared by the worker and the API's service functions."""

import hashlib

from sqlalchemy import text
from sqlmodel import Session

FULL_SWEEP_LOCK_NAME = "aws-inventory-discovery:full-sweep"


def advisory_lock_key(name: str) -> int:
    """Derive the bigint advisory-lock key from a name: the first 8 bytes of its SHA-256,
    read as a big-endian *signed* 64-bit integer (Postgres advisory keys are signed bigints).
    Deterministic, so every process computes the same key without any shared configuration."""
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


FULL_SWEEP_LOCK_KEY = advisory_lock_key(FULL_SWEEP_LOCK_NAME)


def lock_full_sweeps(session: Session) -> None:
    """Serialise every path that creates a full sweep (`POST /scans` with all=true and the
    scheduler) until the session's transaction ends. Check for an existing sweep and insert
    the new one only after this call, in the same transaction, and commit promptly."""
    session.connection().execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": FULL_SWEEP_LOCK_KEY}
    )
