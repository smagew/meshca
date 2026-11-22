"""Apply db/migrations/*.sql in name order, each in its own transaction, once (table schema_migrations)."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from . import db

log = logging.getLogger("meshca.migrate")


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
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
