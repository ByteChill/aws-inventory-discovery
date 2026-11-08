"""Per-type code for the few things a spec file cannot express (resource-types.md section 4).

A hook only reads and enriches data. It never touches the database, redacts, or decides
deletion. `get_live` has three outcomes: the resource, `GONE`, or an exception.

Decisions recorded here (checked against the CloudFormation resource type schemas):

* AWS::ECS::Service has no `list_live` hook. Its `handlers.list` has no handlerSchema that
  requires a parent property and its permissions include `ecs:ListClusters` and
  `ecs:ListServices`: the Cloud Control handler enumerates the clusters itself, so a plain
  `ListResources` is complete.
* AWS::ECS::TaskDefinition has no `filter` hook. Cloud Control exposes neither `Status` nor
  `Revision`, so "only the latest ACTIVE revision per family" cannot be computed from its data.
  The list handler calls `ecs:ListTaskDefinitions`, whose default status filter is ACTIVE, so
  Cloud Control lists every ACTIVE revision (not only the latest). All of them are inventoried;
  a revision that becomes INACTIVE drops out of the list, is marked missing, and the by-ID read
  (`DescribeTaskDefinition` still answers for INACTIVE revisions) keeps it from being deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from boto3.session import Session
from botocore.exceptions import ClientError

from app.aws.session import make_client
from app.types.mapping import ami_gone

Properties = dict[str, Any]


@dataclass(frozen=True)
class Gone:
    """A by-ID read that explicitly says the resource does not exist."""


GONE = Gone()


class ListLive(Protocol):
    def __call__(self, session: Session, *, account_id: str, region: str) -> list[Properties]:
        """List every resource. Raise on any failed page or parent so a list is never partial."""
        ...


class GetLive(Protocol):
    def __call__(
        self, session: Session, *, account_id: str, region: str, resource_id: str
    ) -> Properties | Gone:
        """Return the resource, or GONE when it is explicitly gone. Raise on anything else."""
        ...


class Filter(Protocol):
    def __call__(self, items: list[Properties]) -> list[Properties]: ...


class Enrich(Protocol):
    def __call__(
        self, session: Session, *, account_id: str, region: str, item: Properties
    ) -> Properties: ...


@dataclass(frozen=True)
class Hooks:
    list_live: ListLive | None = None  # default: Cloud Control ListResources
    get_live: GetLive | None = None  # default: Cloud Control GetResource
    filter: Filter | None = None  # default: no filtering
    enrich: Enrich | None = None  # default: no enrichment


# --- AMI (resource-types.md 4.1) ----------------------------------------------------------

IMAGE_FLAGS = {"IncludeDeprecated": True, "IncludeDisabled": True}


def _with_ownership(image: Properties, account_id: str) -> Properties:
    return {**image, "Ownership": "self" if image["OwnerId"] == account_id else "shared"}


def list_amis(session: Session, *, account_id: str, region: str) -> list[Properties]:
    """AMIs the account owns plus those shared with it (or its organization / OU)."""
    ec2 = make_client(session, "ec2", region)
    merged: dict[str, Properties] = {}
    for audience in ({"Owners": ["self"]}, {"ExecutableUsers": ["self"]}):
        for page in ec2.get_paginator("describe_images").paginate(**IMAGE_FLAGS, **audience):
            for image in page["Images"]:
                merged.setdefault(image["ImageId"], image)
    return [_with_ownership(image, account_id) for image in merged.values()]


def get_ami(
    session: Session, *, account_id: str, region: str, resource_id: str
) -> Properties | Gone:
    ec2 = make_client(session, "ec2", region)
    try:
        response = ec2.describe_images(ImageIds=[resource_id], **IMAGE_FLAGS)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "InvalidAMIID.NotFound":
            return GONE
        raise
    if ami_gone(response):
        return GONE
    return _with_ownership(response["Images"][0], account_id)


TYPE_HOOKS: dict[str, Hooks] = {
    "AWS::EC2::Image": Hooks(list_live=list_amis, get_live=get_ami),
}
