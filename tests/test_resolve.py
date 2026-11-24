"""Node resolution on synthetic networks where the true node behind every prefix is known."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from meshca_worker.analytics.resolve import Node, Params, Resolver, distance_km, evaluate, is_unknown, unknown  # noqa: E402


def key(first: str, rest: str) -> str:
    return (first + rest).ljust(64, "0")


def tree_network(seed=7, size=40, first_bytes=6, long_share=0.6, packets=1500):
    """Repeaters form a tree towards the observer; a packet climbs from a random node to a root.
    Many keys share the first byte, the second byte is unique. Returns nodes, paths and the truth for every path."""
    rng = random.Random(seed)
    keys = [key(f"{rng.randrange(first_bytes):02x}", f"{i:02x}ab") for i in range(size)]
    parent = {i: (rng.randrange(max(0, i - 6), i) if i >= 2 else None) for i in range(size)}   # 0 and 1 are heard by the observer
    paths, truth = {}, {}
    for _ in range(packets):
        node = rng.randrange(2, size)
        chain = []
        while parent[node] is not None:
            node = parent[node]
            chain.append(node)
        width = 4 if rng.random() < long_share else 2
        path = tuple(keys[n][:width] for n in chain)
        paths[path] = paths.get(path, 0) + 1
        truth[path] = [keys[n] for n in chain]
    return [Node(k) for k in keys], paths, truth


def accuracy_on_short_hops(nodes, paths, truth, params=None):
    resolved = Resolver(nodes, params).resolve(paths)
    hit = total = 0
    for path, answer in truth.items():
        for i, prefix in enumerate(path):
            if len(prefix) == 2 and sum(n.pubkey.startswith(prefix) for n in nodes) > 1:
                total += 1
                hit += resolved.hops[path][i][0][0] == answer[i]
    return hit / total, total


def test_unambiguous_prefix_gives_its_node():
    nodes = [Node(key("a1", "11")), Node(key("b2", "22"))]
    r = Resolver(nodes).resolve({("a111", "b2"): 5})
    assert r.hops[("a111", "b2")][0][0][0] == nodes[0].pubkey and r.hops[("a111", "b2")][0][0][1] > 0.9
    assert r.hops[("a111", "b2")][1][0][0] == nodes[1].pubkey


def test_prefix_without_known_node_is_reported_as_unknown():
    r = Resolver([Node(key("a1", "11"))]).resolve({("ff", "a1"): 3})
    best, probability = r.hops[("ff", "a1")][0][0]
    assert is_unknown(best) and best == unknown("ff") and probability > 0.99


def test_neighbours_tell_two_nodes_with_the_same_first_byte_apart():
    a1, a2, b, c = key("aa", "01"), key("aa", "02"), key("bb", "01"), key("cc", "01")
    nodes = [Node(k) for k in (a1, a2, b, c)]
    # Long prefixes show the habits: aa01 is always followed by bb, aa02 by cc.
    paths = {("aa01", "bb01"): 30, ("aa02", "cc01"): 30, ("aa", "bb"): 4, ("aa", "cc"): 4}
    r = Resolver(nodes).resolve(paths)
    assert r.hops[("aa", "bb")][0][0][0] == a1 and r.hops[("aa", "bb")][0][0][1] > 0.8
    assert r.hops[("aa", "cc")][0][0][0] == a2 and r.hops[("aa", "cc")][0][0][1] > 0.8


def test_the_observer_side_tells_who_can_be_the_last_hop():
    near, far, mid = key("aa", "01"), key("aa", "02"), key("bb", "01")
    paths = {("aa01",): 40, ("aa02", "bb01", "aa01"): 40, ("bb", "aa"): 5}
    r = Resolver([Node(near), Node(far), Node(mid)]).resolve(paths)
    assert r.hops[("bb", "aa")][1][0][0] == near, "only aa01 is ever heard by the observer itself"


def test_distance_breaks_a_tie_when_paths_say_nothing():
    here, there, neighbour = key("aa", "01"), key("aa", "02"), key("bb", "01")
    nodes = [Node(here, 10.0, 20.0), Node(there, 14.0, 20.0), Node(neighbour, 10.1, 20.0)]
    r = Resolver(nodes).resolve({("aa", "bb01"): 1})
    assert r.hops[("aa", "bb01")][0][0][0] == here
    assert 400 < distance_km(nodes[0], nodes[1]) < 500 and distance_km(nodes[0], Node("x")) is None


def test_inactive_node_loses_to_an_active_one():
    old, new = key("aa", "01"), key("aa", "02")
    r = Resolver([Node(old, active=False), Node(new)]).resolve({("aa",): 1})
    assert r.hops[("aa",)][0][0][0] == new


def test_probabilities_sum_to_one_and_method_converges():
    nodes, paths, _ = tree_network(size=25, packets=400)
    r = Resolver(nodes, Params(min_probability=0.0)).resolve(paths)
    assert r.iterations < Params().max_iterations
    for path, hops in r.hops.items():
        assert len(hops) == len(path)
        for candidates in hops:
            assert abs(sum(p for _, p in candidates) - 1) < 1e-6


def test_tree_network_short_prefixes_are_resolved_well_above_chance():
    nodes, paths, truth = tree_network()
    accuracy, total = accuracy_on_short_hops(nodes, paths, truth)
    assert total > 100
    assert accuracy > 0.9, accuracy


def test_evaluation_by_cutting_long_prefixes_matches_the_truth_based_figure():
    nodes, paths, truth = tree_network()
    e = evaluate(nodes, paths, runs=3)
    d = e.as_dict()
    assert e.runs == 3 and e.hops > 100
    # Cutting hides evidence the real short paths still have, so this figure is lower than the true accuracy.
    assert d["accuracy"] > 0.8 and d["accuracy"] > d["baseline_accuracy"] + 0.1 > d["chance_accuracy"]
    assert d["confident_accuracy"] >= d["accuracy"] and 0 <= d["brier"] < 0.2


def test_evaluation_without_long_prefixes_has_nothing_to_score():
    nodes, paths, _ = tree_network(long_share=0.0, packets=200)
    assert evaluate(nodes, paths).as_dict()["hops"] == 0
