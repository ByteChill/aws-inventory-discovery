"""ResourceWriter on a real Postgres, fed by observations Discovery read from moto / fake-aws.

DESIGN 7.1 cases B, C, D, G, H plus the two-evidence deletion rules of R5. Times are the only
thing the tests set by hand; resource data always comes from the simulated AWS responses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import seed  # ty: ignore[import-not-found]
from botocore.exceptions import ClientError
from sqlalchemy import Engine, text
from sqlmodel import Session, select

from app.core.config import Settings
from app.models import Account, ResourceRelation
from app.models.resource import Resource
from app.models.scan import ScanRequest, ScanShard
from app.types.mapping import ConfigTag, to_config_item
from app.types.registry import TypeRegistry
from app.worker.discovery import Discovery, Observation
from app.worker.writer import ResourceWriter
from tests.conftest import AwsEnv

REGISTRY = TypeRegistry.from_directory()
REGION = "us-east-1"
QUEUE, TASKDEF, LAMBDA = "AWS::SQS::Queue", "AWS::ECS::TaskDefinition", "AWS::Lambda::Function"
T0 = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def minutes(n: int) -> timedelta:
    return timedelta(minutes=n)


@pytest.fixture
def account_id(aws: AwsEnv, session: Session) -> str:
    account = aws.account_ids[0]
    entry = next(e for e in seed.build_registry(aws.account_ids) if e["account_id"] == account)
    session.add(Account(**entry, registry_fetched_at=T0))
    session.commit()
    return account


@pytest.fixture
def writer(engine: Engine, make_settings: Callable[..., Settings]) -> ResourceWriter:
    return ResourceWriter(engine, REGISTRY, make_settings(MISSING_GUARD_MIN=1))


@pytest.fixture
def disc(aws: AwsEnv, account_id: str) -> Discovery:
    return Discovery(aws.session(account_id), account_id, REGION, REGISTRY)


def live(disc: Discovery, type_name: str, when: datetime = T0) -> list[Observation]:
    scan = disc.scan_live(type_name)
    assert scan.complete
    return [replace(o, observed_at=when) for o in scan.observations]


def rows(session: Session, type_name: str) -> dict[str, Resource]:
    session.expire_all()
    found = session.exec(select(Resource).where(Resource.resource_type == type_name)).all()
    return {r.resource_id: r for r in found}


def config_of(row: Resource) -> dict[str, Any]:
    assert row.configuration is not None
    return row.configuration


def live_raw_of(row: Resource) -> dict[str, Any]:
    assert row.live_raw is not None
    return row.live_raw


def gone(writer: ResourceWriter, obs: Observation, when: datetime, **kw: Any) -> str:
    item = obs.item
    return writer.record_gone(
        account_id=item.account_id,
        region=item.aws_region,
        resource_type=item.resource_type,
        resource_id=item.resource_id,
        observed_at=when,
        **kw,
    )


def finish(
    writer: ResourceWriter,
    seen: list[Observation],
    type_name: str,
    when: datetime,
    *,
    account_id: str | None = None,
    complete: bool = True,
    cutoff: datetime | None = None,
):
    account = account_id or seen[0].item.account_id
    return writer.finish_scope(
        account_id=account,
        region=REGION,
        resource_type=type_name,
        seen_ids=[o.item.resource_id for o in seen],
        observed_at=when,
        complete=complete,
        cutoff=cutoff,
    )


# --- write rules (R4, hash, counts) -----------------------------------------------------------


def test_write_counts_hash_and_scan_request_counts(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    request = ScanRequest(trigger="api", priority=0, status="running")
    session.add(request)
    session.commit()
    obs = live(disc, QUEUE)
    assert len(obs) == 2

    assert writer.write(obs, scan_request_id=request.id).new == 2
    unchanged = writer.write([replace(o, observed_at=T0 + minutes(1)) for o in obs])
    assert (unchanged.unchanged, unchanged.updated) == (2, 0)
    first = rows(session, QUEUE)[obs[0].item.resource_id]
    assert first.observed_at == T0 + minutes(1) and first.last_changed_at == T0
    assert first.first_seen_at == T0

    retagged = obs[0].item.model_copy(update={"tags": [ConfigTag(key="Owner", value="TeamB")]})
    changed = writer.write(
        [replace(obs[0], item=retagged, observed_at=T0 + minutes(2))], scan_request_id=request.id
    )
    assert changed.updated == 1
    first = rows(session, QUEUE)[obs[0].item.resource_id]
    assert first.last_changed_at == T0 + minutes(2) and first.tags[0]["value"] == "TeamB"
    session.refresh(request)
    assert request.counts == {"new": 2, "updated": 1}


def test_older_observation_does_not_overwrite_newer(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:  # case B
    (a, *_) = live(disc, QUEUE, T0 + minutes(10))
    writer.write([a])
    older = replace(
        a,
        item=a.item.model_copy(update={"tags": [ConfigTag(key="Owner", value="Stale")]}),
        observed_at=T0,
    )
    assert writer.write([older]).stale == 1
    row = rows(session, QUEUE)[a.item.resource_id]
    assert row.tags[0]["value"] == "TeamA" and row.observed_at == T0 + minutes(10)


def test_overlay_follows_covered_fields_and_keeps_the_rest(
    writer: ResourceWriter, disc: Discovery, aws: AwsEnv, session: Session, account_id: str
) -> None:
    spec = REGISTRY.get(LAMBDA)
    sweep = disc.sweep_config([LAMBDA])
    config_obs = sweep.by_type[LAMBDA][0]
    writer.write([config_obs])
    stored = rows(session, LAMBDA)[config_obs.item.resource_id]
    assert config_of(stored)["memorySize"] == 128 and "codeSize" in config_of(stored)

    (cc_obs,) = live(disc, LAMBDA, when=config_obs.observed_at + minutes(5))
    assert cc_obs.live_raw is not None
    properties = {k: v for k, v in cc_obs.live_raw.items() if k != "MemorySize"}
    item = to_config_item(spec, properties, account_id=account_id, region=REGION)
    writer.write([replace(cc_obs, item=item, live_raw=properties)])

    merged = config_of(rows(session, LAMBDA)[config_obs.item.resource_id])
    assert "memorySize" not in merged  # mapped by Cloud Control, absent now: cleared
    assert merged["codeSize"] == 1024  # only Config has it: kept
    assert merged["timeout"] == 30


def test_secrets_never_reach_the_database(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    (obs,) = live(disc, LAMBDA)
    assert "dummy-password-for-tests" in str(obs.live_raw)  # present before the Writer
    writer.write([obs])
    dumped = (
        session.connection()
        .execute(text("SELECT string_agg(row_to_json(r)::text, ' ') FROM t_inv_aws_resource r"))
        .scalar_one()
    )
    assert "dummy-password-for-tests" not in dumped
    row = rows(session, LAMBDA)[obs.item.resource_id]
    assert live_raw_of(row)["Environment"]["Variables"] == {
        "DB_PASSWORD": "***",
        "LOG_LEVEL": "***",
    }
    assert set(config_of(row)["environment"]["variables"].values()) == {"***"}


def test_relationships_are_replaced_and_resolved_to_row_ids(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::VPC", "AWS::EC2::Subnet"])
    writer.write([o for group in sweep.by_type.values() for o in group])
    (vpc,) = rows(session, "AWS::EC2::VPC").values()
    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    relations = session.exec(
        select(ResourceRelation).where(ResourceRelation.from_resource_id == subnet.id)
    ).all()
    by_target = {r.to_resource_key: r.to_resource_id for r in relations}
    assert by_target[vpc.resource_id] == vpc.id
    assert None in by_target.values()  # the instance is not inventoried: unresolved


def test_relation_to_a_resource_inserted_later_is_resolved_late(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::VPC", "AWS::EC2::Subnet"])
    writer.write(sweep.by_type["AWS::EC2::Subnet"])
    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    to_vpc = select(ResourceRelation).where(
        ResourceRelation.from_resource_id == subnet.id,
        ResourceRelation.to_resource_type == "AWS::EC2::VPC",
    )
    assert session.exec(to_vpc).one().to_resource_id is None

    writer.write(sweep.by_type["AWS::EC2::VPC"])

    (vpc,) = rows(session, "AWS::EC2::VPC").values()
    session.expire_all()
    assert session.exec(to_vpc).one().to_resource_id == vpc.id


# --- deletion (R5, R6) ------------------------------------------------------------------------


def test_resource_created_after_the_list_is_not_missing(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:  # case C
    obs = live(disc, QUEUE, T0 + minutes(5))  # written by a refresh after the scan started
    writer.write(obs)
    outcome = finish(writer, [], QUEUE, T0, account_id=obs[0].item.account_id)
    assert outcome.ran and outcome.missing == 0
    assert {r.status for r in rows(session, QUEUE).values()} == {"active"}


def test_incomplete_read_never_marks_anything(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:  # case D
    obs = live(disc, QUEUE)
    writer.write(obs)
    outcome = finish(writer, obs[:1], QUEUE, T0 + minutes(10), complete=False)
    assert not outcome.ran
    assert {r.status for r in rows(session, QUEUE).values()} == {"active"}
    assert finish(writer, obs[:1], QUEUE, T0 + minutes(10)).missing == 1


def test_config_lag_grace_protects_recent_resources(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:  # case G
    recent, old = live(disc, QUEUE)
    writer.write([replace(recent, observed_at=T0), replace(old, observed_at=T0 - minutes(30))])
    query_started = T0 + minutes(5)
    outcome = finish(
        writer,
        [],
        QUEUE,
        query_started,
        account_id=recent.item.account_id,
        cutoff=writer.config_cutoff(query_started),
    )
    assert outcome.missing == 1
    found = rows(session, QUEUE)
    assert found[recent.item.resource_id].status == "active"  # within 15 minutes of the query
    assert found[old.item.resource_id].status == "missing"


def test_mass_disappearance_is_held_until_confirmed_then_checked_one_by_one(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:  # case H
    obs = live(disc, TASKDEF)
    assert len(obs) == 2
    writer.write(obs)
    account = obs[0].item.account_id

    held = finish(writer, [], TASKDEF, T0 + minutes(10), account_id=account)
    assert held.guard == "abnormal" and held.missing == 0
    assert {r.status for r in rows(session, TASKDEF).values()} == {"active"}
    shard = session.get_one(ScanShard, (account, REGION, TASKDEF))
    assert shard.guard_state == "abnormal" and shard.guard_detected_at is not None
    # The review page's candidates: what the scan wanted to mark missing, held for a person.
    assert {c["resourceId"] for c in shard.candidate_ids or []} == {o.item.resource_id for o in obs}
    assert {c["arn"] for c in shard.candidate_ids or []} == {o.item.arn for o in obs}

    writer.confirm_guard(account_id=account, region=REGION, resource_type=TASKDEF, by="admin")
    confirmed = finish(writer, [], TASKDEF, T0 + minutes(20), account_id=account)
    assert confirmed.missing == 2 and confirmed.confirm_pending == 2
    assert {r.status for r in rows(session, TASKDEF).values()} == {"missing"}  # not deleted
    session.expire_all()
    shard = session.get_one(ScanShard, (account, REGION, TASKDEF))
    assert shard.guard_state == "normal"
    assert shard.candidate_count is None and shard.candidate_ids is None

    assert [gone(writer, o, T0 + minutes(30)) for o in obs] == ["deleted", "deleted"]


def test_deletion_needs_both_pieces_of_evidence_and_a_newer_observation(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    keep, lost = live(disc, QUEUE)
    writer.write([keep, lost])
    finish(writer, [keep], QUEUE, T0 + minutes(10))
    row = rows(session, QUEUE)[lost.item.resource_id]
    assert row.status == "missing" and row.absent_in_scan_at == T0 + minutes(10)

    assert gone(writer, lost, T0 - minutes(1)) == "stale"  # R4: must be newer than observed_at
    assert gone(writer, lost, T0 + minutes(20)) == "deleted"
    row = rows(session, QUEUE)[lost.item.resource_id]
    assert row.status == "deleted" and row.deleted_at == T0 + minutes(20)

    # seen again (an observation newer than the deletion): back to active, evidence cleared
    counts = writer.write([replace(lost, observed_at=T0 + minutes(30))])
    assert counts.restored == 1
    row = rows(session, QUEUE)[lost.item.resource_id]
    assert (row.status, row.deleted_at, row.absent_in_scan_at, row.missing_since) == (
        "active",
        None,
        None,
        None,
    )


def test_refresh_that_finds_nothing_only_marks_missing_until_a_scan_agrees(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    keep, lost = live(disc, QUEUE)
    writer.write([keep, lost])
    assert gone(writer, lost, T0 + minutes(5)) == "missing"
    assert gone(writer, lost, T0 + minutes(6)) == "unchanged"  # second evidence alone: no delete
    row = rows(session, QUEUE)[lost.item.resource_id]
    assert row.status == "missing" and row.absent_in_scan_at is None

    finish(writer, [keep], QUEUE, T0 + minutes(10))  # a complete scan also misses it
    assert rows(session, QUEUE)[lost.item.resource_id].absent_in_scan_at == T0 + minutes(10)
    assert gone(writer, lost, T0 + minutes(11)) == "deleted"
    assert gone(writer, replace_id(lost, "no-such-queue"), T0 + minutes(12)) == "not_found"


def replace_id(obs: Observation, resource_id: str) -> Observation:
    return replace(obs, item=obs.item.model_copy(update={"resource_id": resource_id}))


def test_a_read_error_leaves_the_resource_missing(
    writer: ResourceWriter, disc: Discovery, aws: AwsEnv, session: Session
) -> None:
    keep, lost = live(disc, QUEUE)
    writer.write([keep, lost])
    finish(writer, [keep], QUEUE, T0 + minutes(10))
    aws.admin("POST", "/errors", json={"error": "AccessDenied", "operation": "GetResource"})
    with pytest.raises(ClientError):  # Discovery never turns an error into "gone"
        disc.fetch(QUEUE, lost.item.resource_id)
    writer.record_check_failed(
        account_id=lost.item.account_id,
        region=REGION,
        resource_type=QUEUE,
        resource_id=lost.item.resource_id,
    )
    row = rows(session, QUEUE)[lost.item.resource_id]
    assert (row.status, row.missing_checks) == ("missing", 1)


# --- account access status --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("credential_failed", "codes", "expected"),
    [
        (True, [], "credential_error"),
        (False, ["access_denied", "access_denied"], "access_denied"),
        (False, ["access_denied", None], "partial"),
        (False, [None, "throttled"], "ok"),
    ],
)
def test_access_status(
    writer: ResourceWriter,
    session: Session,
    account_id: str,
    credential_failed: bool,
    codes: list[str | None],
    expected: str,
) -> None:
    status = writer.update_access_status(
        account_id, credential_failed=credential_failed, error_codes=codes, message="why"
    )
    session.expire_all()
    account = session.get_one(Account, account_id)
    assert status == expected == account.access_status
    assert (account.last_scan_ok_at is not None) == (expected == "ok")
    assert (account.access_error is None) == (expected == "ok")


def test_an_accepted_gone_observation_blocks_an_older_in_flight_listing(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    """A list read at T1 is still in flight when a refresh at T2 finds the resource gone (and
    again at T4 / T6): the late T1 write must not bring it back, in any of the three states."""
    keep, lost = live(disc, QUEUE)
    key = lost.item.resource_id
    writer.write([keep, lost])
    stale_listing = replace(lost, observed_at=T0 + minutes(1))

    assert gone(writer, lost, T0 + minutes(2)) == "missing"  # active -> missing
    row = rows(session, QUEUE)[key]
    assert row.observed_at == T0 + minutes(2) and row.observed_source == "cloudcontrol"
    assert writer.write([stale_listing]).stale == 1
    assert rows(session, QUEUE)[key].status == "missing"

    assert gone(writer, lost, T0 + minutes(4)) == "unchanged"  # stays missing
    assert rows(session, QUEUE)[key].observed_at == T0 + minutes(4)
    assert writer.write([replace(lost, observed_at=T0 + minutes(3))]).stale == 1

    finish(writer, [keep], QUEUE, T0 + minutes(5))
    assert gone(writer, lost, T0 + minutes(6), source="native") == "deleted"  # -> deleted
    row = rows(session, QUEUE)[key]
    assert (row.observed_at, row.observed_source) == (T0 + minutes(6), "native")
    assert writer.write([replace(lost, observed_at=T0 + minutes(5))]).stale == 1
    assert rows(session, QUEUE)[key].status == "deleted"


def test_a_relationship_only_change_updates_the_relations_but_is_not_a_resource_change(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::VPC", "AWS::EC2::Subnet"])
    writer.write([o for group in sweep.by_type.values() for o in group])
    (vpc,) = rows(session, "AWS::EC2::VPC").values()
    (subnet_obs,) = sweep.by_type["AWS::EC2::Subnet"]
    full = subnet_obs.relationships or []
    without_vpc = [r for r in full if r["resourceType"] != "AWS::EC2::VPC"]
    assert 0 < len(without_vpc) < len(full)

    def targets() -> dict[str, int | None]:
        session.expire_all()
        (subnet,) = rows(session, "AWS::EC2::Subnet").values()
        found = session.exec(
            select(ResourceRelation).where(ResourceRelation.from_resource_id == subnet.id)
        ).all()
        return {r.to_resource_key: r.to_resource_id for r in found}

    dropped = replace(subnet_obs, relationships=without_vpc, observed_at=T0 + minutes(1))
    counts = writer.write([dropped])

    assert (counts.updated, counts.unchanged) == (0, 1)
    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    assert subnet.relationships == without_vpc
    assert subnet.last_changed_at == subnet_obs.observed_at  # not a resource change
    assert subnet.observed_at == T0 + minutes(1)
    assert vpc.resource_id not in targets()

    regained = replace(subnet_obs, relationships=full, observed_at=T0 + minutes(2))
    assert writer.write([regained]).updated == 0
    assert targets()[vpc.resource_id] == vpc.id  # resolved again
    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    assert subnet.relationships == full and subnet.last_changed_at == subnet_obs.observed_at
