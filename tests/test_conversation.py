import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from meshca_worker.analytics.conversation import (  # noqa: E402
    ChannelMessage, Direct, channel_links, mentions, sessions)


def test_request_and_answer_make_one_session_with_a_response_time():
    out = sessions([Direct(100, "aa", "bb", "REQ"), Direct(103, "bb", "aa", "RESPONSE"),
                    Direct(160, "aa", "bb", "TXT_MSG"), Direct(170, "bb", "aa", "TXT_MSG")])
    assert len(out) == 1
    s = out[0]
    assert (s.a, s.b, s.packets, s.a_to_b, s.b_to_a) == ("aa", "bb", 4, 2, 2)
    assert s.kinds == ["a:REQ", "b:RESPONSE", "a:TXT_MSG", "b:TXT_MSG"]
    assert s.response_s == [3, 10] and s.median_response_s == 6.5


def test_long_pause_starts_a_new_session_and_pairs_are_apart():
    out = sessions([Direct(0, "aa", "bb", "REQ"), Direct(1000, "bb", "aa", "REQ"), Direct(5, "aa", "cc", "TXT_MSG")])
    assert [(s.a, s.b, s.packets) for s in out] == [("aa", "bb", 1), ("aa", "bb", 1), ("aa", "cc", 1)]


def test_late_answer_and_repeated_questions_give_no_extra_response_times():
    out = sessions([Direct(0, "aa", "bb", "REQ"), Direct(60, "bb", "aa", "RESPONSE"),     # too late
                    Direct(100, "aa", "bb", "REQ"), Direct(101, "bb", "aa", "RESPONSE"),
                    Direct(102, "bb", "aa", "RESPONSE")])                                 # one answer per question
    assert out[0].response_s == [1]


def test_packet_to_itself_is_ignored():
    assert sessions([Direct(0, "aa", "aa", "PATH")]) == []


def test_mentions_are_found():
    assert mentions("@[Anna] hi, @[Bob Two] too") == ["Anna", "Bob Two"]
    assert mentions("no mention @Anna") == []
    assert mentions(None) == []


def test_channel_links_count_mentions_and_quick_follows_against_chance():
    msgs = [ChannelMessage(0, "#t", "Anna", "hello"), ChannelMessage(30, "#t", "Bob", "@[Anna] hi"),
            ChannelMessage(3600, "#t", "Anna", "again"), ChannelMessage(3650, "#t", "Bob", "yes"),
            ChannelMessage(7200, "#t", "Cid", "@[Cid] myself"), ChannelMessage(7300, "#u", "Bob", "@[Anna] other channel")]
    links = {(l.channel, l.source, l.target): l for l in channel_links(msgs)}
    bob = links["#t", "Bob", "Anna"]
    assert bob.mentions == 1 and bob.quick == 2
    # 2 messages each over 7200 s: by chance 2 * 2 * 120 / 7200 follows.
    assert abs(bob.expected - 2 * 2 * 120 / 7200) < 1e-9 and bob.lift > 10
    assert ("#t", "Cid", "Cid") not in links                 # mentioning oneself is not a link
    assert links["#u", "Bob", "Anna"].mentions == 1 and links["#u", "Bob", "Anna"].lift is None
