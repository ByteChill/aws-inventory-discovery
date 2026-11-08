"""Task handlers: one method per task kind of `Scanner`, exposed through `Scanner.handlers()`.

A handler takes the scope lease, drives `Discovery` into `ResourceWriter` inside it, and
returns its stats (or None). It signals the outcome by returning or raising:

- `TaskError(code, message)`: the task failed; the code decides whether it is retried
- `TaskStopped` / `TaskCancelled` / `TaskLost`: raised by `TaskContext.checkpoint`
- any other exception: failed with `internal`

A handler that settles the task itself (`leases.acquire_or_settle` skipped or parked it)
simply returns; the worker's own completion then finds the task no longer running.

Everything a scan reads is consumed inside the lease (the worker's heartbeat thread keeps
renewing it), and only a complete read drives the deletion step.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

import boto3
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, col, select

from app.aws import config_source
from app.aws.config_source import RecorderState
from app.aws.credentials import CredentialError, CredentialService
from app.aws.errors import ScanError, classify
from app.aws.regions import ENABLED, NoDescribeRegionsRegion, describe_regions
from app.aws.session import make_client, make_session
from app.core.config import Settings
from app.models import AccountRegion, Resource, ScanRequest, ScanTask
from app.models.scan import utcnow
from app.types.hooks import TYPE_HOOKS, Gone, Hooks
from app.types.mapping import MappingError
from app.types.registry import TypeRegistry, validate_hooks
from app.worker import leases, queue, requests
from app.worker.discovery import AWS_ERRORS, Discovery, Observation, plan_sources
from app.worker.errors import TaskCancelled, TaskError, TaskLost, TaskStopped
from app.worker.writer import BATCH_SIZE, ResourceWriter, WriteCounts

logger = logging.getLogger(__name__)

GLOBAL_REGION = "global"  # the scope region of global types (Route 53 hosted zones)
GLOBAL_HOME = "us-east-1"  # where global types are read
CONFIRM_CHECK_STEP = 20  # checkpoint every so many by-ID reads


@dataclass(frozen=True)
class TaskContext:
    """What a handler may use besides the task itself."""

    new_session: Callable[[], Session]
    worker_id: str
    stop: threading.Event

    def checkpoint(self, task: ScanTask) -> None:
        """Call after every page of work. Raises when the worker is shutting down
        (`TaskStopped`), when this worker no longer owns the task (`TaskLost`: it was reaped
        and claimed again) or when every scan request that needs the task was cancelled
        (`TaskCancelled`; a task shared with a live request keeps going). The handler must
        then stop without any deletion judgement and without writing anything else, and let
        the exception propagate."""
        if self.stop.is_set():
            raise TaskStopped
        with self.new_session() as session:
            queue.assert_owned(session, task)
            if not queue.is_needed(session, task):
                raise TaskCancelled


type Handler = Callable[[ScanTask, TaskContext], dict[str, Any] | None]


def client_region(region: str | None) -> str:
    """The region whose endpoints serve a scope: global types are read from us-east-1."""
    assert region is not None
    return GLOBAL_HOME if region == GLOBAL_REGION else region


def task_error(exc: BaseException) -> TaskError:
    error = classify(exc)
    return TaskError(error.code, error.message)


class Scanner:
    """The five task handlers and what they share: settings, type registry, writer and the
    credential service."""

    def __init__(
        self,
        settings: Settings,
        registry: TypeRegistry,
        writer: ResourceWriter,
        credentials: CredentialService,
        hooks: dict[str, Hooks] | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.writer = writer
        self.credentials = credentials
        self.hooks = TYPE_HOOKS if hooks is None else hooks

    @classmethod
    def from_settings(cls, engine: Engine, settings: Settings) -> Self:
        registry = TypeRegistry.from_directory()
        validate_hooks(registry, TYPE_HOOKS)
        return cls(
            settings,
            registry,
            ResourceWriter(engine, registry, settings),
            CredentialService.from_settings(settings),
        )

    def handlers(self) -> dict[str, Handler]:
        return {
            "sweep_account": self._tracked(self.sweep_account),
            "config_group": self._tracked(self.config_group),
            "shard": self._tracked(self.shard),
            "resource": self.resource,
            "confirm_missing": self._tracked(self.confirm_missing),
        }

    # -- plumbing ----------------------------------------------------------------------------

    def _aws(self, task: ScanTask) -> boto3.Session:
        try:
            return make_session(self.credentials.credentials_for(task.account_id))
        except CredentialError as exc:
            raise TaskError("credential_error", str(exc)) from exc

    def _discovery(self, session: boto3.Session, task: ScanTask, region: str) -> Discovery:
        return Discovery(
            session,
            task.account_id,
            client_region(region),
            self.registry,
            self.hooks,
            item_region=region,
        )

    def _tracked(self, handler: Handler) -> Handler:
        """Update the account's `access_status` (scanner.md §10) after every task that talks
        to the account: from this request's finished tasks plus this task's own outcome."""

        def run(task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
            try:
                stats = handler(task, ctx)
            except TaskError as exc:
                self._update_access(task, ctx, exc.error_code, exc.message)
                raise
            # A sweep that planned children says nothing about access: they report it.
            if task.kind != "sweep_account" or not (stats or {}).get("tasks"):
                self._update_access(task, ctx, None, None)
            return stats

        return run

    def _update_access(
        self, task: ScanTask, ctx: TaskContext, code: str | None, message: str | None
    ) -> None:
        with ctx.new_session() as session:
            rows = session.exec(
                select(ScanTask.kind, ScanTask.status, ScanTask.error_code, ScanTask.error_message)
                .where(
                    ScanTask.scan_request_id == task.scan_request_id,
                    ScanTask.account_id == task.account_id,
                    ScanTask.id != task.id,
                    col(ScanTask.status).in_(("succeeded", "failed")),
                )
                .order_by(col(ScanTask.id))
            ).all()
        # a sweep_account that succeeded only planned work: it made no call worth counting
        rows = [row for row in rows if row[0] != "sweep_account" or row[1] == "failed"]
        codes = [error_code if status == "failed" else None for _, status, error_code, _ in rows]
        codes.append(code)
        failed = [message for _, status, _, message in rows if status == "failed"]
        message = message or (failed[-1] if failed else None)
        try:
            self.writer.update_access_status(
                task.account_id,
                credential_failed="credential_error" in codes,
                error_codes=codes,
                message=message,
            )
        except LookupError:
            logger.warning("access status of an unknown account not updated")

    def _scope_of(self, task: ScanTask) -> ScanRequest:
        with Session(self.writer.engine) as session:
            return session.get_one(ScanRequest, task.scan_request_id)

    @staticmethod
    def _checkpointed(
        observations: Iterable[Observation], task: ScanTask, ctx: TaskContext
    ) -> Iterator[Observation]:
        """Check in between write batches, so a stop or cancel is noticed mid-write."""
        for index, obs in enumerate(observations):
            if index and index % BATCH_SIZE == 0:
                ctx.checkpoint(task)
            yield obs

    def _request_confirmation(
        self, ctx: TaskContext, task: ScanTask, region: str, resource_type: str
    ) -> None:
        """Queue one confirm_missing task for the scope unless one is already waiting."""
        with ctx.new_session() as session:
            waiting = session.exec(
                select(ScanTask.id).where(
                    ScanTask.kind == "confirm_missing",
                    ScanTask.account_id == task.account_id,
                    ScanTask.region == region,
                    ScanTask.resource_type == resource_type,
                    ScanTask.status == "queued",
                )
            ).first()
            if waiting is None:
                requests.add_tasks(
                    session,
                    task.scan_request_id,
                    [requests.NewTask("confirm_missing", task.account_id, region, resource_type)],
                )

    @staticmethod
    def _release(
        ctx: TaskContext, task: ScanTask, scope: leases.Scope, error: ScanError | None
    ) -> None:
        with ctx.new_session() as session:
            leases.release_lease(
                session,
                task,
                scope,
                success=error is None,
                error_code=error.code if error else None,
            )

    @staticmethod
    def _stats(counts: WriteCounts, missing: int = 0, deleted: int = 0) -> dict[str, Any]:
        return {
            "new": counts.new,
            "updated": counts.updated,
            "unchanged": counts.unchanged,
            "restored": counts.restored,
            "missing": missing,
            "deleted": deleted,
        }

    # -- sweep_account (§6.1) ----------------------------------------------------------------

    def sweep_account(self, task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
        aws = self._aws(task)
        request = self._scope_of(task)
        scope = request.scope or {}
        on_demand = leases.is_on_demand(task)
        wanted_regions = set(scope.get("awsRegions") or [])
        wanted_types = set(scope.get("resourceTypes") or []) or None
        try:
            described = describe_regions(aws, self.settings.REACHABLE_REGIONS)
        except AWS_ERRORS as exc:
            raise task_error(exc) from exc
        except NoDescribeRegionsRegion as exc:
            raise TaskError("internal", str(exc), retryable=False) from exc
        now = utcnow()
        self._save_regions(task.account_id, described, now)
        scannable = sorted(
            region
            for region, opt_in in described.items()
            if region in self.settings.REACHABLE_REGIONS
            and opt_in in ENABLED
            and (not wanted_regions or region in wanted_regions)
        )

        planned: list[requests.NewTask] = []
        for region in scannable:
            ctx.checkpoint(task)
            disc = self._discovery(aws, task, region)
            recorder = RecorderState()
            if not on_demand:  # on-demand scans never read Config
                recorder = self._detect_recorder(disc, task.account_id, region)
            plan = plan_sources(
                self.registry, recorder, on_demand=on_demand, type_names=wanted_types
            )
            config_types = 0
            for type_name, source in plan.items():
                is_global = self.registry.get(type_name).global_
                if is_global and region != GLOBAL_HOME:
                    continue  # planned once, from us-east-1
                if source == "config":
                    config_types += 1
                else:
                    scope_region = GLOBAL_REGION if is_global else region
                    planned.append(
                        requests.NewTask(
                            "shard", task.account_id, scope_region, type_name, None, source
                        )
                    )
            if config_types:
                planned.append(
                    requests.NewTask("config_group", task.account_id, region, None, None, "config")
                )
        created = self._add_children(ctx, task, planned)
        return {"regions": len(scannable), "tasks": created}

    def _save_regions(self, account_id: str, described: dict[str, str], now: datetime) -> None:
        rows = [
            {
                "account_id": account_id,
                "region": region,
                "opt_in_status": opt_in,
                "reachable": region in self.settings.REACHABLE_REGIONS,
                "checked_at": now,
            }
            for region, opt_in in described.items()
        ]
        statement = pg_insert(AccountRegion.__table__).values(rows)  # ty: ignore[unresolved-attribute]
        with Session(self.writer.engine) as session, session.begin():
            session.connection().execute(
                statement.on_conflict_do_update(
                    index_elements=["account_id", "region"],
                    set_={
                        name: statement.excluded[name]
                        for name in ("opt_in_status", "reachable", "checked_at")
                    },
                )
            )

    def _detect_recorder(self, disc: Discovery, account_id: str, region: str) -> RecorderState:
        """The region's Config recorder, saved on the region row. A failure means "plan Cloud
        Control for everything": the shards then report the real error themselves."""
        try:
            recorder = disc.recorder()
        except AWS_ERRORS as exc:
            logger.warning(
                "config recorder could not be read: %s", classify(exc).message,
                extra={"region": region},
            )  # fmt: skip
            return RecorderState()
        with Session(self.writer.engine) as session, session.begin():
            row = session.get(AccountRegion, (account_id, region))
            if row is not None:
                row.config_recorder = recorder.as_json()
                session.add(row)
        return recorder

    def _add_children(
        self, ctx: TaskContext, task: ScanTask, planned: list[requests.NewTask]
    ) -> int:
        """Add the planned tasks, minus those an earlier run of this task already added."""
        with ctx.new_session() as session:
            existing = {
                (t.kind, t.region, t.resource_type)
                for t in session.exec(
                    select(ScanTask).where(
                        ScanTask.scan_request_id == task.scan_request_id,
                        ScanTask.account_id == task.account_id,
                        col(ScanTask.kind).in_(("config_group", "shard")),
                    )
                )
            }
            fresh = [p for p in planned if (p.kind, p.region, p.resource_type) not in existing]
            if fresh:
                requests.add_tasks(session, task.scan_request_id, fresh)
        return len(fresh)

    # -- config_group (§6.2) -----------------------------------------------------------------

    def config_group(self, task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
        assert task.region is not None
        aws = self._aws(task)
        disc = self._discovery(aws, task, task.region)
        try:
            recorder = disc.recorder()
        except AWS_ERRORS as exc:
            raise task_error(exc) from exc
        types = sorted(
            type_name
            for type_name, source in plan_sources(self.registry, recorder).items()
            if source == "config"
            and (task.region == GLOBAL_HOME or not self.registry.get(type_name).global_)
        )
        if not types:
            return {"types": 0}
        sweep = disc.sweep_config(types)
        ctx.checkpoint(task)
        total = WriteCounts()
        missing = skipped = 0
        error = None if sweep.complete else (sweep.error or ScanError("internal", "incomplete"))
        for type_name in types:
            region = GLOBAL_REGION if self.registry.get(type_name).global_ else task.region
            scope = leases.Scope(task.account_id, region, type_name)
            with ctx.new_session() as session:
                state = leases.try_acquire(session, task, scope)
            if state is leases.LeaseState.LOST:
                raise TaskLost
            if state is not leases.LeaseState.ACQUIRED:
                skipped += 1  # busy or scanned moments ago: whoever has it covers it
                continue
            observations = sweep.by_type.get(type_name, [])
            total.add(
                self.writer.write(self._checkpointed(observations, task, ctx), task_id=task.id)
            )
            outcome = self.writer.finish_scope(
                account_id=task.account_id,
                region=region,
                resource_type=type_name,
                seen_ids=[o.item.resource_id for o in observations],
                observed_at=sweep.query_started_at,
                complete=sweep.complete,
                cutoff=self.writer.config_cutoff(sweep.query_started_at),
            )
            self._release(ctx, task, scope, error)
            missing += outcome.missing
            if outcome.confirm_pending:
                self._request_confirmation(ctx, task, region, type_name)
            ctx.checkpoint(task)
        if error is not None:
            raise TaskError(error.code, error.message)
        return {**self._stats(total, missing), "types": len(types), "skipped": skipped}

    # -- shard (§6.3) ------------------------------------------------------------------------

    def shard(self, task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
        assert task.region is not None and task.resource_type is not None
        state = self._acquire(task)
        if state is not leases.LeaseState.ACQUIRED:
            return None
        scope = leases.Scope.of(task)
        aws = self._aws(task)
        disc = self._discovery(aws, task, task.region)
        ctx.checkpoint(task)
        scan = disc.scan_live(task.resource_type)
        ctx.checkpoint(task)
        counts = self.writer.write(
            self._checkpointed(scan.observations, task, ctx), task_id=task.id
        )
        outcome = self.writer.finish_scope(
            account_id=task.account_id,
            region=task.region,
            resource_type=task.resource_type,
            seen_ids=[o.item.resource_id for o in scan.observations],
            observed_at=scan.observed_at,
            complete=scan.complete,
        )
        error = None if scan.complete else scan.errors[0]
        self._release(ctx, task, scope, error)
        if outcome.confirm_pending:
            self._request_confirmation(ctx, task, task.region, task.resource_type)
        if error is not None:
            raise TaskError(error.code, error.message)
        return self._stats(counts, outcome.missing)

    def _acquire(self, task: ScanTask) -> leases.LeaseState:
        with Session(self.writer.engine, expire_on_commit=False) as session:
            state = leases.acquire_or_settle(session, task)
        if state is leases.LeaseState.LOST:
            raise TaskLost
        return state

    # -- resource (§6.4) ---------------------------------------------------------------------

    def resource(self, task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
        assert task.region and task.resource_type and task.resource_id
        aws = self._aws(task)
        disc = self._discovery(aws, task, task.region)
        with ctx.new_session() as session:
            configuration = session.exec(
                select(Resource.configuration).where(
                    Resource.account_id == task.account_id,
                    Resource.aws_region == task.region,
                    Resource.resource_type == task.resource_type,
                    Resource.resource_id == task.resource_id,
                )
            ).first()
        started = datetime.now(UTC)
        try:
            found = disc.fetch(task.resource_type, task.resource_id, configuration)
        except AWS_ERRORS as exc:
            raise task_error(exc) from exc
        except MappingError as exc:
            raise TaskError("internal", str(exc), retryable=False) from exc
        ctx.checkpoint(task)
        if isinstance(found, Gone):
            outcome = self.writer.record_gone(
                account_id=task.account_id,
                region=task.region,
                resource_type=task.resource_type,
                resource_id=task.resource_id,
                observed_at=started,
                task_id=task.id,
            )
            return {
                "result": outcome,
                "missing": int(outcome == "missing"),
                "deleted": int(outcome == "deleted"),
            }
        counts = self.writer.write([found], task_id=task.id)
        return {"result": "found", **self._stats(counts)}

    # -- confirm_missing (§6.5) --------------------------------------------------------------

    def confirm_missing(self, task: ScanTask, ctx: TaskContext) -> dict[str, Any] | None:
        assert task.region is not None and task.resource_type is not None
        state = self._acquire(task)
        if state is not leases.LeaseState.ACQUIRED:
            return None
        scope = leases.Scope.of(task)
        spec = self.registry.get(task.resource_type)
        aws = self._aws(task)
        disc = self._discovery(aws, task, task.region)
        with ctx.new_session() as session:
            pending = session.exec(
                select(Resource.resource_id, Resource.configuration)
                .where(
                    Resource.account_id == task.account_id,
                    Resource.aws_region == task.region,
                    Resource.resource_type == task.resource_type,
                    Resource.status == "missing",
                    col(Resource.absent_in_scan_at).is_not(None),
                )
                .order_by(col(Resource.id))
            ).all()
        hooks = self.hooks.get(task.resource_type, Hooks())
        by_config = not spec.sources.cloud_control.read and hooks.get_live is None
        deleted = 0
        restored = 0
        errors: list[ScanError] = []
        deleted_ids: set[str] = set()
        if by_config:  # no by-ID read: Config's ResourceDeleted status is the evidence
            client = make_client(aws, "config", client_region(task.region))
            keys = [(task.resource_type, rid) for rid, _ in pending]
            try:
                statuses = config_source.batch_get_status(client, keys)
            except AWS_ERRORS as exc:
                errors.append(classify(exc))
                statuses = {}
            deleted_ids = {key[1] for key, status in statuses.items() if status.deleted}
        for index, (resource_id, configuration) in enumerate(pending):
            if index % CONFIRM_CHECK_STEP == 0:
                ctx.checkpoint(task)
            started = datetime.now(UTC)
            try:
                if by_config:
                    found: Observation | Gone | None = (
                        Gone() if resource_id in deleted_ids else None
                    )
                else:
                    found = disc.fetch(task.resource_type, resource_id, configuration)
            except (*AWS_ERRORS, MappingError) as exc:
                errors.append(classify(exc))
                self._check_failed(task, resource_id)
                continue
            if found is None:
                continue  # Config does not say deleted: no evidence, stays missing
            if isinstance(found, Gone):
                outcome = self.writer.record_gone(
                    account_id=task.account_id,
                    region=task.region,
                    resource_type=task.resource_type,
                    resource_id=resource_id,
                    observed_at=started,
                    task_id=task.id,
                )
                deleted += outcome == "deleted"
            else:
                restored += self.writer.write([found], task_id=task.id).restored
        error = errors[0] if errors else None
        self._release(ctx, task, scope, error)
        if error is not None:
            raise TaskError(error.code, error.message)
        return {"checked": len(pending), "deleted": deleted, "restored": restored}

    def _check_failed(self, task: ScanTask, resource_id: str) -> None:
        assert task.region is not None and task.resource_type is not None
        self.writer.record_check_failed(
            account_id=task.account_id,
            region=task.region,
            resource_type=task.resource_type,
            resource_id=resource_id,
        )
