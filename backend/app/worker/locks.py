"""Postgres advisory locks shared by the worker and the API's service functions."""

import hashlib

from sqlalchemy import text
from sqlmodel import Session

SCHEDULER_LOCK_NAME = "aws-inventory-discovery:scheduler"
FULL_SWEEP_LOCK_NAME = "aws-inventory-discovery:full-sweep"


def advisory_lock_key(name: str, schema: str) -> int:
    """Derive the bigint advisory-lock key for `name` within one database schema: the first
    8 bytes of SHA-256 over "<name>:<schema>", read as a big-endian *signed* 64-bit integer
    (Postgres advisory keys are signed bigints). Advisory locks are global per database, so
    the schema (DATABASE_SCHEMA) is part of the key: deployments or test runs that share a
    Postgres but use different schemas never contend. Deterministic, so every process of one
    deployment computes the same key without any shared configuration."""
    return int.from_bytes(
        hashlib.sha256(f"{name}:{schema}".encode()).digest()[:8], "big", signed=True
    )


def lock_full_sweeps(session: Session, schema: str) -> None:
    """Serialise every path that creates a full sweep (`POST /scans` with all=true and the
    scheduler) until the session's transaction ends. Check for an existing sweep and insert
    the new one only after this call, in the same transaction, and commit promptly."""
    session.connection().execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": advisory_lock_key(FULL_SWEEP_LOCK_NAME, schema)},
    )
