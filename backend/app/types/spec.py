"""Pydantic model of a resource type file (docs/specs/resource-types.md section 1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class SpecModel(BaseModel):
    """Base for every nested model: camelCase in files, snake_case in Python."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True
    )


class Label(SpecModel):
    zh_cn: str = Field(alias="zh-CN")
    en: str


class Identity(SpecModel):
    identifier: str | list[str]
    resource_id_from: str
    arn_from: str | None = None
    arn_template: str | None = None
    # A path, `tag:<Key>`, or `tail:<path>` (last segment of an ARN-like value).
    name_from: str | None = None

    @model_validator(mode="after")
    def _one_arn_source(self) -> Self:
        if (self.arn_from is None) == (self.arn_template is None):
            raise ValueError("exactly one of arnFrom and arnTemplate is required")
        return self

    @property
    def identifier_fields(self) -> list[str]:
        return [self.identifier] if isinstance(self.identifier, str) else self.identifier


class CloudControlSource(SpecModel):
    read: bool
    list: bool
    list_returns_full_properties: bool = False


class Sources(SpecModel):
    config: bool
    cloud_control: CloudControlSource
    native: bool = False

    @model_validator(mode="after")
    def _some_source(self) -> Self:
        cc = self.cloud_control
        if not (self.config or cc.read or cc.list or self.native):
            raise ValueError("at least one source must be enabled")
        if self.native and (self.config or cc.read or cc.list):
            raise ValueError("native is only for types neither Config nor Cloud Control support")
        return self


class Mapping(SpecModel):
    # camel: lowerCamelCase every key. pascal: keep Cloud Control's PascalCase keys.
    key_case: Literal["camel", "pascal"] = "camel"
    # The Cloud Control property that holds the tags as `[{Key, Value}]`; almost always `Tags`,
    # but e.g. Route 53 hosted zones use `HostedZoneTags`. It becomes `tags`, never part of
    # `configuration`.
    tags_property: str = "Tags"
    # Cloud Control properties this mapping covers (top-level or dotted paths). Without the
    # schema the overlay cannot know which fields Cloud Control would have returned, so the
    # spec lists them. Renamed properties are covered through `rename`.
    properties: list[str] = []
    rename: dict[str, str] = {}
    exclude: list[str] = []
    transform: dict[str, Literal["toString", "toInt", "toFloat", "toBool", "toIsoDatetime"]] = {}
    preserve_keys_under: list[str] = []

    @model_validator(mode="after")
    def _arrays_are_replaced_whole(self) -> Self:
        """A covered path may end in `[]` (the whole array) but never continue inside one:
        overlay replaces arrays atomically and cannot address their elements."""
        covered = [*self.properties, *self.rename, *self.rename.values()]
        for path in covered:
            if any(segment.endswith("[]") for segment in path.split(".")[:-1]):
                raise ValueError(
                    f"mapping path {path!r} points inside an array; arrays are replaced whole"
                )
        return self


class RedactRule(SpecModel):
    path: str
    mode: Literal["values", "drop", "summarize"]
    by: str | None = None

    @model_validator(mode="after")
    def _summarize_needs_by(self) -> Self:
        if (self.mode == "summarize") != (self.by is not None):
            raise ValueError("`by` is required for, and only for, mode summarize")
        return self


class Column(SpecModel):
    key: str
    label: Label
    type: Literal["text", "enum", "number", "bool", "datetime"]
    path: str
    exists: bool = False
    count: bool = False
    mono: bool = False
    sortable: bool = True
    filterable: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.exists and self.count:
            raise ValueError("a column cannot be both exists and count")
        if self.exists and self.type != "bool":
            raise ValueError("an exists column must have type bool")
        if self.count and self.type != "number":
            raise ValueError("a count column must have type number")
        if "[]" in self.path:
            raise ValueError("a column path cannot traverse arrays")
        return self


class ResourceTypeSpec(SpecModel):
    type_name: str
    label: Label
    global_: bool = Field(default=False, alias="global")
    identity: Identity
    sources: Sources
    mapping: Mapping = Mapping()
    redact: list[RedactRule] = []
    volatile: list[str] = []
    columns: list[Column] = []

    @model_validator(mode="after")
    def _unique_columns(self) -> Self:
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate column keys")
        return self

    @classmethod
    def from_file(cls, path: Path) -> Self:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    @property
    def file_name(self) -> str:
        return type_file_name(self.type_name)


def type_file_name(type_name: str) -> str:
    """`AWS::EC2::VPC` -> `aws.ec2.vpc.json`."""
    return type_name.lower().replace("::", ".") + ".json"
