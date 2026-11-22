"""meshca collector: subscribe to the observer's MQTT topics and store every message unchanged in PostgreSQL.

Deliberately simple: it does not parse packets. Messages wait in a bounded in-memory queue while the database is
away; the observer publishes with QoS 0, so what arrives while the collector itself is down is not recovered here.
"""
from __future__ import annotations

import logging
import os
import queue
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import psycopg

from . import db
from .core import Message, to_row
from .healthcheck import HEARTBEAT_FILE

log = logging.getLogger("meshca.collector")

BATCH_MAX = 200
BATCH_WAIT_S = 1.0
QUEUE_MAX = int(os.environ.get("QUEUE_MAX", "200000"))     # about six weeks of traffic at 4,700 receptions a day
STATS_EVERY_S = 300.0
DB_RETRY_MAX_S = 30.0


class Collector:
    def __init__(self) -> None:
        self.queue: queue.Queue[Message] = queue.Queue(maxsize=QUEUE_MAX)
        self.stop = threading.Event()
        self.mqtt_connected = threading.Event()
        self.received = self.stored = self.skipped = self.dropped = 0
        self.topics = [t.strip() for t in os.environ.get("MQTT_TOPICS", "meshcore/#").split(",") if t.strip()]

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="meshca-collector")
        self.client.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASSWORD"])
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # --- MQTT callbacks (network thread) ---

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("MQTT connection refused: %s", reason_code)
            return
        for topic in self.topics:
            client.subscribe(topic, qos=0)
        self.mqtt_connected.set()
        log.info("MQTT connected, subscribed to %s", ", ".join(self.topics))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.mqtt_connected.clear()
        if not self.stop.is_set():
            log.warning("MQTT disconnected (%s), reconnecting", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        self.received += 1
        message = Message(datetime.now(timezone.utc), msg.topic, bytes(msg.payload), bool(msg.retain))
        try:
            self.queue.put_nowait(message)
        except queue.Full:
            self.dropped += 1
            if self.dropped % 1000 == 1:
                log.error("queue is full (%d): dropping messages, %d dropped so far", QUEUE_MAX, self.dropped)

    # --- writer (main thread) ---

    def _next_batch(self) -> list[Message]:
        batch: list[Message] = []
        deadline = time.monotonic() + BATCH_WAIT_S
        while len(batch) < BATCH_MAX:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            try:
                batch.append(self.queue.get(timeout=timeout))
            except queue.Empty:
                break
        return batch

    def _store(self, conn: psycopg.Connection | None, batch: list[Message]) -> psycopg.Connection:
        """Store the batch, reconnecting for as long as it takes. Returns the live connection."""
        rows = [row for row in map(to_row, batch) if row is not None]
        self.skipped += len(batch) - len(rows)
        delay = 1.0
        while True:
            try:
                if conn is None or conn.closed:
                    conn = db.connect()
                if rows:
                    db.insert_rows(conn, rows)
                else:
                    conn.execute("SELECT 1")
                    conn.commit()
                self.stored += len(rows)
                return conn
            except psycopg.Error as e:
                log.warning("database write failed, %d message(s) waiting: %s", len(rows) + self.queue.qsize(),
                            str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__)
                try:
                    if conn is not None:
                        conn.close()
                except psycopg.Error:
                    pass
                conn = None
                if self.stop.wait(delay):
                    raise
                delay = min(delay * 2, DB_RETRY_MAX_S)

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())

        conn = db.connect(retries=30)
        log.info("database connected")
        self.client.connect_async(os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "1883")), keepalive=60)
        self.client.loop_start()

        heartbeat = Path(HEARTBEAT_FILE)
        last_stats = time.monotonic()
        try:
            while not self.stop.is_set():
                batch = self._next_batch()
                conn = self._store(conn, batch)           # an empty batch still checks the connection
                if self.mqtt_connected.is_set():
                    heartbeat.touch()
                if time.monotonic() - last_stats >= STATS_EVERY_S:
                    last_stats = time.monotonic()
                    log.info("received=%d stored=%d skipped_retained=%d dropped=%d queued=%d mqtt=%s",
                             self.received, self.stored, self.skipped, self.dropped, self.queue.qsize(),
                             "up" if self.mqtt_connected.is_set() else "down")
        except psycopg.Error:
            log.error("stopping with the database unreachable: %d message(s) lost", self.queue.qsize())
        finally:
            self.client.loop_stop()
            self.client.disconnect()
            # Whatever is still queued goes out before exit when the database is there.
            try:
                while conn is not None and not conn.closed and not self.queue.empty():
                    rows = [r for r in map(to_row, self._next_batch()) if r is not None]
                    if rows:
                        db.insert_rows(conn, rows)
                        self.stored += len(rows)
            except psycopg.Error as e:
                log.error("final flush failed: %s", e)
            if conn is not None:
                conn.close()
            log.info("stopped: received=%d stored=%d skipped_retained=%d dropped=%d",
                     self.received, self.stored, self.skipped, self.dropped)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    Collector().run()


if __name__ == "__main__":
    main()
