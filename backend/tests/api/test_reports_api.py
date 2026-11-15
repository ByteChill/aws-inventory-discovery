"""Custom reports: CRUD, evaluation against the seeded fixtures, counts and export (v2)."""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert

from app.query.tables import FINDING, REPORT
from app.reports.definition import allowed_fields
from app.types.registry import TypeRegistry
from tests.api.conftest import TEST_REDIS_URL
from tests.support.seed import DEV_ACCOUNT, MAIN_ACCOUNT, Seeded

API = "/api/v1/reports"
LIVE = ("active", "missing")
IMAGE = "AWS::EC2::Image"
INSTANCE = "AWS::EC2::Instance"
SERVICE = "AWS::ECS::Service"
TASK_DEFINITION = "AWS::ECS::TaskDefinition"
SELF_AMI = "ami-1234567890EXAMPLE"  # the instance fixture's imageId and an AMI fixture
INSTANCE_ID = "i-1234567890abcdef0"
FINDINGS_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "findings" / "ami_findings.json"
)
FINDINGS = json.loads(FINDINGS_FIXTURE.read_text())


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def condition(field: str, op: str, value: Any = None, *, key: str | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"field": field, "op": op}
    if value is not None:
        node["value"] = value
    if key is not None:
        node["key"] = key
    return node


def report(
    name: str,
    where: dict[str, Any],
    *,
    subject_type: str | None = None,
    columns: list[str] | None = None,
    enabled: bool = True,
    description: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "where": where, "enabled": enabled}
    if subject_type is not None:
        body["subjectType"] = subject_type
    if columns is not None:
        body["columns"] = columns
    if description is not None:
        body["description"] = description
    return body


def create(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(API, json=body, headers={"X-Caller-Id": "reporter"})
    assert response.status_code == 201, response.text
    return response.json()


def results(client: TestClient, report_id: int, query: str = "") -> dict[str, Any]:
    response = client.get(f"{API}/{report_id}/results?pageSize=200&{query}")
    assert response.status_code == 200, response.text
    return response.json()


def live(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["status"] in LIVE]


def ids(body: dict[str, Any]) -> set[int]:
    return {item["id"] for item in body["items"]}


def tag_value(row: dict[str, Any], key: str) -> str | None:
    return next((tag["value"] for tag in row["tags"] if tag["key"] == key), None)


def images(row: dict[str, Any]) -> list[str]:
    return [
        container.get("Image", "")
        for container in row["configuration"].get("ContainerDefinitions", [])
    ]


def inbound(seeded: Seeded) -> dict[int, int]:
    counts: dict[int, int] = {}
    for relation in seeded.relations:
        if relation["to_resource_id"] is not None:
            counts[relation["to_resource_id"]] = counts.get(relation["to_resource_id"], 0) + 1
    return counts


# ---------------------------------------------------------------- CRUD


def test_create_read_update_and_delete(client: TestClient) -> None:
    body = report(
        unique("ecr"),
        condition("image", "contains", "public.ecr.aws"),
        subject_type="AWS::ECS::TaskDefinition",
        columns=["image", "family"],
        description="任务定义用了公共镜像",
    )
    created = create(client, body)

    assert created["createdBy"] == "reporter"
    assert created["definitionVersion"] == 1
    assert created["enabled"] is True
    assert {k: created["where"][k] for k in body["where"]} == body["where"]
    assert created["columns"] == ["image", "family"]

    listed = client.get(f"{API}?enabled=true").json()
    assert created["id"] in {item["id"] for item in listed}

    changed = report(
        body["name"],
        condition("image", "startsWith", "public.ecr.aws/docker/library/mysql"),
        subject_type="AWS::ECS::TaskDefinition",
        columns=["family"],
    )
    response = client.put(f"{API}/{created['id']}", json=changed, headers={"X-Caller-Id": "editor"})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["updatedBy"] == "editor"
    assert updated["createdBy"] == "reporter"
    assert {k: updated["where"][k] for k in changed["where"]} == changed["where"]
    assert updated["columns"] == ["family"]
    assert client.delete(f"{API}/{created['id']}").status_code == 204
    assert client.get(f"{API}/{created['id']}").status_code == 404


def test_names_are_unique(client: TestClient) -> None:
    name = unique("unique")
    first = create(client, report(name, condition("resourceId", "exists")))
    duplicate = client.post(API, json=report(name, condition("resourceId", "exists")))
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "report_name_taken"

    other = create(client, report(unique("other"), condition("resourceId", "exists")))
    taken = client.put(f"{API}/{other['id']}", json=report(name, condition("resourceId", "exists")))
    assert taken.status_code == 409

    same = client.put(f"{API}/{first['id']}", json=report(name, condition("accountId", "exists")))
    assert same.status_code == 200, same.text


def test_unknown_report_is_404(client: TestClient) -> None:
    assert client.get(f"{API}/99999999").status_code == 404
    assert client.get(f"{API}/99999999/results").status_code == 404
    assert client.delete(f"{API}/99999999").status_code == 404


@pytest.mark.parametrize(
    ("subject_type", "where", "columns"),
    [
        # A field of one type cannot be used by a report over every type.
        (None, condition("family", "eq", "hello_world"), None),
        # A type this deployment does not know.
        ("AWS::Nope::Thing", condition("resourceId", "exists"), None),
        # An operator the field's type does not support.
        ("AWS::ECS::TaskDefinition", condition("containerCount", "contains", "1"), None),
        # A value of the wrong type.
        ("AWS::ECS::TaskDefinition", condition("containerCount", "gt", "one"), None),
        # A tag condition without a key.
        (None, condition("tags", "hasValue", "TeamA"), None),
        # A column the report cannot show.
        (None, condition("resourceId", "exists"), ["family"]),
        # Duplicate columns.
        (None, condition("resourceId", "exists"), ["accountId", "accountId"]),
    ],
)
def test_definitions_that_cannot_be_evaluated_are_rejected(
    client: TestClient,
    subject_type: str | None,
    where: dict[str, Any],
    columns: list[str] | None,
) -> None:
    response = client.post(
        API, json=report(unique("bad"), where, subject_type=subject_type, columns=columns)
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"


def test_empty_condition_groups_are_rejected_by_the_wire_model(client: TestClient) -> None:
    for where in ({"all": []}, {"any": []}, {}):
        response = client.post(API, json=report(unique("empty"), where))
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "validation_error"


def test_the_field_palette_matches_what_a_definition_may_name(
    client: TestClient, registry: TypeRegistry
) -> None:
    for subject_type in (None, "AWS::ECS::TaskDefinition"):
        response = client.get(
            f"{API}/fields",
            params={"subjectType": subject_type} if subject_type else {},
        )
        assert response.status_code == 200, response.text
        palette = response.json()
        assert [field["key"] for field in palette] == list(allowed_fields(subject_type, registry))
        assert all(field["label"] for field in palette)

    image = next(
        field
        for field in client.get(
            f"{API}/fields", params={"subjectType": "AWS::ECS::TaskDefinition"}
        ).json()
        if field["key"] == "image"
    )
    assert (image["label"], image["type"], image["array"], image["group"]) == (
        "镜像",
        "text",
        True,
        "type",
    )

    english = {
        field["key"]: field["label"]
        for field in client.get(
            f"{API}/fields",
            params={"subjectType": "AWS::ECS::TaskDefinition"},
            headers={"Accept-Language": "en"},
        ).json()
    }
    assert english["image"] == "Image"
    assert english["tags"] == "Tags"

    unknown = client.get(f"{API}/fields", params={"subjectType": "AWS::Nope::Thing"})
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "validation_error"


# ---------------------------------------------------------------- evaluation


def test_array_field_matches_when_any_element_matches(client: TestClient, seeded: Seeded) -> None:
    expected = {
        row["id"]
        for row in live(seeded.where(resource_type="AWS::ECS::TaskDefinition"))
        if any("public.ecr.aws" in image for image in images(row))
    }
    body = create(
        client,
        report(
            unique("images"),
            condition("image", "contains", "public.ecr.aws"),
            subject_type="AWS::ECS::TaskDefinition",
            columns=["image"],
        ),
    )

    page = results(client, body["id"])
    assert ids(page) == expected
    assert page["total"] == len(expected)
    assert page["columns"] == [
        {"key": "image", "label": "镜像", "type": "text", "mono": False, "array": True}
    ]
    for item in page["items"]:
        assert item["values"]["image"] == images(seeded.one(id=item["id"]))


def test_array_field_in_negated_operators_requires_no_matching_element(
    client: TestClient, seeded: Seeded
) -> None:
    body = create(
        client,
        report(
            unique("images-not-in"),
            condition("image", "notIn", ["public.ecr.aws/docker/library/mysql:latest"]),
            subject_type="AWS::ECS::TaskDefinition",
        ),
    )

    page = results(client, body["id"])
    expected = {
        row["id"]
        for row in live(seeded.where(resource_type="AWS::ECS::TaskDefinition"))
        if "public.ecr.aws/docker/library/mysql:latest" not in images(row)
    }
    assert ids(page) == expected


def test_a_negated_condition_includes_rows_where_the_field_is_null(
    client: TestClient, seeded: Seeded
) -> None:
    """`not (name = hello_world)` must match a resource with no name, as `neq` does."""
    expected = {
        row["id"] for row in live(seeded.resources) if row["resource_name"] != "hello_world"
    }
    assert any(row["resource_name"] is None for row in live(seeded.resources))

    body = create(
        client,
        report(
            unique("not-name"),
            {"not": condition("resourceName", "eq", "hello_world")},
        ),
    )
    assert ids(results(client, body["id"])) == expected


def test_default_columns_do_not_repeat_a_common_key(client: TestClient) -> None:
    body = create(
        client,
        report(
            unique("default-columns"),
            condition("image", "exists"),
            subject_type="AWS::ECS::TaskDefinition",
        ),
    )

    keys = [column["key"] for column in results(client, body["id"])["columns"]]
    assert len(keys) == len(set(keys))
    assert keys.count("status") == 1
    assert "image" not in keys


def test_tag_conditions(client: TestClient, seeded: Seeded) -> None:
    with_owner = {row["id"] for row in live(seeded.resources) if tag_value(row, "Owner") == "TeamA"}
    without_owner = {row["id"] for row in live(seeded.resources) if tag_value(row, "Owner") is None}

    body = create(
        client,
        report(unique("owner"), condition("tags", "hasValue", "TeamA", key="Owner")),
    )
    assert ids(results(client, body["id"])) == with_owner

    body = create(client, report(unique("no-owner"), condition("tags", "missing", key="Owner")))
    assert ids(results(client, body["id"])) == without_owner

    body = create(client, report(unique("has-owner"), condition("tags", "has", key="Owner")))
    assert ids(results(client, body["id"])) == with_owner | {
        row["id"] for row in live(seeded.resources) if tag_value(row, "Owner") is not None
    }


def test_and_or_not_combine_conditions(client: TestClient, seeded: Seeded) -> None:
    expected = {
        row["id"]
        for row in live(seeded.resources)
        if tag_value(row, "Owner") == "TeamA" and row["aws_region"] != "eu-west-1"
    }
    body = create(
        client,
        report(
            unique("tree"),
            {
                "all": [
                    condition("tags", "hasValue", "TeamA", key="Owner"),
                    {"not": condition("awsRegion", "eq", "eu-west-1")},
                ]
            },
        ),
    )

    assert ids(results(client, body["id"])) == expected


def test_default_status_excludes_deleted_but_an_explicit_status_does_not(
    client: TestClient, seeded: Seeded
) -> None:
    body = create(client, report(unique("live"), condition("resourceId", "exists")))
    assert ids(results(client, body["id"])) == {row["id"] for row in live(seeded.resources)}

    body = create(client, report(unique("deleted"), condition("status", "eq", "deleted")))
    assert ids(results(client, body["id"])) == {
        row["id"] for row in seeded.resources if row["status"] == "deleted"
    }


def test_relationship_counts_are_virtual_number_fields(client: TestClient, seeded: Seeded) -> None:
    counts = inbound(seeded)
    expected = {row["id"] for row in live(seeded.resources) if counts.get(row["id"], 0) >= 1}
    body = create(
        client,
        report(
            unique("referenced"),
            condition("inboundRelationshipCount", "gte", 1),
            columns=["inboundRelationshipCount"],
        ),
    )

    page = results(client, body["id"], "sort=-inboundRelationshipCount")
    assert ids(page) == expected
    assert page["total"] == len(expected)
    values = [item["values"]["inboundRelationshipCount"] for item in page["items"]]
    assert values == sorted(values, reverse=True)
    assert values[0] == max(counts.get(row["id"], 0) for row in live(seeded.resources))


def finding_row(image: dict[str, Any], **counts: Any) -> dict[str, Any]:
    return {
        "account_id": image["account_id"],
        "aws_region": image["aws_region"],
        "resource_type": image["resource_type"],
        "resource_id": image["resource_id"],
        "critical_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "max_score": None,
        "checked_at": datetime.now(UTC),
        **counts,
    }


def test_findings_are_a_current_summary_and_unchecked_is_not_zero(
    client: TestClient, seeded: Seeded, db_engine: Engine
) -> None:
    image = seeded.one(account_id=MAIN_ACCOUNT, resource_type=IMAGE, resource_id=SELF_AMI)
    counts = FINDINGS["counts"]
    with db_engine.begin() as connection:
        connection.execute(
            insert(FINDING).values(
                **finding_row(
                    image,
                    critical_count=counts["critical"],
                    high_count=counts["high"],
                    max_score=FINDINGS["maxScore"],
                )
            )
        )
    try:
        body = create(
            client,
            report(
                unique("findings"),
                condition("findingsHighCount", "gt", 0),
                subject_type=IMAGE,
                columns=["findingsHighCount", "findingsMaxScore"],
            ),
        )
        page = results(client, body["id"])
        assert ids(page) == {image["id"]}
        assert page["items"][0]["values"] == {
            "findingsHighCount": counts["high"],
            "findingsMaxScore": FINDINGS["maxScore"],
        }

        # An image with no summary row has never been checked; it is not "no findings".
        zero = create(
            client,
            report(
                unique("zero"),
                condition("findingsHighCount", "eq", 0),
                subject_type=IMAGE,
            ),
        )
        assert image["id"] not in ids(results(client, zero["id"]))
    finally:
        with db_engine.begin() as connection:
            connection.execute(delete(FINDING).where(FINDING.c.resource_id == SELF_AMI))


def test_relative_time_and_the_related_image_conditions(
    client: TestClient, seeded: Seeded, db_engine: Engine
) -> None:
    instance = seeded.one(account_id=MAIN_ACCOUNT, resource_type=INSTANCE, resource_id=INSTANCE_ID)
    image = seeded.one(account_id=MAIN_ACCOUNT, resource_type=IMAGE, resource_id=SELF_AMI)

    old_image = create(
        client,
        report(
            unique("old-image"),
            condition("creationDate", "olderThanDays", 365),
            subject_type=IMAGE,
        ),
    )
    assert image["id"] in ids(results(client, old_image["id"]))

    expired = create(
        client,
        report(
            unique("expired"),
            {
                "all": [
                    condition("state", "eq", "running"),
                    condition("image.creationDate", "olderThanDays", 365),
                ]
            },
            subject_type=INSTANCE,
        ),
    )
    assert ids(results(client, expired["id"])) == {instance["id"]}

    # The image fixture has no deprecationTime, so "before now" cannot match it.
    deprecated = create(
        client,
        report(
            unique("deprecated"),
            condition("image.deprecationTime", "beforeNow"),
            subject_type=INSTANCE,
        ),
    )
    assert ids(results(client, deprecated["id"])) == set()

    with db_engine.begin() as connection:
        connection.execute(
            insert(FINDING).values(**finding_row(image, high_count=FINDINGS["counts"]["high"]))
        )
    try:
        findings = create(
            client,
            report(
                unique("image-findings"),
                condition("image.findingsHighCount", "gt", 0),
                subject_type=INSTANCE,
                columns=["state", "image.findingsHighCount", "image.creationDate"],
            ),
        )
        page = results(client, findings["id"])
        assert ids(page) == {instance["id"]}
        values = page["items"][0]["values"]
        assert values["image.findingsHighCount"] == [FINDINGS["counts"]["high"]]
        assert len(values["image.creationDate"]) == 1
        assert {column["key"]: column["array"] for column in page["columns"]}[
            "image.findingsHighCount"
        ] is True
    finally:
        with db_engine.begin() as connection:
            connection.execute(delete(FINDING).where(FINDING.c.resource_id == SELF_AMI))


def test_related_common_fields_and_an_unresolved_relation_is_an_empty_array(
    client: TestClient, seeded: Seeded
) -> None:
    instance = seeded.one(account_id=MAIN_ACCOUNT, resource_type=INSTANCE, resource_id=INSTANCE_ID)
    account = next(item for item in seeded.accounts if item["account_id"] == MAIN_ACCOUNT)

    fields = client.get(f"{API}/fields?subjectType={INSTANCE}").json()
    keys = [field["key"] for field in fields]
    assert len(keys) == len(set(keys))
    palette = {field["key"]: field for field in fields if field["group"] == "relation"}
    # The target's common fields are offered too, and win over a type column of the same key.
    assert palette["image.status"]["type"] == "enum"
    assert palette["image.status"]["array"] is True
    assert palette["image.accountName"]["type"] == "text"

    related = create(
        client,
        report(
            unique("related-common"),
            {
                "all": [
                    condition("image.accountId", "eq", MAIN_ACCOUNT),
                    condition("image.accountName", "eq", account["account_name"]),
                    condition("image.status", "eq", "active"),
                ]
            },
            subject_type=INSTANCE,
            columns=["image.status", "image.accountId", "image.accountName"],
        ),
    )
    page = results(client, related["id"])
    assert ids(page) == {instance["id"]}
    values = page["items"][0]["values"]
    assert values["image.status"] == ["active"]
    assert values["image.accountId"] == [MAIN_ACCOUNT]
    assert values["image.accountName"] == [account["account_name"]]

    # The dev account's instance points at an AMI that is not inventoried there: no related
    # row is an empty array, not NULL.
    empty = create(
        client,
        report(
            unique("empty-relation"),
            condition("accountId", "eq", DEV_ACCOUNT),
            subject_type=INSTANCE,
            columns=["image.creationDate"],
        ),
    )
    page = results(client, empty["id"])
    assert ids(page)
    assert all(item["values"]["image.creationDate"] == [] for item in page["items"])


def test_a_relation_field_can_cross_an_array_on_the_target(
    client: TestClient, seeded: Seeded
) -> None:
    task_definitions = {
        row["id"]: row for row in seeded.resources if row["resource_type"] == TASK_DEFINITION
    }
    # What each service's task definition would show as the related image column.
    by_service: dict[int, set[str]] = {}
    for relation in seeded.relations:
        if relation["to_resource_type"] != TASK_DEFINITION or relation["to_resource_id"] is None:
            continue
        target = task_definitions[relation["to_resource_id"]]
        by_service.setdefault(relation["from_resource_id"], set()).update(images(target))
    matching = {
        service_id
        for service_id, uris in by_service.items()
        if any("mysql" in uri for uri in uris)
    }
    assert matching

    palette = {
        field["key"]: field for field in client.get(f"{API}/fields?subjectType={SERVICE}").json()
    }
    assert palette["taskdefinition.image"]["group"] == "relation"
    assert palette["taskdefinition.image"]["type"] == "text"
    assert palette["taskdefinition.image"]["array"] is True

    body = create(
        client,
        report(
            unique("service-image"),
            condition("taskdefinition.image", "contains", "mysql"),
            subject_type=SERVICE,
            columns=["taskdefinition.image"],
        ),
    )
    page = results(client, body["id"])
    assert ids(page) == matching
    for item in page["items"]:
        assert set(item["values"]["taskdefinition.image"]) == by_service[item["id"]]

    # The same field in a condition means "any element of any related resource matches".
    none = create(
        client,
        report(
            unique("service-image-none"),
            condition("taskdefinition.image", "contains", "no-such-registry.invalid"),
            subject_type=SERVICE,
        ),
    )
    assert ids(results(client, none["id"])) == set()

    # A negative operator negates inside one target: no element equals that exact URI.
    wordpress = "public.ecr.aws/docker/library/wordpress:latest"
    negative = create(
        client,
        report(
            unique("service-image-not"),
            condition("taskdefinition.image", "neq", wordpress),
            subject_type=SERVICE,
        ),
    )
    assert ids(results(client, negative["id"])) == {
        service_id for service_id, uris in by_service.items() if wordpress not in uris
    }
    # The control that proves the negated condition can be true: no element equals this URI.
    absent = create(
        client,
        report(
            unique("service-image-not-absent"),
            condition("taskdefinition.image", "neq", "no-such-registry.invalid/x:latest"),
            subject_type=SERVICE,
        ),
    )
    assert ids(results(client, absent["id"])) == set(by_service)

    # A crossing relation column is an array: it cannot be sorted.
    invalid = client.get(f"{API}/{body['id']}/results?sort=taskdefinition.image")
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_sort"

    # Export joins the array with ";" and carries every matching service.
    exported = client.get(f"{API}/{body['id']}/export?format=csv")
    assert exported.status_code == 200
    header, *rows = exported.text.removeprefix("﻿").splitlines()
    assert "镜像" in header
    assert {frozenset(row.strip('"').split(";")) for row in rows} == {
        frozenset(by_service[service_id]) for service_id in matching
    }


def test_scope_narrows_the_result(client: TestClient, seeded: Seeded) -> None:
    body = create(client, report(unique("scoped"), condition("resourceId", "exists")))

    account = {row["id"] for row in live(seeded.resources) if row["account_id"] == DEV_ACCOUNT}
    assert ids(results(client, body["id"], f"s=acct:{DEV_ACCOUNT}")) == account

    payments = {
        row["id"]
        for row in live(seeded.resources)
        if row["account_id"]
        in {a["account_id"] for a in seeded.accounts if a["eim_id"] == "10000001"}
    }
    assert ids(results(client, body["id"], "s=app:10000001")) == payments

    invalid = client.get(f"{API}/{body['id']}/results?s=group:1")
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_filter"


def get_json_page(client: TestClient, report_id: int, query: str) -> dict[str, Any]:
    response = client.get(f"{API}/{report_id}/results?{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_paging_and_invalid_sort(client: TestClient) -> None:
    body = create(client, report(unique("paging"), condition("resourceId", "exists")))
    everything = results(client, body["id"])
    total = everything["total"]
    assert total > 2

    collected: set[int] = set()
    for page in (1, 2):
        body_page = get_json_page(client, body["id"], f"page={page}&pageSize=2")
        assert body_page["page"] == page
        assert body_page["pageSize"] == 2
        assert body_page["total"] == total
        collected |= {item["id"] for item in body_page["items"]}
    assert len(collected) == 4

    invalid = client.get(f"{API}/{body['id']}/results?sort=password")
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_sort"


# ---------------------------------------------------------------- overview and caching


def test_overview_counts_each_enabled_report(client: TestClient, seeded: Seeded) -> None:
    counted = create(
        client,
        report(
            unique("counted"),
            condition("image", "contains", "public.ecr.aws"),
            subject_type="AWS::ECS::TaskDefinition",
        ),
    )
    hidden = create(
        client,
        report(unique("hidden"), condition("resourceId", "exists"), enabled=False),
    )

    response = client.get(f"{API}/overview")
    assert response.status_code == 200, response.text
    overview = {item["reportId"]: item for item in response.json()["items"]}

    assert overview[counted["id"]]["count"] == results(client, counted["id"])["total"]
    assert overview[counted["id"]]["error"] is None
    assert hidden["id"] not in overview


def test_overview_caches_counts_in_redis(
    make_client: Callable[..., TestClient], redis_client: redis.Redis, schema_name: str
) -> None:
    client = make_client(REDIS_URL=TEST_REDIS_URL)
    body = create(client, report(unique("cached"), condition("resourceId", "exists")))

    assert client.get(f"{API}/overview").status_code == 200
    keys = list(
        redis_client.scan_iter(
            f"aws-inventory-discovery:{schema_name}:reports:count:{body['id']}:*"
        )
    )
    assert len(keys) == 1
    assert 0 < redis_client.ttl(keys[0]) <= 60
    cached = redis_client.get(keys[0])
    assert cached is not None
    assert int(cached) == results(client, body["id"])["total"]


def test_a_stored_definition_that_no_longer_validates_reports_an_error(
    client: TestClient, db_engine: Engine
) -> None:
    """A type removed by a deploy must leave the report visible and broken, not break the list."""
    name = unique("stale")
    now = datetime.now(UTC)
    with db_engine.begin() as connection:
        report_id = connection.execute(
            insert(REPORT)
            .values(
                name=name,
                subject_type="AWS::Old::Type",
                definition_version=1,
                definition={
                    "subjectType": "AWS::Old::Type",
                    "where": {"field": "resourceId", "op": "exists"},
                },
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            .returning(REPORT.c.id)
        ).scalar_one()
    try:
        detail = client.get(f"{API}/{report_id}")
        assert detail.status_code == 422
        assert detail.json()["code"] == "validation_error"
        assert client.get(f"{API}/{report_id}/results").status_code == 422

        overview = {
            item["reportId"]: item for item in client.get(f"{API}/overview").json()["items"]
        }
        assert overview[report_id]["count"] == 0
        assert overview[report_id]["error"]
    finally:
        with db_engine.begin() as connection:
            connection.execute(delete(REPORT).where(REPORT.c.id == report_id))


# ---------------------------------------------------------------- export


def test_csv_export_has_a_row_per_match_and_the_report_columns(
    client: TestClient, seeded: Seeded
) -> None:
    body = create(
        client,
        report(
            unique("export"),
            condition("image", "contains", "public.ecr.aws"),
            subject_type="AWS::ECS::TaskDefinition",
            columns=["image", "family"],
        ),
    )

    response = client.get(f"{API}/{body['id']}/export?format=csv")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == ["镜像", "系列"]
    assert len(rows) - 1 == results(client, body["id"])["total"]
    assert all(len(row) == 2 for row in rows)


def test_jsonl_export_carries_the_resource_and_the_values(client: TestClient) -> None:
    body = create(
        client,
        report(
            unique("jsonl"),
            condition("image", "startsWith", "public.ecr.aws"),
            subject_type="AWS::ECS::TaskDefinition",
            columns=["family"],
        ),
    )

    response = client.get(f"{API}/{body['id']}/export?format=jsonl&lang=en")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    items = [json.loads(line) for line in response.text.splitlines()]
    expected = results(client, body["id"])
    assert len(items) == expected["total"]
    assert {item["id"] for item in items} == ids(expected)
    assert all(item["values"]["family"] for item in items)
    assert all(item["resourceType"] == "AWS::ECS::TaskDefinition" for item in items)
