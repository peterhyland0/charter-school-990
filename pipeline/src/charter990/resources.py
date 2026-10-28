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
        live: bool = False,
    ) -> None:
        """Empty a derived table and reload it with COPY, inside the caller's transaction.

        live: the website reads this table, so DELETE instead of TRUNCATE. Readers
        then keep seeing the old rows until commit instead of waiting on a lock.
        """
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table}" if live else f"TRUNCATE {table}")
            with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)

    def sync_table(
        self,
        conn: psycopg.Connection,
        table: str,
        key: str,
        columns: Sequence[str],
        rows: Iterable[Sequence],
        dependents: Sequence[str] = (),
    ) -> int:
        """Bring a table the website reads in line with rows, without emptying it.

        Inside the caller's transaction: rows whose key is gone are deleted, along
        with the rows in dependents that reference them; the rest are upserted,
        and unchanged rows aren't rewritten. Unlike TRUNCATE ... CASCADE, a later
        failure in the pipeline can't leave the dependents empty. Returns the
        number of rows removed.
        """
        cols = ", ".join(columns)
        tmp = f"_new_{table}"
        changed = " OR ".join(f"{table}.{c} IS DISTINCT FROM EXCLUDED.{c}" for c in columns if c != key)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != key)
        gone = f"SELECT {key} FROM {table} EXCEPT SELECT {key} FROM {tmp}"
        with conn.cursor() as cur:
            cur.execute(f"CREATE TEMP TABLE {tmp} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
            with cur.copy(f"COPY {tmp} ({cols}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
            for dependent in dependents:
                cur.execute(f"DELETE FROM {dependent} WHERE {key} IN ({gone})")
            removed = cur.execute(f"DELETE FROM {table} WHERE {key} IN ({gone})").rowcount
            cur.execute(
                f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {tmp} "
                f"ON CONFLICT ({key}) DO UPDATE SET {updates} WHERE {changed}"
            )
        return removed
