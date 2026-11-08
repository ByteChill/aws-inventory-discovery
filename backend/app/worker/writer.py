"""ResourceWriter: the only code that writes resources (scanner.md sections 7 and 8, DESIGN R4-R6).

Locks existing rows, overlays, redacts (the one redaction point), hashes, writes conditionally,
replaces relationships, and runs deletion logic. It never calls AWS: everything it knows about
the outside world arrives as an `Observation` or an explicit "gone" fact.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Any, Literal, Self

from sqlalchemy import ARRAY, Engine, Text, any_, bindparam, delete, func, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, col, select

from app.core.config import Settings, get_settings
from app.core.db import get_engine
from app.models.account import Account
from app.models.resource import Resource, ResourceRelation
from app.models.scan import ScanShard
from app.types.mapping import Path as KeyPath
from app.types.mapping import covered_paths, overlay, split_path
from app.types.redact import redact_configuration, redact_live_raw
from app.types.registry import TypeRegistry
from app.types.spec import ResourceTypeSpec
from app.worker.discovery import Observation

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
Key = tuple[str, str, str, str]  # account_id, aws_region, resource_type, resource_id
GoneOutcome = Literal["missing", "deleted", "unchanged", "stale", "not_found"]


@dataclass
class WriteCounts:
    new: int = 0
    updated: int = 0
    unchanged: int = 0  # same content hash: only the observation time moved
    stale: int = 0  # older than what the database has (R4)
    restored: int = 0  # missing or deleted resources seen again

    def add(self, other: WriteCounts) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass(frozen=True)
class ScopeOutcome:
    """Result of the deletion step for one (account, region, type) scope."""

    ran: bool  # false when the read was incomplete
    missing: int  # resources newly marked missing (first piece of evidence)
    guard: Literal["normal", "abnormal"]
    confirm_pending: int  # missing resources with first evidence: a confirm_missing task is due


def key_of(obs: Observation) -> Key:
    item = obs.item
    return (item.account_id, item.aws_region, item.resource_type, item.resource_id)


def _drop_path(node: Any, path: KeyPath) -> None:
    if isinstance(node, list):
        for child in node:
            _drop_path(child, path)
    elif isinstance(node, dict) and path:
        if len(path) == 1:
            node.pop(path[0], None)
        elif path[0] in node:
            _drop_path(node[path[0]], path[1:])


def content_hash(
    spec: ResourceTypeSpec,
    name: str | None,
    tags: list[dict[str, str]],
    configuration: dict[str, Any],
) -> str:
    """sha256 over name, tags and configuration without the fields that change by themselves."""
    stable = copy.deepcopy(configuration)
    for path in spec.volatile:
        _drop_path(stable, split_path(path))
    document = {
        "resourceName": name,
        "tags": sorted(tags, key=lambda t: (t["key"], t["value"])),
        "configuration": stable,
    }
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_access_status(credential_failed: bool, error_codes: Sequence[str | None]) -> str:
    """scanner.md section 10. `error_codes` has one entry per call: None for success."""
    if credential_failed:
        return "credential_error"
    denied = [code == "access_denied" for code in error_codes]
    if denied and all(denied):
        return "access_denied"
    if any(denied):
        return "partial"
    return "ok"


class ResourceWriter:
    def __init__(self, engine: Engine, registry: TypeRegistry, settings: Settings) -> None:
        self.engine = engine
        self.registry = registry
        self.settings = settings

    @classmethod
    def from_settings(cls, registry: TypeRegistry | None = None) -> Self:
        return cls(get_engine(), registry or TypeRegistry.from_directory(), get_settings())

    # --- write (scanner.md section 7) ---------------------------------------------------

    def write(
        self,
        observations: Iterable[Observation],
        *,
        task_id: int | None = None,
        scan_request_id: int | None = None,
    ) -> WriteCounts:
        """Write observations in transactions of at most 500."""
        total = WriteCounts()
        for chunk in batched(observations, BATCH_SIZE, strict=False):
            latest: dict[Key, Observation] = {}
            for obs in chunk:  # one row per key; the newest observation wins
                current = latest.get(key_of(obs))
                if current is None or obs.observed_at > current.observed_at:
                    latest[key_of(obs)] = obs
            with Session(self.engine) as db, db.begin():
                counts = self._apply(db, latest, task_id)
                if scan_request_id is not None and (counts.new or counts.updated):
                    db.connection().execute(
                        text(
                            "UPDATE t_inv_aws_scan_request SET counts = "
                            "(CASE WHEN jsonb_typeof(counts) = 'object' THEN counts ELSE '{}' END) "
                            "|| jsonb_build_object("
                            "'new', coalesce((counts->>'new')::int, 0) + :new, "
                            "'updated', coalesce((counts->>'updated')::int, 0) + :updated) "
                            "WHERE id = :id"
                        ),
                        {"new": counts.new, "updated": counts.updated, "id": scan_request_id},
                    )
            total.add(counts)
        return total

    def _apply(
        self, db: Session, batch: dict[Key, Observation], task_id: int | None
    ) -> WriteCounts:
        counts = WriteCounts()
        keys = sorted(batch)
        identity = (
            Resource.account_id,
            Resource.aws_region,
            Resource.resource_type,
            Resource.resource_id,
        )
        rows = db.exec(
            select(Resource)
            .where(tuple_(*identity).in_(keys))
            .order_by(*identity)
            .with_for_update()
        ).all()
        existing = {(r.account_id, r.aws_region, r.resource_type, r.resource_id): r for r in rows}

        relation_jobs: list[tuple[int, Observation, list[dict[str, Any]]]] = []
        new_rows: list[dict[str, Any]] = []
        new_values: dict[Key, dict[str, Any]] = {}
        new_obs: dict[Key, Observation] = {}
        for key in keys:
            obs, row = batch[key], existing.get(key)
            spec = self.registry.get(obs.resource_type)
            if row is None:
                values = self._values(spec, obs, None)
                new_values[key] = values
                new_rows.append(
                    {
                        **values,
                        "account_id": key[0],
                        "aws_region": key[1],
                        "resource_type": key[2],
                        "resource_id": key[3],
                        "status": "active",
                        "first_seen_at": obs.observed_at,
                        "last_changed_at": obs.observed_at,
                        "last_task_id": task_id,
                    }
                )
                new_obs[key] = obs
                continue
            if obs.observed_at <= row.observed_at:  # R4
                counts.stale += 1
                continue
            values = self._values(spec, obs, row)
            if row.status != "active":
                if row.status == "missing":
                    logger.info(
                        "missing_but_found account=%s region=%s type=%s id=%s",
                        key[0], key[1], key[2], key[3],
                    )  # fmt: skip
                row.status = "active"
                row.missing_since = row.absent_in_scan_at = row.deleted_at = None
                row.missing_checks = 0
                counts.restored += 1
            row.observed_at = obs.observed_at
            row.observed_source = obs.source
            row.last_task_id = task_id
            self._stamp_source_time(row, obs)
            if obs.live_raw is not None:
                row.live_raw = values["live_raw"]
            if values["content_hash"] == row.content_hash:
                counts.unchanged += 1
            else:
                for name in (
                    "arn",
                    "resource_name",
                    "tags",
                    "configuration",
                    "relationships",
                    "content_hash",
                ):
                    setattr(row, name, values[name])
                row.last_changed_at = obs.observed_at
                counts.updated += 1
                assert row.id is not None
                relation_jobs.append((row.id, obs, values["relationships"]))
            db.add(row)
        db.flush()

        if new_rows:
            inserted = db.exec(
                pg_insert(Resource)
                .values(new_rows)
                .on_conflict_do_nothing(
                    index_elements=["account_id", "aws_region", "resource_type", "resource_id"]
                )
                .returning(
                    col(Resource.id),
                    col(Resource.account_id),
                    col(Resource.aws_region),
                    col(Resource.resource_type),
                    col(Resource.resource_id),
                )
            ).all()
            done = set()
            for row_id, account, region, rtype, rid in inserted:
                key = (account, region, rtype, rid)
                done.add(key)
                relation_jobs.append((row_id, new_obs[key], new_values[key]["relationships"]))
            counts.new += len(done)
            lost_race = {k: o for k, o in new_obs.items() if k not in done}
            if lost_race:  # another writer inserted it first: take the update path
                counts.add(self._apply(db, lost_race, task_id))
        self._replace_relations(db, relation_jobs)
        return counts

    @staticmethod
    def _stamp_source_time(row: Resource, obs: Observation) -> None:
        if obs.source == "config":
            row.config_capture_time = obs.observed_at
        else:
            row.live_observed_at = obs.observed_at

    def _values(
        self, spec: ResourceTypeSpec, obs: Observation, row: Resource | None
    ) -> dict[str, Any]:
        """Overlay, then redact, then hash (the order the spec requires)."""
        item = obs.item
        configuration = item.configuration
        if obs.source != "config" and row is not None and row.configuration is not None:
            configuration = overlay(row.configuration, configuration, covered_paths(spec))
        configuration = redact_configuration(spec, configuration)
        live_raw = redact_live_raw(spec, obs.live_raw) if obs.live_raw is not None else None
        tags = [tag.model_dump() for tag in item.tags]
        if obs.relationships is not None:
            relationships = obs.relationships
        else:  # Cloud Control has none: keep what the database has
            relationships = list(row.relationships) if row is not None else []
        values: dict[str, Any] = {
            "arn": item.arn or "",
            "resource_name": item.resource_name,
            "tags": tags,
            "configuration": configuration,
            "relationships": relationships,
            "live_raw": live_raw,
            "observed_at": obs.observed_at,
            "observed_source": obs.source,
            "content_hash": content_hash(spec, item.resource_name, tags, configuration),
            "config_capture_time": obs.observed_at if obs.source == "config" else None,
            "live_observed_at": obs.observed_at if obs.source != "config" else None,
        }
        return values

    def _replace_relations(
        self, db: Session, jobs: list[tuple[int, Observation, list[dict[str, Any]]]]
    ) -> None:
        if not jobs:
            return
        db.exec(
            delete(ResourceRelation).where(
                col(ResourceRelation.from_resource_id).in_([job[0] for job in jobs])
            )
        )
        wanted: dict[tuple[str, str, str], tuple[str, str]] = {}  # (acct, region, type|id)
        entries: list[tuple[int, Observation, dict[str, Any]]] = []
        for row_id, obs, relationships in jobs:
            for rel in relationships:
                if rel.get("resourceType") and rel.get("resourceId"):
                    entries.append((row_id, obs, rel))
                    scope = (obs.item.account_id, obs.item.aws_region)
                    wanted[(*scope, f"{rel['resourceType']}|{rel['resourceId']}")] = (
                        rel["resourceType"],
                        rel["resourceId"],
                    )
        if not entries:
            return
        targets: dict[tuple[str, str, str, str], int] = {}
        for (account, region), group in _group_scopes(wanted).items():
            found = db.exec(
                select(Resource.resource_type, Resource.resource_id, Resource.id).where(
                    col(Resource.account_id) == account,
                    col(Resource.aws_region) == region,
                    tuple_(Resource.resource_type, Resource.resource_id).in_(group),
                )
            ).all()
            for rtype, rid, target_id in found:
                targets[(account, region, rtype, rid)] = target_id
        rows: dict[tuple[int, str, str, str], dict[str, Any]] = {}
        for row_id, obs, rel in entries:
            scope = (obs.item.account_id, obs.item.aws_region)
            rows[
                (row_id, rel["resourceType"], rel["resourceId"], rel.get("relationshipName", ""))
            ] = {
                "from_resource_id": row_id,
                "to_resource_type": rel["resourceType"],
                "to_resource_key": rel["resourceId"],
                "relation_name": rel.get("relationshipName", ""),
                "to_resource_id": targets.get((*scope, rel["resourceType"], rel["resourceId"])),
            }
        db.exec(pg_insert(ResourceRelation).values(list(rows.values())))

    # --- deletion (scanner.md section 8) ------------------------------------------------

    def config_cutoff(self, query_started_at: datetime) -> datetime:
        """Config lags: a resource seen after this was recorded too recently to call absent."""
        return query_started_at - timedelta(minutes=self.settings.CONFIG_LAG_GRACE_MINUTES)

    def finish_scope(
        self,
        *,
        account_id: str,
        region: str,
        resource_type: str,
        seen_ids: Collection[str],
        observed_at: datetime,
        complete: bool,
        cutoff: datetime | None = None,
    ) -> ScopeOutcome:
        """First piece of deletion evidence after a scan. Does nothing unless `complete`.

        `cutoff` defaults to `observed_at` (Cloud Control, native); for Config pass
        `config_cutoff(query_started_at)`.
        """
        if not complete:
            return ScopeOutcome(False, 0, "normal", 0)
        cutoff = cutoff or observed_at
        with Session(self.engine) as db, db.begin():
            db.exec(
                pg_insert(ScanShard)
                .values(account_id=account_id, region=region, resource_type=resource_type)
                .on_conflict_do_nothing()
            )
            shard = db.exec(
                select(ScanShard)
                .where(
                    col(ScanShard.account_id) == account_id,
                    col(ScanShard.region) == region,
                    col(ScanShard.resource_type) == resource_type,
                )
                .with_for_update()
            ).one()
            in_scope = (
                col(Resource.account_id) == account_id,
                col(Resource.aws_region) == region,
                col(Resource.resource_type) == resource_type,
            )
            unseen = ~(
                col(Resource.resource_id)
                == any_(bindparam("seen", list(seen_ids), type_=ARRAY(Text)))
            )
            candidates = list(
                db.exec(
                    select(Resource.id)
                    .where(
                        *in_scope,
                        col(Resource.status) == "active",
                        unseen,
                        col(Resource.observed_at) < cutoff,
                    )
                    .order_by(col(Resource.id))
                    .with_for_update()
                ).all()
            )
            active_total = db.exec(
                select(func.count())
                .select_from(Resource)
                .where(*in_scope, col(Resource.status) == "active")
            ).one()
            guard_settings = self.settings
            if (
                len(candidates) > guard_settings.MISSING_GUARD_MIN
                and len(candidates) / active_total > guard_settings.MISSING_GUARD_RATIO
                and shard.guard_state != "confirmed"
            ):
                shard.guard_state = "abnormal"
                shard.guard_detected_at = datetime.now(UTC)
                db.add(shard)
                return ScopeOutcome(True, 0, "abnormal", 0)

            if candidates:
                db.exec(
                    update(Resource)
                    .where(col(Resource.id).in_(candidates))
                    .values(
                        status="missing",
                        missing_since=observed_at,
                        absent_in_scan_at=cutoff,
                        missing_checks=0,
                    )
                )
            db.exec(  # missing before (e.g. a refresh) and still not seen: first evidence now
                update(Resource)
                .where(
                    *in_scope,
                    col(Resource.status) == "missing",
                    col(Resource.absent_in_scan_at).is_(None),
                    unseen,
                    col(Resource.observed_at) < cutoff,
                )
                .values(absent_in_scan_at=cutoff)
            )
            if shard.guard_state == "confirmed":
                shard.guard_state = "normal"
                db.add(shard)
            pending = db.exec(
                select(func.count())
                .select_from(Resource)
                .where(
                    *in_scope,
                    col(Resource.status) == "missing",
                    col(Resource.absent_in_scan_at).is_not(None),
                )
            ).one()
            return ScopeOutcome(True, len(candidates), "normal", pending)

    def record_gone(
        self,
        *,
        account_id: str,
        region: str,
        resource_type: str,
        resource_id: str,
        observed_at: datetime,
        task_id: int | None = None,
    ) -> GoneOutcome:
        """A by-ID read explicitly said the resource does not exist (second piece of evidence).

        active -> missing (no first evidence); missing with first evidence -> deleted. The
        observation must be newer than the row's `observed_at` (R4). Errors never reach here.
        """
        with Session(self.engine) as db, db.begin():
            row = db.exec(
                select(Resource)
                .where(
                    col(Resource.account_id) == account_id,
                    col(Resource.aws_region) == region,
                    col(Resource.resource_type) == resource_type,
                    col(Resource.resource_id) == resource_id,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return "not_found"
            if observed_at <= row.observed_at:
                return "stale"
            row.last_task_id = task_id
            if row.status == "active":
                row.status = "missing"
                row.missing_since = observed_at
                outcome: GoneOutcome = "missing"
            elif row.status == "missing" and row.absent_in_scan_at is not None:
                row.status = "deleted"
                row.deleted_at = observed_at
                row.observed_at = observed_at  # an older observation can no longer revive it
                outcome = "deleted"
            else:
                outcome = "unchanged"
            db.add(row)
            return outcome

    def record_check_failed(
        self, *, account_id: str, region: str, resource_type: str, resource_id: str
    ) -> None:
        """A by-ID read errored: the resource stays missing; count the failed check."""
        with Session(self.engine) as db, db.begin():
            db.exec(
                update(Resource)
                .where(
                    col(Resource.account_id) == account_id,
                    col(Resource.aws_region) == region,
                    col(Resource.resource_type) == resource_type,
                    col(Resource.resource_id) == resource_id,
                    col(Resource.status) == "missing",
                )
                .values(missing_checks=Resource.missing_checks + 1)
            )

    def confirm_guard(self, *, account_id: str, region: str, resource_type: str, by: str) -> None:
        """An administrator accepted an abnormal scope: the next scan may mark it missing."""
        with Session(self.engine) as db, db.begin():
            db.exec(
                update(ScanShard)
                .where(
                    col(ScanShard.account_id) == account_id,
                    col(ScanShard.region) == region,
                    col(ScanShard.resource_type) == resource_type,
                    col(ScanShard.guard_state) == "abnormal",
                )
                .values(guard_state="confirmed", guard_confirmed_by=by)
            )

    # --- account access status (scanner.md section 10) ------------------------------------

    def update_access_status(
        self,
        account_id: str,
        *,
        credential_failed: bool,
        error_codes: Sequence[str | None],
        message: str | None = None,
    ) -> str:
        status = compute_access_status(credential_failed, error_codes)
        now = datetime.now(UTC)
        with Session(self.engine) as db, db.begin():
            account = db.get(Account, account_id)
            if account is None:
                raise LookupError(f"unknown account {account_id}")
            account.access_status = status
            account.access_error = None if status == "ok" else message
            account.access_checked_at = now
            if status == "ok":
                account.last_scan_ok_at = now
            db.add(account)
        return status


def _group_scopes(
    wanted: dict[tuple[str, str, str], tuple[str, str]],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for (account, region, _), target in wanted.items():
        groups.setdefault((account, region), []).append(target)
    return groups
