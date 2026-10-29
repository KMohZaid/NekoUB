import unittest
from unittest.mock import AsyncMock, patch

from core.access.database import (
    AccessStore,
    database_name_from_url,
    normalize_async_url,
    quote_database_identifier,
)


class AccessDatabaseTests(unittest.TestCase):
    def test_normalizes_postgres_url_for_async_driver(self):
        self.assertEqual(
            normalize_async_url("postgresql://user:secret@host:5432/nekoub"),
            "postgresql+asyncpg://user:secret@host:5432/nekoub",
        )

    def test_preserves_explicit_async_driver(self):
        url = "postgresql+asyncpg://user:secret@host:5432/nekoub"
        self.assertEqual(normalize_async_url(url), url)

    def test_extracts_database_name_without_exposing_credentials(self):
        self.assertEqual(
            database_name_from_url("postgresql://user:secret@host:5432/nekoub"),
            "nekoub",
        )

    def test_quotes_database_identifier(self):
        self.assertEqual(quote_database_identifier('name"; DROP DATABASE x'), '"name""; DROP DATABASE x"')

    def test_duplicate_create_race_only_ignores_duplicate_database_error(self):
        store = AccessStore("postgresql://user:secret@host/nekoub")
        duplicate = Exception("database already exists")
        duplicate.sqlstate = "42P04"
        other = Exception("permission denied")
        other.sqlstate = "42501"
        self.assertTrue(store._is_duplicate_database_error(duplicate))
        self.assertFalse(store._is_duplicate_database_error(other))

    def test_initialize_rejects_missing_database_url_before_engine_creation(self):
        store = AccessStore("")
        with patch("core.access.database.create_async_engine") as create_engine:
            with self.assertRaisesRegex(ValueError, "DATABASE_URL must be configured"):
                import asyncio
                asyncio.run(store.initialize())
        create_engine.assert_not_called()

    def test_access_schema_has_pair_primary_key(self):
        from core.access.models import AllowedCommand, ShareUser, Sudoer

        self.assertEqual(Sudoer.__tablename__, "sudoers")
        self.assertEqual(ShareUser.__tablename__, "share_users")
        self.assertEqual(AllowedCommand.__tablename__, "allowed_cmds")
        self.assertEqual(
            {column.name for column in AllowedCommand.__table__.primary_key.columns},
            {"user_id", "command"},
        )


if __name__ == "__main__":
    unittest.main()
