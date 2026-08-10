"""Adaptive request pacing: AIMD, the control law behind TCP congestion avoidance.

tenacity retries a *failed call*; this spaces *every call*. Different jobs, so
we need both. No library does adaptive pacing -- pyrate-limiter, limits and
aiolimiter are all fixed-rate.

Why adaptive: the rate a host tolerates is not knowable up front, and guessing
wrong is expensive both ways. Too fast risks the IP; too slow turns a two-hour
backfill into a nine-hour one. So we creep up while it is happy and halve when
it is not, remembering the rate that drew a refusal so the ramp stops just
short of it. Without that memory the limiter oscillates through the host's
limit forever -- measured at hundreds of 429s per crawl instead of one or two.

Clock and randomness are injected so pacing is testable without sleeping.
"""

from __future__ import annotations

import random
import threading
import time


class RateLimitExceeded(RuntimeError):
    """Refusals stopped looking like throttling and started looking like a block."""


class AdaptiveLimiter:
    def __init__(
        self,
        start=1.0,
        *,
        floor=0.25,
        ceiling=6.0,
        jitter=0.3,
        increase_after=20,
        increase_by=0.25,
        decrease_factor=0.5,
        # A 429 means "later", not "never", so this is the "we are blocked"
        # threshold, not the "we got throttled" one. Set high: the caller
        # backs off and retries, and only a host that refuses this many times
        # in a row has actually stopped talking to us.
        max_consecutive_refusals=10,
        sleep=time.sleep,
        monotonic=time.monotonic,
        rand=random.random,
    ):
        self.rate = max(floor, min(ceiling, start))
        self.requests = 0
        self.refusals = 0
        self.effective_ceiling = ceiling

        self._floor, self._ceiling, self._jitter = floor, ceiling, max(0.0, jitter)
        self._increase_after, self._increase_by = increase_after, increase_by
        self._decrease_factor = decrease_factor
        self._max_consecutive_refusals = max_consecutive_refusals
        self._sleep, self._monotonic, self._rand = sleep, monotonic, rand

        self._lock = threading.RLock()
        self._next_at = 0.0
        self._streak = 0            # consecutive clean responses
        self._refusals_in_a_row = 0
        self._cut_at_request = -1

    def wait(self):
        """Block until the next request is allowed out. Safe to share across threads."""
        with self._lock:
            interval = 1.0 / self.rate if self.rate > 0 else 0.0
            now = self._monotonic()
            if now < self._next_at:
                self._sleep(self._next_at - now)
                now = self._monotonic()
            # Jitter: a request every 1000.0ms is a robotic signature.
            spread = 1.0 + self._jitter * (2.0 * self._rand() - 1.0)
            self._next_at = now + max(0.0, interval * spread)
            self.requests += 1

    def on_success(self):
        with self._lock:
            self._refusals_in_a_row = 0
            self._streak += 1
            if self._streak >= self._increase_after:
                self._streak = 0
                self.rate = min(self.effective_ceiling, self.rate + self._increase_by)

    def on_refused(self, retry_after=None):
        """Record a 429/403. `retry_after` is the host's own instruction and wins if longer."""
        with self._lock:
            self.refusals += 1
            self._refusals_in_a_row += 1
            self._streak = 0

            # Cut once per round of in-flight requests, not once per refusal.
            # The workers share this limiter, so one wall draws a burst of
            # refusals all issued at the same rate; cutting for each of them
            # reaches the floor in five and drags the remembered ceiling down
            # with it, where on_success can never climb past. TCP halves once
            # per RTT rather than once per lost packet for the same reason.
            if self.requests > self._cut_at_request:
                # Remember where the wall is so the ramp stops short of it.
                self.effective_ceiling = max(self._floor, self.rate * 0.9)
                self.rate = max(self._floor, self.rate * self._decrease_factor)
                self._cut_at_request = self.requests

            # Apply from now, discarding the slot scheduled at the refused rate.
            hold = 1.0 / self.rate if self.rate > 0 else 0.0
            self._next_at = self._monotonic() + max(hold, float(retry_after or 0))

            if self._refusals_in_a_row > self._max_consecutive_refusals:
                raise RateLimitExceeded(
                    f"refused {self._refusals_in_a_row}x in a row at {self.rate:.2f} req/s"
                )
