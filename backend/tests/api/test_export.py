"""`GET /resources/export`: CSV and JSON Lines."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.query import export as export_module
from app.types.registry import TypeRegistry
from tests.support.seed import Seeded

LIVE = ("active", "missing")


def parse_csv(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


def test_csv_is_utf8_with_bom_and_has_one_row_per_resource(
    client: TestClient, seeded: Seeded
) -> None:
    response = client.get("/api/v1/resources/export?format=csv")

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert response.headers["content-type"].startswith("text/csv")
    assert re.fullmatch(
        r'attachment; filename="aws-inventory-\d{8}T\d{6}Z\.csv"',
        response.headers["content-disposition"],
    )
    rows = parse_csv(response.content)
    assert len(rows) - 1 == len([r for r in seeded.resources if r["status"] in LIVE])


def test_csv_header_labels_follow_accept_language(
    client: TestClient, get_json: Callable[..., Any]
) -> None:
    common = {c["key"]: c for c in get_json("/api/v1/columns")}
    zh = parse_csv(client.get("/api/v1/resources/export").content)[0]
    en = parse_csv(
        client.get(
            "/api/v1/resources/export", headers={"Accept-Language": "en;q=0.8, en-GB"}
        ).content
    )[0]

    assert zh[0] == common["resourceName"]["label"] == "名称"
    assert en[0] == "Name"
    assert len(zh) == len(en) == len(common)


def test_csv_with_a_type_has_its_columns_and_honours_the_columns_parameter(
    client: TestClient, seeded: Seeded, registry: TypeRegistry
) -> None:
    spec = registry.get("AWS::Lambda::Function")
    lambdas = [r for r in seeded.where(resource_type=spec.type_name) if r["status"] in LIVE]

    everything = parse_csv(
        client.get(f"/api/v1/resources/export?resourceType={spec.type_name}").content
    )
    chosen = parse_csv(
        client.get(
            "/api/v1/resources/export",
            params={
                "resourceType": spec.type_name,
                "columns": "resourceName,runtime,memorySize",
                "sort": "resourceName",
            },
            headers={"Accept-Language": "en"},
        ).content
    )

    assert everything[0][-len(spec.columns) :] == [c.label.zh_cn for c in spec.columns]
    assert chosen[0] == ["Name", spec.columns[0].label.en, spec.columns[1].label.en]
    assert len(chosen) - 1 == len(lambdas)
    expected = sorted((r["resource_name"], "python3.12", "128") for r in lambdas)
    assert [tuple(row) for row in chosen[1:]] == expected


def test_unknown_export_column_is_a_422(client: TestClient) -> None:
    response = client.get("/api/v1/resources/export?columns=resourceName,nope")

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_jsonl_has_a_full_config_format_record_per_line_without_live(
    client: TestClient, seeded: Seeded
) -> None:
    response = client.get(
        "/api/v1/resources/export?format=jsonl&status=active&status=missing&status=deleted"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["content-disposition"].endswith('.jsonl"')
    records = [json.loads(line) for line in response.text.splitlines()]
    assert len(records) == len(seeded.resources)
    by_id = {record["id"]: record for record in records}
    for row in seeded.resources:
        record = by_id[row["id"]]
        assert record["configuration"] == row["configuration"]
        assert record["arn"] == row["arn"]
        assert record["relationships"] == [
            {
                "resourceType": r["resourceType"],
                "resourceId": r["resourceId"],
                "name": r.get("relationshipName", ""),
            }
            for r in row["relationships"]
        ]
        assert "live" not in record
        assert record["account"]["accountId"] == row["account_id"]


@pytest.mark.parametrize("fmt", ["csv", "jsonl"])
def test_export_streams_the_whole_dataset_across_several_batches(
    client: TestClient, seeded: Seeded, monkeypatch: pytest.MonkeyPatch, fmt: str
) -> None:
    monkeypatch.setattr(export_module, "BATCH_SIZE", 4)
    expected = len([r for r in seeded.resources if r["status"] in LIVE])
    assert expected > 4 * 3

    with client.stream("GET", f"/api/v1/resources/export?format={fmt}") as response:
        chunks = list(response.iter_text())

    body = "".join(chunks)
    count = len(parse_csv(body.encode())) - 1 if fmt == "csv" else len(body.splitlines())
    assert count == expected
    assert len(chunks) >= 4  # the header chunk plus at least three batches
