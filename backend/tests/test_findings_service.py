"""The findings service client: what it accepts and what it refuses (docs/specs/reports.md)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.aws.findings import FindingsError, FindingsService, FindingsUnavailable

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "findings" / "ami_findings.json").read_text()
)


def service(handler: httpx.MockTransport) -> FindingsService:
    return FindingsService(
        "http://findings",
        client=httpx.Client(transport=handler),
        sleep=lambda _: None,
    )


def test_a_findings_summary_is_read_as_counts_and_the_highest_score() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ami/ami-123/findings"
        return httpx.Response(200, json=FIXTURE)

    levels = service(httpx.MockTransport(handler)).fetch("ami-123")

    assert levels.counts.model_dump() == FIXTURE["counts"]
    assert levels.max_score == FIXTURE["maxScore"]


def test_a_body_without_every_level_is_an_error_not_a_clean_image() -> None:
    for body in ({}, {"counts": {}}, {"counts": {"critical": 0, "high": 0, "medium": 0}}):
        with pytest.raises(FindingsError):
            service(
                httpx.MockTransport(lambda request, body=body: httpx.Response(200, json=body))
            ).fetch("ami-123")


def test_server_errors_are_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=FIXTURE)

    levels = service(httpx.MockTransport(handler)).fetch("ami-123")

    assert len(calls) == 2
    assert levels.counts.high == FIXTURE["counts"]["high"]


def test_a_persistent_server_error_means_the_service_is_unavailable() -> None:
    with pytest.raises(FindingsUnavailable):
        service(httpx.MockTransport(lambda request: httpx.Response(503))).fetch("ami-123")


def test_a_client_error_and_a_malformed_body_are_errors() -> None:
    with pytest.raises(FindingsError):
        service(httpx.MockTransport(lambda request: httpx.Response(400))).fetch("ami-123")
    with pytest.raises(FindingsError):
        service(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"counts": {"critical": 0, "high": -1, "medium": 0, "low": 0}}
                )
            )
        ).fetch("ami-123")
