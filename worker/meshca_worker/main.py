"""meshca worker: turns raw_reception rows into the decoded layer and watches that collection is alive.

It reads the raw records in id order after a stored position, so it can be stopped at any time: the position
and the decoded rows of a batch are written in one transaction.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import timedelta
from pathlib import Path

import psycopg

from . import DECODER_VERSION, monitor, store
from .analytics import jobs
from .healthcheck import HEARTBEAT_FILE

log = logging.getLogger("meshca.worker")

BATCH = 500
IDLE_WAIT_S = 5.0
MONITOR_EVERY_S = 60.0
RETENTION_EVERY_S = 3600.0
DB_RETRY_MAX_S = 30.0


def connect() -> psycopg.Connection:
    return psycopg.connect(connect_timeout=10, application_name="meshca-worker")


def decode_batch(conn: psycopg.Connection, keys, text_retention: timedelta) -> int:
    """Decode the next batch of raw records. Returns how many were read."""
    with conn.transaction(), conn.cursor() as cur:
        last_id = store.get_state(cur, "last_raw_id", 0)
        rows = cur.execute("SELECT id, ingested_at, topic, payload FROM raw_reception WHERE id > %s ORDER BY id LIMIT %s",
                           (last_id, BATCH)).fetchall()
        failed = 0
        for raw_id, ingested_at, topic, payload in rows:
            try:
                with conn.transaction():            # savepoint: one bad record must not stop the rest
                    result = store.store_raw(cur, raw_id, ingested_at, topic, payload, keys, text_retention)
            except (psycopg.OperationalError, psycopg.InterfaceError):
                raise                               # the connection is gone: retry the whole batch later
            except Exception as e:                  # bad data or a decoder bug: record it and go on
                log.exception("raw record %d could not be stored", raw_id)
                store.record_error(cur, raw_id, f"exception_{type(e).__name__}")
                keys.reload(store.load_channels(cur, []))
                result = "exception"
            if result not in ("ok", "duplicate"):
                failed += 1
        if rows:
            store.set_state(cur, "last_raw_id", rows[-1][0])
            if failed:
                log.warning("%d of %d raw record(s) could not be decoded, see decode_error", failed, len(rows))
    return len(rows)


def analytics_loop(stop: threading.Event, every: timedelta, window_days: int) -> None:
    """Calculations run beside decoding, on their own connection: a slow or failed run never delays the packets."""
    stop.wait(120)                                      # let decoding catch up after a start
    while not stop.is_set():
        try:
            with connect() as conn:
                jobs.resolve_paths(conn, window_days)
        except Exception:
            log.exception("analytics run failed")
        stop.wait(every.total_seconds())


def run() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    text_retention = timedelta(days=float(os.environ.get("TEXT_RETENTION_DAYS", "30")))
    status_gap = timedelta(minutes=float(os.environ.get("ALERT_STATUS_GAP_MIN", "15")))
    packet_gap = timedelta(minutes=float(os.environ.get("ALERT_PACKET_GAP_MIN", "30")))
    targets = monitor.targets_from_env(os.environ)
    configured = [c for c in os.environ.get("CHANNELS", "").split(",") if c.strip()]

    threading.Thread(
        target=analytics_loop, name="analytics", daemon=True,
        args=(stop, timedelta(minutes=float(os.environ.get("RESOLVE_INTERVAL_MIN", "60"))),
              int(os.environ.get("RESOLVE_WINDOW_DAYS", "30")))).start()

    heartbeat = Path(HEARTBEAT_FILE)
    conn: psycopg.Connection | None = None
    keys = None
    delay = 1.0
    last_monitor = last_retention = 0.0
    decoded_total = 0

    while not stop.is_set():
        try:
            if conn is None or conn.closed:
                conn = connect()
                with conn.transaction(), conn.cursor() as cur:
                    if store.reset_if_decoder_changed(cur):
                        log.warning("decoder version is now %d: rebuilding the decoded layer", DECODER_VERSION)
                    keys = store.load_channels(cur, configured, text_retention)
                log.info("database connected, decoder version %d, %d channel key(s), alerts %s",
                         DECODER_VERSION, len(keys.names), f"to {len(targets)} target(s)" if targets else "to log only")

            count = decode_batch(conn, keys, text_retention)
            decoded_total += count
            if count:
                log.info("decoded %d raw record(s), %d since start", count, decoded_total)

            if time.monotonic() - last_monitor >= MONITOR_EVERY_S:
                last_monitor = time.monotonic()
                try:
                    monitor.run(conn, status_gap, packet_gap, targets)
                except psycopg.Error:
                    raise
                except Exception:                   # alerting must never stop decoding
                    log.exception("monitor pass failed")
            if time.monotonic() - last_retention >= RETENTION_EVERY_S:
                last_retention = time.monotonic()
                with conn.transaction(), conn.cursor() as cur:
                    removed = store.remove_old_texts(cur, text_retention)
                if removed:
                    log.info("text retention: removed the text of %d channel message(s)", removed)

            heartbeat.touch()
            delay = 1.0
            if count < BATCH:
                stop.wait(IDLE_WAIT_S)
        except psycopg.Error as e:
            log.warning("database error, retrying in %.0f s: %s", delay,
                        str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__)
            try:
                if conn is not None:
                    conn.close()
            except psycopg.Error:
                pass
            conn = None
            stop.wait(delay)
            delay = min(delay * 2, DB_RETRY_MAX_S)

    if conn is not None and not conn.closed:
        conn.close()
    log.info("stopped, %d raw record(s) decoded", decoded_total)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()


if __name__ == "__main__":
    main()
