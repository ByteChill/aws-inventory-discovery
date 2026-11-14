"""Raw AWS responses that fail validation must not leak their values into the task's
error_message or the worker log.

Redaction happens later, in the Writer, so a validation error raised while reading a response
still sees the raw record (environment values, user data). Each test deforms an existing
official fixture just enough to make the read fail, then drives it through a real Worker.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import boto3
import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from app.aws import config_source
from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.models import ScanTask
from app.types.hooks import Hooks
from app.types.mapping import MappingError, to_config_item
from app.types.registry import TypeRegistry
from app.worker.discovery import ConfigPages, Discovery
from app.worker.handlers import Handler, TaskContext
from app.worker.main import Worker
from tests.worker.conftest import REGION, refresh, task_state

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aws"
REGISTRY = TypeRegistry.from_directory()


def fixture(path: str) -> dict[str, Any]:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def task_definition() -> dict[str, Any]:
    return fixture("aws.ecs.taskdefinition/config_item.json")


def environment(raw: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """One environment entry of the task definition fixture (a name and a value)."""
    return raw["configuration"]["ContainerDefinitions"][1]["Environment"][index]


@contextmanager
def worker_log() -> Generator[list[str]]:
    """The worker's log as CloudWatch receives it (the production formatter)."""
    lines: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = Collect()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)  # setLevel clears the cached isEnabledFor() answers
    root.addHandler(handler)
    try:
        yield lines
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def run_handler(
    engine: Engine,
    make_settings: Callable[..., Settings],
    session: Session,
    handler: Handler,
) -> tuple[ScanTask, str]:
    """Run one handler through a real Worker until the task fails; return it and the log."""
    task = refresh(session, "queue-1")
    task.max_attempts = 1  # a raw-response failure is final for the test
    session.add(task)
    session.commit()
    worker = Worker(
        engine,
        make_settings(WORKER_THREADS=1, WORKER_ONDEMAND_THREADS=1),
        handlers={"resource": handler},
        poll_interval=0.05,
        heartbeat_interval=0.2,
    )
    with worker_log() as lines:
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while task_state(session, task).status != "failed":
                assert time.monotonic() < deadline, "task did not fail"
                time.sleep(0.05)
        finally:
            worker.stop()
            assert worker.join(timeout=5)
    return task_state(session, task), "\n".join(lines)


def assert_not_leaked(message: str, logs: str, env: dict[str, Any]) -> None:
    for raw_value in env.values():
        assert raw_value not in message
        assert raw_value not in logs


def test_a_config_item_validation_failure_hides_the_raw_record(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = task_definition()
    env = environment(raw)
    # One short value: without hiding, Pydantic echoes it in full in the error.
    raw["configuration"] = env["Value"]
    monkeypatch.setattr(config_source, "select_pages", lambda client, types: iter([[raw]]))

    def scan(task: ScanTask, ctx: TaskContext) -> None:
        for _ in ConfigPages(None, ["AWS::ECS::TaskDefinition"]):
            pass

    failed, logs = run_handler(engine, make_settings, session, scan)

    message = failed.error_message or ""
    assert (failed.status, failed.error_code) == ("failed", "internal")
    assert "validation error for ConfigItem" in message and "configuration" in message
    assert_not_leaked(message, logs, env)


def test_a_malformed_capture_time_hides_the_raw_value(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = task_definition()
    env = environment(raw)
    raw["configurationItemCaptureTime"] = env["Value"]
    monkeypatch.setattr(config_source, "select_pages", lambda client, types: iter([[raw]]))

    def scan(task: ScanTask, ctx: TaskContext) -> None:
        for _ in ConfigPages(None, ["AWS::ECS::TaskDefinition"]):
            pass

    failed, logs = run_handler(engine, make_settings, session, scan)

    message = failed.error_message or ""
    assert "configurationItemCaptureTime is not an ISO 8601 timestamp" in message
    assert_not_leaked(message, logs, env)


def test_a_cloud_control_tag_validation_failure_hides_the_raw_record(
    engine: Engine,
    session: Session,
    make_settings: Callable[..., Settings],
) -> None:
    document = fixture("aws.ecs.taskdefinition/cloudcontrol_get.json")
    properties = json.loads(document["ResourceDescription"]["Properties"])
    env = properties["ContainerDefinitions"][1]["Environment"][0]
    properties["Tags"] = [{**properties["Tags"][0], "Value": env}]

    def scan(task: ScanTask, ctx: TaskContext) -> None:
        hooks = {
            "AWS::ECS::TaskDefinition": Hooks(list_live=lambda session, **_: iter([[properties]]))
        }
        disc = Discovery(
            boto3.Session(),  # no AWS call: the listing hook above supplies the response
            task.account_id,
            task.region or REGION,
            REGISTRY,
            hooks=hooks,
        )
        for _ in disc.live_pages("AWS::ECS::TaskDefinition"):
            pass

    failed, logs = run_handler(engine, make_settings, session, scan)

    message = failed.error_message or ""
    assert "validation error for ConfigTag" in message
    assert_not_leaked(message, logs, env)


def test_a_failed_transform_hides_the_raw_value_and_its_chained_error() -> None:
    document = fixture("aws.ec2.instance/cloudcontrol_get.json")
    properties = json.loads(document["ResourceDescription"]["Properties"])
    sensitive = properties["UserData"]
    properties["State"]["Code"] = sensitive

    with pytest.raises(MappingError) as caught:
        to_config_item(
            REGISTRY.get("AWS::EC2::Instance"), properties, account_id="111122223333", region=REGION
        )

    rendered = "".join(traceback.format_exception(caught.value))
    assert "transform of state.code failed (ValueError)" in rendered
    assert sensitive not in rendered
