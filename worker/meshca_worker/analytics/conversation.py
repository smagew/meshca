"""Who talks to whom. Pure functions, no I/O.

Direct packets (REQ, RESPONSE, TXT_MSG, PATH) carry only one byte of the key of each end, so a "pair" is a pair of
those bytes, not of nodes. Packets of one pair with pauses shorter than SESSION_GAP_S form a session. A packet
answered from the other side within REPLY_WINDOW_S gives a response time.

Channel messages carry the sender's name in the text. A reply is visible two ways:
* a mention "@[name]" at the start or inside the text: certain;
* a message within QUICK_S after someone else's message in the same channel: may be chance. The expected number
  of such follows if both wrote at random times is n_a * n_b * QUICK_S / span of the channel; lift = seen / expected.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

METHOD_VERSION = 1
SESSION_GAP_S = 600.0
REPLY_WINDOW_S = 30.0
QUICK_S = 120.0
MENTION = re.compile(r"@\[([^\]]{1,64})\]")


@dataclass(frozen=True)
class Direct:
    at: float           # seconds
    src: str            # one-byte hash, hex
    dst: str
    kind: str           # REQ, RESPONSE, TXT_MSG, PATH


@dataclass
class Session:
    a: str
    b: str              # a < b
    start: float
    end: float
    packets: int = 0
    a_to_b: int = 0
    b_to_a: int = 0
    kinds: list[str] = field(default_factory=list)          # in order, with the direction: "a:REQ", "b:RESPONSE"
    response_s: list[float] = field(default_factory=list)   # delays of answers from the other side

    @property
    def median_response_s(self) -> float | None:
        return statistics.median(self.response_s) if self.response_s else None


def sessions(packets: list[Direct]) -> list[Session]:
    by_pair: dict[tuple[str, str], list[Direct]] = defaultdict(list)
    for p in packets:
        if p.src != p.dst:
            by_pair[tuple(sorted((p.src, p.dst)))].append(p)
    out: list[Session] = []
    for (a, b), items in sorted(by_pair.items()):
        items.sort(key=lambda p: p.at)
        current: Session | None = None
        for p in items:
            if current is None or p.at - current.end > SESSION_GAP_S:
                current = Session(a, b, p.at, p.at)
                out.append(current)
                last: dict[str, float] = {}             # last packet time per sender within the session
            # An answer: the other side sent something shortly before.
            other = b if p.src == a else a
            if other in last and p.at - last[other] <= REPLY_WINDOW_S:
                current.response_s.append(p.at - last[other])
                del last[other]                         # one answer per question
            last[p.src] = p.at
            current.end = p.at
            current.packets += 1
            if p.src == a:
                current.a_to_b += 1
            else:
                current.b_to_a += 1
            current.kinds.append(("a:" if p.src == a else "b:") + p.kind)
    return out


@dataclass(frozen=True)
class ChannelMessage:
    at: float
    channel: str
    sender: str
    text: str


@dataclass
class Link:
    channel: str
    source: str         # who wrote
    target: str         # to whom
    mentions: int = 0
    quick: int = 0      # messages within QUICK_S after target's message
    expected: float = 0.0

    @property
    def lift(self) -> float | None:
        return self.quick / self.expected if self.expected > 0 else None


def mentions(text: str) -> list[str]:
    return [m.strip() for m in MENTION.findall(text or "") if m.strip()]


def channel_links(messages: list[ChannelMessage]) -> list[Link]:
    links: dict[tuple[str, str, str], Link] = {}

    def link(channel: str, source: str, target: str) -> Link:
        key = (channel, source, target)
        if key not in links:
            links[key] = Link(channel, source, target)
        return links[key]

    by_channel: dict[str, list[ChannelMessage]] = defaultdict(list)
    for m in messages:
        by_channel[m.channel].append(m)
    for channel, items in by_channel.items():
        items.sort(key=lambda m: m.at)
        counts: dict[str, int] = defaultdict(int)
        for m in items:
            counts[m.sender] += 1
            for name in set(mentions(m.text)):
                if name != m.sender:
                    link(channel, m.sender, name).mentions += 1
        # Quick follows: only the latest message of another sender counts, so a burst is not counted many times.
        for prev, m in zip(items, items[1:]):
            if m.sender != prev.sender and m.at - prev.at <= QUICK_S:
                link(channel, m.sender, prev.sender).quick += 1
        span = items[-1].at - items[0].at if len(items) > 1 else 0.0
        if span > 0:
            for (ch, source, target), l in links.items():
                if ch == channel and target in counts:
                    l.expected = counts[source] * counts[target] * QUICK_S / span
    return sorted(links.values(), key=lambda l: (l.channel, -(l.mentions + l.quick), l.source, l.target))
