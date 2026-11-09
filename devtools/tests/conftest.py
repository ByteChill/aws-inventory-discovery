"""Run fake-aws (uvicorn in a thread) and moto (ThreadedMotoServer) in-process, seed a small
dataset from the fixture files, and talk to both with real boto3 clients."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

import boto3
import httpx
import pytest
import uvicorn
from botocore.config import Config as BotoConfig
from moto.server import ThreadedMotoServer

import seed
from fake_aws.app import create_app

REGIONS = ["us-east-1", "eu-west-2"]
ACCOUNT_IDS = seed.account_ids(3)  # the third account has Config off in eu-west-2


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class Env:
    fake_url: str
    moto_url: str
    dataset: dict[str, Any]

    def credentials(self, account_id: str) -> dict[str, str]:
        response = httpx.get(f"{self.fake_url}/credentials", params={"account_id": account_id})
        response.raise_for_status()
        return response.json()

    def client(self, service: str, account_id: str, region: str) -> Any:
        creds = self.credentials(account_id)
        return boto3.client(
            service,
            endpoint_url=self.moto_url if service in ("sts", "ec2") else self.fake_url,
            region_name=region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            config=BotoConfig(retries={"max_attempts": 1}),
        )

    def load(self) -> None:
        response = httpx.put(
            f"{self.fake_url}/_admin/dataset", params={"reset": True}, json=self.dataset
        )
        response.raise_for_status()


@pytest.fixture(scope="session")
def env():
    moto_port, fake_port = free_port(), free_port()
    moto = ThreadedMotoServer(port=moto_port, verbose=False)
    moto.start()
    moto_url = f"http://127.0.0.1:{moto_port}"
    server = uvicorn.Server(
        uvicorn.Config(create_app(moto_url), port=fake_port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield Env(
            f"http://127.0.0.1:{fake_port}", moto_url, seed.build_dataset(ACCOUNT_IDS, REGIONS)
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        moto.stop()


@pytest.fixture(autouse=True)
def fresh_dataset(env: Env):
    """Every test starts from the same dataset and no injected errors."""
    env.load()
    yield
