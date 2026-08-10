"""The one HTTP client. Everything outbound goes through it.

Division of labour: `AdaptiveLimiter` spaces requests and learns the host's
tolerance; tenacity retries the ones that fail anyway.

A 429/403 is treated as "we are too fast" and fed back to the limiter. A 5xx
is the host's own problem and is retried without slowing the crawl down.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .ratelimit import AdaptiveLimiter

# Bot mitigation at a CDN edge, not a client mistake.
REFUSAL_STATUSES = (403, 429)
RETRY_STATUSES = (408, 429, 403, 500, 502, 503, 504)

# Everything that can go wrong before the host answers: dropped connection,
# timeout, DNS. Re-exported because the single-client invariant stops any
# other module -- tests included -- importing an HTTP library.
TransportFailure = httpx.TransportError

# Measured per-request round trip against the store.
MEASURED_LATENCY_S = 0.8
MAX_WORKERS = 16


def workers_for(ceiling: float, task_requests: int = 1, host_requests: int = 1) -> int:
    """How many threads it takes to actually reach a rate ceiling.

    A worker is busy for `task_requests * latency` and contributes
    `host_requests` to the host being paced in that time, so holding that host
    at `ceiling` needs `ceiling * latency * task_requests / host_requests`
    workers. When a task makes one request the two cancel and it is just
    `ceiling * latency`.

    The distinction matters as soon as a task talks to more than one host: a
    worker blocked on Metacritic is not fetching from the store, so the pool
    has to be wider than the store's rate alone would suggest.

    In code rather than in prose because raising the ceiling without raising
    the worker count is the easy mistake, and it silently buys nothing.
    """
    return max(1, min(MAX_WORKERS, math.ceil(
        ceiling * MEASURED_LATENCY_S * task_requests / host_requests)))


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)

    def json(self):
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class HttpError(RuntimeError):
    def __init__(self, status: int | None, url: str, body: str):
        label = f"HTTP {status}" if status else "transport failure"
        super().__init__(f"{label} for {url}: {body[:200]}")
        self.status = status
        self.url = url


class _Retryable(HttpError):
    """Marker for statuses worth another attempt."""


class HttpClient:
    def __init__(
        self,
        limiter: AdaptiveLimiter | None = None,
        *,
        # host suffix -> its own limiter. Anything unlisted falls back to
        # `limiter`. Explicit rather than cloned per host, so the pacing
        # policy is visible where the crawl is configured.
        limiters: dict | None = None,
        transport=None,
        user_agent="new-game-plus/0.1 (+https://github.com/new-game-plus)",
        proxy: str | None = None,
        direct_hosts=(),
        timeout=45.0,
        # Generous: a throttled row must be recovered, not dropped. Worst-case
        # cumulative backoff is ~4 min, after which the TTL cursor picks the
        # row up on the next run -- so nothing is ever lost either way.
        max_attempts=6,
        sleep=time.sleep,
    ):
        self.limiter = limiter or AdaptiveLimiter()
        self.limiters = dict(limiters or {})
        self.stats = {"requests": 0, "retries": 0, "refusals": 0}
        # One pooled client for the whole run. A fresh connection per request
        # is 2x slower and looks nothing like a browser. http2 lets the worker
        # threads multiplex over a single connection.
        self.pooled_client = None if transport else httpx.Client(
            http2=True, timeout=timeout, follow_redirects=True
        )
        self._transport = transport or self._send
        self._user_agent = user_agent
        self._proxy = proxy
        self._direct_hosts = tuple(direct_hosts)
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._sleep = sleep

    def _send(self, method, url, headers, body, timeout, proxies):
        # httpx pins the proxy at client construction, so a proxied request
        # gets its own short-lived client; the common unproxied path pools.
        client = self.pooled_client
        if proxies:
            with httpx.Client(http2=True, timeout=timeout, proxy=proxies["https"],
                              follow_redirects=True) as one_off:
                r = one_off.request(method, url, headers=headers, content=body)
        else:
            r = client.request(method, url, headers=headers, content=body)
        return Response(status=r.status_code, body=r.content, headers=dict(r.headers))

    def close(self):
        if self.pooled_client:
            self.pooled_client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _proxies_for(self, url):
        """Hosts that work without the proxy skip it -- routing them adds only risk."""
        if not self._proxy:
            return None
        host = urlsplit(url).hostname or ""
        if any(host == d or host.endswith("." + d) for d in self._direct_hosts):
            return None
        return {"http": self._proxy, "https": self._proxy}

    def _limiter_for(self, url) -> AdaptiveLimiter:
        """One control loop per host.

        Different hosts are different companies with different infrastructure:
        making Metacritic's requests queue behind PlayStation's token bucket
        costs wall-clock and buys nothing, and a refusal from one says nothing
        about how fast the other wants to be asked.
        """
        host = urlsplit(url).hostname or ""
        for suffix, limiter in self.limiters.items():
            if host == suffix or host.endswith("." + suffix):
                return limiter
        return self.limiter

    def request(self, method, url, *, headers=None, body=None) -> Response:
        sent = {"user-agent": self._user_agent, **(headers or {})}
        proxies = self._proxies_for(url)
        limiter = self._limiter_for(url)

        def attempt():
            limiter.wait()
            self.stats["requests"] += 1
            try:
                resp = self._transport(method, url, sent, body, self._timeout, proxies)
            except TransportFailure as exc:
                # The host never answered, so this says nothing about our pace.
                # Retry it like a 502 rather than losing the row.
                raise _Retryable(None, url, str(exc)) from exc

            if resp.status in REFUSAL_STATUSES:
                self.stats["refusals"] += 1
                retry_after = resp.headers.get("Retry-After")
                # May raise RateLimitExceeded, which must not be retried.
                limiter.on_refused(float(retry_after) if retry_after else None)
                raise _Retryable(resp.status, url, resp.text)
            if resp.status in RETRY_STATUSES:
                raise _Retryable(resp.status, url, resp.text)
            if resp.status >= 400:
                raise HttpError(resp.status, url, resp.text)

            limiter.on_success()
            return resp

        for state in Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(initial=2, max=120),
            retry=retry_if_exception_type(_Retryable),
            sleep=self._sleep,
            reraise=True,
        ):
            with state:
                if state.retry_state.attempt_number > 1:
                    self.stats["retries"] += 1
                return attempt()

    def get(self, url, **kw) -> Response:
        return self.request("GET", url, **kw)

    def get_json(self, url, **kw):
        return self.get(url, **kw).json()
