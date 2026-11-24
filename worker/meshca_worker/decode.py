"""MeshCore packet decoder: pure functions, no I/O.

Packet layout: header(1) | transport codes(4, routes 0 and 3) | path_len(1) | path | payload.
Payload formats follow docs/payloads.md of meshcore-dev/MeshCore. The path length fallback, the packet hash
and the channel text handling follow payload_decode.py / packet_capture.py of agessaman/meshcore-packet-capture
(MIT licence), so that our hash equals the one the observer publishes.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROUTE_TRANSPORT_FLOOD, ROUTE_FLOOD, ROUTE_DIRECT, ROUTE_TRANSPORT_DIRECT = 0, 1, 2, 3
FLOOD_ROUTES = (ROUTE_TRANSPORT_FLOOD, ROUTE_FLOOD)

PT_REQ, PT_RESPONSE, PT_TXT_MSG, PT_ACK, PT_ADVERT, PT_GRP_TXT, PT_GRP_DATA, PT_ANON_REQ = range(8)
PT_PATH, PT_TRACE, PT_MULTIPART, PT_CONTROL, PT_RAW_CUSTOM = 8, 9, 10, 11, 15
DIRECT_TYPES = (PT_REQ, PT_RESPONSE, PT_TXT_MSG, PT_PATH)
GROUP_TYPES = (PT_GRP_TXT, PT_GRP_DATA)

MAX_PATH_BYTES = 64

ADV_TYPE_MASK, ADV_LATLON, ADV_FEAT1, ADV_FEAT2, ADV_NAME = 0x0F, 0x10, 0x20, 0x40, 0x80

# The fixed key of the default "Public" channel (not the hashtag derivation of "#public").
PUBLIC_CHANNEL_KEY = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")
HASHTAG_RE = re.compile(r"(?<![\w#])#([a-z0-9][a-z0-9-]{1,30})(?![\w-])")


class DecodeError(ValueError):
    """The packet cannot be decoded; the message is a short stable reason code."""


@dataclass(frozen=True)
class Packet:
    route_type: int
    payload_type: int
    version: int
    transport_codes: bytes | None
    path: list[str]             # lowercase hex, one element per hop
    prefix_size: int
    payload: bytes
    packet_hash: str            # 16 uppercase hex characters, same as the observer publishes

    @property
    def hop_count(self) -> int:
        return len(self.path)

    @property
    def last_hop(self) -> str | None:
        """The repeater the observer heard. Only a flood path lists the hops already passed."""
        return self.path[-1] if self.path and self.route_type in FLOOD_ROUTES else None


def _path_layout(path_len_byte: int) -> tuple[int, int]:
    """Return (path length in bytes, bytes per hop). Low 6 bits: hop count; high 2 bits: bytes per hop minus one.

    Size mode 3 is reserved and an oversized value is invalid: both fall back to the legacy reading
    (the byte is the path length, one byte per hop), exactly as the observer does.
    """
    hops, size = path_len_byte & 0x3F, (path_len_byte >> 6) + 1
    if size == 4 or hops * size > MAX_PATH_BYTES:
        return path_len_byte, 1
    return hops * size, size


def packet_hash(payload_type: int, path_len_byte: int, payload: bytes) -> str:
    """Packet::calculatePacketHash of the firmware: the path is not part of it, except its length for TRACE."""
    h = hashlib.sha256(bytes([payload_type]))
    if payload_type == PT_TRACE:
        h.update(path_len_byte.to_bytes(2, "little"))
    h.update(payload)
    return h.hexdigest()[:16].upper()


def parse_packet(raw_hex: str) -> Packet:
    try:
        data = bytes.fromhex(raw_hex)
    except (ValueError, TypeError):
        raise DecodeError("raw_not_hex") from None
    if len(data) < 2:
        raise DecodeError("too_short")

    header = data[0]
    route_type, payload_type, version = header & 0x03, (header >> 2) & 0x0F, header >> 6
    if version != 0:
        raise DecodeError(f"unknown_version_{version}")

    offset = 1
    transport = None
    if route_type in (ROUTE_TRANSPORT_FLOOD, ROUTE_TRANSPORT_DIRECT):
        transport = data[1:5]
        offset = 5
    if len(data) <= offset:
        raise DecodeError("no_path_length")

    path_len_byte = data[offset]
    offset += 1
    path_bytes_len, size = _path_layout(path_len_byte)
    if len(data) < offset + path_bytes_len:
        raise DecodeError("path_longer_than_packet")
    path_bytes = data[offset:offset + path_bytes_len]
    payload = data[offset + path_bytes_len:]

    return Packet(
        route_type=route_type,
        payload_type=payload_type,
        version=version,
        transport_codes=transport,
        path=[path_bytes[i:i + size].hex() for i in range(0, len(path_bytes), size)],
        prefix_size=size,
        payload=payload,
        packet_hash=packet_hash(payload_type, path_len_byte, payload),
    )


def payload_offset(raw_hex: str) -> int:
    """Byte offset of the payload inside the packet (used when the payload was cut out of a raw record)."""
    data = bytes.fromhex(raw_hex)
    if len(data) < 2:
        raise DecodeError("too_short")
    offset = 5 if data[0] & 0x03 in (ROUTE_TRANSPORT_FLOOD, ROUTE_TRANSPORT_DIRECT) else 1
    if len(data) <= offset:
        raise DecodeError("no_path_length")
    return offset + 1 + _path_layout(data[offset])[0]


# --- ADVERT ---

@dataclass(frozen=True)
class Advert:
    pubkey: str
    advert_time: int
    role: int | None
    lat: float | None
    lon: float | None
    name: str | None
    signature_ok: bool


def parse_advert(payload: bytes) -> Advert:
    """pubkey(32) | timestamp(4) | signature(64) | app data: flags(1) [lat(4) lon(4)] [feat1(2)] [feat2(2)] [name]."""
    if len(payload) < 100:
        raise DecodeError("advert_too_short")
    pubkey, timestamp, signature, app = payload[:32], payload[32:36], payload[36:100], payload[100:]

    role = lat = lon = name = None
    if app:
        flags = app[0]
        role = flags & ADV_TYPE_MASK
        i = 1
        if flags & ADV_LATLON and len(app) >= i + 8:
            lat = int.from_bytes(app[i:i + 4], "little", signed=True) / 1e6
            lon = int.from_bytes(app[i + 4:i + 8], "little", signed=True) / 1e6
            i += 8
            if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
                lat = lon = None                    # 0,0 is "not set", anything out of range is garbage
        if flags & ADV_FEAT1:
            i += 2
        if flags & ADV_FEAT2:
            i += 2
        if flags & ADV_NAME and len(app) > i:
            name = app[i:].decode("utf-8", errors="replace").replace("\x00", "").strip() or None

    try:
        Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, pubkey + timestamp + app)
        signature_ok = True
    except (InvalidSignature, ValueError):
        signature_ok = False

    return Advert(pubkey.hex(), int.from_bytes(timestamp, "little"), role, lat, lon, name, signature_ok)


# --- channels (GRP_TXT, GRP_DATA) ---

def hashtag_key(name: str) -> bytes:
    """Key of a hashtag channel: the first 16 bytes of SHA-256 of the lowercased "#name"."""
    name = name if name.startswith("#") else "#" + name
    return hashlib.sha256(name.lower().encode("utf-8")).digest()[:16]


def channel_hash(key: bytes) -> str:
    return f"{hashlib.sha256(key).digest()[0]:02x}"


def find_hashtags(text: str) -> set[str]:
    """Channel names mentioned in a message, as "#name"."""
    return {"#" + m for m in HASHTAG_RE.findall(text.lower())}


@dataclass
class ChannelKeys:
    """Known channel keys by the one-byte channel hash a packet carries (several keys may share a hash)."""
    by_hash: dict[str, list[tuple[bytes, str]]] = field(default_factory=dict)
    names: set[str] = field(default_factory=set)

    def add(self, name: str, key: bytes) -> bool:
        if name in self.names or len(key) != 16:
            return False
        self.names.add(name)
        self.by_hash.setdefault(channel_hash(key), []).append((key, name))
        return True

    def reload(self, other: "ChannelKeys") -> None:
        self.by_hash, self.names = other.by_hash, other.names

    def candidates(self, hash_hex: str) -> list[tuple[bytes, str]]:
        return self.by_hash.get(hash_hex, [])


@dataclass(frozen=True)
class GroupMessage:
    payload_type: int
    channel_hash: str
    ciphertext_len: int
    channel: str | None = None
    decrypted: bool = False
    msg_time: int | None = None
    sender: str | None = None
    text: str | None = None         # GRP_TXT: the message without the "sender: " part
    full_text: str | None = None    # GRP_TXT: the message as sent
    data_type: int | None = None    # GRP_DATA


def _decrypt(ciphertext: bytes, mac: bytes, key: bytes) -> bytes | None:
    """MAC is HMAC-SHA256(key + 16 zero bytes, ciphertext)[:2]; the cipher is AES-128-ECB without padding."""
    if len(ciphertext) < 16 or len(ciphertext) % 16:
        return None
    if not hmac.compare_digest(hmac.new(key + bytes(16), ciphertext, hashlib.sha256).digest()[:2], mac):
        return None
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return d.update(ciphertext) + d.finalize()


def parse_group(payload_type: int, payload: bytes, keys: ChannelKeys | None = None) -> GroupMessage:
    """channel hash(1) | MAC(2) | ciphertext. Plain text: GRP_TXT timestamp(4) flags(1) "sender: text" NUL...;
    GRP_DATA data type(2) data len(1) data."""
    if len(payload) < 3:
        raise DecodeError("group_too_short")
    hash_hex, mac, ciphertext = f"{payload[0]:02x}", payload[1:3], payload[3:]
    base = GroupMessage(payload_type, hash_hex, len(ciphertext))

    for key, name in (keys.candidates(hash_hex) if keys else []):
        plain = _decrypt(ciphertext, mac, key)
        if plain is None:
            continue
        if payload_type == PT_GRP_DATA:
            return GroupMessage(payload_type, hash_hex, len(ciphertext), name, True,
                                data_type=int.from_bytes(plain[0:2], "little"))
        try:
            # The MAC is two bytes, so a wrong key passes once in 65,536: text that is not UTF-8 means "wrong key".
            full = plain[5:].split(b"\x00", 1)[0].decode("utf-8")
        except UnicodeDecodeError:
            continue
        sender, text = None, full
        colon = full.find(": ")
        if 0 < colon < 50 and not any(c in full[:colon] for c in ":[]"):
            sender, text = full[:colon], full[colon + 2:]
        return GroupMessage(payload_type, hash_hex, len(ciphertext), name, True,
                            msg_time=int.from_bytes(plain[0:4], "little"), sender=sender, text=text, full_text=full)
    return base


# --- the rest ---

def parse_direct(payload: bytes) -> tuple[str, str]:
    """REQ, RESPONSE, TXT_MSG, PATH: destination hash(1) | source hash(1) | MAC(2) | ciphertext."""
    if len(payload) < 2:
        raise DecodeError("direct_too_short")
    return f"{payload[0]:02x}", f"{payload[1]:02x}"


def parse_anon_request(payload: bytes) -> tuple[str, str]:
    """ANON_REQ: destination hash(1) | sender public key(32) | MAC(2) | ciphertext."""
    if len(payload) < 33:
        raise DecodeError("anon_req_too_short")
    return f"{payload[0]:02x}", payload[1:33].hex()
