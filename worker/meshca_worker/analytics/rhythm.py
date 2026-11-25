"""Time patterns of one sender: how bursty it is, whether it repeats with a fixed period, when it is active.
Pure functions, no I/O. Times are seconds (Unix time); local hour and weekday are given by the caller.

Burstiness  B = (sigma - mu) / (sigma + mu) of the gaps between events: -1 a clock, 0 random (Poisson), toward 1
            bursts separated by long silence.
Period      Rayleigh test on a circle of length T: every event becomes the angle 2*pi*t/T, R is the length of the
            mean vector (1 = all at the same phase). Candidates for T come from the gaps themselves (the median gap
            and its fractions, because a missed event doubles a gap), each refined on a fine grid. Scanning many T
            always finds some phase alignment, so the best T of the first half of the data must also be significant
            on the second half, where it was not searched: only then the period counts as confirmed.
Profile     168 hours of the week, the share of events at night.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

METHOD_VERSION = 1
MIN_EVENTS = 30                 # fewer events (bursts counted once): written but not judged (SPEC 7.5)
MIN_PERIOD_S = 600.0            # shorter periods are not searched
BURST_S = 300.0                 # events closer than this to the previous one belong to the same burst
MAX_GRID = 1500                 # grid points per candidate period; coarser beyond that
MAX_EVENTS = 1000               # the period is searched on the latest events only, to bound the cost
SHORTER_MARGIN = 0.05           # how much better a shorter candidate period must align
CONFIRM_P = 0.01                # Rayleigh p-value the period must reach on the half it was not found on
NIGHT_HOURS = range(0, 6)


@dataclass
class Rhythm:
    events: int
    span_s: float
    median_gap_s: float | None = None
    burstiness: float | None = None
    period_s: float | None = None
    period_strength: float | None = None    # R on all data, 0..1
    period_p: float | None = None           # Rayleigh p-value on the confirming half
    period_confirmed: bool = False
    night_share: float | None = None
    profile: list[int] = field(default_factory=lambda: [0] * 168)   # Monday 00h first
    judged: bool = False


def burstiness(gaps: list[float]) -> float | None:
    if len(gaps) < 2:
        return None
    mu, sigma = statistics.fmean(gaps), statistics.pstdev(gaps)
    return None if mu + sigma == 0 else (sigma - mu) / (sigma + mu)


def rayleigh(times: list[float], period: float) -> tuple[float, float]:
    """(R, p-value) of the events folded on a circle of the given length."""
    n = len(times)
    if n == 0:
        return 0.0, 1.0
    w = 2 * math.pi / period
    c = sum(math.cos(w * t) for t in times)
    s = sum(math.sin(w * t) for t in times)
    r = math.hypot(c, s) / n
    if n < 10:
        return r, min(1.0, math.exp(-n * r * r))
    # Zar's approximation of the exact p-value, good from n of about 10.
    p = math.exp(math.sqrt(1 + 4 * n + 4 * (n * n - (n * r) ** 2)) - (1 + 2 * n))
    return r, min(1.0, max(p, 0.0))


def _grid(center: float, span: float) -> list[float]:
    """Periods within 30% of the center, close enough that the phase over the span drifts by at most 1/8 turn
    (coarser when that would take more than MAX_GRID points)."""
    lo, hi = center * 0.7, center * 1.3
    needed = 0.6 * center * 8 * span / (center * center)
    coarse = max(1.0, needed / MAX_GRID)
    out, t = [], lo
    while t <= hi:
        out.append(t)
        t *= 1 + max(coarse * t / (8 * span), 1e-6)
    return out


def _thinned(times: list[float]) -> list[float]:
    """One event per burst: a retry or a reply minutes after an event lands at the same phase and would make the
    test count one moment twice, as if it were twice as certain."""
    out: list[float] = []
    for t in times:
        if not out or t - out[-1] >= BURST_S:
            out.append(t)
    return out


def best_period(times: list[float]) -> tuple[float, float] | None:
    """(T, R) maximising R over candidates derived from the gaps; None when there is too little to search."""
    times = _thinned(sorted(times))[-MAX_EVENTS:]
    if len(times) < 3:
        return None
    span = times[-1] - times[0]
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not gaps or span <= 0:
        return None
    median = statistics.median(gaps)
    best: tuple[float, float] | None = None
    for k in (1, 2, 3, 4):
        center = median / k
        if center < MIN_PERIOD_S or center * 2 > span:
            continue
        found = max(((t, rayleigh(times, t)[0]) for t in _grid(center, span)), key=lambda x: x[1])
        if best is None:
            best = found
            continue
        # Events that repeat every T line up just as well on T/2, T/3...: when the longer period is a whole multiple
        # of the shorter one, the shorter has to be clearly better. A real shorter period with missed events is:
        # folded on the longer one, its events fall at different phases.
        ratio = best[0] / found[0]
        harmonic = abs(ratio - round(ratio)) < 0.03 * ratio
        if found[1] > best[1] + (SHORTER_MARGIN if harmonic else 0.0):
            best = found
    return best


def analyse(times: list[float], local_slots: list[tuple[int, int]]) -> Rhythm:
    """times: event times in seconds; local_slots: (ISO weekday 1-7, hour 0-23) of each event in local time."""
    times = sorted(times)
    result = Rhythm(events=len(times), span_s=(times[-1] - times[0]) if times else 0.0)
    for weekday, hour in local_slots:
        result.profile[(weekday - 1) * 24 + hour] += 1
    if local_slots:
        result.night_share = sum(1 for _, h in local_slots if h in NIGHT_HOURS) / len(local_slots)
    gaps = [b - a for a, b in zip(times, times[1:])]
    if gaps:
        result.median_gap_s = statistics.median(gaps)
    result.burstiness = burstiness(gaps)
    result.judged = len(_thinned(times)) >= MIN_EVENTS        # bursts count once

    found = best_period(times)
    if found:
        result.period_s, result.period_strength = found
    if result.judged:
        half = len(times) // 2
        first, second = times[:half], times[half:]
        found_first = best_period(first)
        if found_first:
            _, p = rayleigh(_thinned(second), found_first[0])
            result.period_p = p
            # Confirmed when the period found on the first half holds on the second and agrees with the full-data one.
            agrees = result.period_s is not None and abs(found_first[0] - result.period_s) <= 0.05 * result.period_s
            result.period_confirmed = p < CONFIRM_P and agrees
    return result
