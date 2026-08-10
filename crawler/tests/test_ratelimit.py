"""Adaptive request pacing -- the crawler's most important safety device.

The goal is the *minimum* crawl time that does not get us throttled or
banned. A fixed rate cannot do that: too fast risks the IP, too slow wastes
hours. So the limiter uses AIMD (additive increase, multiplicative decrease),
the same control law as TCP congestion avoidance -- creep upward while the
host is happy, halve the instant it is not.

Clock and randomness are injected so these tests are deterministic and run
in milliseconds.
"""

import pytest

from ngp.ratelimit import AdaptiveLimiter, RateLimitExceeded


class FakeClock:
    """Monotonic clock that only advances when someone sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, "never sleep a negative duration"
        self.slept.append(seconds)
        self.now += seconds


def limiter(**kw):
    clock = FakeClock()
    kw.setdefault("start", 1.0)
    kw.setdefault("jitter", 0.0)
    kw.setdefault("rand", lambda: 0.5)
    return AdaptiveLimiter(
        sleep=clock.sleep, monotonic=clock.monotonic, **kw
    ), clock


class TestPacing:
    def test_first_request_does_not_wait(self):
        rl, clock = limiter()
        rl.wait()
        assert clock.slept == []

    def test_second_request_waits_the_interval(self):
        rl, clock = limiter(start=1.0)
        rl.wait()
        rl.wait()
        assert clock.slept == [pytest.approx(1.0)]

    def test_faster_rate_waits_less(self):
        rl, clock = limiter(start=2.0)
        rl.wait()
        rl.wait()
        assert clock.slept == [pytest.approx(0.5)]

    def test_no_wait_when_enough_time_already_passed(self):
        rl, clock = limiter(start=1.0)
        rl.wait()
        clock.now += 5.0
        rl.wait()
        assert clock.slept == []


class TestJitter:
    """A request every 1000.0 ms is a robotic signature. Vary it."""

    def test_jitter_lower_bound(self):
        rl, clock = limiter(start=1.0, jitter=0.3, rand=lambda: 0.0)
        rl.wait()
        rl.wait()
        assert clock.slept == [pytest.approx(0.7)]

    def test_jitter_upper_bound(self):
        rl, clock = limiter(start=1.0, jitter=0.3, rand=lambda: 1.0)
        rl.wait()
        rl.wait()
        assert clock.slept == [pytest.approx(1.3)]

    def test_jitter_never_sleeps_a_negative_duration(self):
        # jitter > 1.0 would drive the scale negative without clamping.
        # FakeClock.sleep asserts on that, so completing the test is the check.
        rl, clock = limiter(start=1.0, jitter=2.0, rand=lambda: 0.0)
        rl.wait()
        rl.wait()
        assert all(s >= 0 for s in clock.slept)


class TestAdditiveIncrease:
    """Creep upward while the host is happy, so a long crawl finishes sooner."""

    def test_rate_holds_until_enough_consecutive_successes(self):
        rl, _ = limiter(start=1.0, increase_after=3, increase_by=0.5)
        rl.on_success()
        rl.on_success()
        assert rl.rate == pytest.approx(1.0)

    def test_rate_increases_after_enough_successes(self):
        rl, _ = limiter(start=1.0, increase_after=3, increase_by=0.5)
        for _ in range(3):
            rl.on_success()
        assert rl.rate == pytest.approx(1.5)

    def test_increase_is_additive_not_multiplicative(self):
        rl, _ = limiter(start=1.0, increase_after=1, increase_by=0.5)
        for _ in range(3):
            rl.on_success()
        assert rl.rate == pytest.approx(2.5)

    def test_rate_never_exceeds_the_ceiling(self):
        rl, _ = limiter(start=1.0, ceiling=2.0, increase_after=1, increase_by=0.5)
        for _ in range(50):
            rl.on_success()
        assert rl.rate == pytest.approx(2.0)

    def test_success_streak_resets_after_an_increase(self):
        rl, _ = limiter(start=1.0, increase_after=2, increase_by=0.5)
        rl.on_success()
        rl.on_success()          # -> 1.5, streak resets
        rl.on_success()          # streak = 1, not enough
        assert rl.rate == pytest.approx(1.5)


class TestMultiplicativeDecrease:
    """Back off hard, not gently. A throttle ignored becomes a ban."""

    def test_refusal_halves_the_rate(self):
        rl, _ = limiter(start=2.0, decrease_factor=0.5)
        rl.on_refused()
        assert rl.rate == pytest.approx(1.0)

    def test_decrease_applies_immediately_to_the_next_request(self):
        # The request right after a refusal is the dangerous one. It must not
        # go out at the rate that was just refused.
        rl, clock = limiter(start=2.0)
        rl.wait()                       # schedules the next slot at +0.5s
        rl.on_refused()                 # rate -> 1.0
        rl.wait()
        assert clock.slept == [pytest.approx(1.0)]

    def test_rate_never_drops_below_the_floor(self):
        rl, _ = limiter(start=1.0, floor=0.25, max_consecutive_refusals=99)
        for _ in range(20):
            rl.on_refused()
        assert rl.rate == pytest.approx(0.25)

    def test_refusal_resets_the_success_streak(self):
        rl, _ = limiter(start=1.0, increase_after=2, increase_by=0.5)
        rl.on_success()
        rl.on_refused()                 # rate -> 0.5, streak cleared
        rl.on_success()                 # streak = 1, must not trigger increase
        assert rl.rate == pytest.approx(0.5)

    def test_success_clears_the_consecutive_refusal_count(self):
        rl, _ = limiter(start=1.0, max_consecutive_refusals=2)
        rl.on_refused()
        rl.on_success()
        rl.on_refused()
        rl.on_refused()                 # only 2 in a row -> still alive
        assert rl.rate > 0


class TestRememberedCeiling:
    """AIMD alone thrashes: it climbs back to the rate that was just refused,
    gets refused again, and keeps provoking the host forever. So the rate that
    drew a refusal becomes a remembered ceiling (TCP's ssthresh), and the
    limiter converges just below it instead of oscillating through it."""

    def test_refusal_lowers_the_effective_ceiling(self):
        rl, _ = limiter(start=4.0, ceiling=6.0)
        rl.on_refused()
        assert rl.effective_ceiling < 4.0

    def test_never_climbs_back_to_a_refused_rate(self):
        rl, _ = limiter(start=4.0, ceiling=6.0, increase_after=1, increase_by=0.5)
        rl.on_refused()                      # refused at 4.0
        for _ in range(500):
            rl.on_success()
        assert rl.rate < 4.0

    def test_converges_and_stops_provoking_refusals(self):
        # A host that refuses above 2 req/s should see a burst of refusals
        # during discovery and then essentially none.
        rl, _ = limiter(start=4.0, ceiling=6.0, increase_after=10,
                        increase_by=0.25, max_consecutive_refusals=99)
        early = late = 0
        for i in range(4000):
            if rl.rate > 2.0:
                rl.on_refused()
                if i < 500:
                    early += 1
                else:
                    late += 1
            else:
                rl.on_success()
        assert early > 0, "should discover the limit"
        assert late == 0, f"should stop provoking refusals, got {late}"

    def test_hard_ceiling_still_applies_when_never_refused(self):
        rl, _ = limiter(start=1.0, ceiling=3.0, increase_after=1, increase_by=0.5)
        for _ in range(100):
            rl.on_success()
        assert rl.rate == pytest.approx(3.0)


class TestRetryAfter:
    """When the host states how long to wait, that beats our own guess."""

    def test_retry_after_delays_the_next_request(self):
        rl, clock = limiter(start=1.0)
        rl.on_refused(retry_after=30.0)
        rl.wait()
        assert clock.slept == [pytest.approx(30.0)]

    def test_retry_after_wins_when_longer_than_our_interval(self):
        rl, clock = limiter(start=0.5)   # our own interval would be 2.0s
        rl.on_refused(retry_after=10.0)
        rl.wait()
        assert clock.slept[0] == pytest.approx(10.0)

    def test_our_interval_wins_when_retry_after_is_shorter(self):
        rl, clock = limiter(start=1.0)
        rl.on_refused(retry_after=0.1)   # backed-off interval is 2.0s
        rl.wait()
        assert clock.slept[0] == pytest.approx(2.0)


class TestGivingUp:
    """Distinguish 'being throttled' from 'being blocked'."""

    def test_survives_isolated_refusals(self):
        rl, _ = limiter(start=1.0, max_consecutive_refusals=3)
        for _ in range(3):
            rl.on_refused()
        assert rl.refusals == 3

    def test_raises_once_refusals_are_clearly_a_block(self):
        rl, _ = limiter(start=1.0, max_consecutive_refusals=3)
        for _ in range(3):
            rl.on_refused()
        with pytest.raises(RateLimitExceeded):
            rl.on_refused()

    def test_error_names_the_rate_it_died_at(self):
        rl, _ = limiter(start=1.0, max_consecutive_refusals=1)
        rl.on_refused()
        with pytest.raises(RateLimitExceeded, match="req/s"):
            rl.on_refused()


class TestObservability:
    """A run must be able to report what rate it settled on."""

    def test_reports_current_rate(self):
        rl, _ = limiter(start=1.5)
        assert rl.rate == pytest.approx(1.5)

    def test_counts_requests_and_refusals(self):
        rl, _ = limiter()
        rl.wait()
        rl.on_success()
        rl.wait()
        rl.on_refused()
        assert rl.requests == 2
        assert rl.refusals == 1
