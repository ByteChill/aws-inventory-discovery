from __future__ import annotations

import json

import httpx
import pytest
from botocore.exceptions import ClientError

import seed

from .conftest import ACCOUNT_IDS, REGIONS, Env

A, B, NO_CONFIG = ACCOUNT_IDS
SELECT = (
    "SELECT arn, resourceId, resourceName, resourceType, awsRegion, accountId, configuration, "
    "supplementaryConfiguration, tags, relationships, configurationItemCaptureTime, "
    "configurationItemStatus WHERE resourceType IN ({types})"
)


def expected_resources(env: Env, account: str, region: str, type_name: str) -> dict[str, dict]:
    return {
        r["identifier"]: r["properties"]
        for r in env.dataset["resources"]
        if (r["account_id"], r["region"], r["type_name"]) == (account, region, type_name)
    }


def expected_items(env: Env, account: str, region: str, types: list[str]) -> list[dict]:
    return [
        i
        for i in env.dataset["config_items"]
        if (i["accountId"], i["awsRegion"]) == (account, region) and i["resourceType"] in types
    ]


def select_all(config, types: list[str], limit: int) -> tuple[list[dict], int]:
    expression = SELECT.format(types=", ".join(f"'{t}'" for t in types))
    rows, pages, token = [], 0, None
    while True:
        kwargs = {"Expression": expression, "Limit": limit}
        if token:
            kwargs["NextToken"] = token
        response = config.select_resource_config(**kwargs)
        rows += [json.loads(r) for r in response["Results"]]
        pages += 1
        token = response.get("NextToken")
        if not token:
            return rows, pages


def test_registry(env: Env):
    entries = httpx.get(f"{env.fake_url}/registry/accounts").json()
    assert entries == env.dataset["registry"]


def test_cloudcontrol_list_paginates_and_get(env: Env):
    cc = env.client("cloudcontrol", A, "us-east-1")
    type_name = "AWS::ECS::Cluster"
    expected = expected_resources(env, A, "us-east-1", type_name)
    assert len(expected) == 2

    pages = list(
        cc.get_paginator("list_resources").paginate(
            TypeName=type_name, PaginationConfig={"PageSize": 1}
        )
    )
    assert [len(p["ResourceDescriptions"]) for p in pages] == [1, 1]
    listed = {
        d["Identifier"]: json.loads(d["Properties"])
        for p in pages
        for d in p["ResourceDescriptions"]
    }
    assert listed == expected

    identifier = next(iter(expected))
    got = cc.get_resource(TypeName=type_name, Identifier=identifier)["ResourceDescription"]
    assert json.loads(got["Properties"]) == expected[identifier]

    with pytest.raises(cc.exceptions.ResourceNotFoundException):
        cc.get_resource(TypeName=type_name, Identifier="no-such-cluster")


def test_config_select_in_paginates_and_scopes_by_account_and_region(env: Env):
    types = ["AWS::EC2::VPC", "AWS::EC2::Subnet", "AWS::SQS::Queue", "AWS::ECS::Cluster"]
    config = env.client("config", A, "us-east-1")
    rows, pages = select_all(config, types, limit=2)
    expected = expected_items(env, A, "us-east-1", types)
    assert pages > 1
    ordered = sorted(expected, key=lambda i: (i["resourceType"], i["resourceId"]))
    assert len(rows) == len(ordered)
    for row, item in zip(rows, ordered, strict=True):
        assert row == {k: item[k] for k in row}
        assert row["resourceId"] == item["resourceId"]

    response = config.select_resource_config(
        Expression=SELECT.split(" WHERE")[0] + " WHERE resourceType = 'AWS::EC2::VPC'", Limit=100
    )
    assert response["QueryInfo"]["SelectFields"][0] == {"Name": "arn"}
    assert len(response["Results"]) == len(expected_items(env, A, "us-east-1", ["AWS::EC2::VPC"]))

    other_region, _ = select_all(env.client("config", A, "eu-west-2"), types, limit=100)
    other_account, _ = select_all(env.client("config", B, "us-east-1"), types, limit=100)
    ids = {r["resourceId"] for r in rows}
    assert {r["awsRegion"] for r in other_region} == {"eu-west-2"}
    assert {r["accountId"] for r in other_account} == {B}
    assert ids.isdisjoint(r["resourceId"] for r in other_region + other_account)


def test_config_recorders(env: Env):
    config = env.client("config", A, "us-east-1")
    recorders = config.describe_configuration_recorders()["ConfigurationRecorders"]
    expected = next(
        r for r in env.dataset["recorders"] if (r["account_id"], r["region"]) == (A, "us-east-1")
    )
    assert recorders[0]["name"] == expected["configuration_recorder"]["name"]
    assert recorders[0]["recordingGroup"]["allSupported"] is True
    status = config.describe_configuration_recorder_status()["ConfigurationRecordersStatus"]
    assert status[0]["recording"] is True
    assert status[0]["lastStartTime"].year == 2026

    off = env.client("config", NO_CONFIG, "eu-west-2")
    assert off.describe_configuration_recorders()["ConfigurationRecorders"] == []
    assert off.describe_configuration_recorder_status()["ConfigurationRecordersStatus"] == []
    assert select_all(off, ["AWS::EC2::VPC"], limit=100)[0] == []


def test_deleted_resource_is_gone_from_both_sources(env: Env):
    cc = env.client("cloudcontrol", A, "us-east-1")
    config = env.client("config", A, "us-east-1")
    type_name = "AWS::SQS::Queue"
    item = expected_items(env, A, "us-east-1", [type_name])[0]
    identifier = next(iter(expected_resources(env, A, "us-east-1", type_name)))
    key = {"resourceType": type_name, "resourceId": item["resourceId"]}

    assert (
        config.batch_get_resource_config(resourceKeys=[key])["baseConfigurationItems"][0][
            "configurationItemStatus"
        ]
        == "OK"
    )

    result = httpx.delete(
        f"{env.fake_url}/_admin/resources",
        params={
            "account_id": A,
            "region": "us-east-1",
            "type_name": type_name,
            "identifier": identifier,
            "config_resource_id": item["resourceId"],
        },
    ).json()
    assert result == {"cloudcontrol": True, "config": True}

    base = config.batch_get_resource_config(resourceKeys=[key])["baseConfigurationItems"][0]
    assert base["configurationItemStatus"] == "ResourceDeleted"
    assert json.loads(base["configuration"]) == item["configuration"]
    remaining, _ = select_all(config, [type_name], limit=100)
    assert item["resourceId"] not in {r["resourceId"] for r in remaining}
    with pytest.raises(cc.exceptions.ResourceNotFoundException):
        cc.get_resource(TypeName=type_name, Identifier=identifier)


def test_credentials_come_from_moto_and_calls_are_attributed_to_the_account(env: Env):
    seed.create_amis(env.fake_url, env.moto_url, ACCOUNT_IDS, REGIONS)

    for account in (A, B):
        sts = env.client("sts", account, "us-east-1")
        assert sts.get_caller_identity()["Account"] == account

    ec2_a, ec2_b = env.client("ec2", A, "us-east-1"), env.client("ec2", B, "us-east-1")
    own_a = ec2_a.describe_images(Owners=["self"])["Images"]
    own_b = ec2_b.describe_images(Owners=["self"])["Images"]
    assert {i["OwnerId"] for i in own_a} == {A}
    assert {i["OwnerId"] for i in own_b} == {B}
    # moto keeps AMIs per account, so launch permissions are visible to the owner only.
    shared = ec2_a.describe_images(Owners=["self"], ExecutableUsers=[B])["Images"]
    shared_fixture = json.loads(
        (seed.FIXTURES / seed.IMAGE_FIXTURE / "describe_images_shared.json").read_text()
    )["Images"][0]
    assert shared_fixture["Name"] in {i["Name"] for i in shared}

    ids_a = {
        d["Identifier"]
        for d in env.client("cloudcontrol", A, "us-east-1").list_resources(
            TypeName="AWS::EC2::VPC"
        )["ResourceDescriptions"]
    }
    ids_b = {
        d["Identifier"]
        for d in env.client("cloudcontrol", B, "us-east-1").list_resources(
            TypeName="AWS::EC2::VPC"
        )["ResourceDescriptions"]
    }
    assert ids_a == set(expected_resources(env, A, "us-east-1", "AWS::EC2::VPC"))
    assert ids_b == set(expected_resources(env, B, "us-east-1", "AWS::EC2::VPC"))
    assert ids_a.isdisjoint(ids_b)


def _list_vpcs(env: Env, account: str):
    return env.client("cloudcontrol", account, "us-east-1").list_resources(TypeName="AWS::EC2::VPC")


def _describe_recorders(env: Env, account: str):
    return env.client("config", account, "us-east-1").describe_configuration_recorders()


def _get_vpc(env: Env, account: str):
    identifier = next(iter(expected_resources(env, account, "us-east-1", "AWS::EC2::VPC")))
    return env.client("cloudcontrol", account, "us-east-1").get_resource(
        TypeName="AWS::EC2::VPC", Identifier=identifier
    )


@pytest.mark.parametrize(
    ("error", "code", "call"),
    [
        ("AccessDenied", "AccessDeniedException", _list_vpcs),
        ("Throttling", "ThrottlingException", _describe_recorders),
        ("ResourceNotFound", "ResourceNotFoundException", _get_vpc),
    ],
)
def test_injected_errors_surface_as_botocore_errors(env: Env, error, code, call):
    httpx.post(
        f"{env.fake_url}/_admin/errors",
        json={"error": error, "account_id": A, "region": "us-east-1"},
    ).raise_for_status()

    with pytest.raises(ClientError) as raised:
        call(env, A)
    assert raised.value.response["Error"]["Code"] == code
    call(env, B)  # other accounts are unaffected

    httpx.delete(f"{env.fake_url}/_admin/errors").raise_for_status()
    call(env, A)


def test_unknown_access_key_is_rejected(env: Env):
    client = env.client("cloudcontrol", A, "us-east-1")
    client._request_signer._credentials.access_key = "AKIAUNKNOWN"
    with pytest.raises(ClientError) as raised:
        client.list_resources(TypeName="AWS::EC2::VPC")
    assert raised.value.response["Error"]["Code"] == "UnrecognizedClientException"
