"""Who sent a packet that carries only the first byte of the sender's key. Pure functions, no I/O.

REQ, RESPONSE, TXT_MSG and PATH packets name the sender by one byte, which usually fits several known nodes.
What tells them apart is where the packet enters the mesh: a node's packets start through the same one or two
repeaters. That "path fingerprint" is learned from packets whose sender is certain (signed adverts, anonymous
requests) and compared with the first hops of the packet in question.

A feature is "position:node", for the first two repeaters of a flood path, or "1:@direct" when the observer
heard the sender itself. Candidates are the known nodes whose key starts with the byte, plus "a node we have
no advert of".
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass

METHOD_VERSION = 1
DIRECT = "1:@direct"


@dataclass(frozen=True)
class Params:
    beta: float = 0.2                # smoothing of fingerprint counts
    # Expected number of nodes we have no advert of that share a given first byte, against 1 for each known
    # candidate. The job estimates it from the data (unknown_weight_from_data); this is only a default.
    unknown_weight: float = 0.05
    inactive_weight: float = 0.3     # a known node without a recent advert
    min_probability: float = 0.01


def unknown(src_hash: str) -> str:
    return "?" + src_hash


def is_unknown(node_id: str) -> bool:
    return node_id.startswith("?")


def unknown_weight_from_data(known_first_bytes: set[str], seen_first_bytes: set[str]) -> float:
    """How many unseen nodes to expect behind one byte value.

    Take the byte values no known node has. If senders we do not know were spread evenly, the share of those
    values that still showed up in traffic is 1 - exp(-rate), where rate is the number of unknown nodes per value.
    """
    empty = 256 - len(known_first_bytes)
    occupied = len(seen_first_bytes - known_first_bytes)
    if empty <= 0:
        return 0.05
    share = min(occupied / empty, 0.99)
    return min(max(-math.log(1.0 - share), 0.02), 5.0)


class OriginResolver:
    def __init__(self, nodes: dict[str, bool], known: list[tuple[str, frozenset[str]]], params: Params | None = None) -> None:
        """nodes: public key -> advertised recently. known: (sender key, features) of packets with a certain sender."""
        self.params = params or Params()
        self.nodes = nodes
        self.counts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.packets: dict[str, float] = defaultdict(float)
        features = {DIRECT}
        for pubkey, packet_features in known:
            self.packets[pubkey] += 1
            for f in packet_features:
                self.counts[pubkey][f] += 1
                features.add(f)
        self.feature_count = len(features) + 1
        self._by_hash: dict[str, list[str]] = defaultdict(list)
        for pubkey in nodes:
            self._by_hash[pubkey[:2]].append(pubkey)

    def known_candidates(self, src_hash: str) -> list[str]:
        return sorted(self._by_hash.get(src_hash, []))

    def likelihood(self, pubkey: str, features: frozenset[str]) -> float:
        """How well the first hops fit the node's fingerprint: share of its known packets that had each feature."""
        p, n = self.params, self.packets.get(pubkey, 0.0)
        value = 1.0
        for f in features:
            value *= (self.counts[pubkey].get(f, 0.0) + p.beta) / (n + p.beta * self.feature_count)
        return value

    def resolve(self, src_hash: str, features: frozenset[str]) -> list[tuple[str, float]]:
        """Candidates with probabilities, best first."""
        p = self.params
        scores = {unknown(src_hash): p.unknown_weight * (1.0 / self.feature_count) ** len(features)}
        for pubkey in self.known_candidates(src_hash):
            scores[pubkey] = (1.0 if self.nodes[pubkey] else p.inactive_weight) * self.likelihood(pubkey, features)
        total = sum(scores.values())
        ranked = sorted(((c, s / total) for c, s in scores.items()), key=lambda x: (-x[1], x[0]))
        return [(c, pr) for c, pr in ranked if pr >= p.min_probability] or ranked[:1]


@dataclass
class Evaluation:
    packets: int = 0             # hidden packets whose first byte fits two or more known nodes
    correct: int = 0
    baseline_correct: int = 0    # the candidate with the most known packets, fingerprint ignored
    chance: float = 0
    confident: int = 0
    confident_correct: int = 0
    runs: int = 0

    def as_dict(self) -> dict:
        n = self.packets
        share = lambda x: round(x / n, 4) if n else None   # noqa: E731
        return {"packets": n, "runs": self.runs, "accuracy": share(self.correct),
                "baseline_accuracy": share(self.baseline_correct), "chance_accuracy": share(self.chance),
                "confident_share": share(self.confident),
                "confident_accuracy": round(self.confident_correct / self.confident, 4) if self.confident else None}


def evaluate(nodes: dict[str, bool], known: list[tuple[str, frozenset[str]]], params: Params | None = None,
             holdout: float = 0.3, runs: int = 5, threshold: float = 0.7, seed: int = 1) -> Evaluation:
    """Hide the sender of a random part of the packets where it is certain, keep its first byte, and find it again
    from fingerprints learned on the rest. Scored are the packets whose byte fits two or more known nodes."""
    result = Evaluation()
    rng = random.Random(seed)
    for _ in range(runs):
        hidden_flags = [rng.random() < holdout for _ in known]
        train = [k for k, hidden in zip(known, hidden_flags) if not hidden]
        test = [k for k, hidden in zip(known, hidden_flags) if hidden]
        if not train or not test:
            continue
        result.runs += 1
        resolver = OriginResolver(nodes, train, params)
        for truth, features in test:
            candidates = resolver.known_candidates(truth[:2])
            if len(candidates) < 2 or truth not in nodes:
                continue
            best, probability = resolver.resolve(truth[:2], features)[0]
            result.packets += 1
            result.correct += best == truth
            result.baseline_correct += max(candidates, key=lambda c: (resolver.packets.get(c, 0.0), c)) == truth
            result.chance += 1.0 / len(candidates)
            if probability >= threshold and not is_unknown(best):
                result.confident += 1
                result.confident_correct += best == truth
    return result
