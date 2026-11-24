import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from meshca_worker.analytics.topology import critical_nodes, diversity, summarise  # noqa: E402


def test_single_route_makes_every_repeater_critical():
    assert critical_nodes({("A", "B", "C"): 10}) == ["A", "B", "C"]


def test_two_separate_routes_leave_nothing_critical():
    assert critical_nodes({("A", "B"): 5, ("C", "D"): 5}) == []


def test_shared_repeater_of_two_routes_is_critical():
    assert critical_nodes({("A", "X", "B"): 5, ("C", "X", "D"): 5}) == ["X"]


def test_crossing_routes_are_combined():
    # A-X-B and C-X-D cross at X; A-Y-D adds a way around X, so nothing is critical any more.
    assert critical_nodes({("A", "X", "B"): 1, ("C", "X", "D"): 1, ("A", "Y", "D"): 1}) == []


def test_sender_heard_directly_has_no_critical_repeater():
    assert critical_nodes({(): 3, ("A",): 3}) == []
    assert critical_nodes({(): 3}) == []


def test_diversity():
    assert diversity({("A",): 10}) == (1, 0.0)
    assert diversity({("A",): 5, ("B",): 5}) == (2, 1.0)
    count, entropy = diversity({("A",): 9, ("B",): 1})
    assert count == 2 and 0.4 < entropy < 0.5
    assert diversity({}) == (0, 0.0)


def test_summary_ignores_rare_senders_for_the_critical_list():
    senders, nodes = summarise({"busy": {("A", "X"): 10}, "rare": {("B", "X"): 9}, "free": {("A",): 4, ("C",): 4, (): 2}})
    by = {s["sender"]: s for s in senders}
    assert by["busy"]["critical"] == ["A", "X"] and by["busy"]["judged"] and not by["rare"]["judged"]
    assert by["free"]["routes"] == 3 and by["free"]["direct_share"] == 0.2 and by["free"]["critical"] == []
    assert nodes == [{"node": "A", "senders_cut": 1, "senders": ["busy"]}, {"node": "X", "senders_cut": 1, "senders": ["busy"]}]
