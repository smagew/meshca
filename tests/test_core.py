import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

from meshca_collector.core import Message, split_payload, to_row, topic_kind  # noqa: E402

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def msg(topic, payload, retained=False):
    return Message(NOW, topic, payload, retained)


def test_topic_kind():
    assert topic_kind("meshcore/XXX/ABCD/packets") == "packets"
    assert topic_kind("meshcore/XXX/ABCD/status") == "status"
    assert topic_kind("meshcore/XXX/ABCD/") == "unknown"
    assert topic_kind("packets") == "packets"


def test_packet_goes_to_raw_reception_unchanged():
    body = {"type": "PACKET", "raw": "11AABB", "SNR": "12.0", "RSSI": "-83", "hash": "17313FA01DEF45BE"}
    row = to_row(msg("meshcore/XXX/ABCD/packets", json.dumps(body).encode()))
    assert row.table == "raw_reception" and row.kind == "packets" and row.ingested_at == NOW
    assert json.loads(row.payload_json) == body and row.payload_text is None


def test_retained_packet_is_skipped_but_retained_status_is_kept():
    assert to_row(msg("meshcore/XXX/ABCD/packets", b'{"raw":"11"}', retained=True)) is None
    row = to_row(msg("meshcore/XXX/ABCD/status", b'{"status":"online"}', retained=True))
    assert row.table == "observer_message" and row.kind == "status" and row.retained is True


def test_other_topics_go_to_observer_message():
    row = to_row(msg("meshcore/XXX/ABCD/neighbors", b'{"total_neighbors":2}'))
    assert row.table == "observer_message" and row.kind == "neighbors"


def test_payload_that_is_not_json_is_kept_as_text():
    assert split_payload(b"not json") == (None, "not json")
    assert split_payload(b"") == (None, "")
    assert split_payload(b"42") == (None, "42")                       # a bare scalar is not a message object
    assert split_payload(b"\xff\xfe{") == (None, "��{")     # invalid UTF-8 never raises


def test_nul_never_reaches_the_database():
    assert split_payload(b'ab\x00c') == (None, "abc")
    payload_json, payload_text = split_payload(b'{"name":"a\\u0000b"}')
    assert payload_json is None and payload_text == '{"name":"a\\u0000b"}'


def test_unicode_is_preserved():
    payload_json, _ = split_payload('{"name":"Узел 🚀"}'.encode())
    assert json.loads(payload_json) == {"name": "Узел 🚀"}
