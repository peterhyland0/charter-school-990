"""Postgres connection for the assets."""

from collections.abc import Iterable, Sequence

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

    def replace_table(
        self,
        conn: psycopg.Connection,
        table: str,
        columns: Sequence[str],
        rows: Iterable[Sequence],
        cascade: bool = False,
    ) -> None:
        """Empty a derived table and reload it with COPY, inside the caller's transaction."""
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {table}{' CASCADE' if cascade else ''}")
            with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
