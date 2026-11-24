"""Decoder tests on synthetic packets only: captured traffic never goes to the repository."""
import hashlib
import hmac
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from meshca_worker import decode  # noqa: E402
from meshca_worker.decode import ChannelKeys, DecodeError  # noqa: E402


def build(route, ptype, path=b"", size=1, payload=b"", transport=b""):
    hops = len(path) // size
    return (bytes([route | ptype << 2]) + transport + bytes([hops | (size - 1) << 6]) + path + payload).hex()


def encrypt(key, plain):
    plain += bytes(-len(plain) % 16)
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    ciphertext = e.update(plain) + e.finalize()
    return hmac.new(key + bytes(16), ciphertext, hashlib.sha256).digest()[:2] + ciphertext


def group_payload(key, text, timestamp=1795000000):
    return bytes.fromhex(decode.channel_hash(key)) + encrypt(key, timestamp.to_bytes(4, "little") + b"\x00" + text.encode())


def advert_payload(name="Test Node", role=2, position=(10.5, 20.25), timestamp=1795000000):
    private = Ed25519PrivateKey.generate()
    pubkey = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    flags = role | (0x10 if position else 0) | (0x80 if name else 0)
    app = bytes([flags])
    if position:
        app += int(position[0] * 1e6).to_bytes(4, "little", signed=True) + int(position[1] * 1e6).to_bytes(4, "little", signed=True)
    app += (name or "").encode()
    ts = timestamp.to_bytes(4, "little")
    return pubkey + ts + private.sign(pubkey + ts + app) + app, pubkey.hex()


def test_flood_packet_with_one_byte_path():
    p = decode.parse_packet(build(1, 2, path=bytes.fromhex("a1b2c3"), payload=b"\x10\x20rest"))
    assert (p.route_type, p.payload_type, p.prefix_size) == (1, 2, 1)
    assert p.path == ["a1", "b2", "c3"] and p.hop_count == 3 and p.last_hop == "c3"
    assert p.payload == b"\x10\x20rest" and p.transport_codes is None


def test_two_and_three_byte_prefixes():
    p = decode.parse_packet(build(1, 5, path=bytes.fromhex("a1b2c3d4"), size=2, payload=b"xyz"))
    assert p.path == ["a1b2", "c3d4"] and p.prefix_size == 2 and p.last_hop == "c3d4"
    p = decode.parse_packet(build(1, 5, path=bytes.fromhex("a1b2c3d4e5f6"), size=3, payload=b"xyz"))
    assert p.path == ["a1b2c3", "d4e5f6"] and p.prefix_size == 3


def test_transport_codes_and_direct_route():
    p = decode.parse_packet(build(0, 4, path=b"\x01", payload=b"p", transport=b"\xaa\xbb\xcc\xdd"))
    assert p.transport_codes == b"\xaa\xbb\xcc\xdd" and p.path == ["01"] and p.payload == b"p"
    direct = decode.parse_packet(build(2, 0, path=b"\x01\x02", payload=b"p"))
    assert direct.last_hop is None, "a direct path lists hops to go, not hops passed"
    assert decode.parse_packet(build(1, 0, payload=b"p")).last_hop is None


def test_hash_ignores_path_and_route():
    a = decode.parse_packet(build(1, 5, path=b"\x01", payload=b"same payload"))
    b = decode.parse_packet(build(1, 5, path=b"\x01\x02\x03\x04", size=2, payload=b"same payload"))
    assert a.packet_hash == b.packet_hash == hashlib.sha256(b"\x05same payload").hexdigest()[:16].upper()
    assert decode.parse_packet(build(1, 6, path=b"\x01", payload=b"same payload")).packet_hash != a.packet_hash


def test_trace_hash_includes_path_length():
    one = decode.parse_packet(build(2, 9, path=b"\x01", payload=b"trace"))
    two = decode.parse_packet(build(2, 9, path=b"\x01\x02", payload=b"trace"))
    assert one.packet_hash != two.packet_hash
    assert one.packet_hash == hashlib.sha256(b"\x09\x01\x00trace").hexdigest()[:16].upper()


def test_reserved_size_mode_falls_back_to_legacy_length():
    # 0xC2: size mode 3 is reserved, the observer reads the byte as a plain length; here the packet is too short.
    with pytest.raises(DecodeError, match="path_longer_than_packet"):
        decode.parse_packet("05c2aabb")


@pytest.mark.parametrize("raw, reason", [
    ("zz", "raw_not_hex"), ("05", "too_short"), ("0503aa", "path_longer_than_packet"),
    ("4500", "unknown_version_1"), ("00aabbccdd", "no_path_length"),
])
def test_malformed_packets(raw, reason):
    with pytest.raises(DecodeError, match=reason):
        decode.parse_packet(raw)


def test_payload_offset():
    assert decode.payload_offset(build(1, 5, path=b"\x01\x02", payload=b"x")) == 4
    assert decode.payload_offset(build(0, 5, path=b"\x01\x02\x03\x04", size=2, payload=b"x", transport=bytes(4))) == 10


def test_advert_with_valid_signature():
    payload, pubkey = advert_payload()
    a = decode.parse_advert(payload)
    assert a.pubkey == pubkey and a.signature_ok and a.role == 2 and a.name == "Test Node"
    assert a.lat == pytest.approx(10.5) and a.lon == pytest.approx(20.25) and a.advert_time == 1795000000


def test_advert_with_changed_name_fails_signature():
    payload, _ = advert_payload(name="Honest")
    a = decode.parse_advert(payload[:-1] + b"X")
    assert not a.signature_ok and a.name == "HonesX"


def test_advert_without_position_and_zero_position():
    assert decode.parse_advert(advert_payload(position=None)[0]).lat is None
    zero = decode.parse_advert(advert_payload(position=(0, 0))[0])
    assert zero.lat is None and zero.lon is None and zero.name == "Test Node"
    with pytest.raises(DecodeError):
        decode.parse_advert(b"short")


def test_channel_keys():
    assert decode.PUBLIC_CHANNEL_KEY.hex() == "8b3387e9c5cdea6ac9e5edbaa115cd72"
    assert decode.hashtag_key("#Test") == decode.hashtag_key("test") == hashlib.sha256(b"#test").digest()[:16]
    assert decode.hashtag_key("#public") != decode.PUBLIC_CHANNEL_KEY
    assert decode.find_hashtags("go to #Town and #my-mesh, not a#b or ##x or #a") == {"#town", "#my-mesh"}


def test_group_text_decrypts_with_known_key():
    keys = ChannelKeys()
    keys.add("Public", decode.PUBLIC_CHANNEL_KEY)
    m = decode.parse_group(5, group_payload(decode.PUBLIC_CHANNEL_KEY, "alice: hello #mesh"), keys)
    assert m.decrypted and m.channel == "Public" and m.sender == "alice" and m.text == "hello #mesh"
    assert m.full_text == "alice: hello #mesh" and m.msg_time == 1795000000 and m.ciphertext_len == 32


def test_group_text_without_key_keeps_only_the_channel_hash():
    key = decode.hashtag_key("#secret")
    m = decode.parse_group(5, group_payload(key, "bob: hi"), ChannelKeys())
    assert not m.decrypted and m.text is None and m.channel_hash == decode.channel_hash(key)


def test_group_text_wrong_key_with_same_hash_is_rejected():
    key = decode.hashtag_key("#real")
    other = next(k for k in (hashlib.sha256(bytes([i, j])).digest()[:16] for i in range(256) for j in range(256))
                 if decode.channel_hash(k) == decode.channel_hash(key) and k != key)
    keys = ChannelKeys()
    keys.add("#other", other)
    assert not decode.parse_group(5, group_payload(key, "bob: hi"), keys).decrypted
    keys.add("#real", key)
    assert decode.parse_group(5, group_payload(key, "bob: hi"), keys).channel == "#real"


def test_group_text_without_sender_and_group_data():
    keys = ChannelKeys()
    keys.add("#x1", decode.hashtag_key("#x1"))
    m = decode.parse_group(5, group_payload(decode.hashtag_key("#x1"), "no sender here"), keys)
    assert m.sender is None and m.text == "no sender here"
    key = decode.hashtag_key("#x1")
    data = bytes.fromhex(decode.channel_hash(key)) + encrypt(key, (0x0102).to_bytes(2, "little") + b"\x03abc")
    d = decode.parse_group(6, data, keys)
    assert d.decrypted and d.data_type == 0x0102 and d.text is None
    with pytest.raises(DecodeError):
        decode.parse_group(5, b"\x01\x02", keys)


def test_direct_and_anon_request():
    assert decode.parse_direct(bytes.fromhex("a1b2ffff00")) == ("a1", "b2")
    assert decode.parse_anon_request(b"\xa1" + bytes(range(32)) + b"\x00\x00") == ("a1", bytes(range(32)).hex())
    with pytest.raises(DecodeError):
        decode.parse_direct(b"\x01")
    with pytest.raises(DecodeError):
        decode.parse_anon_request(bytes(10))
