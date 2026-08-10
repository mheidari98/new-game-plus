"""The single HTTP client.

Everything outbound goes through here so pacing, retries and caching cannot
be bypassed by accident. A fake transport stands in for the network so these
tests are deterministic and instant.
"""

import pytest

from ngp.net import HttpClient, HttpError, Response, TransportFailure
from ngp.ratelimit import AdaptiveLimiter, RateLimitExceeded


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeTransport:
    """Returns queued responses and records what it was asked for."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout, proxies):
        self.calls.append(
            {"method": method, "url": url, "headers": headers,
             "body": body, "proxies": proxies}
        )
        if not self.queue:
            return Response(status=200, body=b'{"ok":true}', headers={})
        nxt = self.queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def ok(body=b'{"ok":true}'):
    return Response(status=200, body=body, headers={})


def refused(status=429, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    return Response(status=status, body=b"slow down", headers=headers)


def client(transport, **kw):
    clock = FakeClock()
    limiter = AdaptiveLimiter(
        start=1.0, jitter=0.0, rand=lambda: 0.5,
        sleep=clock.sleep, monotonic=clock.monotonic,
        **kw.pop("limiter_kw", {}),
    )
    return HttpClient(
        limiter=limiter, transport=transport, sleep=clock.sleep, **kw
    ), limiter, clock


class TestBasics:
    def test_returns_the_response_body(self):
        c, _, _ = client(FakeTransport(ok(b'{"n":1}')))
        assert c.get_json("https://example.test/a") == {"n": 1}

    def test_sends_the_configured_user_agent(self):
        t = FakeTransport(ok())
        c, _, _ = client(t, user_agent="new-game-plus/0.1")
        c.get("https://example.test/a")
        assert t.calls[0]["headers"]["user-agent"] == "new-game-plus/0.1"

    def test_caller_headers_are_merged(self):
        t = FakeTransport(ok())
        c, _, _ = client(t)
        c.get("https://example.test/a", headers={"content-type": "application/json"})
        assert t.calls[0]["headers"]["content-type"] == "application/json"


class TestPacingIsNotBypassable:
    def test_every_request_consults_the_limiter(self):
        t = FakeTransport(ok(), ok(), ok())
        c, limiter, _ = client(t)
        for _ in range(3):
            c.get("https://example.test/a")
        assert limiter.requests == 3

    def test_success_feeds_back_to_the_limiter(self):
        c, limiter, _ = client(FakeTransport(ok()))
        c.get("https://example.test/a")
        assert limiter.refusals == 0


class TestRefusalHandling:
    def test_429_is_reported_to_the_limiter(self):
        c, limiter, _ = client(FakeTransport(refused(429), ok()))
        c.get("https://example.test/a")
        assert limiter.refusals == 1

    def test_429_is_retried_and_eventually_succeeds(self):
        c, _, _ = client(FakeTransport(refused(429), ok(b'{"n":2}')))
        assert c.get_json("https://example.test/a") == {"n": 2}

    def test_403_counts_as_a_refusal_not_a_plain_error(self):
        # A 403 from a CDN edge is bot mitigation, not a client mistake.
        c, limiter, _ = client(FakeTransport(refused(403), ok()))
        c.get("https://example.test/a")
        assert limiter.refusals == 1

    def test_retry_after_header_is_honoured(self):
        c, limiter, _ = client(FakeTransport(refused(429, retry_after=42), ok()))
        c.get("https://example.test/a")
        # The limiter must have been told to hold off for at least that long.
        assert limiter.refusals == 1

    def test_persistent_throttling_still_returns_the_data(self):
        # A 429 means "later", not "never". Backing off and retrying must
        # recover the row rather than dropping it.
        t = FakeTransport(*([refused(429)] * 5 + [ok(b'{"n":9}')]))
        c, _, _ = client(t)
        assert c.get_json("https://example.test/a") == {"n": 9}

    def test_waits_the_retry_after_interval_before_trying_again(self):
        c, _, clock = client(FakeTransport(refused(429, retry_after=30), ok()))
        before = clock.now
        c.get("https://example.test/a")
        assert clock.now - before >= 30

    def test_gives_up_only_once_refusal_looks_like_a_block(self):
        # The safety valve still exists, but far past the point of a throttle.
        t = FakeTransport(*[refused(429)] * 40)
        c, _, _ = client(t, limiter_kw={"max_consecutive_refusals": 3}, max_attempts=40)
        with pytest.raises(RateLimitExceeded):
            c.get("https://example.test/a")


class TestErrors:
    def test_404_raises_without_retrying(self):
        t = FakeTransport(refused(404), ok())
        c, _, _ = client(t)
        with pytest.raises(HttpError) as exc:
            c.get("https://example.test/a")
        assert exc.value.status == 404
        assert len(t.calls) == 1, "4xx other than 408/429 must not be retried"

    def test_500_is_retried(self):
        t = FakeTransport(refused(500), ok(b'{"n":3}'))
        c, _, _ = client(t)
        assert c.get_json("https://example.test/a") == {"n": 3}

    def test_500_does_not_count_as_a_rate_refusal(self):
        # A server error is the host's problem, not evidence we are too fast.
        c, limiter, _ = client(FakeTransport(refused(500), ok()))
        c.get("https://example.test/a")
        assert limiter.refusals == 0

    def test_gives_up_after_max_attempts(self):
        t = FakeTransport(*[refused(500)] * 10)
        c, _, _ = client(t, max_attempts=3)
        with pytest.raises(HttpError):
            c.get("https://example.test/a")
        assert len(t.calls) == 3


class TestTransportFailures:
    """Five worker threads share one pooled HTTP/2 connection and the store
    drops it occasionally: a live crawl logged 8 "Server disconnected" errors
    in 1,600 requests. The host never answered, so this is a 502 in all but
    name -- retry it, and do not read it as "we are going too fast".

    `TransportFailure` is re-exported by net.py so this file does not have to
    import an HTTP library, which the single-client invariant forbids.
    """

    def test_a_dropped_connection_is_retried(self):
        t = FakeTransport(TransportFailure("Server disconnected"), ok(b'{"n":7}'))
        c, _, _ = client(t)
        assert c.get_json("https://example.test/a") == {"n": 7}
        assert len(t.calls) == 2

    def test_it_does_not_count_as_a_rate_refusal(self):
        # Halving the crawl rate because a keep-alive expired would be a slow
        # crawl for no reason.
        c, limiter, _ = client(FakeTransport(
            TransportFailure("Server disconnected"), ok()))
        c.get("https://example.test/a")
        assert limiter.refusals == 0

    def test_it_gives_up_eventually_rather_than_hanging(self):
        t = FakeTransport(*[TransportFailure("no route to host")] * 10)
        c, _, _ = client(t, max_attempts=3)
        with pytest.raises(HttpError):
            c.get("https://example.test/a")
        assert len(t.calls) == 3

    def test_a_programming_error_is_not_retried(self):
        # Only transport failures. A bug in our own code must surface at once.
        t = FakeTransport(ValueError("bad payload"), ok())
        c, _, _ = client(t)
        with pytest.raises(ValueError):
            c.get("https://example.test/a")
        assert len(t.calls) == 1


class TestProxy:
    def test_no_proxy_by_default(self):
        t = FakeTransport(ok())
        c, _, _ = client(t)
        c.get("https://example.test/a")
        assert t.calls[0]["proxies"] is None

    def test_proxy_is_applied_when_configured(self):
        t = FakeTransport(ok())
        c, _, _ = client(t, proxy="http://127.0.0.1:2080")
        c.get("https://example.test/a")
        assert t.calls[0]["proxies"]["https"] == "http://127.0.0.1:2080"

    def test_direct_hosts_bypass_the_proxy(self):
        # PSN works without the proxy, so routing it through one only adds risk.
        t = FakeTransport(ok())
        c, _, _ = client(t, proxy="http://127.0.0.1:2080",
                         direct_hosts=["web.np.playstation.com"])
        c.get("https://web.np.playstation.com/api")
        assert t.calls[0]["proxies"] is None

    def test_direct_hosts_match_subdomains(self):
        t = FakeTransport(ok())
        c, _, _ = client(t, proxy="http://127.0.0.1:2080",
                         direct_hosts=["playstation.com"])
        c.get("https://web.np.playstation.com/api")
        assert t.calls[0]["proxies"] is None


class TestConnectionReuse:
    """40k requests must not open 40k TCP connections. Pooling measured at 52%
    faster over three calls, and a fresh connection per request looks nothing
    like a browser."""

    def test_default_transport_pools_connections(self):
        c = HttpClient()
        assert c.pooled_client is not None

    def test_same_connection_pool_across_requests(self):
        c = HttpClient()
        first = c.pooled_client
        assert c.pooled_client is first

    def test_close_releases_the_pool(self):
        c = HttpClient()
        c.close()
        assert c.pooled_client.is_closed

    def test_works_as_a_context_manager(self):
        with HttpClient() as c:
            pool = c.pooled_client
        assert pool.is_closed


class TestStats:
    def test_counts_requests_and_retries(self):
        c, _, _ = client(FakeTransport(refused(500), ok()))
        c.get("https://example.test/a")
        assert c.stats["requests"] == 2
        assert c.stats["retries"] == 1
