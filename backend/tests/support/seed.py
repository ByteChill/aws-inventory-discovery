"""Loads the official fixtures into an inventory database.

Only fixture values are copied: Config items as they are, Cloud Control items through
`to_config_item`, AMIs through `ami_to_config_item`. Account, region and the observation times
are the only things chosen here. A later Writer milestone may replace this helper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, insert

from app.query.tables import ACCOUNT, ACCOUNT_REGION, RELATION, RESOURCE, SHARD
from app.types.mapping import ami_to_config_item, to_config_item
from app.types.redact import redact_configuration, redact_live_raw
from app.types.registry import TypeRegistry

TESTS = Path(__file__).resolve().parents[1]
AWS_FIXTURES = TESTS / "fixtures" / "aws"
ACCOUNTS_FILE = TESTS / "fixtures" / "registry" / "accounts.json"

MAIN_ACCOUNT = "111122223333"  # payments-prod: the Config fixtures
IMAGES_ACCOUNT = "444455556666"  # shared-images: owns the shared AMI
DEV_ACCOUNT = "777788889999"  # payments-dev: the Cloud Control fixtures
DEV_REGION = "eu-west-1"
CAPTURED = datetime(2026, 11, 6, 9, 15, 22, 401000, tzinfo=UTC)  # the fixtures' capture time
OBSERVED = datetime(2026, 11, 7, 10, 0, 0, tzinfo=UTC)
FIRST_SEEN_DEV = datetime(2026, 11, 1, 8, 0, 0, tzinfo=UTC)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Seeded:
    """What was inserted, with database IDs, for tests to compare API answers against."""

    accounts: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)

    def where(self, **match: Any) -> list[dict[str, Any]]:
        return [r for r in self.resources if all(r[k] == v for k, v in match.items())]

    def one(self, **match: Any) -> dict[str, Any]:
        (found,) = self.where(**match)
        return found


def _row(
    item: Any,
    *,
    spec_registry: TypeRegistry,
    source: str,
    observed_at: datetime,
    first_seen_at: datetime,
    relationships: list[Any] | None = None,
    capture_time: datetime | None = None,
    live_raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `t_inv_aws_resource` row from a Config-format item (a dict or a `ConfigItem`)."""
    data = item if isinstance(item, dict) else item.model_dump(by_alias=True)
    spec = spec_registry.get(data["resourceType"])
    return {
        "arn": data["arn"],
        "account_id": data["accountId"],
        "aws_region": data["awsRegion"],
        "resource_type": data["resourceType"],
        "resource_id": data["resourceId"],
        "resource_name": data.get("resourceName"),
        "tags": [{"key": t["key"], "value": t["value"]} for t in data.get("tags", [])],
        "configuration": redact_configuration(spec, data["configuration"]),
        "relationships": relationships or [],
        "live_raw": redact_live_raw(spec, live_raw) if live_raw is not None else None,
        "config_capture_time": capture_time,
        "live_observed_at": observed_at if live_raw is not None else None,
        "observed_at": observed_at,
        "observed_source": source,
        "content_hash": "fixture",
        "status": "active",
        "first_seen_at": first_seen_at,
        "last_changed_at": observed_at,
    }


def resource_rows(registry: TypeRegistry) -> list[dict[str, Any]]:
    rows = []
    # Config items: the main account, us-east-1 (they are already in Config format).
    for path in sorted(AWS_FIXTURES.glob("*/config_item*.json")):
        item = load_json(path)
        rows.append(
            _row(
                item,
                spec_registry=registry,
                source="config",
                observed_at=CAPTURED,
                first_seen_at=CAPTURED,
                relationships=item["relationships"],
                capture_time=CAPTURED,
            )
        )
    # Cloud Control lists: the dev account, eu-west-1 (mapped like the worker maps them).
    for path in sorted(AWS_FIXTURES.glob("*/cloudcontrol_list.json")):
        listing = load_json(path)
        spec = registry.get(listing["TypeName"])
        for description in listing["ResourceDescriptions"]:
            properties = json.loads(description["Properties"])
            item = to_config_item(spec, properties, account_id=DEV_ACCOUNT, region=DEV_REGION)
            rows.append(
                _row(
                    item,
                    spec_registry=registry,
                    source="cloudcontrol",
                    observed_at=OBSERVED,
                    first_seen_at=FIRST_SEEN_DEV,
                    live_raw=properties,
                )
            )
    # The shared AMI: owned by one account, visible in another with the same ARN.
    image = load_json(AWS_FIXTURES / "aws.ec2.image" / "describe_images_shared.json")["Images"][0]
    spec = registry.get("AWS::EC2::Image")
    for account in (MAIN_ACCOUNT, IMAGES_ACCOUNT):  # the owner is listed second on purpose
        item = ami_to_config_item(spec, image, account_id=account, region="us-east-1")
        rows.append(
            _row(
                item,
                spec_registry=registry,
                source="native",
                observed_at=OBSERVED,
                first_seen_at=OBSERVED,
            )
        )
    return rows


def account_rows(now: datetime) -> list[dict[str, Any]]:
    states = {
        MAIN_ACCOUNT: {"active": True, "access_status": "ok", "last_scan_ok_at": now},
        IMAGES_ACCOUNT: {
            "active": True,
            "access_status": "credential_error",
            "access_error": "cannot assume the scan role",
        },
        DEV_ACCOUNT: {
            "active": False,
            "inactive_since": now - timedelta(days=5),
            "access_status": "ok",
        },
    }
    return [
        {**account, "registry_fetched_at": now, **states[account["account_id"]]}
        for account in load_json(ACCOUNTS_FILE)
    ]


def seed(engine: Engine, registry: TypeRegistry) -> Seeded:
    now = datetime.now(UTC)
    seeded = Seeded(accounts=account_rows(now))
    with engine.begin() as connection:
        for account in seeded.accounts:
            connection.execute(insert(ACCOUNT).values(**account))
        rows = resource_rows(registry)
        # Two resources the scans no longer see: one possibly deleted, one deleted.
        vpc = next(
            r
            for r in rows
            if r["account_id"] == DEV_ACCOUNT and r["resource_type"] == "AWS::EC2::VPC"
        )
        queue = next(
            r
            for r in rows
            if r["account_id"] == DEV_ACCOUNT and r["resource_type"] == "AWS::SQS::Queue"
        )
        vpc.update(status="missing", missing_since=now - timedelta(days=2))
        queue.update(status="deleted", deleted_at=now - timedelta(hours=3))
        for row in rows:
            row["id"] = connection.execute(
                insert(RESOURCE).values(**row).returning(RESOURCE.c.id)
            ).scalar_one()
            seeded.resources.append(row)
        # Relationships as the Writer stores them: resolved when the target is in the database.
        for resource in seeded.resources:
            for related in resource["relationships"]:
                target = next(
                    (
                        r
                        for r in seeded.resources
                        if (r["account_id"], r["aws_region"], r["resource_type"], r["resource_id"])
                        == (
                            resource["account_id"],
                            resource["aws_region"],
                            related["resourceType"],
                            related["resourceId"],
                        )
                    ),
                    None,
                )
                relation = {
                    "from_resource_id": resource["id"],
                    "to_resource_type": related["resourceType"],
                    "to_resource_key": related["resourceId"],
                    "to_resource_id": target["id"] if target else None,
                    "relation_name": related.get("relationshipName", ""),
                }
                connection.execute(insert(RELATION).values(**relation))
                seeded.relations.append(relation)
        # Scan scopes: fresh for the main account, except the VPC scope scanned 13 hours ago.
        scopes = {(r["account_id"], r["aws_region"], r["resource_type"]) for r in seeded.resources}
        for account_id, region, resource_type in sorted(scopes):
            if account_id != MAIN_ACCOUNT:
                continue
            age = timedelta(hours=13 if resource_type == "AWS::EC2::VPC" else 1)
            connection.execute(
                insert(SHARD).values(
                    account_id=account_id,
                    region=region,
                    resource_type=resource_type,
                    last_succeeded_at=now - age,
                )
            )
        # Scopes with other outcomes, for the coverage grid.
        for region, resource_type, values in (
            (
                "eu-west-1",
                "AWS::EC2::VPC",
                {"consecutive_failures": 2, "last_error_code": "Throttling"},
            ),
            (
                "eu-west-1",
                "AWS::SQS::Queue",
                {"consecutive_failures": 1, "last_error_code": "AccessDeniedException"},
            ),
            ("eu-west-1", "AWS::SNS::Topic", {}),
        ):
            connection.execute(
                insert(SHARD).values(
                    account_id=MAIN_ACCOUNT, region=region, resource_type=resource_type, **values
                )
            )
        connection.execute(
            insert(ACCOUNT_REGION),
            [
                {
                    "account_id": MAIN_ACCOUNT,
                    "region": "us-east-1",
                    "opt_in_status": "opt-in-not-required",
                    "reachable": True,
                    "config_recorder": {"recording": True},
                    "checked_at": now,
                },
                {
                    "account_id": MAIN_ACCOUNT,
                    "region": "ap-southeast-1",
                    "opt_in_status": "opt-in-not-required",
                    "reachable": False,
                    "config_recorder": None,
                    "checked_at": now,
                },
            ],
        )
    return seeded
