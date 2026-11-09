"""Demo seed: copies the official-doc fixtures to 20 accounts x 4 regions and loads them into
fake-aws (registry, Config recorders and items, Cloud Control resources) and moto (AMIs).

Only the account id, the region and the resource identifiers (and the ARNs, URLs and names that
embed them) change; field structure and every other value stay as in the fixture.

    uv run python seed.py --reset
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import httpx
from botocore.config import Config as BotoConfig

FIXTURES = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures" / "aws"
REGIONS = ("ap-east-1", "us-east-1", "eu-west-1", "eu-west-2")
ACCOUNT_COUNT = 20
FIXTURE_ACCOUNT = "111122223333"
FIXTURE_REGION = "us-east-1"
# Global types (Route 53): one copy per account, read through the home region's endpoints.
GLOBAL_REGION = "global"
GLOBAL_HOME = "us-east-1"
IMAGE_FIXTURE = "aws.ec2.image"

# Config is off in these (account index, region); one account records only a subset; one
# recorder exists but is stopped. Everything else records all supported types continuously.
CONFIG_OFF = {(2, "eu-west-2"), (10, "ap-east-1")}
CONFIG_STOPPED = {(14, "eu-west-1")}
CONFIG_SUBSET = {
    (6, "us-east-1"): ["AWS::EC2::VPC", "AWS::EC2::Subnet", "AWS::EC2::Instance"],
}
RECORDER_TIME = "2026-10-01T08:00:00Z"

_TEAMS = ("payments", "ledger", "risk", "treasury", "markets", "onboarding", "reporting")
_ACCOUNT_TYPES = ("dev", "preprod", "prod")

# --- fixture copying -------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"(?<![0-9A-Za-z])(?P<prefix>(?:vpc|subnet|sg|eni|snap|vol|dopt|ami|i|cidr-assoc)-)"
    r"(?P<tail>[0-9a-zA-Z]{8,17})(?![0-9A-Za-z-])"
    r"|(?<=[/-])(?P<hash>[0-9a-f]{16})(?![0-9A-Za-z])"
)
_SECRET_RE = re.compile(r"(secret:[A-Za-z0-9/_+=.@-]+-)([A-Za-z0-9]{6})(?![A-Za-z0-9])")


def _digest(account_id: str, region: str, token: str, length: int) -> str:
    return hashlib.sha256(f"{account_id}|{region}|{token}".encode()).hexdigest()[:length]


def remap(value: Any, account_id: str, region: str) -> Any:
    """A copy of a fixture value with only account, region and resource identifiers changed."""

    def token(match: re.Match[str]) -> str:
        if match["hash"]:
            return _digest(account_id, region, match["hash"], 16)
        tail = match["tail"]
        return match["prefix"] + _digest(account_id, region, match["prefix"] + tail, len(tail))

    text = json.dumps(value)
    text = _TOKEN_RE.sub(token, text)
    text = _SECRET_RE.sub(
        lambda m: m[1] + _digest(account_id, region, m[0], 6),
        text,
    )
    text = text.replace(FIXTURE_ACCOUNT, account_id).replace(FIXTURE_REGION, region)
    return json.loads(text)


def load_fixtures(fixtures: Path = FIXTURES) -> list[dict[str, Any]]:
    """One entry per resource type: its Cloud Control resources and Config items."""
    types = []
    for directory in sorted(fixtures.iterdir()):
        if not directory.is_dir() or directory.name == IMAGE_FIXTURE:
            continue
        listing = json.loads((directory / "cloudcontrol_list.json").read_text())
        resources = [
            (d["Identifier"], json.loads(d["Properties"])) for d in listing["ResourceDescriptions"]
        ]
        items = [json.loads(p.read_text()) for p in sorted(directory.glob("config_item*.json"))]
        types.append(
            {
                "type_name": listing["TypeName"],
                "resources": resources,
                "items": items,
                # a Config item of a global type is filed under the region `global`
                "global": any(item["awsRegion"] == GLOBAL_REGION for item in items),
            }
        )
    return types


# --- registry and Config recorders ------------------------------------------------------------


def account_ids(count: int = ACCOUNT_COUNT) -> list[str]:
    return [f"{111122220000 + i:012d}" for i in range(1, count + 1)]


def build_registry(ids: list[str]) -> list[dict[str, str]]:
    """Account registry ① entries (format assumed in DESIGN section 2)."""
    entries = []
    for index, account_id in enumerate(ids):
        app = index // 2
        entries.append(
            {
                "account_id": account_id,
                "account_name": (
                    f"{_TEAMS[index % len(_TEAMS)]}-{_ACCOUNT_TYPES[index % 3]}-{index + 1:02d}"
                ),
                "account_type": _ACCOUNT_TYPES[index % 3],
                "eim_id": f"{10000000 + app * 111:08d}",
                "eim_app": f"eim-app-{app + 1:02d}",
                "ba_id": f"BA{1000 + (index // 4) * 7:04d}",
            }
        )
    return entries


def build_recorder(
    index: int, account_id: str, region: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str] | None]:
    """(DescribeConfigurationRecorders item, status, recorded types or None for all)."""
    if (index, region) in CONFIG_OFF:
        return None, None, []
    subset = CONFIG_SUBSET.get((index, region))
    group = (
        {
            "allSupported": False,
            "includeGlobalResourceTypes": False,
            "resourceTypes": subset,
            "recordingStrategy": {"useOnly": "INCLUSION_BY_RESOURCE_TYPES"},
        }
        if subset
        else {
            "allSupported": True,
            "includeGlobalResourceTypes": region == "us-east-1",
            "resourceTypes": [],
            "recordingStrategy": {"useOnly": "ALL_SUPPORTED_RESOURCE_TYPES"},
        }
    )
    recorder = {
        "name": "default",
        "roleARN": f"arn:aws:iam::{account_id}:role/aws-service-role/config.amazonaws.com/"
        "AWSServiceRoleForConfig",
        "recordingGroup": group,
        "recordingMode": {"recordingFrequency": "CONTINUOUS", "recordingModeOverrides": []},
    }
    stopped = (index, region) in CONFIG_STOPPED
    status = {
        "recording": not stopped,
        "lastStatus": "SUCCESS",
        "lastStartTime": RECORDER_TIME,
        "lastStatusChangeTime": RECORDER_TIME,
        **({"lastStopTime": RECORDER_TIME} if stopped else {}),
    }
    return recorder, status, ([] if stopped else subset)


# --- dataset -----------------------------------------------------------------------------------


def build_dataset(
    ids: list[str], regions: tuple[str, ...] | list[str], fixtures: Path = FIXTURES
) -> dict[str, Any]:
    """The payload of fake-aws `PUT /_admin/dataset` for these accounts and regions."""
    types = load_fixtures(fixtures)
    dataset: dict[str, Any] = {
        "registry": build_registry(ids),
        "recorders": [],
        "config_items": [],
        "resources": [],
    }
    for index, account_id in enumerate(ids):
        for region in regions:
            recorder, status, recorded = build_recorder(index, account_id, region)
            dataset["recorders"].append(
                {
                    "account_id": account_id,
                    "region": region,
                    "configuration_recorder": recorder,
                    "recorder_status": status,
                }
            )
            for spec in types:
                # global types are copied once per account, into the home region
                if not spec["global"] or region == GLOBAL_HOME:
                    _add_type(dataset, spec, account_id, region, recorded)
    return dataset


def _add_type(
    dataset: dict[str, Any],
    spec: dict[str, Any],
    account_id: str,
    region: str,
    recorded: list[str] | None,
) -> None:
    """Copy one type's Cloud Control resources (served from `region`) and, if the recorder
    records it, its Config items into the dataset."""
    for identifier, props in spec["resources"]:
        dataset["resources"].append(
            {
                "account_id": account_id,
                "region": region,
                "type_name": spec["type_name"],
                "identifier": remap(identifier, account_id, region),
                "properties": remap(props, account_id, region),
            }
        )
    if recorded is not None and spec["type_name"] not in recorded:
        return
    dataset["config_items"].extend(remap(item, account_id, region) for item in spec["items"])


# --- AMIs in moto ----------------------------------------------------------------------------


def ec2_client(fake_url: str, moto_url: str, account_id: str, region: str) -> Any:
    """An EC2 client in moto acting as `account_id` (via the fake's credential service)."""
    creds = httpx.get(f"{fake_url}/credentials", params={"account_id": account_id}).json()
    return boto3.client(
        "ec2",
        endpoint_url=moto_url,
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        config=BotoConfig(retries={"max_attempts": 1}),
    )


_REGISTER_KEYS = (
    "Name",
    "Description",
    "Architecture",
    "RootDeviceName",
    "BlockDeviceMappings",
    "VirtualizationType",
    "EnaSupport",
)


def _register(ec2: Any, image: dict[str, Any]) -> str | None:
    """Register an AMI built from fixture attributes; skip if the account already has one."""
    if ec2.describe_images(Owners=["self"], Filters=[{"Name": "name", "Values": [image["Name"]]}])[
        "Images"
    ]:
        return None
    image_id = ec2.register_image(**{k: image[k] for k in _REGISTER_KEYS if k in image})["ImageId"]
    if image.get("Public"):
        ec2.modify_image_attribute(ImageId=image_id, LaunchPermission={"Add": [{"Group": "all"}]})
    return image_id


def create_amis(
    fake_url: str,
    moto_url: str,
    ids: list[str],
    regions: tuple[str, ...] | list[str],
    fixtures: Path = FIXTURES,
) -> None:
    """Own AMI in every account/region; the first account shares one AMI per region with the next
    five accounts via launch permissions; one account deregisters its AMI in eu-west-1."""
    own = json.loads((fixtures / IMAGE_FIXTURE / "describe_images_self.json").read_text())[
        "Images"
    ][0]
    shared = json.loads((fixtures / IMAGE_FIXTURE / "describe_images_shared.json").read_text())[
        "Images"
    ][0]
    for region in regions:
        for index, account_id in enumerate(ids):
            ec2 = ec2_client(fake_url, moto_url, account_id, region)
            own_id = _register(ec2, own)
            if index == 0:
                shared_id = _register(ec2, shared)
                if shared_id:
                    ec2.modify_image_attribute(
                        ImageId=shared_id,
                        LaunchPermission={"Add": [{"UserId": other} for other in ids[1:6]]},
                    )
            if own_id and index == 4 and region == "eu-west-1":
                ec2.deregister_image(ImageId=own_id)


# --- CLI ---------------------------------------------------------------------------------------


def wait_for(url: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if httpx.get(url, timeout=3).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for {url}")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--reset", action="store_true", help="wipe fake-aws and moto first")
    parser.add_argument(
        "--fake-url", default=os.environ.get("FAKE_AWS_URL", "http://localhost:4567")
    )
    parser.add_argument(
        "--moto-url", default=os.environ.get("MOTO_ENDPOINT_URL", "http://localhost:5000")
    )
    args = parser.parse_args()

    wait_for(f"{args.fake_url}/health")
    # Not /moto-api/data.json: it serialises every backend (hundreds of MB, several seconds).
    wait_for(f"{args.moto_url}/moto-api/")
    ids = account_ids()
    if args.reset:
        httpx.post(f"{args.moto_url}/moto-api/reset").raise_for_status()
        httpx.post(f"{args.fake_url}/_admin/reset", params={"forget_credentials": True})

    dataset = build_dataset(ids, REGIONS)
    registry = dataset["registry"]
    for index, account_id in enumerate(ids):
        chunk = {
            "registry": [registry[index]],
            **{
                key: [
                    row
                    for row in dataset[key]
                    if row.get("account_id", row.get("accountId")) == account_id
                ]
                for key in ("recorders", "config_items", "resources")
            },
        }
        httpx.put(f"{args.fake_url}/_admin/dataset", json=chunk, timeout=60).raise_for_status()
    create_amis(args.fake_url, args.moto_url, ids, REGIONS)
    counts = httpx.get(f"{args.fake_url}/_admin/dataset").json()
    print(f"seeded fake-aws: {counts}; AMIs registered in moto", file=sys.stderr)


if __name__ == "__main__":
    main()
