"""Sender resolution on synthetic traffic where the true sender is known."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from meshca_worker.analytics.origin import DIRECT, OriginResolver, evaluate, is_unknown, unknown  # noqa: E402


def key(first: str, rest: str) -> str:
    return (first + rest).ljust(64, "0")


def features(*hops):
    return frozenset(f"{i}:{h}" for i, h in enumerate(hops, start=1))


def traffic(seed=3, senders=60, first_bytes=12, repeaters=10, packets=3000):
    """Every sender has a home repeater it nearly always enters the mesh through, sometimes a second one."""
    rng = random.Random(seed)
    keys = [key(f"{rng.randrange(first_bytes):02x}", f"{i:04x}") for i in range(senders)]
    home = {k: (f"R{rng.randrange(repeaters)}", f"R{rng.randrange(repeaters)}") for k in keys}
    known = []
    for _ in range(packets):
        k = rng.choice(keys)
        first = home[k][0] if rng.random() < 0.9 else f"R{rng.randrange(repeaters)}"
        known.append((k, features(first, home[k][1])))
    return {k: True for k in keys}, known


def test_fingerprint_tells_two_senders_with_the_same_byte_apart():
    a, b = key("aa", "01"), key("aa", "02")
    known = [(a, features("R1"))] * 20 + [(b, features("R2"))] * 20
    r = OriginResolver({a: True, b: True}, known)
    assert r.resolve("aa", features("R1"))[0][0] == a and r.resolve("aa", features("R1"))[0][1] > 0.9
    assert r.resolve("aa", features("R2"))[0][0] == b


def test_sender_heard_directly_is_a_feature_of_its_own():
    near, far = key("aa", "01"), key("aa", "02")
    known = [(near, frozenset({DIRECT}))] * 10 + [(far, features("R5", "R6"))] * 10
    r = OriginResolver({near: True, far: True}, known)
    assert r.resolve("aa", frozenset({DIRECT}))[0][0] == near


def test_no_known_node_with_this_byte_means_unknown():
    r = OriginResolver({key("aa", "01"): True}, [(key("aa", "01"), features("R1"))])
    best, probability = r.resolve("ff", features("R1"))[0]
    assert is_unknown(best) and best == unknown("ff") and probability == 1.0


def test_a_never_seen_entry_point_lowers_confidence():
    a = key("aa", "01")
    r = OriginResolver({a: True}, [(a, features("R1"))] * 50)
    usual = dict(r.resolve("aa", features("R1")))[a]
    strange = dict(r.resolve("aa", features("R9", "R8"))).get(a, 0.0)
    assert usual > 0.95 and strange < usual


def test_probabilities_sum_to_one_and_inactive_node_loses_a_tie():
    old, new = key("aa", "01"), key("aa", "02")
    r = OriginResolver({old: False, new: True}, [])
    ranked = r.resolve("aa", features("R1"))
    assert abs(sum(p for _, p in ranked) - 1) < 0.02 and ranked[0][0] == new


def test_evaluation_on_hidden_senders_beats_the_busiest_candidate_rule():
    nodes, known = traffic()
    d = evaluate(nodes, known, runs=3).as_dict()
    assert d["packets"] > 500 and d["runs"] == 3
    assert d["accuracy"] > 0.8 and d["accuracy"] > d["baseline_accuracy"] + 0.2 > d["chance_accuracy"]
    assert d["confident_accuracy"] > d["accuracy"]


def test_evaluation_with_nothing_ambiguous_scores_nothing():
    a, b = key("aa", "01"), key("bb", "01")
    known = [(a, features("R1")), (b, features("R2"))] * 20
    assert evaluate({a: True, b: True}, known).as_dict()["packets"] == 0


def test_unknown_weight_follows_how_incomplete_the_directory_is():
    from meshca_worker.analytics.origin import unknown_weight_from_data
    known = {f"{i:02x}" for i in range(56)}                       # 200 byte values have no known node
    few = unknown_weight_from_data(known, known | {f"{i:02x}" for i in range(56, 66)})
    many = unknown_weight_from_data(known, known | {f"{i:02x}" for i in range(56, 206)})
    assert 0.04 < few < 0.06 and 1.3 < many < 1.5, (few, many)
    assert unknown_weight_from_data(known, known) == 0.02


def test_single_known_candidate_without_history_is_not_confident_when_many_nodes_are_unknown():
    from meshca_worker.analytics.origin import Params
    a = key("aa", "01")
    ranked = dict(OriginResolver({a: True}, [], Params(unknown_weight=1.4)).resolve("aa", features("R1")))
    assert ranked[a] < 0.5
