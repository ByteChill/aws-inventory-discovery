"""Reading scan requests, tasks, scopes, the dashboard summary and task logs."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from botocore.exceptions import BotoCoreError, ClientError
from pydantic.alias_generators import to_camel
from sqlalchemy import func, text
from sqlmodel import Session, col, select

from app.core.cache import Cache
from app.core.config import Settings
from app.core.errors import ApiError
from app.models import ScanRequest, ScanShard, ScanTask
from app.models.scan import utcnow
from app.query.search import like_pattern
from app.schemas.base import Page
from app.schemas.scans import (
    Backlog,
    BacklogPair,
    FailureCount,
    InventoryStatus,
    LogEvent,
    ResourceCounts,
    ScanCounts,
    ScanDetail,
    ScanRequestOut,
    ScanScope,
    ScanScopeOut,
    ScanSummary,
    ScopeCandidate,
    SweepProgress,
    TaskLogs,
    TaskOut,
    WorkerActivity,
)

logger = logging.getLogger(__name__)

LOG_WINDOW_PADDING = timedelta(minutes=1)
# The dashboard polls the scan summary every 3–5 s; a TTL just above that removes most of
# the repeated queries without making the progress look stuck.
SUMMARY_CACHE_SECONDS = 10


def scan_not_found(scan_id: int) -> ApiError:
    return ApiError(404, "scan_not_found", "扫描不存在", {"id": scan_id})


def request_out(request: ScanRequest) -> ScanRequestOut:
    stored = request.scope or {}
    scope = {
        "account_ids": stored.get("accountIds") or None,
        "aws_regions": stored.get("awsRegions") or None,
        "resource_types": stored.get("resourceTypes") or None,
        "all": True if stored.get("all") else None,
    }
    counts = request.counts or {}
    assert request.id is not None
    return ScanRequestOut.model_validate(
        {
            "id": request.id,
            "trigger": request.trigger,
            "caller_id": request.caller_id,
            "scope": ScanScope(**scope) if request.scope is not None else None,
            "target": (
                {to_camel(k): v for k, v in request.target.items()} if request.target else None
            ),
            "status": request.status,
            "counts": ScanCounts(
                tasks=counts.get("tasks", {}),
                resources=ResourceCounts(**counts.get("resources", {})),
            ),
            "created_at": request.created_at,
            "started_at": request.started_at,
            "finished_at": request.finished_at,
        }
    )


def failures_by_code(session: Session, scan_id: int) -> list[FailureCount]:
    rows = session.exec(
        select(ScanTask.error_code, func.count())
        .where(ScanTask.scan_request_id == scan_id, ScanTask.status == "failed")
        .group_by(ScanTask.error_code)
        .order_by(func.count().desc())
    ).all()
    return [FailureCount(error_code=code or "internal", count=n) for code, n in rows]


def list_scans(
    session: Session, trigger: str | None, status: str | None, page: int, page_size: int
) -> Page[ScanRequestOut]:
    conditions = []
    if trigger:
        conditions.append(ScanRequest.trigger == trigger)
    if status:
        conditions.append(ScanRequest.status == status)
    total = session.exec(select(func.count()).select_from(ScanRequest).where(*conditions)).one()
    found = session.exec(
        select(ScanRequest)
        .where(*conditions)
        .order_by(col(ScanRequest.created_at).desc(), col(ScanRequest.id).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(items=[request_out(r) for r in found], page=page, page_size=page_size, total=total)


def get_scan(session: Session, scan_id: int) -> ScanDetail:
    request = session.get(ScanRequest, scan_id)
    if request is None:
        raise scan_not_found(scan_id)
    return ScanDetail(
        **request_out(request).model_dump(), failures_by_code=failures_by_code(session, scan_id)
    )


def task_out(task: ScanTask) -> TaskOut:
    return TaskOut.model_validate(task, from_attributes=True)


def list_tasks(
    session: Session,
    scan_id: int,
    *,
    status: str | None,
    error_code: str | None,
    account_id: str | None,
    kind: str | None,
    page: int,
    page_size: int,
) -> Page[TaskOut]:
    if session.get(ScanRequest, scan_id) is None:
        raise scan_not_found(scan_id)
    conditions = [ScanTask.scan_request_id == scan_id]
    for column, value in (
        (ScanTask.status, status),
        (ScanTask.error_code, error_code),
        (ScanTask.account_id, account_id),
        (ScanTask.kind, kind),
    ):
        if value:
            conditions.append(column == value)
    total = session.exec(select(func.count()).select_from(ScanTask).where(*conditions)).one()
    found = session.exec(
        select(ScanTask)
        .where(*conditions)
        .order_by(col(ScanTask.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(items=[task_out(t) for t in found], page=page, page_size=page_size, total=total)


def get_task(session: Session, task_id: int) -> ScanTask:
    task = session.get(ScanTask, task_id)
    if task is None:
        raise ApiError(404, "task_not_found", "任务不存在", {"id": task_id})
    return task


# --------------------------------------------------------------------------- summary


def _backlog(session: Session, priority_clause: str, now: datetime) -> Backlog:
    row = (
        session.connection()
        .execute(
            text(f"""
        SELECT count(*) AS queued, min(not_before) AS oldest FROM t_inv_aws_scan_task
         WHERE status = 'queued' AND not_before <= :now AND {priority_clause}
        """),
            {"now": now},
        )
        .one()
    )
    wait = int((now - row.oldest).total_seconds()) if row.oldest else 0
    return Backlog(queued=row.queued, oldest_wait_seconds=max(wait, 0))


def summary(session: Session, settings: Settings, now: datetime | None = None) -> ScanSummary:
    now = now or utcnow()
    connection = session.connection()
    sweep = None
    running_sweep = connection.execute(
        text("""
        SELECT max(id) FROM t_inv_aws_scan_request
         WHERE status IN ('queued', 'running')
           AND (trigger = 'schedule' OR scope ->> 'all' = 'true')
        """)
    ).scalar()
    request = session.get(ScanRequest, running_sweep) if running_sweep is not None else None
    if request is not None:
        assert request.id is not None
        row = connection.execute(
            text("""
            SELECT count(DISTINCT account_id) AS total,
                   count(DISTINCT account_id) FILTER (WHERE account_id NOT IN (
                       SELECT account_id FROM t_inv_aws_scan_task
                        WHERE scan_request_id = :id AND status IN ('queued', 'running')
                   )) AS done
              FROM t_inv_aws_scan_task WHERE scan_request_id = :id
            """),
            {"id": request.id},
        ).one()
        started = request.started_at or request.created_at
        estimate = None
        if row.done and row.total:
            estimate = started + (now - started) * row.total / row.done
        interval = timedelta(hours=settings.SWEEP_INTERVAL_HOURS)
        sweep = SweepProgress(
            scan_request_id=request.id,
            accounts_done=row.done,
            accounts_total=row.total,
            started_at=started,
            estimated_finish_at=estimate,
            behind_schedule=(estimate or now) > started + interval,
        )
    failures = connection.execute(
        text("""
        SELECT coalesce(error_code, 'internal') AS code, count(*) AS n FROM t_inv_aws_scan_task
         WHERE status = 'failed' AND finished_at > :since
         GROUP BY 1 ORDER BY n DESC
        """),
        {"since": now - timedelta(hours=24)},
    ).all()
    counters = connection.execute(
        text("""
        SELECT (SELECT count(*) FROM t_inv_aws_resource WHERE status = 'missing') AS missing,
               (SELECT count(*) FROM t_inv_aws_scan_shard
                 WHERE guard_state = 'abnormal') AS abnormal,
               (SELECT count(*) FROM t_inv_aws_scan_task WHERE status = 'running') AS active,
               (SELECT max(heartbeat_at) FROM t_inv_aws_scan_task WHERE status = 'running') AS beat
        """)
    ).one()
    return ScanSummary(
        sweep=sweep,
        backlog=BacklogPair(
            on_demand=_backlog(session, "priority = 0", now),
            scheduled=_backlog(session, "priority > 0", now),
        ),
        failures24h=[FailureCount(error_code=r.code, count=r.n) for r in failures],
        missing_count=counters.missing,
        abnormal_scope_count=counters.abnormal,
        workers=WorkerActivity(active_tasks=counters.active, last_heartbeat_at=counters.beat),
    )


def cached_summary(session: Session, settings: Settings, cache: Cache) -> ScanSummary:
    key = cache.key("scans", "summary")
    cached = cache.get(key)
    if cached is not None:
        return ScanSummary.model_validate(cached)
    result = summary(session, settings)
    cache.set(key, result.model_dump(mode="json", by_alias=True), SUMMARY_CACHE_SECONDS)
    return result


# --------------------------------------------------------------------------- scopes


def list_scopes(
    session: Session, guard_state: str, page: int, page_size: int
) -> Page[ScanScopeOut]:
    total = session.exec(
        select(func.count()).select_from(ScanShard).where(ScanShard.guard_state == guard_state)
    ).one()
    rows = (
        session.connection()
        .execute(
            text("""
        SELECT s.id, s.account_id, s.region, s.resource_type, s.guard_state,
               s.guard_detected_at,
               coalesce(s.candidate_count, 0) AS candidates,
               (SELECT count(*) FROM t_inv_aws_resource r
                 WHERE r.account_id = s.account_id AND r.aws_region = s.region
                   AND r.resource_type = s.resource_type AND r.status = 'active') AS active
          FROM t_inv_aws_scan_shard s WHERE s.guard_state = :state
         ORDER BY s.guard_detected_at DESC NULLS LAST, s.account_id, s.region, s.resource_type
         LIMIT :limit OFFSET :offset
        """),
            {"state": guard_state, "limit": page_size, "offset": (page - 1) * page_size},
        )
        .all()
    )
    items = [
        ScanScopeOut(
            scan_shard_id=r.id,
            account_id=r.account_id,
            region=r.region,
            resource_type=r.resource_type,
            guard_state=r.guard_state,
            guard_detected_at=r.guard_detected_at,
            candidate_count=r.candidates,
            active_count=r.active,
        )
        for r in rows
    ]
    return Page(items=items, page=page, page_size=page_size, total=total)


CANDIDATES = """
    SELECT r.id, c.value ->> 'resourceId' AS resource_id, r.resource_name,
           coalesce(r.arn, c.value ->> 'arn') AS arn, r.status
      FROM t_inv_aws_scan_shard s
      CROSS JOIN LATERAL jsonb_array_elements(
               CASE WHEN jsonb_typeof(s.candidate_ids) = 'array' THEN s.candidate_ids
                    ELSE '[]'::jsonb END  -- not saved: SQL NULL, or JSON null once cleared
           ) AS c(value)
      LEFT JOIN t_inv_aws_resource r
        ON r.account_id = s.account_id
       AND r.aws_region = s.region
       AND r.resource_type = s.resource_type
       AND r.resource_id = c.value ->> 'resourceId'
     WHERE s.id = :id AND s.guard_state = 'abnormal'
       AND c.value ->> 'resourceId' IS NOT NULL
"""
CANDIDATES_SEARCH = """
       AND (coalesce(r.resource_name, '') ILIKE :pattern
         OR (c.value ->> 'resourceId') ILIKE :pattern
         OR coalesce(r.arn, c.value ->> 'arn', '') ILIKE :pattern)
"""


def scope_candidates(
    session: Session, scan_shard_id: int, q: str | None, page: int, page_size: int
) -> Page[ScopeCandidate]:
    """The resources the guard spared when it flagged the scope (DESIGN 8.3).

    The saved candidates are `{resourceId, arn}`, expanded in SQL and left-joined to the
    inventory; the name and status come from the row while it is still there. A row the
    retention cleanup removed stays in the list as `purged`, with its resource id and ARN.
    Searching and paging happen in the database, so the saved list never travels to this
    process; each page still expands and sorts the whole list there. Anything but an
    abnormal scope has no review list.
    """
    connection = session.connection()
    shard = connection.execute(
        text("SELECT guard_state FROM t_inv_aws_scan_shard WHERE id = :id"), {"id": scan_shard_id}
    ).first()
    if shard is None:
        raise ApiError(404, "scope_not_found", "扫描范围不存在", {"id": scan_shard_id})
    if shard.guard_state != "abnormal":
        return Page(items=[], page=page, page_size=page_size, total=0)
    search = ""
    params: dict[str, Any] = {"id": scan_shard_id}
    if q:
        search = CANDIDATES_SEARCH
        params["pattern"] = like_pattern(q)
    total = connection.execute(
        text(f"SELECT count(*) FROM ({CANDIDATES}{search}) AS candidates"), params
    ).scalar_one()
    rows = connection.execute(
        text(f"{CANDIDATES}{search} ORDER BY c.value ->> 'resourceId' LIMIT :limit OFFSET :offset"),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    ).all()
    return Page(
        items=[
            ScopeCandidate(
                id=row.id,
                resource_id=row.resource_id,
                resource_name=row.resource_name,
                arn=row.arn or "",
                inventory_status=cast(InventoryStatus, row.status or "purged"),
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


# --------------------------------------------------------------------------- task logs


def _event(raw: dict[str, Any]) -> LogEvent:
    timestamp = datetime.fromtimestamp(raw["timestamp"] / 1000, UTC)
    try:
        line = json.loads(raw["message"])
        if not isinstance(line, dict):
            raise ValueError
    except ValueError:
        return LogEvent(timestamp=timestamp, level="INFO", message=raw["message"], data={})
    level, message = line.pop("level", "INFO"), line.pop("message", "")
    line.pop("time", None)
    return LogEvent(timestamp=timestamp, level=level, message=message, data=line)


def task_logs(
    client: Any, log_group: str, task: ScanTask, limit: int, next_token: str | None
) -> TaskLogs:
    """The task's lines from the worker log group: `{ $.taskId = <id> }` within the task's
    run (start to end, a minute of padding on both sides)."""
    start = (task.started_at or task.created_at) - LOG_WINDOW_PADDING
    end = (task.finished_at or utcnow()) + LOG_WINDOW_PADDING
    arguments: dict[str, Any] = {
        "logGroupName": log_group,
        "filterPattern": f"{{ $.taskId = {task.id} }}",
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "limit": limit,
    }
    if next_token:
        arguments["nextToken"] = next_token
    try:
        response = client.filter_log_events(**arguments)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("filtering the worker logs failed: %s", type(exc).__name__)
        raise ApiError(503, "dependency_unavailable", "暂时无法读取任务日志") from exc
    # CloudWatch has applied the pattern; checking the id again costs nothing and keeps a
    # backend that ignores JSON patterns from leaking other tasks' lines.
    events = [_event(e) for e in response.get("events", [])]
    return TaskLogs(
        events=[e for e in events if e.data.get("taskId") == task.id],
        next_token=response.get("nextToken"),
    )
