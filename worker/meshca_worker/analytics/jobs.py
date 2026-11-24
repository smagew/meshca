"""Scheduled calculations: read from the decoded layer, compute, write results. Each job is one transaction."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from . import resolve

log = logging.getLogger("meshca.analytics")

CONFIDENT = 0.7      # the same threshold the view v_hop_resolved uses


def resolve_paths(conn: psycopg.Connection, window_days: int) -> dict:
    """Stage 4: resolve the prefixes of every distinct flood path seen within the window."""
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    with conn.transaction(), conn.cursor() as cur:
        # Only repeaters and room servers relay packets.
        nodes = [resolve.Node(pubkey, lat, lon, last_seen >= since) for pubkey, lat, lon, last_seen in cur.execute(
            "SELECT pubkey, lat, lon, last_seen FROM node WHERE role IN (2, 3)").fetchall()]
        rows = cur.execute(
            "SELECT path, count(DISTINCT packet_hash) FROM reception "
            "WHERE route_type IN (0, 1) AND hop_count > 0 AND received_at >= %s GROUP BY path", (since,)).fetchall()
        paths = {tuple(path): float(count) for path, count in rows}
        if not paths:
            return {}

        result = resolve.Resolver(nodes).resolve(paths)
        evaluation = resolve.evaluate(nodes, paths).as_dict()

        records, hops_total, hops_named = [], 0.0, 0.0
        for path, hops in result.hops.items():
            key = ",".join(path)
            for position, candidates in enumerate(hops, start=1):
                hops_total += paths[path]
                best, probability = candidates[0]
                if probability >= CONFIDENT and not resolve.is_unknown(best):
                    hops_named += paths[path]
                for rank, (node_id, p) in enumerate(candidates[:5], start=1):
                    records.append((key, position, rank, path[position - 1],
                                    None if resolve.is_unknown(node_id) else node_id,
                                    node_id[1:] if resolve.is_unknown(node_id) else None, p, resolve.METHOD_VERSION, now))

        cur.execute("DELETE FROM path_resolution WHERE path_key = ANY(%s)", ([",".join(p) for p in paths],))
        cur.executemany(
            "INSERT INTO path_resolution (path_key, position, rank, prefix, pubkey, unknown_prefix, probability,"
            " method_version, computed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", records)
        summary = {"nodes": len(nodes), "paths": len(paths), "packets": int(sum(paths.values())),
                   "iterations": result.iterations, "seconds": round(time.monotonic() - started, 2),
                   "resolved_share": round(hops_named / hops_total, 4) if hops_total else None}
        cur.execute(
            "INSERT INTO resolution_run (computed_at, method_version, window_days, nodes, paths, packets, iterations,"
            " seconds, resolved_share, evaluation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (now, resolve.METHOD_VERSION, window_days, summary["nodes"], summary["paths"], summary["packets"],
             summary["iterations"], summary["seconds"], summary["resolved_share"], Jsonb(evaluation)))
        # Runs are history for the accuracy chart; keep it bounded.
        cur.execute("DELETE FROM resolution_run WHERE computed_at < %s", (now - timedelta(days=400),))
    log.info("path resolution: %s; evaluation %s", summary, evaluation)
    return summary
