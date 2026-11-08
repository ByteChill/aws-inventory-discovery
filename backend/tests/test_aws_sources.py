"""AWS access layer against moto and devtools/fake_aws (real boto3 over HTTP)."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from app.aws import config_source
from app.aws.credentials import CredentialError, CredentialService
from app.aws.errors import classify
from app.aws.regions import describe_regions
from app.aws.session import make_client
from app.types.hooks import GONE, TYPE_HOOKS, Gone
from app.types.registry import TypeRegistry, validate_hooks
from app.worker.discovery import Discovery, plan_sources
from tests.conftest import AwsEnv

REGISTRY = TypeRegistry.from_directory()
REGION = "us-east-1"


def discovery(aws: AwsEnv, index: int = 0, region: str = REGION) -> Discovery:
    account = aws.account_ids[index]
    return Discovery(aws.session(account), account, region, REGISTRY)


def config_ids(aws: AwsEnv, index: int, region: str, type_name: str) -> set[str]:
    account = aws.account_ids[index]
    return {
        i["resourceId"]
        for i in aws.dataset["config_items"]
        if (i["accountId"], i["awsRegion"], i["resourceType"]) == (account, region, type_name)
    }


def cc_resources(aws: AwsEnv, index: int, region: str, type_name: str) -> dict[str, Any]:
    account = aws.account_ids[index]
    return {
        r["identifier"]: r["properties"]
        for r in aws.dataset["resources"]
        if (r["account_id"], r["region"], r["type_name"]) == (account, region, type_name)
    }


# --- credentials --------------------------------------------------------------------------


def test_expiring_credentials_are_refreshed_through_the_service(
    aws: AwsEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = aws.credential_service()
    real_fetch = CredentialService.fetch
    calls: list[str] = []

    def fetch(self: CredentialService, account_id: str) -> dict[str, Any]:
        calls.append(account_id)
        metadata = real_fetch(self, account_id)
        if len(calls) == 1:  # the first answer is about to expire: botocore must come back
            metadata["expiry_time"] = "2000-01-01T00:00:00Z"
        return metadata

    monkeypatch.setattr(CredentialService, "fetch", fetch)
    from app.aws.session import make_session

    session = make_session(service.credentials_for(aws.account_ids[0]))
    assert describe_regions(session)["us-east-1"] == "opt-in-not-required"
    assert calls == [aws.account_ids[0]] * 2  # initial fetch, then one refresh


def test_credential_service_failures_become_credential_errors(aws: AwsEnv) -> None:
    unknown = CredentialService(f"{aws.fake_url}/credentials", backoff=0)
    with pytest.raises(CredentialError):
        unknown.fetch("not-an-account")  # the service answers 422: no retry, no credentials
    down = CredentialService("http://127.0.0.1:1/credentials", attempts=2, backoff=0)
    with pytest.raises(CredentialError) as excinfo:
        down.fetch(aws.account_ids[0])
    assert classify(excinfo.value).code == "credential_error"
    assert classify(excinfo.value).retryable


def test_regions_report_opt_in_status(aws: AwsEnv) -> None:
    regions = describe_regions(aws.session(aws.account_ids[0]))
    assert regions["ap-east-1"] == "not-opted-in"
    assert regions["eu-west-2"] == "opt-in-not-required"


# --- recorder detection and source planning ----------------------------------------------


def test_recorder_state_and_source_plan(aws: AwsEnv) -> None:
    full = discovery(aws, 0).recorder()
    assert full.recording and full.all_supported
    scheduled = plan_sources(REGISTRY, full)
    assert scheduled["AWS::EC2::VPC"] == "config"
    assert scheduled["AWS::EC2::Image"] == "native"
    on_demand = plan_sources(REGISTRY, full, on_demand=True)
    assert on_demand["AWS::EC2::VPC"] == "cloudcontrol"
    assert on_demand["AWS::EC2::Image"] == "native"

    subset = discovery(aws, 6).recorder()
    assert subset.as_json()["resourceTypes"] == [
        "AWS::EC2::VPC",
        "AWS::EC2::Subnet",
        "AWS::EC2::Instance",
    ]
    plan = plan_sources(REGISTRY, subset)
    assert plan["AWS::EC2::Subnet"] == "config"
    assert plan["AWS::SQS::Queue"] == "cloudcontrol"  # not recorded: Cloud Control

    off = discovery(aws, 2, "eu-west-2").recorder()
    assert not off.recording
    assert set(plan_sources(REGISTRY, off).values()) == {"cloudcontrol", "native"}


# --- Config -------------------------------------------------------------------------------


def test_config_sweep_pages_through_all_items(aws: AwsEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_source, "PAGE_SIZE", 3)  # force several NextToken round trips
    types = ["AWS::EC2::VPC", "AWS::EC2::Subnet", "AWS::SQS::Queue", "AWS::ECS::Cluster"]
    sweep = discovery(aws).sweep_config(types)
    assert sweep.complete and sweep.error is None
    for type_name in types:
        got = {o.item.resource_id for o in sweep.by_type[type_name]}
        assert got == config_ids(aws, 0, REGION, type_name) != set()
    vpc = sweep.by_type["AWS::EC2::VPC"][0]
    assert vpc.source == "config" and vpc.live_raw is None
    assert vpc.observed_at.year == 2026  # the item's configurationItemCaptureTime
    assert vpc.relationships


def test_config_sweep_failure_is_incomplete(aws: AwsEnv) -> None:
    aws.admin(
        "POST", "/errors", json={"error": "AccessDenied", "operation": "SelectResourceConfig"}
    )
    sweep = discovery(aws).sweep_config(["AWS::EC2::VPC"])
    assert not sweep.complete and sweep.error is not None
    assert sweep.error.code == "access_denied" and not sweep.error.retryable


def test_batch_get_status_reports_config_deletion(aws: AwsEnv) -> None:
    account = aws.account_ids[0]
    (vpc_id, *_) = sorted(config_ids(aws, 0, REGION, "AWS::EC2::VPC"))
    aws.admin(
        "DELETE",
        "/resources",
        params={
            "account_id": account,
            "region": REGION,
            "type_name": "AWS::EC2::VPC",
            "config_resource_id": vpc_id,
        },
    )
    client = make_client(aws.session(account), "config", REGION)
    status = config_source.batch_get_status(
        client, [("AWS::EC2::VPC", vpc_id), ("AWS::EC2::VPC", "vpc-never-discovered")]
    )
    assert status[("AWS::EC2::VPC", vpc_id)].deleted
    assert ("AWS::EC2::VPC", "vpc-never-discovered") not in status


# --- Cloud Control ------------------------------------------------------------------------


def test_scan_live_lists_and_maps_every_resource(aws: AwsEnv) -> None:
    scan = discovery(aws).scan_live("AWS::SQS::Queue")
    assert scan.complete and not scan.errors
    expected = cc_resources(aws, 0, REGION, "AWS::SQS::Queue")
    assert len(scan.observations) == len(expected) > 1
    first = scan.observations[0]
    assert first.source == "cloudcontrol" and first.live_raw is not None
    assert first.item.account_id == aws.account_ids[0] and first.item.aws_region == REGION
    assert first.observed_at == scan.observed_at


def test_fetch_returns_observation_or_gone(aws: AwsEnv) -> None:
    disc = discovery(aws)
    scan = disc.scan_live("AWS::SQS::Queue")
    target = scan.observations[0]
    found = disc.fetch("AWS::SQS::Queue", target.item.resource_id)
    assert not isinstance(found, Gone) and found.item.resource_id == target.item.resource_id
    aws.admin(
        "DELETE",
        "/resources",
        params={
            "account_id": aws.account_ids[0],
            "region": REGION,
            "type_name": "AWS::SQS::Queue",
            "identifier": target.item.resource_id,
        },
    )
    assert disc.fetch("AWS::SQS::Queue", target.item.resource_id) is GONE


def test_composite_identifier_comes_from_the_stored_configuration(aws: AwsEnv) -> None:
    disc = discovery(aws)
    sweep = disc.sweep_config(["AWS::ECS::Service"])
    stored = sweep.by_type["AWS::ECS::Service"][0]
    found = disc.fetch("AWS::ECS::Service", stored.item.resource_id, stored.item.configuration)
    assert not isinstance(found, Gone)
    assert found.item.resource_id == stored.item.resource_id


def test_errors_are_never_gone_and_are_classified(aws: AwsEnv) -> None:
    disc = discovery(aws)
    queue = disc.scan_live("AWS::SQS::Queue").observations[0].item.resource_id
    aws.admin("POST", "/errors", json={"error": "AccessDenied", "operation": "GetResource"})
    with pytest.raises(ClientError) as excinfo:
        disc.fetch("AWS::SQS::Queue", queue)
    assert classify(excinfo.value).code == "access_denied"

    aws.admin("DELETE", "/errors")
    aws.admin("POST", "/errors", json={"error": "AccessDenied", "operation": "ListResources"})
    scan = disc.scan_live("AWS::SQS::Queue")
    assert not scan.complete and scan.errors[0].code == "access_denied"


def test_throttling_is_retried_by_botocore_then_classified(aws: AwsEnv) -> None:
    disc = discovery(aws)
    aws.admin(
        "POST", "/errors", json={"error": "Throttling", "operation": "ListResources", "count": 1}
    )
    assert disc.scan_live("AWS::SQS::Queue").complete  # adaptive retry absorbs one throttle

    aws.admin("POST", "/errors", json={"error": "Throttling", "operation": "ListResources"})
    one_try = aws.session(aws.account_ids[0]).client(
        "cloudcontrol", region_name=REGION, config=Config(retries={"max_attempts": 1})
    )
    with pytest.raises(ClientError) as excinfo:
        one_try.list_resources(TypeName="AWS::SQS::Queue")
    assert classify(excinfo.value).code == "throttled" and classify(excinfo.value).retryable


def test_unreachable_endpoint_is_classified(aws: AwsEnv) -> None:
    client = aws.session(aws.account_ids[0]).client(
        "cloudcontrol",
        region_name=REGION,
        endpoint_url="http://127.0.0.1:1",
        config=Config(retries={"max_attempts": 1}),
    )
    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - classified right below
        client.list_resources(TypeName="AWS::SQS::Queue")
    assert classify(excinfo.value).code == "endpoint_unreachable"


# --- AMI (native) -------------------------------------------------------------------------


def test_ami_listing_merges_owned_and_shared_by_image_id(aws: AwsEnv) -> None:
    disc = discovery(aws)
    scan = disc.scan_live("AWS::EC2::Image")
    assert scan.complete
    ids = Counter(o.item.resource_id for o in scan.observations)
    assert all(count == 1 for count in ids.values())  # present in both queries, listed once
    names = {o.item.resource_name for o in scan.observations}
    assert names == {"RHEL-8.0.0_HVM-20190618-x86_64-1-Hourly2-GP2", "example-shared-ami.1"}
    assert {o.item.configuration["ownership"] for o in scan.observations} == {"self"}
    assert all(o.source == "native" for o in scan.observations)
    # Limitation of moto: launch permissions are not visible across accounts and
    # ExecutableUsers=["self"] only returns the account's own images, so no `shared` AMI shows.


def test_ami_fetch_distinguishes_found_gone_and_error(aws: AwsEnv) -> None:
    account = aws.account_ids[0]
    disc = discovery(aws)
    own = disc.scan_live("AWS::EC2::Image").observations[0]
    found = disc.fetch("AWS::EC2::Image", own.item.resource_id)
    assert not isinstance(found, Gone) and found.item.resource_id == own.item.resource_id

    make_client(aws.session(account), "ec2", REGION).deregister_image(ImageId=own.item.resource_id)
    assert disc.fetch("AWS::EC2::Image", own.item.resource_id) is GONE
    assert disc.fetch("AWS::EC2::Image", "ami-00000000000000000") is GONE

    with pytest.raises(ClientError) as excinfo:
        disc.fetch("AWS::EC2::Image", "not-an-image-id")  # malformed is an error, not "gone"
    assert classify(excinfo.value).code == "internal"


def test_hook_table_satisfies_the_registry() -> None:
    validate_hooks(REGISTRY, TYPE_HOOKS)
    # ECS service only derives its composite Cloud Control identifier, see types/hooks.py
    assert set(TYPE_HOOKS) == {"AWS::EC2::Image", "AWS::ECS::Service"}
