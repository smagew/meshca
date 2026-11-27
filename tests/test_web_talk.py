import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from meshca_web.talk import ChannelMessage, DirectPacket, Node, build  # noqa: E402

RPT = "r1" + "0" * 62          # a repeater with coordinates
PHONE_A = "aa" + "1" * 62      # phones: no coordinates
PHONE_B = "bb" + "2" * 62
NODES = {RPT: Node("Hill", "ретранслятор", 10.0, 20.0), PHONE_A: Node("Anna", "клиент", None, None),
         PHONE_B: Node("Bob", "клиент", None, None)}


def test_private_messages_link_two_phones_placed_at_their_home_repeater():
    packets = [DirectPacket(1, "aa", "bb", 2, PHONE_A, RPT), DirectPacket(5, "bb", "aa", 2, None, RPT),
               DirectPacket(9, "aa", "bb", 2, None, None)]
    out = build(packets, [], NODES)
    (link,) = out["links"]
    assert (link["a"], link["b"]) == (PHONE_A, PHONE_B)
    assert link["messages"] == 3 and link["service"] == 0 and (link["a_to_b"], link["b_to_a"]) == (2, 1)
    assert [i[:3] for i in link["items"]] == [[9, 2, True], [5, 2, False], [1, 2, True]]
    assert not link["uncertain"]
    nodes = {n["id"]: n for n in out["nodes"]}
    assert nodes[PHONE_A]["placed"] == "home" and nodes[PHONE_A]["home"] == "Hill"
    assert abs(nodes[PHONE_A]["lat"] - 10.0) < 0.01
    # Bob's packets also entered at Hill.
    assert nodes[PHONE_B]["placed"] == "home"


def test_service_traffic_to_a_repeater_is_counted_apart():
    packets = [DirectPacket(1, "aa", "r1", 0, PHONE_A, RPT), DirectPacket(2, "r1", "aa", 1, None, None)]
    (link,) = build(packets, [], NODES)["links"]
    assert link["service"] == 2 and link["messages"] == 0


def test_text_messages_to_a_repeater_are_commands_not_conversation():
    packets = [DirectPacket(1, "aa", "r1", 2, PHONE_A, RPT), DirectPacket(2, "r1", "aa", 2, None, None)]
    (link,) = build(packets, [], NODES)["links"]
    assert link["messages"] == 0 and link["service"] == 2


def test_unknown_hash_stays_unknown_and_marks_the_link_uncertain():
    packets = [DirectPacket(1, "aa", "ee", 2, PHONE_A, None)]
    out = build(packets, [], NODES)
    (link,) = out["links"]
    assert (link["a"], link["b"]) == ("#ee", PHONE_A) and link["uncertain"]
    nodes = {n["id"]: n for n in out["nodes"]}
    assert nodes["#ee"]["kind"] == "unknown" and nodes["#ee"]["label"] == "? ee" and nodes["#ee"]["lat"] is None
    assert out["unplaced"] >= 1


def test_channel_mentions_and_quick_replies_against_chance():
    msgs = [ChannelMessage(0, "#t", "Anna", "hi all", RPT), ChannelMessage(20, "#t", "Carl", "@[Anna] hello", RPT),
            ChannelMessage(5000, "#t", "Anna", "again", RPT), ChannelMessage(5030, "#t", "Carl", "yes", RPT),
            ChannelMessage(9000, "#t", "Anna", "more", RPT), ChannelMessage(9040, "#t", "Carl", "ok", RPT)]
    out = build([], msgs, NODES)
    (link,) = out["links"]
    # Anna is a known node by name; Carl is only a name. 1 mention + 3 quick replies (far above chance).
    assert {link["a"], link["b"]} == {PHONE_A, "@Carl"}
    assert link["channel"] == 4 and link["channels"] == ["#t"]
    # Each exchange is kept: the mention at 20 s is also a quick reply, so 3 distinct moments.
    assert sorted(i[0] for i in link["items"]) == [20, 5030, 9040] and all(i[1] == "channel" for i in link["items"])
    carl = {n["id"]: n for n in out["nodes"]}["@Carl"]
    assert carl["kind"] == "person" and carl["placed"] == "home"


def test_one_quick_reply_is_not_enough():
    msgs = [ChannelMessage(0, "#t", "Anna", "hi", None), ChannelMessage(30, "#t", "Carl", "hey", None),
            ChannelMessage(100000, "#t", "Anna", "x", None)]
    assert build([], msgs, NODES)["links"] == []
