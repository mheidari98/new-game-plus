"""The merged enrichment pass.

Store detail, star rating and the Metacritic lookup happen in one task per
concept so the two hosts, which are paced independently, are exercised at the
same time instead of taking turns. The budget rules below are the part that
went wrong in production and are worth pinning.
"""

import pytest

from main import _enrich
from ngp.cache import Cache
from ngp.ratelimit import RateLimitExceeded


class FakeStore:
    def __init__(self):
        self.products = []

    def product(self, pid):
        self.products.append(pid)
        return {"name": f"Game {pid}", "releaseDate": "2024-01-01T00:00:00Z",
                "platforms": ["PS5"], "combinedLocalizedGenres": [],
                "compatibilityNotices": []}

    def stars(self, pid):
        return {"starRating": {"averageRating": 4.5, "totalRatingsCount": 100}}


class FakeMetacritic:
    def __init__(self, score=80):
        self.asked = []
        self.score = score

    def lookup(self, name, release_year=None):
        self.asked.append(name)
        if self.score is None:
            return None
        return type("Critic", (), {"score": self.score, "title": name,
                                   "url": "https://example.test"})()


class Args:
    ttl = 30
    critic_ttl = 14
    limit = None
    cap = 3


def seed(cache, n):
    rows = {str(i): {"concept_id": str(i), "rank": i,
                     "product": {"id": f"UP-{i}", "name": f"Grid {i}"}}
            for i in range(n)}
    cache.upsert_concepts([{"concept_id": c, "rank": r["rank"],
                            "product_id": r["product"]["id"]} for c, r in rows.items()])
    return rows


class TestBudget:
    def test_a_cheap_source_cannot_crowd_out_an_expensive_one(self):
        # Measured in production: capping the union of the two due-lists spent
        # the whole run on 5,000 Metacritic lookups and got through only 2,629
        # store details. Each source is capped on its own instead.
        cache, store, mc = Cache(), FakeStore(), FakeMetacritic()
        rows = seed(cache, 10)
        # Concepts 0-4 already have detail but no critic score.
        for cid in "01234":
            cache.put("product", cid, {"name": f"Game {cid}"})
            cache.mark("product", cid)

        ok, fail, matched, attempted = _enrich(store, mc, cache, rows, Args(), workers=2)
        assert len(store.products) == Args.cap, "detail work was not crowded out"
        assert len(mc.asked) == Args.cap

    def test_each_source_is_still_capped(self):
        cache, store, mc = Cache(), FakeStore(), FakeMetacritic()
        rows = seed(cache, 100)
        _enrich(store, mc, cache, rows, Args(), workers=2)
        assert len(store.products) == Args.cap
        assert len(mc.asked) == Args.cap

    def test_a_concept_does_only_the_work_it_is_due_for(self):
        cache, store, mc = Cache(), FakeStore(), FakeMetacritic()
        rows = seed(cache, 2)
        cache.put("product", "0", {"name": "Game 0"})
        cache.mark("product", "0")

        _enrich(store, mc, cache, rows, Args(), workers=1)
        assert store.products == ["UP-1"], "a fresh detail must not be refetched"
        assert len(mc.asked) == 2


class TestOneTaskTouchesBothHosts:
    def test_metacritic_sees_the_detail_fetched_in_the_same_task(self):
        # The year that separates the 2001 Silent Hill 2 from the 2024 remake
        # comes from the product detail. A concurrent second pass would race
        # it and match on the title alone.
        cache, store, mc = Cache(), FakeStore(), FakeMetacritic()
        rows = seed(cache, 1)
        _enrich(store, mc, cache, rows, Args(), workers=1)
        assert mc.asked == ["Game UP-0"], "used the store name, not the grid name"

    def test_reports_what_each_source_achieved(self):
        cache, store, mc = Cache(), FakeStore(), FakeMetacritic()
        rows = seed(cache, 2)
        ok, fail, matched, attempted = _enrich(store, mc, cache, rows, Args(), workers=1)
        assert (ok, fail, matched, attempted) == (2, 0, 2, 2)

    def test_a_metacritic_miss_is_not_a_failure(self):
        cache, store, mc = Cache(), FakeStore(), FakeMetacritic(score=None)
        rows = seed(cache, 2)
        ok, fail, matched, _ = _enrich(store, mc, cache, rows, Args(), workers=1)
        assert (ok, fail, matched) == (2, 0, 0)

    def test_a_miss_is_cached_so_the_next_run_does_not_re_ask(self):
        cache, store, mc = Cache(), FakeStore(), FakeMetacritic(score=None)
        rows = seed(cache, 2)
        _enrich(store, mc, cache, rows, Args(), workers=1)
        assert cache.due("critic", ttl_days=14) == []

    def test_a_failed_detail_does_not_take_the_run_down(self):
        class Broken(FakeStore):
            def product(self, pid):
                raise RuntimeError("HTTP 500")

        cache, mc = Cache(), FakeMetacritic()
        rows = seed(cache, 2)
        ok, fail, _, _ = _enrich(Broken(), mc, cache, rows, Args(), workers=1)
        assert (ok, fail) == (0, 2)


class TestTheCircuitBreakerIsNotSwallowed:
    """RateLimitExceeded means the refusals stopped looking like throttling.
    Enrichment is ~12,000 requests -- the phase most likely to draw a block --
    so a per-row `except Exception` here would swallow it for the whole run.
    """

    def test_a_blocked_store_stops_the_run(self):
        class Blocked(FakeStore):
            def product(self, pid):
                raise RateLimitExceeded("6.00 req/s refused 5 times in a row")

        cache = Cache()
        rows = seed(cache, 3)
        with pytest.raises(RateLimitExceeded):
            _enrich(Blocked(), FakeMetacritic(), cache, rows, Args(), workers=1)

    def test_a_blocked_metacritic_stops_the_run(self):
        class Blocked(FakeMetacritic):
            def lookup(self, name, release_year=None):
                raise RateLimitExceeded("6.00 req/s refused 5 times in a row")

        cache = Cache()
        rows = seed(cache, 3)
        with pytest.raises(RateLimitExceeded):
            _enrich(FakeStore(), Blocked(), cache, rows, Args(), workers=1)

    def test_an_ordinary_transport_error_is_still_only_one_row(self):
        class Flaky(FakeMetacritic):
            def lookup(self, name, release_year=None):
                raise RuntimeError("HTTP 502")

        cache = Cache()
        rows = seed(cache, 2)
        ok, fail, matched, _ = _enrich(FakeStore(), Flaky(), cache, rows, Args(), workers=1)
        assert (ok, fail, matched) == (2, 0, 0)


class TestCoverArt:
    """Sony ships several image roles per product and no literal "box art" key.

    MASTER was the only role present on all 18 products sampled across the
    popularity range, so it leads the chain. The URL carries a 48-hex asset
    hash and cannot be derived from a product id -- it is captured here or the
    game has no image at all.
    """

    def media(self, *roles):
        return [{"type": "IMAGE", "role": r, "url": f"https://img/{r}.png"}
                for r in roles]

    def enrich_one(self, media):
        cache, mc = Cache(), FakeMetacritic(score=None)
        rows = seed(cache, 1)
        store = FakeStore()
        base = store.product
        store.product = lambda pid: {**base(pid), "media": media}
        _enrich(store, mc, cache, rows, Args, workers=1)
        return cache.get("product", "0", ttl_days=30)["art"]

    def test_prefers_master(self):
        assert self.enrich_one(
            self.media("LOGO", "PORTRAIT_BANNER", "MASTER")) == "https://img/MASTER.png"

    def test_falls_back_when_master_is_absent(self):
        assert self.enrich_one(
            self.media("SCREENSHOT", "GAMEHUB_COVER_ART")) == "https://img/GAMEHUB_COVER_ART.png"

    def test_never_picks_a_logo_or_screenshot(self):
        # Both are present on nearly every product and neither is box art.
        assert self.enrich_one(self.media("LOGO", "SCREENSHOT")) is None

    def test_a_product_with_no_media_is_null_not_missing(self):
        assert self.enrich_one([]) is None

    def test_video_entries_are_ignored(self):
        media = [{"type": "VIDEO", "role": "MASTER", "url": "https://img/clip.mp4"}]
        assert self.enrich_one(media) is None
