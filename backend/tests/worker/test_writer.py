"""ResourceWriter on a real Postgres, fed by observations Discovery read from moto / fake-aws.

DESIGN 7.1 cases B, C, D, G, H plus the two-evidence deletion rules of R5. Times are the only
thing the tests set by hand; resource data always comes from the simulated AWS responses.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, local
from typing import Any

import pytest
import seed  # ty: ignore[import-not-found]
from botocore.exceptions import ClientError
from sqlalchemy import Engine, event, text
from sqlmodel import Session, select

from app.core.config import Settings
from app.models import Account, ResourceRelation
from app.models.resource import Resource
from app.models.scan import ScanRequest, ScanShard
from app.types.mapping import ConfigTag, to_config_item
from app.types.registry import TypeRegistry
from app.worker import leases, queue, requests
from app.worker.discovery import Discovery, Observation
from app.worker.errors import TaskLost
from app.worker.writer import INCOMING_RELATION_SOURCES, RelationJob, ResourceWriter
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
    unique_target_id = edge().to_resource_id
    assert unique_target_id is not None
    duplicate = replace(
        function_obs,
        item=function_obs.item.model_copy(
            update={"resource_id": function_obs.item.resource_id + "-duplicate"}
        ),
    )
    writer.write([duplicate])
    assert edge().to_resource_id is None
    before_source = (
        secret.observed_at,
        secret.last_changed_at,
        secret.content_hash,
    )
    assert duplicate.item.arn is not None
    changed_duplicate = replace(
        duplicate,
        item=duplicate.item.model_copy(update={"arn": duplicate.item.arn + "-changed"}),
        observed_at=duplicate.observed_at + minutes(1),
    )
    writer.write([changed_duplicate])
    assert edge().to_resource_id == unique_target_id
    assert (secret.observed_at, secret.last_changed_at, secret.content_hash) == before_source
    assert writer.write([duplicate]).stale == 1
    assert edge().to_resource_id == unique_target_id


def test_deleting_an_ambiguous_target_resolves_the_surviving_target(
    writer: ResourceWriter, disc: Discovery, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    (function_obs,) = sweep.by_type[LAMBDA]
    (secret_obs,) = sweep.by_type["AWS::SecretsManager::Secret"]
    writer.write([function_obs])
    writer.write([replace(secret_obs, relationships=[])])
    (function,) = rows(session, LAMBDA).values()
    duplicate = replace(
        function_obs,
        item=function_obs.item.model_copy(
            update={"resource_id": function_obs.item.resource_id + "-duplicate"}
        ),
    )
    writer.write([duplicate])
    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()

    def edge() -> ResourceRelation:
        session.expire_all()
        return session.exec(
            select(ResourceRelation).where(ResourceRelation.from_resource_id == secret.id)
        ).one()

    assert edge().to_resource_id is None
    outcome = writer.finish_scope(
        account_id=duplicate.item.account_id,
        region=duplicate.item.aws_region,
        resource_type=duplicate.item.resource_type,
        seen_ids=[function_obs.item.resource_id],
        observed_at=duplicate.observed_at + minutes(1),
        complete=True,
    )
    assert outcome.missing == 1

    resolve = writer._resolve_dangling_relations

    def lose_claim(*args: Any, **kwargs: Any) -> None:
        raise TaskLost

    monkeypatch.setattr(writer, "_resolve_dangling_relations", lose_claim)
    with pytest.raises(TaskLost):
        gone(writer, duplicate, duplicate.observed_at + minutes(2))
    assert edge().to_resource_id is None
    monkeypatch.setattr(writer, "_resolve_dangling_relations", resolve)
    assert gone(writer, duplicate, duplicate.observed_at + minutes(2)) == "stale"
    assert edge().to_resource_id == function.id

    outcome = writer.finish_scope(
        account_id=function_obs.item.account_id,
        region=function_obs.item.aws_region,
        resource_type=function_obs.item.resource_type,
        seen_ids=[],
        observed_at=function_obs.observed_at + minutes(3),
        complete=True,
    )
    assert outcome.missing == 1
    assert gone(writer, function_obs, function_obs.observed_at + minutes(4)) == "deleted"
    assert edge().to_resource_id is None


def test_resolved_source_rechecks_a_target_arn_changed_before_source_commit(
    writer: ResourceWriter, disc: Discovery, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    (function_obs,) = sweep.by_type[LAMBDA]
    (secret_obs,) = sweep.by_type["AWS::SecretsManager::Secret"]
    writer.write([function_obs])
    source_ready, allow_commit = Event(), Event()
    replace_relations = writer._replace_relations

    def hold_source(db: Session, jobs: list[RelationJob]) -> None:
        replace_relations(db, jobs)
        if any(
            relationship.get("relationshipName") == "RotationLambda"
            for job in jobs
            for relationship in job.relationships
        ):
            source_ready.set()
            assert allow_commit.wait(10)

    monkeypatch.setattr(writer, "_replace_relations", hold_source)
    with ThreadPoolExecutor(max_workers=1) as executor:
        source_write = executor.submit(writer.write, [replace(secret_obs, relationships=[])])
        try:
            assert source_ready.wait(10)
            assert function_obs.item.arn is not None
            changed = replace(
                function_obs,
                item=function_obs.item.model_copy(
                    update={"arn": function_obs.item.arn + "-changed"}
                ),
                observed_at=function_obs.observed_at + minutes(1),
            )
            writer.write([changed])
        finally:
            allow_commit.set()
        assert source_write.result(timeout=15).new == 1

    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()
    relation = session.exec(
        select(ResourceRelation).where(ResourceRelation.from_resource_id == secret.id)
    ).one()
    assert relation.to_resource_id is None
    assert relation.to_resource_key == function_obs.item.arn
    assert secret.observed_at == secret.last_changed_at == secret_obs.observed_at


def test_finish_scope_and_reconciliation_lock_active_and_missing_rows_together(
    writer: ResourceWriter, disc: Discovery, session: Session, engine: Engine
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::VPC", "AWS::EC2::Subnet"])
    (vpc_obs,) = sweep.by_type["AWS::EC2::VPC"]
    (subnet_obs,) = sweep.by_type["AWS::EC2::Subnet"]
    second = replace(
        subnet_obs,
        item=subnet_obs.item.model_copy(
            update={"resource_id": subnet_obs.item.resource_id + "-second"}
        ),
    )
    writer.write([vpc_obs, subnet_obs, second])
    assert gone(writer, subnet_obs, subnet_obs.observed_at + minutes(1)) == "missing"
    (vpc,) = rows(session, "AWS::EC2::VPC").values()
    assert vpc.id is not None
    target_id = vpc.id
    session.commit()
    candidates_locked, release_finish, reconciling = Event(), Event(), Event()
    reconcile_pids: list[int] = []

    def after_sql(connection, cursor, statement, parameters, context, executemany):
        if (
            statement.startswith(
                "SELECT t_inv_aws_resource.id, t_inv_aws_resource.resource_id,"
                " t_inv_aws_resource.arn"
            )
            and "FOR UPDATE" in statement
        ):
            candidates_locked.set()
            assert release_finish.wait(10)

    def before_sql(connection, cursor, statement, parameters, context, executemany):
        if "FOR NO KEY UPDATE" in statement:
            reconcile_pids.append(
                connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            )
            reconciling.set()

    event.listen(engine, "after_cursor_execute", after_sql)
    event.listen(engine, "before_cursor_execute", before_sql)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            finished = executor.submit(
                writer.finish_scope,
                account_id=subnet_obs.item.account_id,
                region=subnet_obs.item.aws_region,
                resource_type=subnet_obs.item.resource_type,
                seen_ids=[],
                observed_at=subnet_obs.observed_at + minutes(2),
                complete=True,
            )
            try:
                assert candidates_locked.wait(10)
                reconciled = executor.submit(writer._resolve_dangling_relations, [target_id])
                assert reconciling.wait(10)
                with engine.connect() as connection:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if connection.execute(
                            text("SELECT cardinality(pg_blocking_pids(:pid))"),
                            {"pid": reconcile_pids[0]},
                        ).scalar_one():
                            break
                        time.sleep(0.01)
                    else:
                        pytest.fail("reconciliation did not wait for finish_scope")
            finally:
                release_finish.set()
            outcome = finished.result(timeout=15)
            reconciled.result(timeout=15)
    finally:
        release_finish.set()
        event.remove(engine, "after_cursor_execute", after_sql)
        event.remove(engine, "before_cursor_execute", before_sql)

    assert (outcome.missing, outcome.confirm_pending, outcome.guard) == (1, 2, "normal")
    subnets = rows(session, "AWS::EC2::Subnet")
    assert all(resource.status == "missing" for resource in subnets.values())
    assert all(
        resource.absent_in_scan_at == subnet_obs.observed_at + minutes(2)
        for resource in subnets.values()
    )


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


def test_incoming_reconciliation_uses_bounded_lookups_at_inventory_scale(
    writer: ResourceWriter, disc: Discovery, session: Session, engine: Engine, tmp_path: Path
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    (function_obs,) = sweep.by_type[LAMBDA]
    (secret_obs,) = sweep.by_type["AWS::SecretsManager::Secret"]
    writer.write([function_obs, replace(secret_obs, relationships=[])])
    (function,) = rows(session, LAMBDA).values()
    assert function.id is not None
    function_id = function.id
    session.commit()

    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TEMP TABLE perf_base ON COMMIT DROP AS
            SELECT id, resource_type, resource_id, arn, account_id, aws_region
              FROM t_inv_aws_resource
        """)
        connection.exec_driver_sql("""
            CREATE TEMP TABLE t_inv_aws_resource ON COMMIT DROP AS
            SELECT * FROM perf_base WITH NO DATA
        """)
        connection.exec_driver_sql("""
            INSERT INTO t_inv_aws_resource
            SELECT series * 2 + base.id, base.resource_type,
                   base.resource_id || '-' || series::text,
                   base.arn || '-' || series::text,
                   (base.account_id::bigint + mod(series, 1000))::text::char(12),
                   base.aws_region
              FROM perf_base base CROSS JOIN generate_series(1, 100000) series
        """)
        connection.exec_driver_sql("CREATE UNIQUE INDEX ON t_inv_aws_resource(id)")
        connection.exec_driver_sql("""
            CREATE UNIQUE INDEX ON t_inv_aws_resource
                (account_id, aws_region, resource_type, resource_id)
        """)
        connection.exec_driver_sql("CREATE INDEX ON t_inv_aws_resource(arn)")
        connection.exec_driver_sql("""
            CREATE TEMP TABLE t_inv_aws_resource_relation ON COMMIT DROP AS
            SELECT from_resource_id, to_resource_type, to_resource_field,
                   to_resource_key, to_resource_id
              FROM t_inv_aws_resource_relation WITH NO DATA
        """)
        connection.execute(
            text("""
                INSERT INTO t_inv_aws_resource_relation
                SELECT source.id, target.resource_type, 'arn', target.arn, target.id
                  FROM t_inv_aws_resource target
                  JOIN t_inv_aws_resource source ON source.id = target.id + 1
                 WHERE target.resource_type = :resource_type
            """),
            {"resource_type": LAMBDA},
        )
        connection.exec_driver_sql("""
            CREATE INDEX ON t_inv_aws_resource_relation
                (from_resource_id, to_resource_type, to_resource_key, to_resource_field)
        """)
        connection.exec_driver_sql("""
            CREATE INDEX ON t_inv_aws_resource_relation(to_resource_id)
        """)
        connection.exec_driver_sql("""
            CREATE INDEX ON t_inv_aws_resource_relation
                (to_resource_type, to_resource_field, to_resource_key)
        """)
        connection.exec_driver_sql("ANALYZE t_inv_aws_resource")
        connection.exec_driver_sql("ANALYZE t_inv_aws_resource_relation")
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM t_inv_aws_resource_relation"
            ).scalar_one()
            == 100000
        )
        batches = [
            [function_id + 2],
            [function_id + 2 * series for series in range(1, 100001, 1000)],
            [function_id + 2 * series for series in range(1, 501)],
        ]
        for ids in batches:
            previous = (
                connection.execute(
                    text("SELECT id, arn FROM t_inv_aws_resource WHERE id = ANY(:ids)"),
                    {"ids": ids},
                ).all()
                if len(ids) == 500
                else []
            )
            parameters = {
                "ids": ids,
                "previous_ids": [row[0] for row in previous],
                "previous_arns": [row[1] for row in previous],
            }
            plan = connection.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(INCOMING_RELATION_SOURCES)),
                parameters,
            ).scalar_one()[0]
            (tmp_path / f"query-plan-{len(ids)}.json").write_text(json.dumps(plan, indent=2))
            nodes = [plan["Plan"]]
            scans = []
            while nodes:
                node = nodes.pop()
                nodes.extend(node.get("Plans", []))
                if node.get("Relation Name") in (
                    "t_inv_aws_resource",
                    "t_inv_aws_resource_relation",
                ):
                    scans.append(node)
            assert scans
            assert all(
                "Index" in node["Node Type"] or "Bitmap" in node["Node Type"] for node in scans
            )
            sources = connection.execute(INCOMING_RELATION_SOURCES, parameters).scalars().all()
            assert set(sources) == {target_id + 1 for target_id in ids}


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


def test_config_relationships_without_target_keys_are_kept_but_not_indexed(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::VPC", "AWS::EC2::Subnet"])
    (subnet_obs,) = sweep.by_type["AWS::EC2::Subnet"]
    vpc_edge = next(
        relationship
        for relationship in subnet_obs.relationships or []
        if relationship["resourceType"] == "AWS::EC2::VPC"
    )
    missing_id = {key: value for key, value in vpc_edge.items() if key != "resourceId"}
    missing_type = {key: value for key, value in vpc_edge.items() if key != "resourceType"}
    relationships = [vpc_edge, missing_id, missing_type]

    writer.write(
        [
            *sweep.by_type["AWS::EC2::VPC"],
            replace(subnet_obs, relationships=relationships),
        ]
    )

    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    assert missing_id in subnet.relationships
    assert missing_type in subnet.relationships
    assert (
        next(
            relationship
            for relationship in subnet.relationships
            if relationship.get("resourceId") == vpc_edge["resourceId"]
            and relationship.get("resourceType") == "AWS::EC2::VPC"
        )["relationshipName"]
        == "VPC"
    )
    indexed = session.exec(
        select(ResourceRelation).where(ResourceRelation.from_resource_id == subnet.id)
    ).all()
    assert all(relation.to_resource_key for relation in indexed)
    assert len(indexed) == 1


def test_a_missing_arn_does_not_erase_a_stored_arn_or_unresolve_incoming_edges(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    (function_obs,) = sweep.by_type[LAMBDA]
    (secret_obs,) = sweep.by_type["AWS::SecretsManager::Secret"]
    writer.write([function_obs, replace(secret_obs, relationships=[])])
    (function,) = rows(session, LAMBDA).values()
    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()
    arn = function.arn
    relation_query = select(ResourceRelation).where(ResourceRelation.from_resource_id == secret.id)
    assert session.exec(relation_query).one().to_resource_id == function.id

    counts = writer.write(
        [
            replace(
                function_obs,
                item=function_obs.item.model_copy(update={"arn": None}),
                observed_at=function_obs.observed_at + minutes(1),
            )
        ]
    )

    assert counts.unchanged == 1
    session.expire_all()
    assert session.get_one(Resource, function.id).arn == arn
    assert session.exec(relation_query).one().to_resource_id == function.id

    retagged = function_obs.item.model_copy(
        update={
            "arn": None,
            "tags": [ConfigTag(key="Owner", value="TeamB")],
        }
    )
    counts = writer.write(
        [
            replace(
                function_obs,
                item=retagged,
                observed_at=function_obs.observed_at + minutes(2),
            )
        ]
    )

    assert counts.updated == 1
    session.expire_all()
    assert session.get_one(Resource, function.id).arn == arn
    assert session.exec(relation_query).one().to_resource_id == function.id


def test_distinct_config_names_to_the_same_target_are_preserved(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::EC2::VPC", "AWS::EC2::Subnet"])
    (subnet_obs,) = sweep.by_type["AWS::EC2::Subnet"]
    (vpc_obs,) = sweep.by_type["AWS::EC2::VPC"]
    vpc_edge = next(
        relationship
        for relationship in subnet_obs.relationships or []
        if relationship["resourceType"] == "AWS::EC2::VPC"
    )
    relationships = [
        {**vpc_edge, "relationshipName": "PrimaryVpc"},
        {**vpc_edge, "relationshipName": "SecondaryVpc"},
    ]
    writer.write([vpc_obs, replace(subnet_obs, relationships=relationships)])

    (subnet,) = rows(session, "AWS::EC2::Subnet").values()
    assert {relationship["relationshipName"] for relationship in subnet.relationships} == {
        "PrimaryVpc",
        "SecondaryVpc",
    }
    relations = session.exec(
        select(ResourceRelation).where(
            ResourceRelation.from_resource_id == subnet.id,
            ResourceRelation.to_resource_type == "AWS::EC2::VPC",
        )
    ).all()
    assert {relation.relation_name for relation in relations} == {"PrimaryVpc", "SecondaryVpc"}


def test_a_deleted_target_does_not_resolve_a_relationship(
    writer: ResourceWriter, disc: Discovery, session: Session
) -> None:
    sweep = disc.sweep_config(["AWS::Lambda::Function", "AWS::SecretsManager::Secret"])
    (function_obs,) = sweep.by_type[LAMBDA]
    (secret_obs,) = sweep.by_type["AWS::SecretsManager::Secret"]
    writer.write([function_obs])
    (function,) = rows(session, LAMBDA).values()
    function.status = "deleted"
    session.add(function)
    session.commit()

    writer.write([replace(secret_obs, relationships=[])])

    (secret,) = rows(session, "AWS::SecretsManager::Secret").values()
    relation = session.exec(
        select(ResourceRelation).where(ResourceRelation.from_resource_id == secret.id)
    ).one()
    assert relation.to_resource_id is None


def test_post_commit_reconciliation_rechecks_the_task_claim(
    writer: ResourceWriter,
    disc: Discovery,
    session: Session,
    account_id: str,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = requests.create_sweep_request(
        session, [], trigger="api", priority=0, scope={"all": False}
    )
    assert request.id is not None
    (task,) = requests.add_tasks(
        session,
        request.id,
        [requests.NewTask("shard", account_id, REGION, "AWS::EC2::Subnet", source="config")],
    )
    request.status, request.started_at, request.finished_at = "queued", None, None
    session.add(request)
    session.commit()
    claimed = queue.claim_task(session, "worker-old", per_account_concurrency=1000)
    assert claimed is not None and claimed.id == task.id and claimed.claim_token
    assert (
        leases.try_acquire(
            session,
            claimed,
            leases.Scope(account_id, REGION, "AWS::EC2::Subnet"),
        )
        is leases.LeaseState.ACQUIRED
    )

    resolve = writer._resolve_dangling_relations

    def lose_claim(*args: Any, **kwargs: Any) -> None:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE t_inv_aws_scan_task SET claim_token = 'replacement' WHERE id = :id"),
                {"id": claimed.id},
            )
            connection.execute(
                text(
                    "UPDATE t_inv_aws_scan_shard SET lease_token = 'replacement'"
                    " WHERE account_id = :account AND region = :region"
                    " AND resource_type = 'AWS::EC2::Subnet'"
                ),
                {"account": account_id, "region": REGION},
            )
        resolve(*args, **kwargs)

    monkeypatch.setattr(writer, "_resolve_dangling_relations", lose_claim)
    with pytest.raises(TaskLost):
        writer.write(
            disc.sweep_config(["AWS::EC2::Subnet"]).by_type["AWS::EC2::Subnet"], task=claimed
        )

    assert rows(session, "AWS::EC2::Subnet")


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
