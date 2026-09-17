"""Integration tests for the isolated, disposable Phase-0B PostgreSQL spike."""

import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, TimeoutError


DATABASE_URL = os.environ["DATABASE_URL"]


class PostgreSQLSpikeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            DATABASE_URL,
            pool_size=2,
            max_overflow=0,
            pool_timeout=0.2,
            pool_pre_ping=True,
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_health_connection(self) -> None:
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text("SELECT 1")), 1)

    def test_alembic_upgrade_is_repeatable(self) -> None:
        config = Config("tests/db_tests/alembic.ini")
        config.set_main_option("sqlalchemy.url", DATABASE_URL)
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT version_num FROM alembic_version")),
                "0001_spike_probe",
            )
            self.assertEqual(
                connection.scalar(text("SELECT count(*) FROM db_spike_probe")), 0,
            )

    def test_transaction_rolls_back_ddl_and_row(self) -> None:
        with self.engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("CREATE TABLE rollback_probe (id integer)"))
            connection.execute(text("INSERT INTO rollback_probe VALUES (1)"))
            transaction.rollback()
        with self.engine.connect() as connection:
            self.assertFalse(
                connection.scalar(
                    text("SELECT to_regclass('public.rollback_probe') IS NOT NULL")
                )
            )

    def test_pool_limit_is_enforced(self) -> None:
        first = self.engine.connect()
        second = self.engine.connect()
        try:
            with self.assertRaises(TimeoutError):
                self.engine.connect()
        finally:
            second.close()
            first.close()

    def test_advisory_lock_serializes_connections(self) -> None:
        lock_key = 914_261
        with self.engine.connect() as first, self.engine.connect() as second:
            self.assertTrue(first.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}))
            self.assertFalse(second.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}))
            self.assertTrue(first.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key}))
            self.assertTrue(second.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}))
            self.assertTrue(second.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key}))

    def test_german_full_text_search(self) -> None:
        with self.engine.connect() as connection:
            matched = connection.scalar(
                text(
                    "SELECT to_tsvector('german', :journal) "
                    "@@ plainto_tsquery('german', :query)"
                ),
                {"journal": "Heute schreibe ich ueber schoene Erinnerungen aus dem Garten.", "query": "Erinnerung"},
            )
        self.assertTrue(matched)

    def test_lost_connection_can_reconnect(self) -> None:
        with self.engine.connect() as victim, self.engine.connect() as control:
            backend_pid = victim.scalar(text("SELECT pg_backend_pid()"))
            self.assertTrue(control.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend_pid}))
            with self.assertRaises(OperationalError):
                victim.execute(text("SELECT 1"))
        self.engine.dispose()
        with self.engine.connect() as replacement:
            self.assertEqual(replacement.scalar(text("SELECT 1")), 1)


if __name__ == "__main__":
    unittest.main()
