"""Discovery: reads one account + one region and yields `Observation`s (DESIGN 10.1).

Picks the source per type, flags whether a read was complete, and tells "explicitly gone"
(`GONE`) from errors (which raise or are collected). It never touches the database, overlays
or redacts: observations carry the data exactly as mapped from the source.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
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


class ConfigPages:
    """A Config query in progress. Iterating yields one list of observations per page; the
    state below is final once the iteration ends. A page that failed ends it with `error` set;
    an iteration the consumer abandons (a stop or cancel) is never `complete`."""

    def __init__(self, client: Any, types: Sequence[str]) -> None:
        self._client = client
        self._types = types
        self.query_started_at = datetime.now(UTC)
        self.complete = False
        self.error: ScanError | None = None

    def __iter__(self) -> Iterator[list[Observation]]:
        try:
            for raws in config_source.select_pages(self._client, self._types):
                page = []
                for raw in raws:
                    # Config leaves out fields it has no value for
                    item = ConfigItem.model_validate({"arn": None, "resourceName": None, **raw})
                    page.append(
                        Observation(
                            resource_type=item.resource_type,
                            item=item,
                            live_raw=None,
                            observed_at=capture_time(raw["configurationItemCaptureTime"]),
                            source="config",
                            relationships=raw.get("relationships") or [],
                        )
                    )
                if page:
                    yield page
        except AWS_ERRORS as exc:
            self.error = classify(exc)
            return
        self.complete = True


class LivePages:
    """A listing of one type in progress; same contract as `ConfigPages`. `observed_at` is the
    time before the first call."""

    def __init__(self, disc: Discovery, resource_type: str) -> None:
        self._disc = disc
        self.resource_type = resource_type
        self._spec = disc.registry.get(resource_type)
        self._hooks = disc.hooks.get(resource_type, Hooks())
        self._source: Source = "native" if self._spec.sources.native else "cloudcontrol"
        self.observed_at = datetime.now(UTC)
        self.errors: list[ScanError] = []
        self.finished = False

    @property
    def complete(self) -> bool:
        return self.finished and not self.errors

    def __iter__(self) -> Iterator[list[Observation]]:
        try:
            for raw in self._raw_pages():
                page = self._observe(raw)
                if page:
                    yield page
        except AWS_ERRORS as exc:
            self.errors.append(classify(exc))
        self.finished = True

    def _raw_pages(self) -> Iterator[list[Properties]]:
        disc, hooks = self._disc, self._hooks
        if hooks.list_live is not None:
            pages = hooks.list_live(disc.session, account_id=disc.account_id, region=disc.region)
        else:
            pages = self._cloudcontrol_pages()
        if hooks.filter is None:
            yield from pages
            return
        yield hooks.filter([item for page in pages for item in page])  # sees the whole list

    def _cloudcontrol_pages(self) -> Iterator[list[Properties]]:
        client = self._disc._cloudcontrol
        full = self._spec.sources.cloud_control.list_returns_full_properties
        for page in cloudcontrol_source.list_resource_pages(client, self.resource_type):
            raw: list[Properties] = []
            for identifier, properties in page:
                if not full:
                    properties = cloudcontrol_source.get_resource(
                        client, self.resource_type, identifier
                    )
                    if isinstance(properties, Gone):
                        continue  # deleted between the list and the read: not a failure
                raw.append(properties)
            yield raw

    def _observe(self, raw: list[Properties]) -> list[Observation]:
        disc, hooks = self._disc, self._hooks
        observations: list[Observation] = []
        for properties in raw:
            try:
                if hooks.enrich is not None:
                    properties = hooks.enrich(
                        disc.session,
                        account_id=disc.account_id,
                        region=disc.region,
                        item=properties,
                    )
                item = to_config_item(
                    self._spec, properties, account_id=disc.account_id, region=disc.item_region
                )
            except AWS_ERRORS as exc:
                self.errors.append(classify(exc))
                continue
            except MappingError as exc:
                self.errors.append(ScanError("internal", str(exc)))
                continue
            observations.append(
                Observation(self.resource_type, item, properties, self.observed_at, self._source)
            )
        return observations


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

    def config_pages(self, types: Collection[str]) -> ConfigPages:
        """One Config query for all the types, read page by page."""
        return ConfigPages(self._config, sorted(types))

    def sweep_config(self, types: Collection[str]) -> ConfigSweep:
        """The whole Config query at once. Items read before a failure are kept."""
        pages = self.config_pages(types)
        sweep = ConfigSweep({t: [] for t in types}, True, pages.query_started_at)
        for page in pages:
            for obs in page:
                sweep.by_type.setdefault(obs.resource_type, []).append(obs)
        sweep.complete, sweep.error = pages.complete, pages.error
        return sweep

    # --- Cloud Control and native listing -------------------------------------------------

    def live_pages(self, resource_type: str) -> LivePages:
        """List one type through its hook or Cloud Control, page by page. The read is
        `complete` only when everything (every page, parent, per-item read and enrichment)
        succeeded."""
        return LivePages(self, resource_type)

    def scan_live(self, resource_type: str) -> LiveScan:
        """The whole listing of one type at once."""
        pages = self.live_pages(resource_type)
        observations = [obs for page in pages for obs in page]
        return LiveScan(
            resource_type, observations, pages.complete, pages.observed_at, pages.errors
        )

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
