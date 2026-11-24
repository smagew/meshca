"""Writes the decoded layer. Every function works inside the caller's transaction."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import psycopg

from . import DECODER_VERSION, decode
from .decode import ChannelKeys, DecodeError

log = logging.getLogger("meshca.store")

DERIVED_TABLES = ("node_history", "node", "advert", "channel_message", "direct_packet", "anon_request",
                  "reception", "packet", "decode_error")
MAX_MENTIONED_CHANNELS = 2000     # channels learned from "#name" mentions; configured ones are not limited
MAX_MENTIONS_PER_MESSAGE = 5
U32_MAX = 0xFFFFFFFF


# --- worker state ---

def get_state(cur: psycopg.Cursor, key: str, default=None):
    row = cur.execute("SELECT value FROM worker_state WHERE key = %s", (key,)).fetchone()
    return row[0] if row else default


def set_state(cur: psycopg.Cursor, key: str, value) -> None:
    cur.execute("INSERT INTO worker_state (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, psycopg.types.json.Jsonb(value)))


def reset_if_decoder_changed(cur: psycopg.Cursor) -> bool:
    """A new decoder version rebuilds the whole decoded layer from the raw records."""
    stored = get_state(cur, "decoder_version")
    if stored == DECODER_VERSION:
        return False
    if stored is not None:
        cur.execute("TRUNCATE " + ", ".join(DERIVED_TABLES) + " CASCADE")   # raw_reception is not touched
        set_state(cur, "last_raw_id", 0)
    set_state(cur, "decoder_version", DECODER_VERSION)
    return stored is not None


# --- channels ---

def load_channels(cur: psycopg.Cursor, configured: list[str], text_retention: timedelta | None = None) -> ChannelKeys:
    """Make sure Public and the configured hashtag channels exist, then load every known key.

    With text_retention given, messages stored before a configured channel was known are decrypted now.
    """
    cur.execute("INSERT INTO channel (name, key, hash, source) VALUES ('Public', %s, %s, 'builtin') "
                "ON CONFLICT (name) DO NOTHING",
                (decode.PUBLIC_CHANNEL_KEY.hex(), decode.channel_hash(decode.PUBLIC_CHANNEL_KEY)))
    keys = ChannelKeys()
    for name, key_hex in cur.execute("SELECT name, key FROM channel ORDER BY added_at, name").fetchall():
        keys.add(name, bytes.fromhex(key_hex))
    if text_retention is not None:
        cutoff = datetime.now(timezone.utc) - text_retention
        new = {n if n.startswith("#") else "#" + n for n in (c.strip().lower() for c in configured) if n}
        _open_channels(cur, keys, [n for n in sorted(new) if n not in keys.names], "config", cutoff)
    return keys


def _open_channels(cur: psycopg.Cursor, keys: ChannelKeys, names: list[str], source: str, text_cutoff: datetime) -> None:
    """Add hashtag channels (the key is derived from the name) and decrypt what was stored before they were known.
    Channels mentioned in the newly opened texts are added the same way."""
    pending = [(name, source) for name in names]
    mentioned = cur.execute("SELECT count(*) FROM channel WHERE source = 'mention'").fetchone()[0]
    while pending:
        name, source = pending.pop()
        if name in keys.names or (source == "mention" and mentioned >= MAX_MENTIONED_CHANNELS):
            continue
        mentioned += source == "mention"
        key = decode.hashtag_key(name)
        if not keys.add(name, key):
            continue
        cur.execute("INSERT INTO channel (name, key, hash, source) VALUES (%s, %s, %s, %s) ON CONFLICT (name) DO NOTHING",
                    (name, key.hex(), decode.channel_hash(key), source))
        rows = cur.execute(
            "SELECT m.packet_hash, m.payload_type, p.payload, "
            "(SELECT max(r.ingested_at) FROM reception c JOIN raw_reception r ON r.id = c.id WHERE c.packet_hash = p.hash) "
            "FROM channel_message m JOIN packet p ON p.hash = m.packet_hash "
            "WHERE NOT m.decrypted AND m.channel_hash = %s AND p.payload IS NOT NULL",
            (decode.channel_hash(key),)).fetchall()
        opened = 0
        for packet_hash, payload_type, payload, ingested in rows:
            msg = decode.parse_group(payload_type, bytes(payload), keys)
            if msg.decrypted:
                _write_group(cur, packet_hash, msg, ingested is not None and ingested >= text_cutoff, update=True)
                opened += 1
                if msg.full_text:
                    pending += [(n, "mention") for n in _mentions(msg.full_text, keys)]
        log.info("new channel %s, %d earlier message(s) decrypted", name, opened)


def _learn_hashtags(cur: psycopg.Cursor, keys: ChannelKeys, text: str, text_cutoff: datetime) -> None:
    """A channel mentioned as #name has a key derived from that name."""
    names = _mentions(text, keys)
    if names:
        _open_channels(cur, keys, names, "mention", text_cutoff)


def _mentions(text: str, keys: ChannelKeys) -> list[str]:
    """New channel names in a message. A message full of hashtags is not allowed to add them all."""
    return sorted(n for n in decode.find_hashtags(text) if n not in keys.names)[:MAX_MENTIONS_PER_MESSAGE]


def _timestamp(unix: int | None) -> datetime | None:
    if unix is None or not 0 < unix <= U32_MAX:
        return None
    return datetime.fromtimestamp(unix, tz=timezone.utc)


def _write_group(cur: psycopg.Cursor, packet_hash: str, msg: decode.GroupMessage, keep_text: bool,
                 update: bool = False) -> None:
    text = msg.text if keep_text else None
    values = (msg.payload_type, msg.channel_hash, msg.channel, msg.decrypted, msg.sender, text,
              len(msg.text) if msg.text is not None else None, _timestamp(msg.msg_time), msg.data_type,
              msg.ciphertext_len, msg.decrypted and msg.text is not None and not keep_text)
    if update:
        cur.execute(
            "UPDATE channel_message SET (payload_type, channel_hash, channel, decrypted, sender_name, text, text_len,"
            " msg_time, data_type, ciphertext_len, text_removed) = (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "WHERE packet_hash = %s", values + (packet_hash,))
    else:
        cur.execute(
            "INSERT INTO channel_message (payload_type, channel_hash, channel, decrypted, sender_name, text, text_len,"
            " msg_time, data_type, ciphertext_len, text_removed, packet_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (packet_hash) DO NOTHING",
            values + (packet_hash,))


def _restore_group(cur: psycopg.Cursor, packet_hash: str, pkt: decode.Packet, saved) -> None:
    """A redacted raw record carries what remove_old_texts saved: who, when, which channel, how long."""
    if pkt.payload_type not in decode.GROUP_TYPES or not isinstance(saved, dict) or not pkt.payload:
        return
    cur.execute(
        "INSERT INTO channel_message (packet_hash, payload_type, channel_hash, channel, decrypted, sender_name,"
        " text_len, msg_time, data_type, ciphertext_len, text_removed) VALUES (%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (packet_hash) DO NOTHING",
        (packet_hash, pkt.payload_type, f"{pkt.payload[0]:02x}", saved.get("channel"), saved.get("sender_name"),
         saved.get("text_len"), saved.get("msg_time"), saved.get("data_type"), saved.get("ciphertext_len") or 0,
         saved.get("text_len") is not None))


# --- nodes ---

def _position(lat: float | None, lon: float | None) -> str | None:
    return None if lat is None or lon is None else f"{lat:.6f},{lon:.6f}"


def _upsert_node(cur: psycopg.Cursor, a: decode.Advert, seen: datetime) -> None:
    """Fields missing from an advert keep their previous value; every change goes to node_history."""
    row = cur.execute("SELECT name, role, lat, lon FROM node WHERE pubkey = %s FOR UPDATE", (a.pubkey,)).fetchone()
    if row is None:
        cur.execute("INSERT INTO node (pubkey, name, role, lat, lon, first_seen, last_seen) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)", (a.pubkey, a.name, a.role, a.lat, a.lon, seen, seen))
        return
    name, role, lat, lon = row
    new_name = a.name if a.name is not None else name
    new_role = a.role if a.role is not None else role
    new_lat, new_lon = (a.lat, a.lon) if a.lat is not None else (lat, lon)
    changes = [
        ("name", name, new_name),
        ("role", None if role is None else str(role), None if new_role is None else str(new_role)),
        ("position", _position(lat, lon), _position(new_lat, new_lon)),
    ]
    for field_name, old, new in changes:
        if old != new:
            cur.execute("INSERT INTO node_history (pubkey, changed_at, field, old, new) VALUES (%s,%s,%s,%s,%s)",
                        (a.pubkey, seen, field_name, old, new))
    cur.execute("UPDATE node SET name=%s, role=%s, lat=%s, lon=%s, advert_count = advert_count + 1, "
                "first_seen = LEAST(first_seen, %s), last_seen = GREATEST(last_seen, %s) WHERE pubkey = %s",
                (new_name, new_role, new_lat, new_lon, seen, seen, a.pubkey))


# --- one raw record ---

def _number(value, cast):
    try:
        return cast(float(value))
    except (TypeError, ValueError):
        return None


def _received_at(body: dict, ingested_at: datetime) -> datetime:
    try:
        t = datetime.fromisoformat(body["timestamp"])
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return ingested_at


def record_error(cur: psycopg.Cursor, raw_id: int, reason: str) -> None:
    cur.execute("INSERT INTO decode_error (raw_id, error, decoder_version) VALUES (%s,%s,%s) "
                "ON CONFLICT (raw_id) DO NOTHING", (raw_id, reason, DECODER_VERSION))


def store_raw(cur: psycopg.Cursor, raw_id: int, ingested_at: datetime, topic: str, body: dict | None,
              keys: ChannelKeys, text_retention: timedelta, now: datetime | None = None) -> str:
    """Decode one raw_reception row into the decoded layer. Returns "ok", "duplicate" or the error reason."""
    if not isinstance(body, dict):
        record_error(cur, raw_id, "not_json_object")
        return "not_json_object"
    raw_hex = body.get("raw")
    redacted = bool(body.get("redacted"))
    try:
        if not isinstance(raw_hex, str):
            raise DecodeError("no_raw")
        pkt = decode.parse_packet(raw_hex)
        observer_hash = str(body.get("hash") or "").upper()
        if redacted and not observer_hash:
            # The payload was cut out after the retention period, so the hash cannot be computed again.
            raise DecodeError("redacted_without_hash")
    except DecodeError as e:
        record_error(cur, raw_id, str(e))
        return str(e)

    packet_hash = observer_hash if redacted else pkt.packet_hash
    hash_ok = None if redacted or not observer_hash else observer_hash == pkt.packet_hash
    received_at = _received_at(body, ingested_at)
    parts = topic.split("/")
    observer_id = str(body.get("origin_id") or (parts[2] if len(parts) > 2 else "unknown")).lower()
    packet_len = _number(body.get("len"), int) or len(raw_hex) // 2

    cur.execute(
        "INSERT INTO reception (id, received_at, observer_id, packet_hash, route_type, snr, rssi, packet_len, path,"
        " hop_count, prefix_size, last_hop, transport_codes, hash_ok, decoder_version) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
        (raw_id, received_at, observer_id, packet_hash, pkt.route_type, _number(body.get("SNR"), float),
         _number(body.get("RSSI"), int), packet_len, pkt.path, pkt.hop_count, pkt.prefix_size, pkt.last_hop,
         pkt.transport_codes.hex() if pkt.transport_codes else None, hash_ok, DECODER_VERSION))
    if cur.rowcount == 0:
        return "duplicate"

    payload_len = packet_len - decode.payload_offset(raw_hex) if redacted else len(pkt.payload)
    is_new = cur.execute(
        "INSERT INTO packet (hash, first_seen, last_seen, payload_type, payload_len, payload) VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (hash) DO UPDATE SET reception_count = packet.reception_count + 1, "
        "first_seen = LEAST(packet.first_seen, EXCLUDED.first_seen), "
        "last_seen = GREATEST(packet.last_seen, EXCLUDED.last_seen) RETURNING (xmax = 0)",
        (packet_hash, received_at, received_at, pkt.payload_type, payload_len,
         None if redacted else pkt.payload)).fetchone()[0]
    if not is_new:
        return "ok"
    if redacted:
        _restore_group(cur, packet_hash, pkt, body.get("meshca"))
        return "ok"

    now = now or datetime.now(timezone.utc)
    try:
        if pkt.payload_type == decode.PT_ADVERT:
            a = decode.parse_advert(pkt.payload)
            cur.execute("INSERT INTO advert (packet_hash, pubkey, advert_time, role, lat, lon, name, signature_ok) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (packet_hash, a.pubkey, _timestamp(a.advert_time), a.role, a.lat, a.lon, a.name, a.signature_ok))
            if a.signature_ok:
                _upsert_node(cur, a, received_at)
        elif pkt.payload_type in decode.GROUP_TYPES:
            msg = decode.parse_group(pkt.payload_type, pkt.payload, keys)
            cutoff = now - text_retention
            # Age is counted by our own clock: the observer's may be wrong.
            _write_group(cur, packet_hash, msg, ingested_at >= cutoff)
            if msg.full_text:
                _learn_hashtags(cur, keys, msg.full_text, cutoff)
        elif pkt.payload_type in decode.DIRECT_TYPES:
            dst, src = decode.parse_direct(pkt.payload)
            cur.execute("INSERT INTO direct_packet (packet_hash, dst_hash, src_hash) VALUES (%s,%s,%s)",
                        (packet_hash, dst, src))
        elif pkt.payload_type == decode.PT_ANON_REQ:
            dst, sender = decode.parse_anon_request(pkt.payload)
            cur.execute("INSERT INTO anon_request (packet_hash, dst_hash, sender_pubkey) VALUES (%s,%s,%s)",
                        (packet_hash, dst, sender))
    except DecodeError as e:
        # Header and path are fine, only the payload is not: the reception stays, the reason is recorded.
        record_error(cur, raw_id, str(e))
    return "ok"


# --- text retention ---

def remove_old_texts(cur: psycopg.Cursor, text_retention: timedelta, now: datetime | None = None) -> int:
    """Drop decrypted channel texts older than the retention period, together with the ciphertext they can be
    recovered from (in packet and in the raw records). Header, path, signal levels and the hash stay.

    This is the only place that changes raw records, and it cannot be undone. So it is strict about what it takes:
    text messages only (their plain text was checked, a GRP_DATA match rests on a two-byte MAC alone), age by the
    collector clock, and every raw record of the packet that still carries the ciphertext, whenever it arrived.
    """
    cutoff = (now or datetime.now(timezone.utc)) - text_retention
    hashes = [r[0] for r in cur.execute(
        "SELECT c.packet_hash FROM channel_message m JOIN reception c ON c.packet_hash = m.packet_hash "
        "JOIN raw_reception r ON r.id = c.id "
        "WHERE m.decrypted AND m.payload_type = %s AND NOT (r.payload ? 'redacted') "
        "GROUP BY c.packet_hash HAVING max(r.ingested_at) < %s LIMIT 5000", (decode.PT_GRP_TXT, cutoff)).fetchall()]
    if not hashes:
        return 0
    cur.execute("UPDATE channel_message SET text = NULL, text_removed = (text_len IS NOT NULL) "
                "WHERE packet_hash = ANY(%s)", (hashes,))
    # Keep the packet up to and including the one-byte channel hash.
    cur.execute(
        "UPDATE raw_reception r SET payload = r.payload || jsonb_build_object('redacted', true, 'raw', "
        "left(r.payload->>'raw', length(r.payload->>'raw') - 2 * p.payload_len + 2), 'meshca', "
        "jsonb_build_object('channel', m.channel, 'sender_name', m.sender_name, 'text_len', m.text_len, "
        "'msg_time', m.msg_time, 'data_type', m.data_type, 'ciphertext_len', m.ciphertext_len)) "
        "FROM reception c JOIN packet p ON p.hash = c.packet_hash JOIN channel_message m ON m.packet_hash = p.hash "
        "WHERE c.id = r.id AND p.hash = ANY(%s) AND NOT (r.payload ? 'redacted') AND p.payload_len >= 1 "
        "AND length(r.payload->>'raw') >= 2 * p.payload_len", (hashes,))
    cur.execute("UPDATE packet SET payload = NULL WHERE hash = ANY(%s)", (hashes,))
    return len(hashes)
