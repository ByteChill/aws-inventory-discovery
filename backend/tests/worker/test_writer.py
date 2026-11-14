"""ResourceWriter on a real Postgres, fed by observations Discovery read from moto / fake-aws.

DESIGN 7.1 cases B, C, D, G, H plus the two-evidence deletion rules of R5. Times are the only
thing the tests set by hand; resource data always comes from the simulated AWS responses.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, local
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
from app.worker.writer import RelationJob, ResourceWriter
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


def test_derived_relationships_come_from_the_configuration_and_resolve_to_row_ids(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(
        ["AWS::ECS::Service", "AWS::ECS::TaskDefinition", "AWS::ECS::Cluster"]
    )
    writer.write([o for group in sweep.by_type.values() for o in group])

    (service,) = rows(session, "AWS::ECS::Service").values()
    (taskdef,) = rows(session, "AWS::ECS::TaskDefinition").values()
    (cluster,) = rows(session, "AWS::ECS::Cluster").values()
    derived = {r["relationshipName"]: r for r in service.relationships if r.get("origin")}
    assert derived["TaskDefinition"]["resourceId"] == taskdef.resource_id
    assert derived["Cluster"]["resourceId"] == cluster.resource_id

    relations = session.exec(
        select(ResourceRelation).where(ResourceRelation.from_resource_id == service.id)
    ).all()
    resolved = {r.relation_name: r.to_resource_id for r in relations}
    assert resolved["TaskDefinition"] == taskdef.id
    assert resolved["Cluster"] == cluster.id


def test_array_paths_derive_every_reference_and_resolve_them(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::Subnet", "AWS::Lambda::Function"])
    (lambda_obs,) = sweep.by_type["AWS::Lambda::Function"]
    observations = [
        *sweep.by_type["AWS::EC2::Subnet"],
        replace(lambda_obs, relationships=[]),  # isolate the derived edges
        *live(disc, "AWS::EC2::SecurityGroup"),
    ]
    writer.write(observations)

    (function,) = rows(session, "AWS::Lambda::Function").values()
    derived = {
        relationship["relationshipName"]: relationship
        for relationship in function.relationships
        if relationship.get("origin") == "derived"
    }
    assert set(derived) == {"Subnet", "SecurityGroup"}

    subnets = rows(session, "AWS::EC2::Subnet")
    groups = rows(session, "AWS::EC2::SecurityGroup")
    relations = session.exec(
        select(ResourceRelation).where(ResourceRelation.from_resource_id == function.id)
    ).all()
    resolved = {relation.relation_name: relation.to_resource_id for relation in relations}
    assert resolved["Subnet"] == subnets[derived["Subnet"]["resourceId"]].id
    assert resolved["SecurityGroup"] == groups[derived["SecurityGroup"]["resourceId"]].id


def test_ecs_target_group_array_is_replaced_and_stale_data_cannot_restore_removed_edges(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    (service_obs,) = live(disc, "AWS::ECS::Service")
    properties = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/aws/aws.ecs.service/relationship_properties.json"
        ).read_text()
    )
    configuration = {**service_obs.item.configuration, **properties}
    service_obs = replace(
        service_obs,
        item=service_obs.item.model_copy(update={"configuration": configuration}),
    )
    writer.write([service_obs])
    (service,) = rows(session, "AWS::ECS::Service").values()
    target_query = select(ResourceRelation).where(
        ResourceRelation.from_resource_id == service.id,
        ResourceRelation.to_resource_type == "AWS::ElasticLoadBalancingV2::TargetGroup",
    )
    edges = session.exec(target_query).all()
    assert {edge.to_resource_key for edge in edges} == {
        entry["TargetGroupArn"] for entry in properties["LoadBalancers"]
    }
    assert all(edge.to_resource_field == "arn" for edge in edges)
    configuration = {key: value for key, value in configuration.items() if key != "LoadBalancers"}
    writer.write(
        [
            replace(
                service_obs,
                item=service_obs.item.model_copy(update={"configuration": configuration}),
                observed_at=T0 + minutes(1),
            )
        ]
    )
    assert writer.write([service_obs]).stale == 1
    session.expire_all()
    assert session.exec(target_query).all() == []
    assert not any(
        edge["resourceType"] == "AWS::ElasticLoadBalancingV2::TargetGroup"
        for edge in service.relationships
    )


def test_arn_references_resolve_and_are_late_resolved(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    target_groups = live(disc, "AWS::ElasticLoadBalancingV2::TargetGroup")
    writer.write(target_groups)
    target_rows = rows(session, "AWS::ElasticLoadBalancingV2::TargetGroup")
    assert target_rows
    assert all(
        relation.to_resource_id is None
        for relation in session.exec(
            select(ResourceRelation).where(
                ResourceRelation.to_resource_type == "AWS::ElasticLoadBalancingV2::LoadBalancer"
            )
        ).all()
    )

    writer.write(live(disc, "AWS::ElasticLoadBalancingV2::LoadBalancer"))

    load_balancers = rows(session, "AWS::ElasticLoadBalancingV2::LoadBalancer")
    assert load_balancers
    resolved = session.exec(
        select(ResourceRelation).where(
            ResourceRelation.to_resource_type == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        )
    ).all()
    assert resolved
    assert all(
        relation.to_resource_id in {row.id for row in load_balancers.values()}
        for relation in resolved
    )


def test_config_wins_when_an_arn_derived_edge_late_resolves_to_the_same_target(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    (secret_obs,) = sweep.by_type["AWS::SecretsManager::Secret"]
    (lambda_obs,) = sweep.by_type["AWS::Lambda::Function"]
    writer.write([secret_obs])

    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()
    assert (
        len(
            [
                relationship
                for relationship in secret.relationships
                if relationship["resourceType"] == "AWS::Lambda::Function"
            ]
        )
        == 2
    )

    writer.write([lambda_obs])

    (lambda_row,) = rows(session, "AWS::Lambda::Function").values()
    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()
    lambda_edges = [
        relationship
        for relationship in secret.relationships
        if relationship["resourceType"] == "AWS::Lambda::Function"
    ]
    assert len(lambda_edges) == 1
    assert lambda_edges[0].get("origin") != "derived"
    relations = session.exec(
        select(ResourceRelation).where(
            ResourceRelation.from_resource_id == secret.id,
            ResourceRelation.to_resource_type == "AWS::Lambda::Function",
        )
    ).all()
    assert len(relations) == 1
    assert relations[0].to_resource_id == lambda_row.id
    assert relations[0].relation_name == lambda_edges[0]["relationshipName"] == "RotationLambda"
    assert secret.observed_at == secret.last_changed_at == secret_obs.observed_at


def test_concurrent_source_and_target_writes_resolve_after_both_commit(
    writer: ResourceWriter, disc: Discovery, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    observations = [observation for group in sweep.by_type.values() for observation in group]
    rendezvous = Barrier(2, timeout=10)
    thread_state = local()
    replace_relations = writer._replace_relations

    def overlap(db: Session, jobs: list[RelationJob]) -> None:
        replace_relations(db, jobs)
        if not getattr(thread_state, "waited", False):
            thread_state.waited = True
            rendezvous.wait()

    monkeypatch.setattr(writer, "_replace_relations", overlap)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(writer.write, [observation]) for observation in observations]
        assert sum(future.result(timeout=15).new for future in futures) == 2

    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()
    (function,) = rows(session, LAMBDA).values()
    relation = session.exec(
        select(ResourceRelation).where(ResourceRelation.from_resource_id == secret.id)
    ).one()
    assert relation.to_resource_id == function.id
    assert relation.to_resource_field == "resourceId"
    assert relation.relation_name == secret.relationships[0]["relationshipName"]
    assert len(secret.relationships) == 1


def test_arn_resolution_stays_in_account_region_and_does_not_guess_ambiguous_targets(
    writer: ResourceWriter, disc: Discovery, session: Session, aws: AwsEnv
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    (function_obs,) = sweep.by_type[LAMBDA]
    (secret_obs,) = sweep.by_type["AWS::SecretsManager::Secret"]
    source = replace(secret_obs, relationships=[])
    other_account = aws.account_ids[1]
    entry = next(
        entry
        for entry in seed.build_registry(aws.account_ids)
        if entry["account_id"] == other_account
    )
    session.add(Account(**entry, registry_fetched_at=T0))
    session.commit()
    for overrides in ({"account_id": other_account}, {"aws_region": "eu-west-2"}):
        writer.write([replace(function_obs, item=function_obs.item.model_copy(update=overrides))])
    writer.write([source])
    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()

    def edge() -> ResourceRelation:
        session.expire_all()
        return session.exec(
            select(ResourceRelation).where(ResourceRelation.from_resource_id == secret.id)
        ).one()

    assert edge().to_resource_id is None
    writer.write([function_obs])
    assert edge().to_resource_id is not None
    duplicate = replace(
        function_obs,
        item=function_obs.item.model_copy(
            update={"resource_id": function_obs.item.resource_id + "-duplicate"}
        ),
    )
    writer.write([duplicate])
    assert edge().to_resource_id is None


def test_existing_target_receiving_an_arn_resolves_incoming_edges(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    (function_obs,) = sweep.by_type[LAMBDA]
    (secret_obs,) = sweep.by_type["AWS::SecretsManager::Secret"]
    writer.write(
        [
            replace(function_obs, item=function_obs.item.model_copy(update={"arn": None})),
            replace(secret_obs, relationships=[]),
        ]
    )
    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()
    (function,) = rows(session, LAMBDA).values()
    unchanged_at = function.last_changed_at
    relation_query = select(ResourceRelation).where(ResourceRelation.from_resource_id == secret.id)
    assert session.exec(relation_query).one().to_resource_id is None
    assert (
        writer.write(
            [replace(function_obs, observed_at=function_obs.observed_at + minutes(1))]
        ).unchanged
        == 1
    )
    session.expire_all()
    assert session.exec(relation_query).one().to_resource_id == function.id
    assert function.last_changed_at == unchanged_at


def test_a_config_relationship_is_not_duplicated_by_the_derived_one(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::VPC", "AWS::EC2::Subnet"])
    writer.write([o for group in sweep.by_type.values() for o in group])

    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    vpc_edges = [r for r in subnet.relationships if r["resourceType"] == "AWS::EC2::VPC"]
    assert len(vpc_edges) == 1
    assert vpc_edges[0].get("origin") != "derived"


def test_a_cloud_control_write_keeps_config_relationships_and_recomputes_derived_ones(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::VPC", "AWS::EC2::Subnet"])
    writer.write([o for group in sweep.by_type.values() for o in group])
    (config_subnet,) = rows(session, "AWS::EC2::Subnet").values()
    before = [dict(r) for r in config_subnet.relationships]

    live_subnet = next(
        o for o in live(disc, "AWS::EC2::Subnet") if o.item.resource_id == config_subnet.resource_id
    )
    writer.write([live_subnet])

    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    assert subnet.relationships == before


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

    assert writer.confirm_guard(
        account_id=account,
        region=REGION,
        resource_type=TASKDEF,
        by="admin",
        detected_at=shard.guard_detected_at,
    )
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
    (subnet_obs,) = sweep.by_type["AWS::EC2::Subnet"]
    full = subnet_obs.relationships or []
    instance_id = next(r["resourceId"] for r in full if r["resourceType"] == "AWS::EC2::Instance")
    without_instance = [r for r in full if r["resourceType"] != "AWS::EC2::Instance"]
    assert 0 < len(without_instance) < len(full)

    def targets() -> dict[str, int | None]:
        session.expire_all()
        (subnet,) = rows(session, "AWS::EC2::Subnet").values()
        found = session.exec(
            select(ResourceRelation).where(ResourceRelation.from_resource_id == subnet.id)
        ).all()
        return {r.to_resource_key: r.to_resource_id for r in found}

    def edges(relationships: list[dict[str, Any]]) -> set[tuple[str, str]]:
        return {(r["resourceType"], r["resourceId"]) for r in relationships}

    dropped = replace(subnet_obs, relationships=without_instance, observed_at=T0 + minutes(1))
    counts = writer.write([dropped])

    assert (counts.updated, counts.unchanged) == (0, 1)
    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    assert edges(subnet.relationships) == edges(without_instance)
    assert subnet.last_changed_at == subnet_obs.observed_at  # not a resource change
    assert subnet.observed_at == T0 + minutes(1)
    assert instance_id not in targets()

    regained = replace(subnet_obs, relationships=full, observed_at=T0 + minutes(2))
    assert writer.write([regained]).updated == 0
    assert targets()[instance_id] is None  # the instance is not inventoried
    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    assert edges(subnet.relationships) == edges(full)
    assert subnet.last_changed_at == subnet_obs.observed_at
