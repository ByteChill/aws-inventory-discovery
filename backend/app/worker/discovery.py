"""Discovery: reads one account + one region and yields `Observation`s (DESIGN 10.1).

Picks the source per type, flags whether a read was complete, and tells "explicitly gone"
(`GONE`) from errors (which raise or are collected). It never touches the database, overlays
or redacts: observations carry the data exactly as mapped from the source.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from typing import Any, Literal

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.aws import cloudcontrol_source, config_source
from app.aws.config_source import RecorderState, capture_time
from app.aws.credentials import CredentialError
from app.aws.errors import ScanError, classify
from app.aws.session import make_client
from app.types.hooks import GONE, TYPE_HOOKS, Gone, Hooks, Properties
from app.types.mapping import (
    ConfigItem,
    MappingError,
    cloudcontrol_identifier,
    to_config_item,
)
from app.types.registry import TypeRegistry

Source = Literal["config", "cloudcontrol", "native"]

# What a read can legitimately fail with; anything else is a bug and propagates.
AWS_ERRORS = (ClientError, BotoCoreError, CredentialError)


@dataclass(frozen=True)
class Observation:
    """One resource as seen by one source, not yet overlaid or redacted."""

    resource_type: str
    item: ConfigItem  # Config-format record (CC / native data already mapped)
    live_raw: dict[str, Any] | None  # raw Cloud Control properties or native response
    observed_at: datetime
    source: Source
    relationships: list[dict[str, Any]] | None = None  # only Config provides them


@dataclass
class ConfigSweep:
    by_type: dict[str, list[Observation]]
    complete: bool
    query_started_at: datetime
    error: ScanError | None = None


@dataclass
class LiveScan:
    resource_type: str
    observations: list[Observation]
    complete: bool
    observed_at: datetime  # the time before the first call
    errors: list[ScanError] = field(default_factory=list)


def plan_sources(
    registry: TypeRegistry,
    recorder: RecorderState,
    *,
    on_demand: bool = False,
    type_names: Collection[str] | None = None,
) -> dict[str, Source]:
    """Which source reads each type in a region (scanner.md 6.1 step 4).

    Scheduled scans use Config for types it records continuously and Cloud Control for the
    rest; on-demand scans never use Config. Types no source can list are left out.
    """
    plan: dict[str, Source] = {}
    for spec in registry:
        if type_names is not None and spec.type_name not in type_names:
            continue
        if spec.sources.native:
            plan[spec.type_name] = "native"
        elif (
            not on_demand
            and spec.sources.config
            and recorder.continuous(spec.type_name, is_global=spec.global_)
        ):
            plan[spec.type_name] = "config"
        elif spec.sources.cloud_control.list:
            plan[spec.type_name] = "cloudcontrol"
    return plan


class Discovery:
    def __init__(
        self,
        session: boto3.Session,
        account_id: str,
        region: str,
        registry: TypeRegistry,
        hooks: Mapping[str, Hooks] = TYPE_HOOKS,
        item_region: str | None = None,
    ) -> None:
        self.session = session
        self.account_id = account_id
        self.region = region  # where the clients connect
        # The region items are filed under: `global` for a global type read from one region.
        self.item_region = item_region or region
        self.registry = registry
        self.hooks = hooks

    @cached_property
    def _config(self) -> Any:
        return make_client(self.session, "config", self.region)

    @cached_property
    def _cloudcontrol(self) -> Any:
        return make_client(self.session, "cloudcontrol", self.region)

    def recorder(self) -> RecorderState:
        return config_source.detect_recorder(self._config)

    # --- Config ---------------------------------------------------------------------------

    def sweep_config(self, types: Collection[str]) -> ConfigSweep:
        """One Config query for all the types. Items read before a failure are kept."""
        started = datetime.now(UTC)
        sweep = ConfigSweep({t: [] for t in types}, True, started)
        try:
            for raw in config_source.select(self._config, sorted(types)):
                # Config leaves out fields it has no value for
                item = ConfigItem.model_validate({"arn": None, "resourceName": None, **raw})
                sweep.by_type.setdefault(item.resource_type, []).append(
                    Observation(
                        resource_type=item.resource_type,
                        item=item,
                        live_raw=None,
                        observed_at=capture_time(raw["configurationItemCaptureTime"]),
                        source="config",
                        relationships=raw.get("relationships") or [],
                    )
                )
        except AWS_ERRORS as exc:
            sweep.complete = False
            sweep.error = classify(exc)
        return sweep

    # --- Cloud Control and native listing -------------------------------------------------

    def scan_live(self, resource_type: str) -> LiveScan:
        """List one type through its hook or Cloud Control. `complete` only when everything
        (every page, parent, per-item read and enrichment) succeeded."""
        spec = self.registry.get(resource_type)
        hooks = self.hooks.get(resource_type, Hooks())
        source: Source = "native" if spec.sources.native else "cloudcontrol"
        started = datetime.now(UTC)
        errors: list[ScanError] = []
        raw: list[Properties] = []
        try:
            if hooks.list_live is not None:
                raw = hooks.list_live(self.session, account_id=self.account_id, region=self.region)
            else:
                for identifier, properties in cloudcontrol_source.list_resources(
                    self._cloudcontrol, resource_type
                ):
                    if not spec.sources.cloud_control.list_returns_full_properties:
                        properties = cloudcontrol_source.get_resource(
                            self._cloudcontrol, resource_type, identifier
                        )
                        if isinstance(properties, Gone):
                            continue  # deleted between the list and the read: not a failure
                    raw.append(properties)
        except AWS_ERRORS as exc:
            errors.append(classify(exc))
        if hooks.filter is not None:
            raw = hooks.filter(raw)

        observations: list[Observation] = []
        for properties in raw:
            try:
                if hooks.enrich is not None:
                    properties = hooks.enrich(
                        self.session,
                        account_id=self.account_id,
                        region=self.region,
                        item=properties,
                    )
                item = to_config_item(
                    spec, properties, account_id=self.account_id, region=self.item_region
                )
            except AWS_ERRORS as exc:
                errors.append(classify(exc))
                continue
            except MappingError as exc:
                errors.append(ScanError("internal", str(exc)))
                continue
            observations.append(Observation(resource_type, item, properties, started, source))
        return LiveScan(resource_type, observations, not errors, started, errors)

    # --- By-ID read -----------------------------------------------------------------------

    def fetch(
        self,
        resource_type: str,
        resource_id: str,
        configuration: dict[str, Any] | None = None,
    ) -> Observation | Gone:
        """Read one resource. `GONE` only when the source explicitly says so; errors raise.

        `configuration` (the stored Config-format data) supplies the other parts of a composite
        Cloud Control identifier, such as the ECS cluster of a service; without it the type's
        `cloudcontrol_identifier` hook derives them from the resource id.
        """
        spec = self.registry.get(resource_type)
        hooks = self.hooks.get(resource_type, Hooks())
        started = datetime.now(UTC)
        if hooks.get_live is not None:
            properties = hooks.get_live(
                self.session,
                account_id=self.account_id,
                region=self.region,
                resource_id=resource_id,
            )
        else:
            if configuration is not None:
                identifier = cloudcontrol_identifier(spec, configuration)
            elif hooks.cloudcontrol_identifier is not None:
                identifier = hooks.cloudcontrol_identifier(resource_id)
            elif spec.identity.identifier_fields == [spec.identity.resource_id_from]:
                identifier = resource_id
            else:
                raise MappingError(f"{resource_type}: the stored configuration is needed")
            properties = cloudcontrol_source.get_resource(
                self._cloudcontrol, resource_type, identifier
            )
        if isinstance(properties, Gone):
            return GONE
        item = to_config_item(spec, properties, account_id=self.account_id, region=self.item_region)
        source: Source = "native" if spec.sources.native else "cloudcontrol"
        return Observation(resource_type, item, properties, started, source)
