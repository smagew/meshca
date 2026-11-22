"""Database access of the collector: connection settings from the standard PG* environment variables."""
from __future__ import annotations

import logging
import time

import psycopg

from .core import Row

log = logging.getLogger("meshca.db")

INSERT_RECEPTION = (
    "INSERT INTO raw_reception (ingested_at, topic, payload, payload_text) VALUES (%s, %s, %s::jsonb, %s)"
)
INSERT_MESSAGE = (
    "INSERT INTO observer_message (ingested_at, topic, kind, retained, payload, payload_text) "
    "VALUES (%s, %s, %s, %s, %s::jsonb, %s)"
)


def connect(retries: int = 0, delay_s: float = 2.0) -> psycopg.Connection:
    """Connect using PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD. retries=0 means one attempt."""
    attempt = 0
    while True:
        try:
            return psycopg.connect(connect_timeout=10, application_name="meshca-collector")
        except psycopg.OperationalError as e:
            attempt += 1
            if attempt > retries:
                raise
            log.warning("database not reachable (attempt %d/%d): %s", attempt, retries, str(e).strip())
            time.sleep(delay_s)


def insert_rows(conn: psycopg.Connection, rows: list[Row]) -> None:
    """All rows in one transaction: either the whole batch is stored or none of it."""
    receptions = [(r.ingested_at, r.topic, r.payload_json, r.payload_text) for r in rows if r.table == "raw_reception"]
    messages = [(r.ingested_at, r.topic, r.kind, r.retained, r.payload_json, r.payload_text)
                for r in rows if r.table == "observer_message"]
    with conn.transaction():
        with conn.cursor() as cur:
            if receptions:
                cur.executemany(INSERT_RECEPTION, receptions)
            if messages:
                cur.executemany(INSERT_MESSAGE, messages)
