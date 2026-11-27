"""The link graph for the map page. Pure functions, no I/O.

A link "A -> B" means B heard A and passed the packet on (or B is our observer). Node ids are public keys,
"?prefix" for a node we have no advert of, "@observer" for our observer. Nodes without coordinates are put among
their neighbours that have them, so the map can still show where they probably are.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

OBSERVER = "@observer"
KINDS = ("adverts", "channel", "direct", "service")


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    packets: int
    adverts: int = 0
    channel: int = 0
    direct: int = 0              # private messages and their acknowledgements
    service: int = 0             # requests to repeaters and rooms, answers, anonymous requests, returned paths


@dataclass(frozen=True)
class NodeInfo:
    name: str | None = None
    role: str | None = None
    lat: float | None = None
    lon: float | None = None


def _jitter(node_id: str, scale: float = 0.004) -> tuple[float, float]:
    """A small fixed offset from the node id, so guessed nodes do not sit exactly on top of each other."""
    h = hashlib.sha256(node_id.encode()).digest()
    return (h[0] / 255 - 0.5) * scale, (h[1] / 255 - 0.5) * scale * 1.7


def label(node_id: str, info: NodeInfo | None) -> str:
    if node_id == OBSERVER:
        return "наш observer"
    if node_id.startswith("?"):
        return "? " + node_id[1:]
    return (info.name if info and info.name else None) or node_id[:8]


def build(edges: list[Edge], info: dict[str, NodeInfo], critical: dict[str, list[str]]) -> dict:
    """JSON-ready {nodes, edges}. critical: node id -> senders that would stop reaching us without it."""
    ids = sorted({e.source for e in edges} | {e.target for e in edges})
    neighbours: dict[str, set[str]] = defaultdict(set)
    flow = {i: dict.fromkeys(("packets",) + KINDS, 0) for i in ids}
    heard = {i: 0 for i in ids}
    for e in edges:
        neighbours[e.source].add(e.target)
        neighbours[e.target].add(e.source)
        # What a node passed on; for the observer, what it received.
        sides = (e.source, e.target) if e.target == OBSERVER else (e.source,)
        for side in sides:
            for key, value in (("packets", e.packets), ("adverts", e.adverts), ("channel", e.channel), ("direct", e.direct),
                               ("service", e.service)):
                flow[side][key] += value
        heard[e.target] += e.packets

    position: dict[str, tuple[float, float]] = {}
    placed: dict[str, str] = {}
    for i in ids:
        n = info.get(i)
        if n and n.lat is not None and n.lon is not None and not (n.lat == 0 and n.lon == 0):
            position[i], placed[i] = (n.lat, n.lon), "own"
    # Two rounds: a node may only have neighbours that were themselves placed in the first round.
    for _ in range(2):
        guessed = {}
        for i in ids:
            if i in position:
                continue
            near = [position[j] for j in neighbours[i] if j in position]
            if near:
                dy, dx = _jitter(i)
                guessed[i] = (sum(p[0] for p in near) / len(near) + dy, sum(p[1] for p in near) / len(near) + dx)
        for i, p in guessed.items():
            position[i], placed[i] = p, "neighbours"

    nodes = []
    for i in ids:
        n = info.get(i)
        lat, lon = position.get(i, (None, None))
        nodes.append({
            "id": i, "label": label(i, n), "role": n.role if n else None,
            "kind": "observer" if i == OBSERVER else "unknown" if i.startswith("?") else "node",
            "lat": lat, "lon": lon, "placed": placed.get(i),
            "relayed": flow[i]["packets"], "heard": heard[i],
            "mix": {k: flow[i][k] for k in KINDS},
            "critical": critical.get(i, []),
        })
    return {
        "nodes": nodes,
        "edges": [{"source": e.source, "target": e.target, "packets": e.packets,
                   "mix": {"adverts": e.adverts, "channel": e.channel, "direct": e.direct,
                           "service": e.service}} for e in edges],
        "unplaced": sum(1 for n in nodes if n["lat"] is None),
    }
