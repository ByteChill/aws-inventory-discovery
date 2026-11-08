"""Which regions an account has enabled (`ec2:DescribeRegions`, called in ap-east-1)."""

from __future__ import annotations

import boto3

from app.aws.session import make_client

HOME_REGION = "ap-east-1"
ENABLED = ("opt-in-not-required", "opted-in")


def describe_regions(session: boto3.Session, region: str = HOME_REGION) -> dict[str, str]:
    """`{region name: OptInStatus}` for every region, enabled or not."""
    response = make_client(session, "ec2", region).describe_regions(AllRegions=True)
    return {r["RegionName"]: r["OptInStatus"] for r in response["Regions"]}
