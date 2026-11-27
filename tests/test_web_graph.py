import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from meshca_web.graph import OBSERVER, Edge, NodeInfo, build  # noqa: E402


def test_flows_positions_and_critical_nodes():
    edges = [Edge("aa", "bb", 10, 6, 3, 1), Edge("bb", OBSERVER, 12, 6, 4, 2), Edge("?cc", "bb", 2, 0, 0, 2)]
    info = {"aa": NodeInfo("Alpha", "ретранслятор", 10.0, 20.0), "bb": NodeInfo("Beta", "ретранслятор", 10.2, 20.4)}
    out = build(edges, info, {"bb": ["Alpha"]})
    nodes = {n["id"]: n for n in out["nodes"]}

    assert nodes["aa"]["label"] == "Alpha" and nodes["aa"]["placed"] == "own" and nodes["aa"]["relayed"] == 10
    assert nodes["bb"]["mix"] == {"adverts": 6, "channel": 4, "direct": 2, "service": 0} and nodes["bb"]["critical"] == ["Alpha"]
    # The observer counts what it received; its place is guessed from its only neighbour.
    obs = nodes[OBSERVER]
    assert obs["kind"] == "observer" and obs["heard"] == 12 and obs["relayed"] == 12 and obs["placed"] == "neighbours"
    assert abs(obs["lat"] - 10.2) < 0.01 and abs(obs["lon"] - 20.4) < 0.01
    unknown = nodes["?cc"]
    assert unknown["kind"] == "unknown" and unknown["label"] == "? cc" and unknown["placed"] == "neighbours"
    assert out["unplaced"] == 0 and len(out["edges"]) == 3


def test_node_without_any_placed_neighbour_stays_off_the_map():
    out = build([Edge("?aa", "?bb", 3)], {}, {})
    assert out["unplaced"] == 2 and all(n["lat"] is None for n in out["nodes"])


def test_zero_coordinates_mean_no_coordinates():
    out = build([Edge("aa", "bb", 1)], {"aa": NodeInfo("A", None, 0.0, 0.0), "bb": NodeInfo("B", None, 12.0, 30.0)}, {})
    nodes = {n["id"]: n for n in out["nodes"]}
    assert nodes["aa"]["placed"] == "neighbours"


def test_guessed_nodes_get_distinct_places():
    edges = [Edge("?a1", "hub", 1), Edge("?a2", "hub", 1)]
    out = build(edges, {"hub": NodeInfo("Hub", None, 12.0, 30.0)}, {})
    places = {(n["lat"], n["lon"]) for n in out["nodes"] if n["id"] != "hub"}
    assert len(places) == 2
