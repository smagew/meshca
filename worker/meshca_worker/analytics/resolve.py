"""Which node stands behind a path prefix. Pure functions, no I/O.

A flood path is a list of prefixes (1 to 3 bytes of a public key), one per repeater, in the order the packet
passed them; the last one is the repeater the observer heard. A one-byte prefix usually fits several known
repeaters. The method uses what the paths themselves tell:

* how much traffic a candidate carries overall;
* who stands next to it in the path: repeaters hear a limited set of neighbours, so pairs repeat;
* whether the distance to the neighbour is plausible, when both published coordinates.

Every prefix occurrence gets a probability for each candidate. Pair and traffic counts are computed from these
probabilities and the probabilities are recomputed from the counts until they stop changing (expectation
maximisation with mean-field updates). A path's own contribution is left out of the counts it is judged by.

Candidates are known repeaters whose key starts with the prefix, plus "a node we have no advert of" for every
prefix seen ("?" + prefix), so that missing directory entries do not force a wrong known node.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field

METHOD_VERSION = 1
OBSERVER = "@observer"


def unknown(prefix: str) -> str:
    return "?" + prefix


def is_unknown(node_id: str) -> bool:
    return node_id.startswith("?")


@dataclass(frozen=True)
class Node:
    pubkey: str
    lat: float | None = None
    lon: float | None = None
    active: bool = True          # advertised recently


@dataclass(frozen=True)
class Params:
    alpha: float = 0.5           # smoothing of the traffic count of a candidate
    beta: float = 0.05           # smoothing of pair counts
    unknown_weight: float = 0.05     # weight of "a node not in the directory" against a known candidate
    inactive_weight: float = 0.3     # a known node without a recent advert
    near_km: float = 40.0        # up to here distance says nothing
    decay_km: float = 40.0       # beyond: plausibility falls e times per this many km
    far_floor: float = 0.02
    damping: float = 0.5
    tolerance: float = 1e-4
    max_iterations: int = 60
    min_probability: float = 0.01    # weaker candidates are not reported
    prune_below: float = 1e-4        # a candidate this unlikely after the first rounds is dropped from the hop
    prune_after: int = 5


@dataclass
class Result:
    hops: dict[tuple[str, ...], list[list[tuple[str, float]]]]   # path -> position -> [(node id, probability)] best first
    iterations: int = 0
    traffic: dict[str, float] = field(default_factory=dict)      # node id -> expected number of packets relayed


def distance_km(a: Node, b: Node) -> float | None:
    if None in (a.lat, a.lon, b.lat, b.lon):
        return None
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b.lon - a.lon) / 2) ** 2)
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


class Resolver:
    def __init__(self, nodes: list[Node], params: Params | None = None) -> None:
        self.params = params or Params()
        self.nodes = {n.pubkey: n for n in nodes}
        self._geo: dict[tuple[str, str], float] = {}

    def candidates(self, prefix: str, seen_prefixes: set[str]) -> list[str]:
        """Known nodes whose key starts with the prefix, then nodes we have no advert of: one for the prefix
        itself and one for every longer seen prefix that extends it and fits no known node."""
        known = sorted(k for k in self.nodes if k.startswith(prefix))
        orphans = sorted(unknown(p) for p in seen_prefixes
                         if len(p) > len(prefix) and p.startswith(prefix) and not any(k.startswith(p) for k in self.nodes))
        return known + [unknown(prefix)] + orphans

    def _base_weight(self, node_id: str) -> float:
        if is_unknown(node_id):
            return self.params.unknown_weight
        return 1.0 if self.nodes[node_id].active else self.params.inactive_weight

    def _plausible(self, a: str, b: str) -> float:
        """1 when the distance between two nodes says nothing against them being neighbours, less when far."""
        if a not in self.nodes or b not in self.nodes:
            return 1.0
        key = (a, b) if a < b else (b, a)
        if key not in self._geo:
            d = distance_km(self.nodes[a], self.nodes[b])
            p = self.params
            self._geo[key] = 1.0 if d is None or d <= p.near_km else max(p.far_floor, math.exp(-(d - p.near_km) / p.decay_km))
        return self._geo[key]

    def resolve(self, paths: dict[tuple[str, ...], float]) -> Result:
        """paths: path -> weight (number of distinct packets that came by it)."""
        p = self.params
        paths = {path: w for path, w in paths.items() if path and w > 0}
        seen = {prefix for path in paths for prefix in path}
        cands = {prefix: self.candidates(prefix, seen) for prefix in seen}
        universe = len({c for cs in cands.values() for c in cs}) + 1

        # q[path][i] = {candidate: probability}; the position after the last hop is the observer itself.
        q: dict[tuple[str, ...], list[dict[str, float]]] = {}
        for path in paths:
            q[path] = [_normalised({c: self._base_weight(c) for c in cands[prefix]}) for prefix in path]
            q[path].append({OBSERVER: 1.0})

        iterations = 0
        traffic: dict[str, float] = {}
        for iterations in range(1, p.max_iterations + 1):
            traffic = defaultdict(float)
            pair: dict[tuple[str, str], float] = defaultdict(float)
            out: dict[str, float] = defaultdict(float)
            inn: dict[str, float] = defaultdict(float)
            for path, w in paths.items():
                dist = q[path]
                for i in range(len(path)):
                    for a, pa in dist[i].items():
                        traffic[a] += w * pa
                        for b, pb in dist[i + 1].items():
                            x = w * pa * pb
                            pair[a, b] += x
                            out[a] += x
                            inn[b] += x

            change = 0.0
            new_q: dict[tuple[str, ...], list[dict[str, float]]] = {}
            for path, w in paths.items():
                dist = q[path]
                updated: list[dict[str, float]] = []
                for i in range(len(path)):
                    nxt, prv = dist[i + 1], dist[i - 1] if i > 0 else None
                    scores: dict[str, float] = {}
                    for c, own in dist[i].items():
                        score = (traffic[c] - w * own + p.alpha) * self._base_weight(c)
                        own_out = w * own                      # this path's share of everything counted for c
                        follow = 0.0
                        for d, pd in nxt.items():
                            rest = max(pair.get((c, d), 0.0) - w * own * pd, 0.0)
                            follow += pd * (rest + p.beta) / (max(out[c] - own_out, 0.0) + p.beta * universe) * self._plausible(c, d)
                        score *= follow
                        if prv is not None:
                            precede = 0.0
                            for a, pa in prv.items():
                                rest = max(pair.get((a, c), 0.0) - w * pa * own, 0.0)
                                precede += pa * (rest + p.beta) / (max(inn[c] - own_out, 0.0) + p.beta * universe) * self._plausible(a, c)
                            score *= precede
                        scores[c] = score
                    fresh = _normalised(scores)
                    mixed = {c: p.damping * dist[i][c] + (1 - p.damping) * fresh[c] for c in fresh}
                    if iterations >= p.prune_after and len(mixed) > 1:
                        kept = {c: v for c, v in mixed.items() if v >= p.prune_below}
                        if len(kept) < len(mixed):
                            mixed = _normalised(kept) | {c: 0.0 for c in mixed if c not in kept}
                    change = max(change, max(abs(mixed[c] - dist[i][c]) for c in mixed))
                    updated.append({c: v for c, v in mixed.items() if v > 0.0})
                updated.append(dist[-1])
                new_q[path] = updated
            q = new_q
            if change < p.tolerance:
                break

        hops = {path: [sorted(((c, pr) for c, pr in d.items() if pr >= p.min_probability), key=lambda x: (-x[1], x[0]))
                       for d in dist[:-1]] for path, dist in q.items()}
        return Result(hops=hops, iterations=iterations, traffic=dict(traffic))


def _normalised(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return {c: 1.0 / len(weights) for c in weights}
    return {c: v / total for c, v in weights.items()}


# --- accuracy on paths where the answer is known ---

@dataclass
class Evaluation:
    hops: int = 0                # truncated hops with a known answer and more than one known candidate
    correct: float = 0           # the best candidate is the true node
    baseline_correct: float = 0  # the candidate with the most traffic is the true node (no neighbours, no distance)
    chance: float = 0            # expected hits of a random pick among known candidates
    confident: int = 0           # the best candidate reached the confidence threshold
    confident_correct: int = 0
    brier: float = 0             # mean squared error of the probability given to the true node
    runs: int = 0

    def as_dict(self) -> dict:
        n = self.hops
        share = lambda x: round(x / n, 4) if n else None   # noqa: E731
        return {"hops": n, "runs": self.runs, "accuracy": share(self.correct), "baseline_accuracy": share(self.baseline_correct),
                "chance_accuracy": share(self.chance), "confident_share": share(self.confident),
                "confident_accuracy": round(self.confident_correct / self.confident, 4) if self.confident else None,
                "brier": share(self.brier)}


def evaluate(nodes: list[Node], paths: dict[tuple[str, ...], float], params: Params | None = None, holdout: float = 0.3,
             runs: int = 5, threshold: float = 0.7, seed: int = 1) -> Evaluation:
    """Hide what we know and see whether the method finds it again.

    A path written with two- or three-byte prefixes often names its nodes without doubt. A random part of such
    paths is cut down to one-byte prefixes, as if an older device had written them, and resolved together with
    the untouched rest. Scored are the hops whose true node is known and whose one-byte prefix fits two or more
    known nodes: the cases that are actually hard.
    """
    resolver = Resolver(nodes, params)

    def truth_of(prefix: str) -> str | None:
        if len(prefix) <= 2:
            return None
        known = [k for k in resolver.nodes if k.startswith(prefix)]
        return known[0] if len(known) == 1 else None

    eligible = sorted(path for path in paths if path and all(len(x) > 2 for x in path) and any(truth_of(x) for x in path))
    result = Evaluation()
    rng = random.Random(seed)
    for _ in range(runs):
        hidden = {path for path in eligible if rng.random() < holdout}
        if not hidden:
            continue
        result.runs += 1
        mixed: dict[tuple[str, ...], float] = defaultdict(float)
        for path, w in paths.items():
            mixed[tuple(x[:2] for x in path) if path in hidden else path] += w
        resolved = Resolver(nodes, params).resolve(mixed)
        # Baseline: traffic counted only where the prefix leaves no doubt, then "take the busiest candidate".
        certain: dict[str, float] = defaultdict(float)
        for path, w in mixed.items():
            for prefix in path:
                fits = [k for k in resolver.nodes if k.startswith(prefix)]
                if len(fits) == 1:
                    certain[fits[0]] += w
        for path in hidden:
            short = tuple(x[:2] for x in path)
            for i, prefix in enumerate(path):
                truth = truth_of(prefix)
                known = [k for k in resolver.nodes if k.startswith(short[i])]
                if truth is None or len(known) < 2:
                    continue
                ranked = resolved.hops[short][i]
                given = dict(ranked).get(truth, 0.0)
                best, best_p = ranked[0]
                result.hops += 1        # every distinct hidden hop counts once, however much traffic its path carried
                result.correct += best == truth
                result.baseline_correct += max(known, key=lambda k: (certain.get(k, 0.0), k)) == truth
                result.chance += 1.0 / len(known)
                result.brier += (1.0 - given) ** 2
                if best_p >= threshold and not is_unknown(best):
                    result.confident += 1
                    result.confident_correct += best == truth
    return result
