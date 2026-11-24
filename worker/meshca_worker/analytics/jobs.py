"""Scheduled calculations: read from the decoded layer, compute, write results. Each job is one transaction."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from . import origin, resolve, topology

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


FEATURES_SQL = """
    WITH feature AS (
        SELECT packet_hash, position || ':' || label AS f FROM v_hop_resolved
        WHERE position <= 2 AND received_at >= %(since)s
        UNION
        SELECT packet_hash, '1:@direct' FROM reception
        WHERE route_type IN (0, 1) AND hop_count = 0 AND received_at >= %(since)s
    ), per_packet AS (
        SELECT packet_hash, array_agg(DISTINCT f) AS features FROM feature GROUP BY packet_hash
    )
"""


def resolve_origins(conn: psycopg.Connection, window_days: int) -> dict:
    """Stage 4: name the sender of packets that carry one byte of its key, by the path fingerprint.
    Runs after resolve_paths: the fingerprint is made of resolved first hops."""
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    args = {"since": now - timedelta(days=window_days)}
    with conn.transaction(), conn.cursor() as cur:
        nodes = {pubkey: last_seen >= args["since"] for pubkey, last_seen in cur.execute(
            "SELECT pubkey, last_seen FROM node").fetchall()}
        known = [(pubkey, frozenset(features)) for pubkey, features in cur.execute(
            FEATURES_SQL + "SELECT s.pubkey, p.features FROM per_packet p JOIN ("
            " SELECT packet_hash, pubkey FROM advert WHERE signature_ok"
            " UNION ALL SELECT packet_hash, sender_pubkey FROM anon_request) s USING (packet_hash)", args).fetchall()]
        # A key seen in an anonymous request is a real node even when its advert never reached us.
        for pubkey, _ in known:
            nodes.setdefault(pubkey, True)
        targets = cur.execute(
            FEATURES_SQL + "SELECT d.packet_hash, d.src_hash, p.features FROM per_packet p "
            "JOIN direct_packet d USING (packet_hash)", args).fetchall()
        if not targets:
            return {}

        params = origin.Params(unknown_weight=origin.unknown_weight_from_data(
            {pubkey[:2] for pubkey in nodes}, {src_hash for _, src_hash, _ in targets}))
        resolver = origin.OriginResolver(nodes, known, params)
        evaluation = origin.evaluate(nodes, known, params).as_dict()
        records, named = [], 0
        for packet_hash, src_hash, features in targets:
            ranked = resolver.resolve(src_hash, frozenset(features))
            named += ranked[0][1] >= CONFIDENT and not origin.is_unknown(ranked[0][0])
            for rank, (node_id, p) in enumerate(ranked[:3], start=1):
                records.append((packet_hash, rank, None if origin.is_unknown(node_id) else node_id, p,
                                "path_fingerprint", origin.METHOD_VERSION, now))

        cur.execute("DELETE FROM origin_resolution WHERE packet_hash = ANY(%s)", ([t[0] for t in targets],))
        cur.executemany(
            "INSERT INTO origin_resolution (packet_hash, rank, pubkey, probability, method, method_version, computed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)", records)
        summary = {"nodes": len(nodes), "known_packets": len(known), "packets": len(targets),
                   "seconds": round(time.monotonic() - started, 2), "resolved_share": round(named / len(targets), 4),
                   "unknown_per_byte": round(params.unknown_weight, 3)}
        cur.execute(
            "INSERT INTO origin_run (computed_at, method_version, window_days, nodes, known_packets, packets, seconds,"
            " resolved_share, evaluation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (now, origin.METHOD_VERSION, window_days, summary["nodes"], summary["known_packets"], summary["packets"],
             summary["seconds"], summary["resolved_share"], Jsonb(evaluation)))
        cur.execute("DELETE FROM origin_run WHERE computed_at < %s", (now - timedelta(days=400),))
    log.info("origin resolution: %s; evaluation %s", summary, evaluation)
    return summary


def build_topology(conn: psycopg.Connection, window_days: int) -> dict:
    """Stage 5: links between neighbouring repeaters, routes of every known sender, critical repeaters."""
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM edge_daily WHERE day >= %s::date", (since,))
        cur.execute("""
            INSERT INTO edge_daily (day, from_node, to_node, packets)
            SELECT day, from_node, to_node, count(DISTINCT packet_hash) FROM (
                SELECT a.received_at::date AS day, a.packet_hash, a.node_id AS from_node,
                       b.node_id AS to_node
                FROM v_hop_resolved a JOIN v_hop_resolved b ON b.reception_id = a.reception_id AND b.position = a.position + 1
                WHERE a.received_at >= %(since)s::date
                UNION ALL
                SELECT received_at::date, packet_hash, node_id, '@observer'
                FROM v_hop_resolved WHERE from_end = 0 AND received_at >= %(since)s::date
            ) e WHERE from_node <> to_node GROUP BY 1, 2, 3""", {"since": since})
        edges = cur.rowcount

        # One row per reception of a packet whose sender is certain: the route as a list of node ids.
        rows = cur.execute("""
            SELECT s.sender, coalesce(h.route, ARRAY[]::text[]), count(*)
            FROM v_sender s JOIN reception r ON r.packet_hash = s.packet_hash
            LEFT JOIN LATERAL (SELECT array_agg(node_id ORDER BY position) AS route
                               FROM v_hop_resolved WHERE reception_id = r.id) h ON true
            WHERE r.route_type IN (0, 1) AND r.received_at >= %s AND s.sender IS NOT NULL
            GROUP BY 1, 2""", (since,)).fetchall()
        routes: dict[str, dict[tuple[str, ...], float]] = {}
        for sender, route, count in rows:
            routes.setdefault(sender, {})[tuple(route)] = float(count)
        senders, critical = topology.summarise(routes)

        cur.execute("DELETE FROM sender_route")
        cur.executemany(
            "INSERT INTO sender_route (sender, packets, routes, entropy, direct_share, critical, judged, computed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            [(s["sender"], int(s["packets"]), s["routes"], s["entropy"], s["direct_share"], s["critical"], s["judged"], now)
             for s in senders])
        cur.execute("DELETE FROM critical_node")
        cur.executemany("INSERT INTO critical_node (node, senders_cut, senders, computed_at) VALUES (%s,%s,%s,%s)",
                        [(c["node"], c["senders_cut"], c["senders"], now) for c in critical])
    summary = {"edges": edges, "senders": len(senders), "judged": sum(s["judged"] for s in senders),
               "critical_nodes": len(critical), "seconds": round(time.monotonic() - started, 2)}
    log.info("topology: %s", summary)
    return summary
