"""Report definitions: what a saved condition tree may say (docs/specs/reports.md)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.reports.definition import (
    MAX_CONDITIONS,
    MAX_DEPTH,
    ReportDefinition,
    allowed_fields,
    referenced_fields,
    validate_definition,
)
from app.types.registry import TypeRegistry

REGISTRY = TypeRegistry.from_directory()
TASK_DEFINITION = "AWS::ECS::TaskDefinition"


def definition(
    where: dict[str, Any], *, subject_type: str | None = None, columns: list[str] | None = None
) -> ReportDefinition:
    return ReportDefinition.model_validate(
        {"subjectType": subject_type, "where": where, "columns": columns or []}
    )


def task_definition(where: dict[str, Any], columns: list[str] | None = None) -> ReportDefinition:
    return definition(where, subject_type=TASK_DEFINITION, columns=columns)


def test_findings_and_related_fields_belong_to_the_subject_type() -> None:
    image = allowed_fields("AWS::EC2::Image", REGISTRY)
    instance = allowed_fields("AWS::EC2::Instance", REGISTRY)

    assert {"findingsCriticalCount", "findingsHighCount", "findingsMaxScore"} <= set(image)
    assert "findingsHighCount" not in allowed_fields("AWS::EC2::VPC", REGISTRY)
    # The instance reaches its AMI's columns, report fields and findings summary.
    assert instance["image.creationDate"] == "datetime"
    assert instance["image.deprecationTime"] == "datetime"
    assert instance["image.findingsHighCount"] == "number"
    # Only declared relationships are usable, and only one hop.
    assert "subnet.cidrBlock" in instance
    assert "image.image.deprecationTime" not in instance


def test_the_image_report_field_crosses_the_container_array() -> None:
    fields = allowed_fields(TASK_DEFINITION, REGISTRY)

    assert fields["image"] == "text"
    assert fields["family"] == "text"
    assert fields["containerCount"] == "number"
    assert "image" not in allowed_fields(None, REGISTRY)
    assert {"resourceName", "status", "tags", "inboundRelationshipCount"} <= set(
        allowed_fields(None, REGISTRY)
    )


def test_a_valid_tree_collects_the_fields_it_references() -> None:
    tree = task_definition(
        {
            "all": [
                {"field": "image", "op": "contains", "value": "123456789012.dkr.ecr."},
                {"not": {"field": "status", "op": "eq", "value": "deleted"}},
                {"any": [{"field": "inboundRelationshipCount", "op": "gte", "value": 1}]},
            ]
        },
        columns=["image", "family"],
    )

    validate_definition(tree, REGISTRY)
    assert referenced_fields(tree.where) == {"image", "status", "inboundRelationshipCount"}


@pytest.mark.parametrize(
    ("subject_type", "where"),
    [
        ("AWS::EC2::Instance", {"field": "image.nope", "op": "exists"}),
        ("AWS::EC2::Instance", {"field": "nope.creationDate", "op": "exists"}),
        ("AWS::EC2::Image", {"field": "findingsHighCount", "op": "contains", "value": "1"}),
        ("AWS::EC2::Image", {"field": "creationDate", "op": "olderThanDays", "value": -1}),
        ("AWS::EC2::Image", {"field": "creationDate", "op": "olderThanDays", "value": 1.5}),
        ("AWS::EC2::Image", {"field": "creationDate", "op": "olderThanDays", "value": True}),
        ("AWS::EC2::Image", {"field": "creationDate", "op": "beforeNow", "value": "x"}),
    ],
)
def test_new_datetime_and_relation_conditions_that_cannot_be_evaluated_are_refused(
    subject_type: str, where: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        validate_definition(definition(where, subject_type=subject_type), REGISTRY)


@pytest.mark.parametrize(
    ("subject_type", "where"),
    [
        ("AWS::Nope::Thing", {"field": "resourceId", "op": "exists"}),
        (None, {"field": "family", "op": "eq", "value": "hello_world"}),
        (TASK_DEFINITION, {"field": "nope", "op": "exists"}),
        (TASK_DEFINITION, {"field": "containerCount", "op": "contains", "value": "1"}),
        (TASK_DEFINITION, {"field": "family", "op": "gt", "value": "a"}),
        (TASK_DEFINITION, {"field": "containerCount", "op": "gt", "value": "one"}),
        (TASK_DEFINITION, {"field": "image", "op": "eq", "value": 1}),
        (None, {"field": "tags", "op": "eq", "value": "x"}),
        (None, {"field": "tags", "op": "hasValue", "value": "TeamA"}),
        (None, {"field": "tags", "op": "has", "value": "TeamA", "key": "Owner"}),
        (None, {"field": "resourceName", "op": "eq", "value": "x", "key": "Owner"}),
        (None, {"field": "tags", "op": "missing"}),
        (None, {"field": "lastChangedAt", "op": "gte", "value": "yesterday"}),
        (TASK_DEFINITION, {"field": "containerCount", "op": "eq", "value": float("nan")}),
        (None, {"field": "resourceName", "op": "eq", "value": "x" * 501}),
        (None, {"field": "resourceName", "op": "in", "value": ["x"] * 101}),
        (None, {"field": "tags", "op": "hasValue", "value": 1, "key": "Owner"}),
        (None, {"field": "tags", "op": "has", "key": "x" * 501}),
    ],
)
def test_definitions_that_cannot_be_evaluated_are_refused(
    subject_type: str | None, where: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        validate_definition(definition(where, subject_type=subject_type), REGISTRY)


def test_relative_datetime_conditions_need_no_time() -> None:
    validate_definition(
        definition(
            {
                "any": [
                    {"field": "creationDate", "op": "olderThanDays", "value": 29},
                    {"field": "deprecationTime", "op": "beforeNow"},
                    {"field": "deprecationTime", "op": "afterNow"},
                ]
            },
            subject_type="AWS::EC2::Image",
        ),
        REGISTRY,
    )


def test_a_datetime_condition_takes_an_iso_8601_time() -> None:
    validate_definition(
        definition({"field": "lastChangedAt", "op": "gte", "value": "2026-11-01T00:00:00Z"}),
        REGISTRY,
    )


def test_a_related_field_can_be_a_display_column() -> None:
    validate_definition(
        definition(
            {"field": "state", "op": "eq", "value": "running"},
            subject_type="AWS::EC2::Instance",
            columns=["state", "image.creationDate", "image.findingsHighCount"],
        ),
        REGISTRY,
    )
    with pytest.raises(ValueError, match="报表没有这个列"):
        validate_definition(
            definition(
                {"field": "state", "op": "eq", "value": "running"},
                subject_type="AWS::EC2::Instance",
                columns=["image.noSuchField"],
            ),
            REGISTRY,
        )


def test_columns_must_exist_for_the_subject_type() -> None:
    with pytest.raises(ValueError, match="报表没有这个列"):
        validate_definition(
            task_definition({"field": "resourceId", "op": "exists"}, columns=["nope"]), REGISTRY
        )
    with pytest.raises(ValueError, match="列重复"):
        validate_definition(
            task_definition({"field": "resourceId", "op": "exists"}, columns=["family", "family"]),
            REGISTRY,
        )


def test_only_definition_version_1_is_understood() -> None:
    with pytest.raises(ValueError, match="版本"):
        validate_definition(
            ReportDefinition.model_validate(
                {"definitionVersion": 2, "where": {"field": "resourceId", "op": "exists"}}
            ),
            REGISTRY,
        )


def test_condition_groups_need_at_least_one_condition() -> None:
    for where in ({"all": []}, {"any": []}, {}):
        with pytest.raises(ValidationError):
            definition(where)


def test_a_condition_has_exactly_one_form() -> None:
    leaf = {"field": "status", "op": "eq", "value": "active"}
    for where in ({"all": [leaf], "field": "status", "op": "eq"}, {"not": leaf, "any": [leaf]}):
        with pytest.raises(ValidationError):
            definition(where)


def nested_negations(count: int) -> dict[str, Any]:
    tree: dict[str, Any] = {"field": "status", "op": "eq", "value": "active"}
    for _ in range(count):
        tree = {"not": tree}
    return tree


def leaves(count: int) -> dict[str, Any]:
    return {"all": [{"field": "status", "op": "eq", "value": "active"}] * count}


def test_depth_and_size_limits() -> None:
    validate_definition(definition(nested_negations(MAX_DEPTH)), REGISTRY)
    with pytest.raises(ValueError, match="嵌套太深"):
        validate_definition(definition(nested_negations(MAX_DEPTH + 1)), REGISTRY)

    validate_definition(definition(leaves(MAX_CONDITIONS - 1)), REGISTRY)
    with pytest.raises(ValueError, match="条件太多"):
        validate_definition(definition(leaves(MAX_CONDITIONS)), REGISTRY)
