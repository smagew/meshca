import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from meshca_worker.analytics.rhythm import analyse, best_period, burstiness, rayleigh  # noqa: E402

HOUR = 3600.0
START = 1_700_000_000.0


def slots(times):
    # Synthetic local time: hours since START, weekdays from Monday.
    return [(int((t - START) // 86400) % 7 + 1, int((t - START) // HOUR) % 24) for t in times]


def test_burstiness_of_a_clock_poisson_and_bursts():
    assert burstiness([60.0] * 20) == -1.0
    rng = random.Random(1)
    poisson = [rng.expovariate(1 / 60) for _ in range(5000)]
    assert abs(burstiness(poisson)) < 0.05
    bursts = ([1.0] * 50 + [10_000.0]) * 10
    assert burstiness(bursts) > 0.5
    assert burstiness([5.0]) is None


def test_rayleigh_sees_phase_alignment_only_at_the_right_period():
    times = [START + i * 3 * HOUR for i in range(100)]
    r, p = rayleigh(times, 3 * HOUR)
    assert r > 0.999 and p < 1e-10
    r_off, p_off = rayleigh(times, 3.37 * HOUR)
    assert r_off < 0.3 and p_off > 1e-3


def test_period_with_jitter_and_missed_events_is_found_and_confirmed():
    rng = random.Random(2)
    # Every 3 hours with a few minutes of jitter; a third of the events never reached the observer.
    times = [START + i * 3 * HOUR + rng.gauss(0, 120) for i in range(240) if rng.random() > 0.33]
    result = analyse(times, slots(times))
    assert result.judged
    assert abs(result.period_s - 3 * HOUR) < 0.01 * 3 * HOUR
    assert result.period_confirmed
    assert result.period_strength > 0.8


def test_exact_timer_is_not_mistaken_for_its_fraction():
    # Every hour to the second: folding on 15 or 30 minutes aligns just as well, the answer must still be 60.
    times = [START + i * HOUR for i in range(40)]
    result = analyse(times, slots(times))
    assert abs(result.period_s - HOUR) < 0.01 * HOUR
    assert result.burstiness == -1.0


def test_shorter_real_period_with_many_misses_is_found():
    rng = random.Random(4)
    # Every 20 minutes, but most events are lost, so the median gap is about an hour.
    times = [START + i * 1200 for i in range(600) if rng.random() < 0.3]
    assert abs(analyse(times, slots(times)).period_s - 1200) < 12


def test_random_events_have_no_confirmed_period():
    rng = random.Random(3)
    unconfirmed = 0
    for seed in range(10):
        rng.seed(seed)
        t, times = START, []
        for _ in range(120):
            t += rng.expovariate(1 / (2 * HOUR))
            times.append(t)
        unconfirmed += not analyse(times, slots(times)).period_confirmed
    assert unconfirmed >= 9


def test_a_burst_counts_once():
    # 20 moments, each with two quick retries: still too few for a verdict, and the period is the moments' one.
    times = [START + i * 2 * HOUR + d for i in range(20) for d in (0, 60, 150)]
    result = analyse(times, slots(times))
    assert result.events == 60 and not result.judged
    assert abs(best_period(times)[0] - 2 * HOUR) < 0.01 * 2 * HOUR


def test_few_events_are_not_judged():
    times = [START + i * HOUR for i in range(10)]
    result = analyse(times, slots(times))
    assert not result.judged and not result.period_confirmed
    assert result.events == 10 and result.median_gap_s == HOUR


def test_profile_and_night_share():
    # Active only at 02h and 14h local every day for 2 weeks.
    times = [START + d * 86400 + h * HOUR for d in range(14) for h in (2, 14)]
    result = analyse(times, slots(times))
    assert sum(result.profile) == 28
    assert result.profile[2] == 2 and result.profile[14] == 2 and result.profile[3] == 0
    assert result.night_share == 0.5


def test_empty_and_tiny_inputs():
    assert analyse([], []).events == 0
    assert best_period([START]) is None
    assert best_period([START, START]) is None
