"""Pure helpers of the collector (no MQTT, no database): what a message is and how it is stored."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

PACKETS = "packets"


@dataclass(frozen=True)
class Message:
    received_at: datetime
    topic: str
    payload: bytes
    retained: bool = False


@dataclass(frozen=True)
class Row:
    table: str                   # raw_reception | observer_message
    ingested_at: datetime
    topic: str
    kind: str
    retained: bool
    payload_json: Optional[str]  # canonical JSON text, when the payload is valid JSON
    payload_text: Optional[str]  # the payload as text otherwise


def topic_kind(topic: str) -> str:
    """Last topic segment: meshcore/{IATA}/{PUBLIC_KEY}/packets -> packets."""
    return topic.rsplit("/", 1)[-1] or "unknown"


def split_payload(payload: bytes) -> tuple[Optional[str], Optional[str]]:
    """(json, None) for a JSON object or array, (None, text) for everything else. Never raises.

    PostgreSQL text and jsonb cannot hold NUL, so NUL is dropped from text and JSON with \\u0000 is kept as text.
    """
    text = payload.decode("utf-8", errors="replace").replace("\x00", "")
    try:
        value = json.loads(text)
    except ValueError:
        return None, text
    if not isinstance(value, (dict, list)):
        return None, text
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if "\\u0000" in encoded:
        return None, text
    return encoded, None


def to_row(message: Message) -> Optional[Row]:
    """The row to store, or None when the message must be skipped.

    A retained packet is a copy the broker re-sends on every (re)subscription, not a new reception: skip it.
    Retained messages of other kinds (status) are kept and marked.
    """
    kind = topic_kind(message.topic)
    if kind == PACKETS and message.retained:
        return None
    payload_json, payload_text = split_payload(message.payload)
    table = "raw_reception" if kind == PACKETS else "observer_message"
    return Row(table, message.received_at, message.topic, kind, message.retained, payload_json, payload_text)
