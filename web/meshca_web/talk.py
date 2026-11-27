"""Who talks to whom, for the map. Pure functions, no I/O.

Two sources:
* direct packets carry one byte of the key of each end. Text messages to a repeater are its remote console, so
  they count as service traffic, like requests, answers and returned paths. The sender is often named by the path fingerprint
  (origin_resolution); the other end is named by its own packets in the same pair, or by being the only known
  node whose key starts with that byte. What stays unnamed is shown as "#xx".
* channel messages carry the sender's name. A reply is a mention "@[name]" or a message within QUICK_S after
  someone else's message, counted only when it happens clearly more often than chance.

Most talking devices are phones without coordinates. They are put next to their "home" repeater: the first
repeater their packets pass, i.e. where they enter the network. That is an area, not a place.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

QUICK_S = 120.0
MENTION = re.compile(r"@\[([^\]]{1,64})\]")
MESSAGE_KINDS = {2}             # TXT_MSG
SERVICE_KINDS = {0, 1, 8}       # REQ, RESPONSE, PATH


@dataclass(frozen=True)
class DirectPacket:
    at: float
    src: str                    # one-byte hash
    dst: str
    kind: int                   # payload type
    src_key: str | None         # named sender (path fingerprint, probability >= 0.7)
    first_hop: str | None       # public key of the first repeater, when known
    hash: str | None = None     # packet hash


@dataclass(frozen=True)
class ChannelMessage:
    at: float
    channel: str
    sender: str
    text: str
    first_hop: str | None
    hash: str | None = None


@dataclass(frozen=True)
class Node:
    name: str | None
    role: str | None
    lat: float | None
    lon: float | None


@dataclass
class Link:
    a: str
    b: str
    messages: int = 0
    service: int = 0
    channel: int = 0
    a_to_b: int = 0
    b_to_a: int = 0
    last: float = 0.0
    channels: set[str] = field(default_factory=set)
    uncertain: bool = False
    items: list[tuple] = field(default_factory=list)    # (time, packet type, from a to b, channel, packet hash)


def _has_coords(n: Node | None) -> bool:
    return n is not None and n.lat is not None and n.lon is not None and not (n.lat == 0 and n.lon == 0)


def _jitter(key: str, scale: float = 0.006) -> tuple[float, float]:
    h = hashlib.sha256(key.encode()).digest()
    return (h[0] / 255 - 0.5) * scale, (h[1] / 255 - 0.5) * scale * 1.7


def build(direct: list[DirectPacket], messages: list[ChannelMessage], nodes: dict[str, Node]) -> dict:
    by_prefix: dict[str, list[str]] = defaultdict(list)
    by_name: dict[str, list[str]] = defaultdict(list)
    for key, n in nodes.items():
        by_prefix[key[:2]].append(key)
        if n.name:
            by_name[n.name].append(key)

    # --- direct packets: name both ends of every pair of hashes ---
    global_votes: dict[str, Counter] = defaultdict(Counter)
    pair_votes: dict[tuple[str, str], Counter] = defaultdict(Counter)       # (hash, the hash it talks to)
    for p in direct:
        if p.src_key:
            global_votes[p.src][p.src_key] += 1
            pair_votes[p.src, p.dst][p.src_key] += 1

    def name_end(h: str, other: str) -> tuple[str, bool]:
        """(identity, certain) of hash h talking to hash other."""
        votes = pair_votes.get((h, other))
        if votes:
            return votes.most_common(1)[0][0], True
        if len(by_prefix.get(h, [])) == 1:
            return by_prefix[h][0], True
        if global_votes.get(h):
            return global_votes[h].most_common(1)[0][0], False
        return "#" + h, False

    links: dict[tuple[str, str], Link] = {}
    homes: dict[str, Counter] = defaultdict(Counter)

    def link(x: str, y: str) -> tuple[Link, bool]:
        a, b = sorted((x, y))
        if (a, b) not in links:
            links[a, b] = Link(a, b)
        return links[a, b], x == a

    for p in direct:
        if p.src == p.dst or p.kind not in MESSAGE_KINDS | SERVICE_KINDS:
            continue
        src, src_ok = name_end(p.src, p.dst)
        dst, dst_ok = name_end(p.dst, p.src)
        if src == dst:
            continue
        l, forward = link(src, dst)
        # A text message to or from a repeater is a command to it (its remote console), not a conversation.
        to_repeater = any((nodes.get(end) or Node(None, None, None, None)).role == "ретранслятор" for end in (src, dst))
        if p.kind in MESSAGE_KINDS and not to_repeater:
            l.messages += 1
        else:
            l.service += 1
        l.a_to_b += forward
        l.b_to_a += not forward
        l.last = max(l.last, p.at)
        l.items.append((p.at, p.kind, forward, None, p.hash))
        l.uncertain = l.uncertain or not (src_ok and dst_ok)
        if p.first_hop:
            homes[src][p.first_hop] += 1

    # --- channels: mentions and quick replies that beat chance ---
    def person(name: str) -> str:
        keys = by_name.get(name, [])
        return keys[0] if len(keys) == 1 else "@" + name

    by_channel: dict[str, list[ChannelMessage]] = defaultdict(list)
    for m in messages:
        by_channel[m.channel].append(m)
        if m.first_hop:
            homes[person(m.sender)][m.first_hop] += 1
    for channel, items in by_channel.items():
        items.sort(key=lambda m: m.at)
        mentions: Counter = Counter()
        quick: Counter = Counter()
        count: Counter = Counter(m.sender for m in items)
        last: dict[tuple[str, str], float] = {}
        said: dict[tuple[str, str], list[ChannelMessage]] = defaultdict(list)       # mentions
        replied: dict[tuple[str, str], list[ChannelMessage]] = defaultdict(list)    # quick replies
        for m in items:
            for name in set(x.strip() for x in MENTION.findall(m.text or "")):
                if name and name != m.sender:
                    mentions[m.sender, name] += 1
                    last[m.sender, name] = m.at
                    said[m.sender, name].append(m)
        for prev, m in zip(items, items[1:]):
            if m.sender != prev.sender and m.at - prev.at <= QUICK_S:
                quick[m.sender, prev.sender] += 1
                last[m.sender, prev.sender] = m.at
                replied[m.sender, prev.sender].append(m)
        span = items[-1].at - items[0].at if len(items) > 1 else 0.0
        for pair in set(mentions) | set(quick):
            source, target = pair
            expected = count[source] * count[target] * QUICK_S / span if span > 0 else 0.0
            significant_quick = quick[pair] if quick[pair] >= 2 and quick[pair] >= 3 * expected else 0
            n = mentions[pair] + significant_quick
            if not n:
                continue
            x, y = person(source), person(target)
            if x == y:
                continue
            l, forward = link(x, y)
            l.channel += n
            l.a_to_b += n if forward else 0
            l.b_to_a += 0 if forward else n
            l.channels.add(channel)
            l.last = max(l.last, last[pair])
            seen = {m.at: m for m in said[pair] + (replied[pair] if significant_quick else [])}
            l.items.extend((m.at, 5, forward, channel, m.hash) for _, m in sorted(seen.items()))

    # --- places ---
    ids = sorted({l.a for l in links.values()} | {l.b for l in links.values()})
    out_nodes = []
    for i in ids:
        n = nodes.get(i)
        entry = {"id": i, "label": _label(i, n), "role": n.role if n else None,
                 "kind": "node" if n else ("unknown" if i.startswith("#") else "person"),
                 "lat": None, "lon": None, "placed": None, "home": None}
        if _has_coords(n):
            entry.update(lat=n.lat, lon=n.lon, placed="own")
        else:
            for hop, _ in homes.get(i, Counter()).most_common():
                h = nodes.get(hop)
                if _has_coords(h) and hop != i:
                    dy, dx = _jitter(i)
                    entry.update(lat=h.lat + dy, lon=h.lon + dx, placed="home", home=_label(hop, h))
                    break
        out_nodes.append(entry)
    totals: Counter = Counter()
    for l in links.values():
        for end in (l.a, l.b):
            totals[end] += l.messages + l.service + l.channel
    for entry in out_nodes:
        entry["total"] = totals[entry["id"]]
    return {
        "nodes": out_nodes,
        "links": [{"a": l.a, "b": l.b, "messages": l.messages, "service": l.service, "channel": l.channel,
                   "a_to_b": l.a_to_b, "b_to_a": l.b_to_a, "last": l.last, "channels": sorted(l.channels),
                   "uncertain": l.uncertain,
                   "items": [list(i) for i in sorted(l.items, key=lambda i: -i[0])[:150]]}
                  for l in sorted(links.values(), key=lambda l: (l.a, l.b))],
        "unplaced": sum(1 for e in out_nodes if e["lat"] is None),
    }


def _label(node_id: str, n: Node | None) -> str:
    if node_id.startswith("#"):
        return "? " + node_id[1:]
    if node_id.startswith("@"):
        return node_id[1:]
    return (n.name if n and n.name else None) or node_id[:8]
