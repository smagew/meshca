"""Network topology as seen from the observer. Pure functions, no I/O.

A route is the list of repeaters a packet of some sender passed on its way to the observer. From the routes of
one sender two things follow: how many different ways its packets reach us, and which repeaters are on every
one of them. Such a repeater is critical for that sender: without it nothing of the sender would reach the
observer by the routes seen so far. All of this is "from our point": other parts of the mesh may know more ways.
"""
from __future__ import annotations

import math
from collections import defaultdict

SENDER, OBSERVER = "@sender", "@observer"


def diversity(routes: dict[tuple[str, ...], float]) -> tuple[int, float]:
    """Number of distinct routes and the entropy of their use in bits (0 = always the same route)."""
    total = sum(routes.values())
    if total <= 0:
        return 0, 0.0
    entropy = -sum(w / total * math.log2(w / total) for w in routes.values() if w > 0)
    return len(routes), round(entropy + 0.0, 4)


def critical_nodes(routes: dict[tuple[str, ...], float]) -> list[str]:
    """Repeaters without which the sender cannot reach the observer in the graph made of all its routes.

    Routes are joined into one graph first: two routes that cross can be combined into a third one, so a
    repeater is critical only when every way through that graph passes it.
    """
    edges: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for route in routes:
        chain = (SENDER, *route, OBSERVER)
        for a, b in zip(chain, chain[1:]):
            if a != b:
                edges[a].add(b)
        nodes.update(route)

    def reachable(without: str) -> bool:
        seen, stack = {SENDER}, [SENDER]
        while stack:
            for nxt in edges[stack.pop()]:
                if nxt == OBSERVER:
                    return True
                if nxt != without and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    return sorted(n for n in nodes if not reachable(n))


def summarise(routes_by_sender: dict[str, dict[tuple[str, ...], float]], min_packets: float = 10) -> tuple[list[dict], list[dict]]:
    """Per sender: receptions (called packets here), routes, entropy, share heard directly, critical repeaters.
    Per repeater: the senders it is critical for. Senders with fewer packets than min_packets are described
    but not used for the second list: one or two packets always look like "a single route"."""
    senders, cut = [], defaultdict(list)
    for sender, routes in sorted(routes_by_sender.items()):
        packets = sum(routes.values())
        count, entropy = diversity(routes)
        critical = critical_nodes(routes)
        senders.append({"sender": sender, "packets": packets, "routes": count, "entropy": entropy,
                        "direct_share": round(routes.get((), 0.0) / packets, 4) if packets else 0.0,
                        "critical": critical, "judged": packets >= min_packets})
        if packets >= min_packets:
            for node in critical:
                cut[node].append(sender)
    nodes = [{"node": node, "senders_cut": len(names), "senders": sorted(names)}
             for node, names in sorted(cut.items(), key=lambda x: (-len(x[1]), x[0]))]
    return senders, nodes
