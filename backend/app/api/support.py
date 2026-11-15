"""Helpers of the write endpoints that are more than a database call: accounts the snapshot
does not know yet, resolving a refresh target and waiting for a refresh task."""

from __future__ import annotations

import time
from collections.abc import Sequence

from sqlalchemy import text
from sqlmodel import Session, col, select

from app.core.config import Settings
from app.core.errors import ApiError
from app.models import Account, ScanTask
from app.schemas.scans import RefreshByArn, RefreshByKey
from app.types.hooks import TYPE_HOOKS
from app.types.mapping import MappingError
from app.types.registry import TypeRegistry
from app.worker.requests import ResourceKey

POLL_INTERVAL_SECONDS = 0.5
NOT_FINISHED = ("queued", "running")


def check_resource_type(registry: TypeRegistry, type_name: str) -> None:
    if type_name not in registry:
        raise ApiError(422, "validation_error", "未知的资源类型", {"resourceType": type_name})


def check_resource_id(type_name: str, resource_id: str) -> None:
    """Reject at the door a resource id that can never be read (e.g. a short-format ECS
    service ARN), instead of failing a queued task."""
    hooks = TYPE_HOOKS.get(type_name)
    if hooks is not None and hooks.cloudcontrol_identifier is not None:
        try:
            hooks.cloudcontrol_identifier(resource_id)
        except MappingError as exc:
            raise ApiError(422, "validation_error", str(exc), {"resourceId": resource_id}) from exc


def ensure_accounts(session: Session, settings: Settings, account_ids: Sequence[str]) -> None:
    """The accounts must exist in local master data and be active.

    `settings` stays in the signature because this helper is shared by write endpoints; the
    registry is no longer consulted here (manual/local account data is authoritative).
    """
    del settings
    active_before = set(
        session.exec(
            select(Account.account_id).where(
                col(Account.account_id).in_(account_ids), col(Account.active)
            )
        )
    )
    unknown = [a for a in dict.fromkeys(account_ids) if a not in active_before]
    if not unknown:
        return
    raise ApiError(404, "account_not_found", "账号不存在", {"accountIds": unknown})


def resolve_refresh_target(session: Session, body: RefreshByKey | RefreshByArn) -> ResourceKey:
    if isinstance(body, RefreshByKey):
        return ResourceKey(body.account_id, body.aws_region, body.resource_type, body.resource_id)
    rows = (
        session.connection()
        .execute(
            text("""
        SELECT account_id, aws_region, resource_type, resource_id FROM t_inv_aws_resource
         WHERE arn = :arn ORDER BY id
        """),
            {"arn": body.arn},
        )
        .all()
    )
    if not rows:
        raise ApiError(404, "resource_not_found", "资源不存在", {"arn": body.arn})
    accounts = sorted({row.account_id for row in rows})
    if len(accounts) > 1:
        raise ApiError(409, "ambiguous_arn", "这个 ARN 对应多个账号", {"accountIds": accounts})
    return ResourceKey(*rows[0])


def wait_for_task(session: Session, task_id: int, wait: int) -> ScanTask:
    """Poll the task every 500 ms until it leaves queued/running or `wait` seconds passed."""
    deadline = time.monotonic() + wait
    while True:
        task = session.get_one(ScanTask, task_id, populate_existing=True)
        session.commit()  # don't hold a transaction while sleeping
        if task.status not in NOT_FINISHED or time.monotonic() >= deadline:
            return task
        time.sleep(POLL_INTERVAL_SECONDS)
