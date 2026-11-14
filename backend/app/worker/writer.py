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
from app.models.scan import ScanShard, ScanTask
from app.types.mapping import Path as KeyPath
from app.types.mapping import covered_paths, overlay, split_path
from app.types.redact import redact_configuration, redact_live_raw
from app.types.registry import TypeRegistry
from app.types.spec import ResourceTypeSpec
from app.worker.discovery import Observation, Source
from app.worker.errors import TaskLost
from app.worker.leases import Scope, takes_lease

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
Key = tuple[str, str, str, str]  # account_id, aws_region, resource_type, resource_id
ACCESS_TASK_KINDS = ("sweep_account", "config_group", "shard", "confirm_missing")
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


def add_task_stats(db: Session, task_id: int | None, **deltas: int) -> None:
    """Add counts to `scan_task.stats` inside the caller's transaction, so what a task reports
    is exactly what its committed writes did, however the task ends (a failure, a retry)."""
    deltas = {name: int(n) for name, n in deltas.items() if n}
    if task_id is None or not deltas:
        return
    db.connection().execute(
        text(
            "UPDATE t_inv_aws_scan_task SET stats = (CASE WHEN jsonb_typeof(stats) = 'object'"
            " THEN stats ELSE '{}'::jsonb END) || ("
            " SELECT jsonb_object_agg(d.key,"
            " coalesce((stats ->> d.key)::bigint, 0) + d.value::bigint)"
            " FROM jsonb_each_text(CAST(:deltas AS jsonb)) AS d) WHERE id = :id"
        ),
        {"deltas": json.dumps(deltas), "id": task_id},
    )


def lock_worker_claim(db: Session, task: ScanTask, scopes: Collection[Scope] = ()) -> None:
    """Fence a mutation until commit, taking scope locks before the task lock.

    Use UPDATE locks immediately: the writer also updates task stats, so a shared lock
    followed by an upgrade could deadlock concurrent transactions of the same claim.
    Resource refreshes have no scope lease; their task lock still fences every write.
    """
    if takes_lease(task):
        for scope in sorted(set(scopes)):
            shard = (
                db.connection()
                .execute(
                    text("""
                SELECT lease_task_id, lease_token FROM t_inv_aws_scan_shard
                 WHERE account_id = :a AND region = :r AND resource_type = :t
                   FOR UPDATE
                """),
                    {"a": scope.account_id, "r": scope.region, "t": scope.resource_type},
                )
                .first()
            )
            if (
                shard is None
                or shard.lease_task_id != task.id
                or shard.lease_token != task.claim_token
            ):
                raise TaskLost
    owned = (
        db.connection()
        .execute(
            text("""
        SELECT 1 FROM t_inv_aws_scan_task
         WHERE id = :id AND status = 'running' AND claim_token = :token FOR UPDATE
        """),
            {"id": task.id, "token": task.claim_token or ""},
        )
        .first()
    )
    if owned is None:
        raise TaskLost


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
        task: ScanTask | None = None,
        scan_request_id: int | None = None,
    ) -> WriteCounts:
        """Write observations in transactions of at most 500.

        Workers must supply their claimed task. Omitting it is reserved for imports/seeding.
        """
        task_id = task.id if task is not None else None
        total = WriteCounts()
        for chunk in batched(observations, BATCH_SIZE, strict=False):
            latest: dict[Key, Observation] = {}
            for obs in chunk:  # one row per key; the newest observation wins
                current = latest.get(key_of(obs))
                if current is None or obs.observed_at > current.observed_at:
                    latest[key_of(obs)] = obs
            with Session(self.engine) as db, db.begin():
                if task is not None:
                    lock_worker_claim(db, task, [Scope(*key[:3]) for key in latest])
                counts = self._apply(db, latest, task_id)
                add_task_stats(
                    db,
                    task_id,
                    new=counts.new,
                    updated=counts.updated,
                    unchanged=counts.unchanged,
                    restored=counts.restored,
                )
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
        inserted_keys: list[tuple[int, str, str, str, str]] = []
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
                # relationships are outside the hash and are not a resource change (DESIGN 5.4):
                # keep them current without counting an update or moving last_changed_at
                if _canonical(values["relationships"]) != _canonical(row.relationships):
                    row.relationships = values["relationships"]
                    assert row.id is not None
                    relation_jobs.append((row.id, obs, values["relationships"]))
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
                inserted_keys.append((row_id, account, region, rtype, rid))
                relation_jobs.append((row_id, new_obs[key], new_values[key]["relationships"]))
            counts.new += len(done)
            lost_race = {k: o for k, o in new_obs.items() if k not in done}
            if lost_race:  # another writer inserted it first: take the update path
                counts.add(self._apply(db, lost_race, task_id))
        self._replace_relations(db, relation_jobs)
        self._resolve_dangling_relations(db, inserted_keys)
        return counts

    @staticmethod
    def _resolve_dangling_relations(
        db: Session, inserted: list[tuple[int, str, str, str, str]]
    ) -> None:
        """Late resolution: relations written before their target existed (`to_resource_id`
        NULL) point at the new resource now. Same account; same region, unless the target is
        global."""
        if not inserted:
            return
        db.connection().execute(
            text("""
            UPDATE t_inv_aws_resource_relation rel SET to_resource_id = n.id
              FROM unnest(CAST(:ids AS bigint[]), CAST(:accounts AS text[]),
                          CAST(:regions AS text[]), CAST(:types AS text[]),
                          CAST(:keys AS text[])) AS n(id, account_id, region, type, key),
                   t_inv_aws_resource f
             WHERE rel.to_resource_id IS NULL
               AND rel.to_resource_type = n.type AND rel.to_resource_key = n.key
               AND f.id = rel.from_resource_id AND f.account_id = n.account_id
               AND (f.aws_region = n.region OR n.region = 'global')
            """),
            {
                "ids": [i[0] for i in inserted],
                "accounts": [i[1] for i in inserted],
                "regions": [i[2] for i in inserted],
                "types": [i[3] for i in inserted],
                "keys": [i[4] for i in inserted],
            },
        )

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
        task: ScanTask | None = None,
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
            if task is not None:
                lock_worker_claim(db, task, [Scope(account_id, region, resource_type)])
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
                    select(Resource.id, Resource.resource_id, Resource.arn)
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
            candidate_ids = [row[0] for row in candidates]
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
                shard.candidate_count = len(candidates)
                shard.candidate_ids = [
                    {"resourceId": resource_id, "arn": arn} for _, resource_id, arn in candidates
                ]
                db.add(shard)
                return ScopeOutcome(True, 0, "abnormal", 0)

            if candidates:
                db.exec(
                    update(Resource)
                    .where(col(Resource.id).in_(candidate_ids))
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
            if shard.guard_state in ("confirmed", "abnormal"):
                shard.guard_state = "normal"
                shard.candidate_count = None
                shard.candidate_ids = None
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
            add_task_stats(db, task.id if task is not None else None, missing=len(candidates))
            return ScopeOutcome(True, len(candidates), "normal", pending)

    def record_gone(
        self,
        *,
        account_id: str,
        region: str,
        resource_type: str,
        resource_id: str,
        observed_at: datetime,
        task: ScanTask | None = None,
        source: Source = "cloudcontrol",
    ) -> GoneOutcome:
        """A by-ID read explicitly said the resource does not exist (second piece of evidence).

        active -> missing (no first evidence); missing with first evidence -> deleted. The
        observation must be newer than the row's `observed_at` (R4), and every accepted one advances
        `observed_at` / `observed_source` under that same guard, so an older in-flight listing
        can no longer restore the resource. Errors never reach here.
        """
        task_id = task.id if task is not None else None
        with Session(self.engine) as db, db.begin():
            if task is not None:
                lock_worker_claim(db, task, [Scope(account_id, region, resource_type)])
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
            row.observed_at = observed_at
            row.observed_source = source
            if row.status == "active":
                row.status = "missing"
                row.missing_since = observed_at
                outcome: GoneOutcome = "missing"
            elif row.status == "missing" and row.absent_in_scan_at is not None:
                row.status = "deleted"
                row.deleted_at = observed_at
                outcome = "deleted"
            else:
                outcome = "unchanged"
            db.add(row)
            db.flush()
            add_task_stats(db, task_id, missing=outcome == "missing", deleted=outcome == "deleted")
            return outcome

    def record_check_failed(
        self,
        *,
        account_id: str,
        region: str,
        resource_type: str,
        resource_id: str,
        task: ScanTask | None = None,
    ) -> None:
        """A by-ID read errored: the resource stays missing; count the failed check."""
        with Session(self.engine) as db, db.begin():
            if task is not None:
                lock_worker_claim(db, task, [Scope(account_id, region, resource_type)])
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
        with Session(self.engine) as db, db.begin():
            return self._store_access(db, account_id, credential_failed, error_codes, message)

    def refresh_access_status(self, account_id: str, scan_request_id: int) -> str:
        """Recompute the account's `access_status` from every settled task of the account in
        the scan request. Call it after a task is settled: the account row is locked for the
        read and the write, so two tasks finishing together each see the other and the last
        one stores the combined result."""
        with Session(self.engine) as db, db.begin():
            account = db.exec(
                select(Account).where(col(Account.account_id) == account_id).with_for_update()
            ).one_or_none()
            if account is None:
                raise LookupError(f"unknown account {account_id}")
            tasks = db.exec(
                select(ScanTask)
                .where(
                    ScanTask.scan_request_id == scan_request_id,
                    ScanTask.account_id == account_id,
                    col(ScanTask.kind).in_(ACCESS_TASK_KINDS),
                    col(ScanTask.status).in_(("succeeded", "failed")),
                )
                .order_by(col(ScanTask.id))
            ).all()
            # A sweep_account that planned children made no call worth counting: they report.
            tasks = [
                t
                for t in tasks
                if not (
                    t.kind == "sweep_account"
                    and t.status == "succeeded"
                    and (t.stats or {}).get("tasks")
                )
            ]
            if not tasks:  # nothing has reported yet: no evidence either way
                return account.access_status
            codes = [t.error_code if t.status == "failed" else None for t in tasks]
            failed = [t.error_message for t in tasks if t.status == "failed"]
            return self._store_access(
                db,
                account_id,
                "credential_error" in codes,
                codes,
                failed[-1] if failed else None,
            )

    @staticmethod
    def _store_access(
        db: Session,
        account_id: str,
        credential_failed: bool,
        error_codes: Sequence[str | None],
        message: str | None,
    ) -> str:
        status = compute_access_status(credential_failed, error_codes)
        now = datetime.now(UTC)
        account = db.exec(
            select(Account).where(col(Account.account_id) == account_id).with_for_update()
        ).one_or_none()
        if account is None:
            raise LookupError(f"unknown account {account_id}")
        account.access_status = status
        account.access_error = None if status == "ok" else message
        account.access_checked_at = now
        if status == "ok":
            account.last_scan_ok_at = now
        db.add(account)
        return status


def _canonical(relationships: list[dict[str, Any]] | None) -> str:
    """Relationships compared as a set: Config does not promise an order."""
    return json.dumps(
        sorted(json.dumps(r, sort_keys=True) for r in relationships or []), separators=(",", ":")
    )


def _group_scopes(
    wanted: dict[tuple[str, str, str], tuple[str, str]],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for (account, region, _), target in wanted.items():
        groups.setdefault((account, region), []).append(target)
    return groups
