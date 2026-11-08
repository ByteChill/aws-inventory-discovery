"""In-memory state of the fake: registry, Config recorders and items, Cloud Control resources,
issued access keys and injected errors. One lock guards everything."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Registry entry format (account registry ①, DESIGN section 2). Kept in one place.
REGISTRY_FIELDS = ("account_id", "account_name", "account_type", "eim_id", "eim_app", "ba_id")

Scope = tuple[str, str]  # (account_id, region)

# The injectable errors, per service: (code, HTTP status).
INJECTABLE = ("AccessDenied", "Throttling", "ResourceNotFound")
CLOUDCONTROL_ERRORS = {
    "AccessDenied": ("AccessDeniedException", 403),
    "Throttling": ("ThrottlingException", 400),
    "ResourceNotFound": ("ResourceNotFoundException", 404),
}
CONFIG_ERRORS = {
    "AccessDenied": ("AccessDeniedException", 400),
    "Throttling": ("ThrottlingException", 400),
    "ResourceNotFound": ("ResourceNotFoundException", 400),
}


@dataclass
class ErrorRule:
    error: str
    account_id: str | None = None
    region: str | None = None
    type_name: str | None = None
    operation: str | None = None
    count: int | None = None  # None: until cleared

    def matches(self, account_id: str, region: str, operation: str, types: list[str]) -> bool:
        if self.account_id not in (None, account_id):
            return False
        if self.region not in (None, region):
            return False
        if self.operation not in (None, operation):
            return False
        return self.type_name is None or self.type_name in types


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.clear()
        self.key_to_account: dict[str, str] = {}

    def clear(self) -> None:
        with self.lock:
            self.registry: dict[str, dict[str, Any]] = {}
            self.recorders: dict[Scope, dict[str, Any]] = {}
            self.config_items: dict[Scope, dict[tuple[str, str], dict[str, Any]]] = {}
            self.resources: dict[Scope, dict[tuple[str, str], dict[str, Any]]] = {}
            self.errors: list[ErrorRule] = []

    # --- dataset -------------------------------------------------------------------------

    def load(self, dataset: dict[str, Any]) -> None:
        """Upsert a dataset: `registry`, `recorders`, `config_items`, `resources`."""
        with self.lock:
            for entry in dataset.get("registry", []):
                self.registry[entry["account_id"]] = {k: entry[k] for k in REGISTRY_FIELDS}
            for rec in dataset.get("recorders", []):
                self.recorders[(rec["account_id"], rec["region"])] = {
                    "configuration_recorder": rec.get("configuration_recorder"),
                    "recorder_status": rec.get("recorder_status"),
                }
            for item in dataset.get("config_items", []):
                self.put_config_item(item)
            for res in dataset.get("resources", []):
                self.put_resource(
                    res["account_id"],
                    res["region"],
                    res["type_name"],
                    res["identifier"],
                    res["properties"],
                )

    def put_config_item(self, item: dict[str, Any]) -> None:
        scope = (item["accountId"], item["awsRegion"])
        with self.lock:
            self.config_items.setdefault(scope, {})[(item["resourceType"], item["resourceId"])] = (
                copy.deepcopy(item)
            )

    def put_resource(
        self, account_id: str, region: str, type_name: str, identifier: str, props: dict[str, Any]
    ) -> None:
        with self.lock:
            self.resources.setdefault((account_id, region), {})[(type_name, identifier)] = (
                copy.deepcopy(props)
            )

    def delete_resource(
        self,
        account_id: str,
        region: str,
        type_name: str,
        identifier: str | None,
        config_resource_id: str | None,
        config_status: str,
    ) -> dict[str, bool]:
        """Delete from Cloud Control; mark the Config item deleted (or drop it)."""
        scope = (account_id, region)
        result = {"cloudcontrol": False, "config": False}
        with self.lock:
            if identifier is not None:
                result["cloudcontrol"] = (
                    self.resources.get(scope, {}).pop((type_name, identifier), None) is not None
                )
            if config_resource_id is not None:
                items = self.config_items.get(scope, {})
                item = items.get((type_name, config_resource_id))
                if item is not None:
                    result["config"] = True
                    if config_status == "remove":
                        del items[(type_name, config_resource_id)]
                    else:
                        item["configurationItemStatus"] = config_status
                        item["configurationItemCaptureTime"] = now_iso()
        return result

    def counts(self) -> dict[str, int]:
        with self.lock:
            return {
                "accounts": len(self.registry),
                "recorders": len(self.recorders),
                "config_items": sum(len(v) for v in self.config_items.values()),
                "resources": sum(len(v) for v in self.resources.values()),
                "errors": len(self.errors),
                "access_keys": len(self.key_to_account),
            }

    # --- credentials ---------------------------------------------------------------------

    def remember_key(self, access_key_id: str, account_id: str) -> None:
        with self.lock:
            self.key_to_account[access_key_id] = account_id

    def account_for_key(self, access_key_id: str) -> str | None:
        with self.lock:
            return self.key_to_account.get(access_key_id)

    # --- error injection -----------------------------------------------------------------

    def add_error(self, rule: ErrorRule) -> None:
        with self.lock:
            self.errors.append(rule)

    def take_error(
        self, account_id: str, region: str, operation: str, types: list[str]
    ) -> str | None:
        """The injected error (`AccessDenied` ...) for this call, consuming one use."""
        with self.lock:
            for rule in self.errors:
                if rule.matches(account_id, region, operation, types):
                    if rule.count is not None:
                        rule.count -= 1
                        if rule.count <= 0:
                            self.errors.remove(rule)
                    return rule.error
        return None
