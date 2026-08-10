"""The merged enrichment pass.

Store detail, star rating and the Metacritic lookup happen in one task per
concept so the two hosts, which are paced independently, are exercised at the
same time instead of taking turns. The budget rules below are the part that
went wrong in production and are worth pinning.
"""

from main import _enrich
from ngp.cache import Cache


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
