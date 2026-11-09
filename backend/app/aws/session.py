"""Per-account boto3 sessions. Endpoint overrides come only from boto3's own environment
variables (`AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_CLOUDCONTROL`, ...); nothing is configured here."""

from __future__ import annotations

from typing import Any

import boto3
import botocore.session
from botocore.config import Config
from botocore.credentials import RefreshableCredentials

CLIENT_CONFIG = Config(
    retries={"mode": "adaptive", "max_attempts": 10},
    connect_timeout=10,
    read_timeout=60,
)


def make_session(credentials: RefreshableCredentials) -> boto3.Session:
    """A session whose signing credentials refresh themselves through botocore.

    Assigning `_credentials` on a botocore session is the established way to plug in
    `RefreshableCredentials`; boto3 has no public parameter for it.
    """
    core = botocore.session.get_session()
    core._credentials = credentials
    return boto3.Session(botocore_session=core)


def make_client(session: boto3.Session, service: str, region: str) -> Any:
    return session.client(service, region_name=region, config=CLIENT_CONFIG)
