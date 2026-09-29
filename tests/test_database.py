import os
import unittest
from unittest.mock import patch

from backend import database


class DatabaseTests(unittest.TestCase):
    def test_parameters_preserve_literals_and_casts(self):
        query, returns_id = database.translate("SELECT title::text FROM docs WHERE title LIKE :term AND note='why? 20%' AND id=?")
        self.assertEqual(query, "SELECT title::text FROM docs WHERE title ILIKE %(term)s AND note='why? 20%%' AND id=%s")
        self.assertFalse(returns_id)

    def test_insert_returns_generated_id_and_ignore_preserves_conflicts(self):
        query, returns_id = database.translate("INSERT OR IGNORE INTO users (name) VALUES (?)")
        self.assertEqual(query, "INSERT INTO users (name) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id")
        self.assertTrue(returns_id)

    def test_missing_database_never_creates_sqlite_and_other_schemas_are_denied(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(KeyError):
                database.connect_database("ignored.sqlite3", database.SCHEMA)
        with self.assertRaises(RuntimeError):
            database.connect_database("ignored.sqlite3", "identity")

    def test_existing_platform_owned_index_is_not_recreated(self):
        with patch("backend.database.psycopg2.connect") as connect:
            connect.return_value.info.dbname = "website_business"
            cursor = connect.return_value.cursor.return_value
            cursor.fetchone.return_value = ["existing_index"]
            with patch.dict(os.environ, {"DATABASE_URL": "test"}):
                with database.Connection() as connection:
                    connection.execute("CREATE INDEX IF NOT EXISTS existing_index ON users(name)")
            self.assertEqual(cursor.execute.call_count, 1)
            self.assertEqual(cursor.execute.call_args.args[0], "SELECT to_regclass(%s)")
