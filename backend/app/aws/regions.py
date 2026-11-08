"""Which regions an account has enabled (`ec2:DescribeRegions`)."""

from __future__ import annotations

from collections.abc import Sequence

import boto3

from app.aws.session import make_client

ENABLED = ("opt-in-not-required", "opted-in")

# Opt-in regions: disabled by default, and AWS services cannot be called in one an account has
# not enabled. Source: https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-regions.html
OPT_IN_REGIONS = frozenset(
    {
        "af-south-1",
        "ap-east-1",
        "ap-east-2",
        "ap-south-2",
        "ap-southeast-3",
        "ap-southeast-4",
        "ap-southeast-5",
        "ap-southeast-6",
        "ap-southeast-7",
        "ca-west-1",
        "eu-central-2",
        "eu-south-1",
        "eu-south-2",
        "il-central-1",
        "me-central-1",
        "me-south-1",
        "mx-central-1",
    }
)


class NoDescribeRegionsRegion(RuntimeError):
    """`REACHABLE_REGIONS` has no region that is enabled in every account by default."""


def home_region(reachable: Sequence[str]) -> str:
    """The first reachable region that is enabled by default, where `DescribeRegions` is
    called: an opt-in region may be disabled in the target account."""
    for region in reachable:
        if region not in OPT_IN_REGIONS:
            return region
    raise NoDescribeRegionsRegion(
        "REACHABLE_REGIONS has no default-enabled region to call ec2:DescribeRegions in "
        f"(opt-in regions: {', '.join(sorted(OPT_IN_REGIONS & set(reachable))) or 'none'}); "
        "add one, e.g. us-east-1"
    )


def describe_regions(session: boto3.Session, reachable: Sequence[str]) -> dict[str, str]:
    """`{region name: OptInStatus}` for every region, enabled or not."""
    client = make_client(session, "ec2", home_region(reachable))
    response = client.describe_regions(AllRegions=True)
    return {r["RegionName"]: r["OptInStatus"] for r in response["Regions"]}
