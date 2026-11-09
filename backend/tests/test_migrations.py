import psycopg

TABLES = {
    "t_inv_aws_account",
    "t_inv_aws_account_region",
    "t_inv_aws_resource",
    "t_inv_aws_resource_relation",
    "t_inv_aws_scan_request",
    "t_inv_aws_scan_task",
    "t_inv_aws_scan_shard",
}


def index_definitions(db: psycopg.Connection, table: str) -> dict[str, str]:
    rows = db.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() "
        "AND tablename = %s",
        (table,),
    ).fetchall()
    return dict(rows)


def test_migration_creates_the_seven_tables_and_version_table(
    db: psycopg.Connection,
) -> None:
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()

    assert {name for (name,) in rows} == TABLES | {"alembic_version"}


def test_resource_identity_is_unique_but_arn_is_not(db: psycopg.Connection) -> None:
    indexes = index_definitions(db, "t_inv_aws_resource")

    identity = "(account_id, aws_region, resource_type, resource_id)"
    unique = [d for d in indexes.values() if "UNIQUE" in d and identity in d]
    assert len(unique) == 1
    assert "UNIQUE" not in indexes["ix_t_inv_aws_resource_arn"]


def test_resource_search_indexes(db: psycopg.Connection) -> None:
    indexes = index_definitions(db, "t_inv_aws_resource")

    assert "USING gin (tags jsonb_path_ops)" in indexes["ix_t_inv_aws_resource_tags"]
    for column, name in [
        ("resource_name", "name"),
        ("arn", "arn"),
        ("resource_id", "resource_id"),
    ]:
        assert (
            f"USING gin ({column} public.gin_trgm_ops)"
            in indexes[f"ix_t_inv_aws_resource_{name}_trgm"]
        )
    assert (
        "WHERE ((status)::text <> 'deleted'::text)" in indexes["ix_t_inv_aws_resource_scope_live"]
    )


def test_scan_task_queue_and_in_flight_resource_indexes(db: psycopg.Connection) -> None:
    indexes = index_definitions(db, "t_inv_aws_scan_task")

    assert "WHERE ((status)::text = 'queued'::text)" in indexes["ix_t_inv_aws_scan_task_queue"]
    inflight = indexes["uq_t_inv_aws_scan_task_resource_inflight"]
    assert "UNIQUE" in inflight
    assert "(account_id, region, resource_type, resource_id)" in inflight
    assert "(kind)::text = 'resource'::text" in inflight
    assert "'queued'::character varying" in inflight


def test_resource_has_absent_in_scan_at(db: psycopg.Connection) -> None:
    row = db.execute(
        "SELECT data_type FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = 't_inv_aws_resource' AND column_name = 'absent_in_scan_at'"
    ).fetchone()

    assert row == ("timestamp with time zone",)


def test_claim_query_has_a_partial_index_for_the_per_account_running_count(
    db: psycopg.Connection,
) -> None:
    running = index_definitions(db, "t_inv_aws_scan_task")["ix_t_inv_aws_scan_task_running_account"]

    assert "(account_id)" in running
    assert "WHERE ((status)::text = 'running'::text)" in running


def test_claim_and_lease_tokens_fence_task_and_shard_ownership(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND column_name IN ('claim_token', 'lease_token')"
    ).fetchall()

    assert set(rows) == {
        ("t_inv_aws_scan_task", "claim_token"),
        ("t_inv_aws_scan_shard", "lease_token"),
    }
