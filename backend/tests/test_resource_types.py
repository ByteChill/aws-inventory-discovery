"""Resource type specs against the official-doc fixtures in tests/fixtures/aws/."""

from __future__ import annotations

import copy
import json
import shutil
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from app.types.columns import column_paths, column_values
from app.types.hooks import TYPE_HOOKS, Hooks
from app.types.mapping import (
    MISSING,
    MappingError,
    ami_gone,
    ami_to_config_item,
    camel_key,
    cloudcontrol_identifier,
    config_path_of,
    convert_configuration,
    covered_paths,
    get_path,
    overlay,
    split_identifier,
    to_config_item,
)
from app.types.mapping import (
    Path as KeyPath,
)
from app.types.redact import MASK, redact_configuration, redact_live_raw
from app.types.registry import RegistryError, TypeRegistry, validate_hooks
from app.types.spec import ResourceTypeSpec

FIXTURES = Path(__file__).parent / "fixtures" / "aws"
REGISTRY = TypeRegistry.from_directory()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Case(NamedTuple):
    spec: ResourceTypeSpec
    properties: dict[str, Any]
    identifier: str
    config_item: dict[str, Any]


def fixture_cases() -> list[Any]:
    cases = []
    for spec in REGISTRY:
        folder = FIXTURES / spec.file_name.removesuffix(".json")
        for get_file in sorted(folder.glob("cloudcontrol_get*.json")):
            config_file = folder / get_file.name.replace("cloudcontrol_get", "config_item")
            if not config_file.exists():
                continue
            description = load(get_file)["ResourceDescription"]
            case = Case(
                spec,
                json.loads(description["Properties"]),
                description["Identifier"],
                load(config_file),
            )
            cases.append(pytest.param(case, id=f"{spec.type_name}:{get_file.stem}"))
    return cases


CASES = fixture_cases()


def map_case(case: Case) -> Any:
    return to_config_item(
        case.spec,
        case.properties,
        account_id=case.config_item["accountId"],
        region=case.config_item["awsRegion"],
    )


def leaf_paths(data: dict[str, Any], prefix: KeyPath = ()) -> list[KeyPath]:
    paths = []
    for key, value in data.items():
        if isinstance(value, dict) and value:
            paths.extend(leaf_paths(value, (*prefix, key)))
        else:
            paths.append((*prefix, key))
    return paths


def under(path: KeyPath, covered: frozenset[KeyPath]) -> bool:
    return any(path[:n] in covered for n in range(1, len(path) + 1))


# --------------------------------------------------------------------------- registry


def test_registry_loads_a_spec_per_fixture_folder() -> None:
    folders = {path.name for path in FIXTURES.glob("aws.*") if path.is_dir()}
    assert {spec.file_name.removesuffix(".json") for spec in REGISTRY} == folders
    assert len(REGISTRY) == 12


def test_invalid_spec_file_fails_loudly(tmp_path: Path) -> None:
    specs_dir = Path(__file__).parents[1] / "app" / "types" / "specs"
    shutil.copytree(specs_dir, tmp_path / "specs")
    broken = tmp_path / "specs" / "aws.ec2.vpc.json"
    data = load(broken)
    data["mapping"]["keyCase"] = "snake"
    broken.write_text(json.dumps(data))
    with pytest.raises(RegistryError, match=r"aws\.ec2\.vpc\.json"):
        TypeRegistry.from_directory(tmp_path / "specs")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("properties", ["Foo[].Bar"]),
        ("rename", {"Foo[].Bar": "baz"}),
        ("rename", {"Foo": "baz[].qux"}),
    ],
)
def test_covered_paths_inside_an_array_are_rejected_when_the_registry_loads(
    tmp_path: Path, field: str, value: Any
) -> None:
    # Arrays are replaced whole; a path into one could never be overlaid.
    specs_dir = Path(__file__).parents[1] / "app" / "types" / "specs"
    shutil.copytree(specs_dir, tmp_path / "specs")
    broken = tmp_path / "specs" / "aws.ec2.vpc.json"
    data = load(broken)
    data["mapping"][field] = value
    broken.write_text(json.dumps(data))
    with pytest.raises(RegistryError, match=r"(?s)aws\.ec2\.vpc\.json.*inside an array"):
        TypeRegistry.from_directory(tmp_path / "specs")


def test_native_type_needs_both_hooks() -> None:
    validate_hooks(REGISTRY, TYPE_HOOKS)  # the real table registers the AMI hooks
    with pytest.raises(RegistryError, match="AWS::EC2::Image"):
        validate_hooks(REGISTRY, {})

    def stub(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    validate_hooks(REGISTRY, {"AWS::EC2::Image": Hooks(list_live=stub, get_live=stub)})
    with pytest.raises(RegistryError, match="AWS::EC2::Image"):
        validate_hooks(REGISTRY, {"AWS::EC2::Image": Hooks(list_live=stub)})


# --------------------------------------------------------------------------- mapping


@pytest.mark.parametrize(
    ("key", "expected"),
    [("VpcId", "vpcId"), ("VPCId", "vpcId"), ("DNSName", "dnsName"), ("Arn", "arn"), ("ID", "id")],
)
def test_camel_key_rule(key: str, expected: str) -> None:
    assert camel_key(key) == expected


@pytest.mark.parametrize("case", CASES)
def test_mapped_cloud_control_data_equals_config(case: Case) -> None:
    spec, config = case.spec, case.config_item
    item = map_case(case)

    compared = 0
    for path in covered_paths(spec):
        expected, actual = (
            get_path(config["configuration"], path),
            get_path(item.configuration, path),
        )
        if expected is not MISSING and actual is not MISSING:
            assert actual == expected, ".".join(path)
            compared += 1
    assert compared

    assert item.arn == config["arn"]
    assert item.resource_id == config["resourceId"]
    if "resourceName" in config:
        assert item.resource_name == config["resourceName"]
    assert [tag.model_dump() for tag in item.tags] == config["tags"]
    assert cloudcontrol_identifier(spec, item.configuration) == case.identifier
    assert "|".join(split_identifier(spec, case.identifier).values()) == case.identifier


@pytest.mark.parametrize("case", CASES)
def test_cloud_control_identifier_can_be_derived_from_the_resource_id_alone(case: Case) -> None:
    """A first-time refresh has only the resource id (no stored configuration): it must yield
    the Cloud Control Identifier the type's fixture documents (`ClusterName` for an ECS cluster
    whose resource id is its ARN)."""
    spec = case.spec
    resource_id = map_case(case).resource_id
    hooks = TYPE_HOOKS.get(spec.type_name, Hooks())
    if hooks.cloudcontrol_identifier is not None:
        derived = hooks.cloudcontrol_identifier(resource_id)
    elif spec.identity.identifier_fields == [spec.identity.resource_id_from]:
        derived = resource_id
    else:
        pytest.fail(f"{spec.type_name}: no way to derive the identifier from the resource id")
    assert derived == case.identifier


@pytest.mark.parametrize("case", CASES)
def test_every_fixture_property_is_accounted_for(case: Case) -> None:
    mapping = case.spec.mapping
    known = {*mapping.properties, *mapping.rename, *mapping.exclude, "Tags"}
    assert set(case.properties) <= known


@pytest.mark.parametrize("case", CASES)
def test_overlay_follows_new_data_and_keeps_the_rest(case: Case) -> None:
    existing = case.config_item["configuration"]
    new = map_case(case).configuration
    covered = covered_paths(case.spec)

    result = overlay(existing, new, covered)

    for path in covered:
        assert get_path(result, path) == get_path(new, path)
    for path in leaf_paths(existing):
        if not under(path, covered):
            assert get_path(result, path) == get_path(existing, path), ".".join(path)


@pytest.mark.parametrize("case", CASES)
def test_overlay_clears_a_covered_field_removed_from_new_data(case: Case) -> None:
    spec, existing = case.spec, case.config_item["configuration"]
    candidates = [
        key
        for key in case.properties
        if key in spec.mapping.properties
        and get_path(existing, config_path_of(spec, (key,))) is not MISSING
    ]
    assert candidates
    removed = candidates[0]
    properties = {k: v for k, v in case.properties.items() if k != removed}
    new = convert_configuration(spec, properties)

    result = overlay(existing, new, covered_paths(spec))

    assert get_path(result, config_path_of(spec, (removed,))) is MISSING


def array_paths(case: Case) -> list[KeyPath]:
    covered, existing = covered_paths(case.spec), case.config_item["configuration"]
    return sorted(p for p in covered if isinstance(get_path(existing, p), list))


ARRAY_CASES: list[Any] = [
    pytest.param(c.values[0], id=c.id) for c in CASES if array_paths(c.values[0])
]


@pytest.mark.parametrize("case", ARRAY_CASES)
def test_overlay_replaces_a_covered_array_as_one_value(case: Case) -> None:
    existing = case.config_item["configuration"]
    for path in array_paths(case):
        old = get_path(existing, path)
        shorter = copy.deepcopy(map_case(case).configuration)
        parent = get_path(shorter, path[:-1]) if len(path) > 1 else shorter
        parent[path[-1]] = old[:-1]

        result = overlay(existing, shorter, covered_paths(case.spec))

        assert get_path(result, path) == old[:-1]


def test_overlay_refuses_a_covered_path_that_runs_through_an_array() -> None:
    case: Case = ARRAY_CASES[0].values[0]
    path = array_paths(case)[0]
    existing = case.config_item["configuration"]

    with pytest.raises(MappingError, match="inside an array"):
        overlay(existing, map_case(case).configuration, [(*path, "anything")])


# --------------------------------------------------------------------------- redaction

PLAINTEXT: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "AWS::Lambda::Function": lambda p: list(p["Environment"]["Variables"].values()),
    "AWS::ECS::TaskDefinition": lambda p: [
        e["Value"]
        for c in p["ContainerDefinitions"]
        for e in c["Environment"]
        if "PASSWORD" in e["Name"]
    ],
    "AWS::EC2::Instance": lambda p: [p["UserData"]],
    "AWS::SNS::Topic": lambda p: [s["Endpoint"] for s in p["Subscription"]],
}
REDACTED_CASES = [c for c in CASES if c.values[0].spec.redact]


@pytest.mark.parametrize("case", REDACTED_CASES)
def test_redaction_leaves_no_plaintext(case: Case) -> None:
    spec = case.spec
    secrets = PLAINTEXT[spec.type_name](case.properties)
    assert secrets
    merged = overlay(
        case.config_item["configuration"], map_case(case).configuration, covered_paths(spec)
    )
    before = copy.deepcopy((case.properties, merged))

    outputs = [
        redact_configuration(spec, merged),
        redact_configuration(spec, case.config_item["configuration"]),
        redact_live_raw(spec, case.properties),
    ]

    assert (case.properties, merged) == before
    for output in outputs:
        text = json.dumps(output)
        assert not any(secret in text for secret in secrets)


def test_lambda_environment_keeps_names_and_masks_values() -> None:
    case = next(c.values[0] for c in CASES if c.values[0].spec.type_name == "AWS::Lambda::Function")
    names = list(case.properties["Environment"]["Variables"])
    raw = redact_live_raw(case.spec, case.properties)
    configuration = redact_configuration(case.spec, map_case(case).configuration)
    assert raw["Environment"]["Variables"] == dict.fromkeys(names, MASK)
    assert configuration["environment"]["variables"] == dict.fromkeys(names, MASK)


def test_task_definition_masks_every_container_environment_value() -> None:
    case = next(
        c.values[0] for c in CASES if c.values[0].spec.type_name == "AWS::ECS::TaskDefinition"
    )
    for output in (
        redact_live_raw(case.spec, case.properties),
        redact_configuration(case.spec, case.config_item["configuration"]),
    ):
        values = [e["Value"] for c in output["ContainerDefinitions"] for e in c["Environment"]]
        assert values
        assert set(values) == {MASK}


def test_instance_user_data_is_removed() -> None:
    case = next(c.values[0] for c in CASES if c.values[0].spec.type_name == "AWS::EC2::Instance")
    assert "UserData" not in redact_live_raw(case.spec, case.properties)
    assert "userData" not in redact_configuration(case.spec, map_case(case).configuration)


def test_sns_subscriptions_are_summarised_by_protocol() -> None:
    case = next(c.values[0] for c in CASES if c.values[0].spec.type_name == "AWS::SNS::Topic")
    expected = dict(Counter(s["Protocol"] for s in case.properties["Subscription"]))
    assert redact_live_raw(case.spec, case.properties)["Subscription"] == expected
    assert redact_configuration(case.spec, map_case(case).configuration)["Subscription"] == expected


# --------------------------------------------------------------------------- columns


def ami_item(image_file: str, account_id: str) -> Any:
    image = load(FIXTURES / "aws.ec2.image" / image_file)["Images"][0]
    return ami_to_config_item(
        REGISTRY.get("AWS::EC2::Image"), image, account_id=account_id, region="us-east-1"
    )


def main_account() -> str:
    return load(FIXTURES / "aws.ec2.vpc" / "config_item.json")["accountId"]


@pytest.mark.parametrize("case", CASES)
def test_every_column_has_a_value(case: Case) -> None:
    configuration = overlay(
        case.config_item["configuration"], map_case(case).configuration, covered_paths(case.spec)
    )
    values = column_values(case.spec, configuration)
    assert values
    assert {k: v for k, v in values.items() if v is None} == {}
    assert set(column_paths(case.spec)) == set(values)


@pytest.mark.parametrize("image_file", ["describe_images_self.json", "describe_images_shared.json"])
def test_every_ami_column_has_a_value(image_file: str) -> None:
    spec = REGISTRY.get("AWS::EC2::Image")
    values = column_values(spec, ami_item(image_file, main_account()).configuration)
    assert {k: v for k, v in values.items() if v is None} == {}


# --------------------------------------------------------------------------- AMI


@pytest.mark.parametrize(
    ("image_file", "ownership"),
    [("describe_images_self.json", "self"), ("describe_images_shared.json", "shared")],
)
def test_ami_maps_with_ownership(image_file: str, ownership: str) -> None:
    image = load(FIXTURES / "aws.ec2.image" / image_file)["Images"][0]
    item = ami_item(image_file, main_account())
    assert item.resource_id == image["ImageId"]
    assert item.arn == f"arn:aws:ec2:us-east-1::image/{image['ImageId']}"
    assert item.resource_name == image["Name"]
    assert item.configuration["ownership"] == ownership
    assert item.configuration["ownerId"] == image["OwnerId"]


@pytest.mark.parametrize("image_file", ["describe_images_self.json", "describe_images_shared.json"])
def test_every_ami_field_is_accounted_for(image_file: str) -> None:
    image = load(FIXTURES / "aws.ec2.image" / image_file)["Images"][0]
    mapping = REGISTRY.get("AWS::EC2::Image").mapping
    assert set(image) <= {*mapping.properties, *mapping.rename, *mapping.exclude, "Tags"}


def test_ami_overlay_clears_a_field_that_disappeared_from_describe_images() -> None:
    spec = REGISTRY.get("AWS::EC2::Image")
    image = load(FIXTURES / "aws.ec2.image" / "describe_images_self.json")["Images"][0]
    existing = ami_item("describe_images_self.json", main_account()).configuration
    assert existing["description"]
    without = {k: v for k, v in image.items() if k != "Description"}
    new = ami_to_config_item(spec, without, account_id=main_account(), region="us-east-1")

    result = overlay(existing, new.configuration, covered_paths(spec))

    assert "description" not in result
    assert result["ownership"] == existing["ownership"]


@pytest.mark.parametrize(
    ("response_file", "gone"),
    [
        ("describe_images_by_id_deregistered.json", True),
        ("describe_images_by_id_empty.json", True),
        ("describe_images_self.json", False),
    ],
)
def test_ami_gone(response_file: str, gone: bool) -> None:
    assert ami_gone(load(FIXTURES / "aws.ec2.image" / response_file)) is gone
