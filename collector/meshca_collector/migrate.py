"""Apply db/migrations/*.sql in name order, each in its own transaction, once (table schema_migrations)."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from psycopg import sql

from . import db

log = logging.getLogger("meshca.migrate")


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

    conn = db.connect(retries=30)
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
