"""Apply db/migrations/*.sql in name order, each in its own transaction, once (table schema_migrations)."""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import psycopg
from psycopg import sql

log = logging.getLogger("meshca.migrate")


def connect(retries: int = 30, delay_s: float = 2.0) -> psycopg.Connection:
    """Connect using PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD, waiting for the database to come up."""
    for attempt in range(1, retries + 2):
        try:
            return psycopg.connect(connect_timeout=10, application_name="meshca-migrate")
        except psycopg.OperationalError as e:
            if attempt > retries:
                raise
            log.warning("database not reachable (attempt %d/%d): %s", attempt, retries, str(e).strip())
            time.sleep(delay_s)


def _ensure_reader(conn) -> None:
    """Login role for the dashboards: read-only, with the time zone the owner wants hours of the day shown in."""
    password = os.environ.get("READER_PASSWORD", "")
    if not password:
        return
    zone = os.environ.get("REPORT_TIMEZONE", "") or "UTC"
    with conn.transaction():
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'meshca_grafana'").fetchone()
        verb = sql.SQL("ALTER" if exists else "CREATE")
        conn.execute(sql.SQL("{} ROLE meshca_grafana LOGIN PASSWORD {}").format(verb, sql.Literal(password)))
        conn.execute("GRANT meshca_read TO meshca_grafana")
        conn.execute(sql.SQL("ALTER ROLE meshca_grafana SET timezone = {}").format(sql.Literal(zone)))
        conn.execute("ALTER ROLE meshca_grafana SET default_transaction_read_only = on")
        conn.execute("ALTER ROLE meshca_grafana SET statement_timeout = '30s'")
    log.info("read-only role meshca_grafana is ready, time zone %s", zone)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    directory = Path(os.environ.get("MIGRATIONS_DIR", "/app/migrations"))
    files = sorted(directory.glob("*.sql"))
    if not files:
        log.error("no migrations found in %s", directory)
        return 1

    conn = connect()
    try:
        with conn.transaction():
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
        applied = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        conn.commit()
        for path in files:
            if path.name in applied:
                continue
            log.info("applying %s", path.name)
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        log.info("schema is up to date (%d migration(s) known)", len(files))
        _ensure_reader(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
