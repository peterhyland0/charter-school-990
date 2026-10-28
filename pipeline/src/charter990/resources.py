"""Postgres connection for the assets."""

import psycopg
from dagster import ConfigurableResource


class PostgresResource(ConfigurableResource):
    dsn: str

    def connect(self) -> psycopg.Connection:
        # Used as a context manager: commits on success, rolls back on error.
        return psycopg.connect(self.dsn)

    def log_error(self, conn: psycopg.Connection, stage: str, key: str, error: str) -> None:
        conn.execute(
            "INSERT INTO load_errors (stage, key, error) VALUES (%s, %s, %s)",
            (stage, key, error[:2000]),
        )
