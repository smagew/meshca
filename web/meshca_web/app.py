"""The map page: static files plus a small read-only JSON API over the meshca database.

Runs as the read-only database role; every query has a statement timeout set on that role. Meant for the local
network only, like the dashboards: there is no login.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg

from . import graph

log = logging.getLogger("meshca.web")
STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_DAYS = 30


def connect() -> psycopg.Connection:
    # Connection settings come from the standard PG* environment variables.
    return psycopg.connect(connect_timeout=5, application_name="meshca-web", autocommit=True)


def load_graph(conn: psycopg.Connection, days: int) -> dict:
    edges = [graph.Edge(*row) for row in conn.execute(
        "SELECT from_node, to_node, sum(packets)::int, sum(adverts)::int, sum(channel)::int, sum(direct)::int "
        "FROM edge_daily WHERE day > current_date - %s GROUP BY 1, 2", (days,)).fetchall()]
    ids = list({e.source for e in edges} | {e.target for e in edges})
    info = {pubkey: graph.NodeInfo(name, role, lat, lon) for pubkey, name, role, lat, lon in conn.execute(
        "SELECT pubkey, name, node_role_name(role), lat, lon FROM node WHERE pubkey = ANY(%s)", (ids,)).fetchall()}
    critical = {node: senders for node, senders in conn.execute(
        "SELECT node, senders FROM critical_node").fetchall()}
    result = graph.build(edges, info, critical)
    updated = conn.execute("SELECT max(computed_at) FROM critical_node").fetchone()[0]
    result.update(days=days, computed_at=updated)
    return result


def load_node(conn: psycopg.Connection, node_id: str) -> dict:
    row = conn.execute(
        "SELECT name, node_role_name(role), lat, lon, first_seen, last_seen, advert_count FROM node WHERE pubkey = %s",
        (node_id,)).fetchone()
    result: dict = {"id": node_id, "key": node_id if not node_id.startswith(("?", "@")) else None}
    if row:
        result.update(zip(("name", "role", "lat", "lon", "first_seen", "last_seen", "adverts"), row))
    result["rhythm"] = [dict(zip(("kind", "events", "median_gap_s", "period_s", "period_confirmed", "burstiness",
                                  "night_share", "judged"), r)) for r in conn.execute(
        "SELECT kind, events, median_gap_s, period_s, period_confirmed, burstiness, night_share, judged "
        "FROM node_rhythm WHERE subject = %s ORDER BY events DESC", (node_id,)).fetchall()]
    return result


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


class Handler(BaseHTTPRequestHandler):
    server_version = "meshca-web"

    def log_message(self, fmt: str, *args) -> None:       # access log at debug level only
        log.debug("%s " + fmt, self.address_string(), *args)

    def _send(self, status: int, body: bytes, kind: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value, status: int = 200) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False, default=_json_default).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/healthz":
                with connect() as conn:
                    conn.execute("SELECT 1")
                return self._send(200, b"ok", "text/plain")
            if url.path == "/api/graph":
                try:
                    days = min(MAX_DAYS, max(1, int(query.get("days", ["7"])[0])))
                except ValueError:
                    return self._json({"error": "days must be a number"}, 400)
                with connect() as conn:
                    return self._json(load_graph(conn, days))
            if url.path == "/api/node":
                node_id = query.get("id", [""])[0]
                if not node_id or len(node_id) > 80:
                    return self._json({"error": "id is required"}, 400)
                with connect() as conn:
                    return self._json(load_node(conn, node_id))
            return self._static(url.path)
        except psycopg.Error as e:
            log.warning("database error on %s: %s", url.path, type(e).__name__)
            return self._json({"error": "база данных недоступна"}, HTTPStatus.SERVICE_UNAVAILABLE)

    def _static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC / relative).resolve()
        if not target.is_relative_to(STATIC) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/javascript",):
            kind += "; charset=utf-8"
        cache = "no-cache" if target.suffix in (".html", ".js", ".css") else "max-age=86400"
        self._send(200, target.read_bytes(), kind, cache)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    port = int(os.environ.get("WEB_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("map page on port %d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
