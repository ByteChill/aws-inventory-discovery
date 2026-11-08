"""The explicit set of resource types, loaded from the spec files."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Self

from app.types.hooks import Hooks
from app.types.spec import ResourceTypeSpec

SPECS_DIR = Path(__file__).parent / "specs"


class RegistryError(Exception):
    """A spec file or hook table is invalid. Start-up must fail."""


class TypeRegistry:
    def __init__(self, specs: Mapping[str, ResourceTypeSpec]) -> None:
        self._specs = dict(specs)

    @classmethod
    def from_directory(cls, path: Path = SPECS_DIR) -> Self:
        specs: dict[str, ResourceTypeSpec] = {}
        files = sorted(path.glob("*.json"))
        if not files:
            raise RegistryError(f"no resource type files in {path}")
        for file in files:
            try:
                spec = ResourceTypeSpec.from_file(file)
            except ValueError as exc:  # json errors and pydantic.ValidationError
                raise RegistryError(f"{file.name}: {exc}") from exc
            if file.name != spec.file_name:
                raise RegistryError(f"{file.name}: must be named {spec.file_name}")
            if spec.type_name in specs:
                raise RegistryError(f"{file.name}: duplicate type {spec.type_name}")
            specs[spec.type_name] = spec
        return cls(specs)

    def get(self, type_name: str) -> ResourceTypeSpec:
        try:
            return self._specs[type_name]
        except KeyError:
            raise KeyError(f"unknown resource type {type_name}") from None

    def __iter__(self) -> Iterator[ResourceTypeSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, type_name: object) -> bool:
        return type_name in self._specs


def validate_hooks(registry: TypeRegistry, hooks: Mapping[str, Hooks]) -> None:
    """Fail start-up when a native type lacks its read hooks or a hook names no type."""
    problems = [f"hooks for unknown type {name}" for name in hooks if name not in registry]
    for spec in registry:
        if not spec.sources.native:
            continue
        hook = hooks.get(spec.type_name)
        if hook is None or hook.list_live is None or hook.get_live is None:
            problems.append(f"{spec.type_name} is native and needs list_live and get_live hooks")
    if problems:
        raise RegistryError("; ".join(problems))
