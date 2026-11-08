"""The fake AWS service: Cloud Control + AWS Config on `POST /`, account registry ①,
credential service ②, and the `/_admin` API used by the seed and by tests."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Body, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import cloudcontrol, config
from .awsjson import AwsError, error_response, json_response, parse_authorization
from .store import ErrorRule, Store

DEFAULT_MOTO_ENDPOINT = "http://localhost:5000"
ASSUMED_ROLE_NAME = "ADFS-readonly"

# X-Amz-Target prefix -> (operations, JSON protocol version, scope service name)
SERVICES = {
    cloudcontrol.TARGET_PREFIX: (cloudcontrol.OPERATIONS, cloudcontrol.JSON_VERSION),
    config.TARGET_PREFIX: (config.OPERATIONS, config.JSON_VERSION),
}


class ErrorRequest(BaseModel):
    """Make matching calls fail. A `None` field matches anything."""

    error: Literal["AccessDenied", "Throttling", "ResourceNotFound"]
    account_id: str | None = None
    region: str | None = None
    type_name: str | None = None
    operation: str | None = None
    count: int | None = None  # None: until cleared


def create_app(moto_endpoint: str | None = None) -> FastAPI:
    moto_endpoint = moto_endpoint or os.environ.get("MOTO_ENDPOINT_URL", DEFAULT_MOTO_ENDPOINT)
    store = Store()
    app = FastAPI(title="fake-aws", docs_url=None, redoc_url=None)
    app.state.store = store

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", **store.counts()}

    # --- AWS JSON protocols ----------------------------------------------------------------

    @app.post("/")
    async def aws_json(request: Request) -> Response:
        target = request.headers.get("x-amz-target", "")
        prefix, _, operation = target.partition(".")
        service = SERVICES.get(prefix)
        version = service[1] if service else "1.1"
        try:
            if service is None or operation not in service[0]:
                raise AwsError("UnknownOperationException", f"Unknown operation: {target!r}")
            caller = parse_authorization(request.headers.get("authorization"))
            account = store.account_for_key(caller.access_key_id)
            if account is None:
                raise AwsError(
                    "UnrecognizedClientException",
                    "The security token included in the request is invalid.",
                )
            try:
                body = json.loads(await request.body() or b"{}")
            except ValueError:
                raise AwsError("SerializationException", "Request body is not valid JSON") from None
            if not isinstance(body, dict):
                raise AwsError("SerializationException", "Request body must be a JSON object")
            return json_response(
                service[0][operation](store, account, caller.region, body), version
            )
        except AwsError as err:
            return error_response(err, version)

    # --- account registry ① ---------------------------------------------------------------

    @app.get("/registry/accounts")
    def registry_accounts() -> list[dict[str, Any]]:
        with store.lock:
            return sorted(store.registry.values(), key=lambda e: e["account_id"])

    # --- credential service ② -------------------------------------------------------------

    @app.get("/credentials")
    def credentials(account_id: str = Query(pattern=r"^\d{12}$")) -> Response:
        sts = boto3.client(
            "sts",
            endpoint_url=moto_endpoint,
            region_name="us-east-1",
            aws_access_key_id="fake-aws",
            aws_secret_access_key="fake-aws",
            config=BotoConfig(retries={"max_attempts": 1}),
        )
        try:
            creds = sts.assume_role(
                RoleArn=f"arn:aws:iam::{account_id}:role/{ASSUMED_ROLE_NAME}",
                RoleSessionName="aws-inventory-discovery",
            )["Credentials"]
        except (ClientError, BotoCoreError) as exc:
            return JSONResponse({"message": f"moto STS AssumeRole failed: {exc}"}, status_code=502)
        store.remember_key(creds["AccessKeyId"], account_id)
        return JSONResponse(
            {
                "AccessKeyId": creds["AccessKeyId"],
                "SecretAccessKey": creds["SecretAccessKey"],
                "SessionToken": creds["SessionToken"],
                "Expiration": creds["Expiration"].isoformat(),
            }
        )

    # --- admin ---------------------------------------------------------------------------

    @app.put("/_admin/dataset")
    async def admin_load(
        dataset: dict[str, Any] = Body(...), reset: bool = False
    ) -> dict[str, int]:
        """Upsert `registry`, `recorders`, `config_items`, `resources` (optionally after reset)."""
        if reset:
            store.clear()
        store.load(dataset)
        return store.counts()

    @app.get("/_admin/dataset")
    def admin_counts() -> dict[str, int]:
        return store.counts()

    @app.post("/_admin/reset")
    def admin_reset(forget_credentials: bool = False) -> dict[str, int]:
        store.clear()
        if forget_credentials:
            with store.lock:
                store.key_to_account.clear()
        return store.counts()

    @app.put("/_admin/resources")
    async def admin_put_resource(
        account_id: str = Body(),
        region: str = Body(),
        type_name: str = Body(),
        identifier: str | None = Body(default=None),
        properties: dict[str, Any] | None = Body(default=None),
        config_item: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, int]:
        """Add or replace one Cloud Control resource and/or one Config item."""
        if identifier is not None and properties is not None:
            store.put_resource(account_id, region, type_name, identifier, properties)
        if config_item is not None:
            store.put_config_item(
                {
                    **config_item,
                    "accountId": account_id,
                    "awsRegion": region,
                    "resourceType": type_name,
                }
            )
        return store.counts()

    @app.delete("/_admin/resources")
    def admin_delete_resource(
        account_id: str,
        region: str,
        type_name: str,
        identifier: str | None = None,
        config_resource_id: str | None = None,
        config_status: Literal["ResourceDeleted", "ResourceDeletedNotRecorded", "remove"] = (
            "ResourceDeleted"
        ),
    ) -> dict[str, bool]:
        """Remove a resource from Cloud Control (`identifier`) and mark its Config item deleted
        (`config_resource_id`) or drop it (`config_status=remove`)."""
        return store.delete_resource(
            account_id, region, type_name, identifier, config_resource_id, config_status
        )

    @app.post("/_admin/errors")
    def admin_add_error(req: ErrorRequest) -> dict[str, int]:
        store.add_error(ErrorRule(**req.model_dump()))
        return store.counts()

    @app.delete("/_admin/errors")
    def admin_clear_errors() -> dict[str, int]:
        with store.lock:
            store.errors.clear()
        return store.counts()

    return app


app = create_app()
