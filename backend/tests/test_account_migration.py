"""Migration backfill for accounts that predate local scan-policy ownership."""

import json
import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from tests.conftest import BACKEND_DIR, TEST_DATABASE_URL, clear_caches, libpq_url


def test_existing_accounts_become_legacy_scan_enabled_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = f"test_account_migration_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(libpq_url(TEST_DATABASE_URL), autocommit=True)
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    clear_caches()
    try:
        command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "0007")
        account = json.loads(
            (BACKEND_DIR / "tests" / "fixtures" / "registry" / "accounts.json").read_text()
        )[0]
        fetched_at = "2026-11-08T12:00:00Z"
        with psycopg.connect(
            libpq_url(TEST_DATABASE_URL),
            options=f"-csearch_path={schema}",
            autocommit=True,
        ) as connection:
            connection.execute(
                """
                INSERT INTO t_inv_aws_account (
                    account_id, account_name, account_type, eim_id, eim_app, ba_id,
                    active, registry_fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, true, %s)
                """,
                (
                    account["account_id"],
                    account["account_name"],
                    account["account_type"],
                    account["eim_id"],
                    account["eim_app"],
                    account["ba_id"],
                    fetched_at,
                ),
            )

        command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")

        with psycopg.connect(
            libpq_url(TEST_DATABASE_URL),
            options=f"-csearch_path={schema}",
        ) as connection:
            row = connection.execute(
                """
                SELECT source, scan_enabled, scan_region_mode, updated_at,
                       registry_fetched_at IS NULL
                  FROM t_inv_aws_account
                 WHERE account_id = %s
                """,
                (account["account_id"],),
            ).fetchone()
            registry_nullable = connection.execute(
                """
                SELECT is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 't_inv_aws_account'
                   AND column_name = 'registry_fetched_at'
                """
            ).fetchone()
            selected_table = connection.execute(
                "SELECT to_regclass('t_inv_aws_account_scan_region')"
            ).fetchone()

        assert row is not None
        source, enabled, mode, updated_at, registry_value_is_null = row
        assert (source, enabled, mode) == ("legacy", True, "all")
        assert updated_at.isoformat() == "2026-11-08T12:00:00+00:00"
        assert registry_value_is_null is False
        assert registry_nullable == ("YES",)
        assert selected_table == ("t_inv_aws_account_scan_region",)
    finally:
        clear_caches()
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()
