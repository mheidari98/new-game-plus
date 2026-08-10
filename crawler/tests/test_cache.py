"""The resumable TTL cursor and parsed-extract cache.

One mechanism covers four jobs: cold-start backfill, TTL refresh, catalogue
growth and crash resume. It lives in actions/cache, is fully rebuildable, and
never goes in git.
"""

import pytest

from ngp.cache import Cache

DAY = 86400.0


class FakeTime:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, days):
        self.now += days * DAY


@pytest.fixture
def clock():
    return FakeTime()


@pytest.fixture
def cache(clock):
    return Cache(":memory:", now=clock)


def seed(cache, n=3):
    cache.upsert_concepts(
        [{"concept_id": f"C{i}", "rank": i, "product_id": f"P{i}"} for i in range(n)]
    )


class TestCursor:
    def test_never_fetched_concepts_are_due(self, cache):
        seed(cache)
        assert cache.due("product", ttl_days=30) == ["C0", "C1", "C2"]

    def test_due_is_ordered_by_popularity(self, cache):
        cache.upsert_concepts([
            {"concept_id": "slow", "rank": 900, "product_id": "P1"},
            {"concept_id": "hot", "rank": 1, "product_id": "P2"},
        ])
        # The grid's default sort is sales30, so rank IS popularity, for free.
        assert cache.due("product", ttl_days=30) == ["hot", "slow"]

    def test_limit_caps_the_batch(self, cache):
        seed(cache, 10)
        assert len(cache.due("product", ttl_days=30, limit=4)) == 4

    def test_marking_removes_it_from_the_queue(self, cache):
        seed(cache)
        cache.mark("product", "C1")
        assert "C1" not in cache.due("product", ttl_days=30)

    def test_sources_are_tracked_independently(self, cache):
        seed(cache)
        cache.mark("product", "C0")
        assert "C0" in cache.due("stars", ttl_days=7)

    def test_row_becomes_due_again_after_its_ttl(self, cache, clock):
        seed(cache)
        cache.mark("product", "C0")
        clock.advance(31)
        assert "C0" in cache.due("product", ttl_days=30)

    def test_row_stays_fresh_inside_its_ttl(self, cache, clock):
        seed(cache)
        cache.mark("product", "C0")
        clock.advance(29)
        assert "C0" not in cache.due("product", ttl_days=30)


class TestCrashResume:
    """A run that dies halfway leaves its rows unstamped, so the next run
    picks them up. There is no separate cursor to corrupt."""

    def test_unmarked_rows_return_after_a_crash(self, cache):
        seed(cache, 5)
        for cid in cache.due("product", ttl_days=30, limit=2):
            cache.mark("product", cid)
        # ...process dies here...
        assert cache.due("product", ttl_days=30) == ["C2", "C3", "C4"]

    def test_reseeding_does_not_duplicate_or_reset_progress(self, cache):
        seed(cache, 3)
        cache.mark("product", "C0")
        seed(cache, 3)                      # next run re-enumerates the catalogue
        assert cache.due("product", ttl_days=30) == ["C1", "C2"]

    def test_new_catalogue_entries_join_the_queue(self, cache):
        seed(cache, 2)
        cache.mark("product", "C0")
        cache.mark("product", "C1")
        cache.upsert_concepts([{"concept_id": "C9", "rank": 9, "product_id": "P9"}])
        assert cache.due("product", ttl_days=30) == ["C9"]


class TestPayloadCache:
    def test_round_trips_a_payload(self, cache):
        cache.put("product", "C0", {"name": "A Game", "players": 4})
        assert cache.get("product", "C0", ttl_days=30) == {"name": "A Game", "players": 4}

    def test_missing_key_is_none(self, cache):
        assert cache.get("product", "nope", ttl_days=30) is None

    def test_expired_payload_is_none(self, cache, clock):
        cache.put("product", "C0", {"n": 1})
        clock.advance(31)
        assert cache.get("product", "C0", ttl_days=30) is None

    def test_put_overwrites(self, cache):
        cache.put("product", "C0", {"n": 1})
        cache.put("product", "C0", {"n": 2})
        assert cache.get("product", "C0", ttl_days=30) == {"n": 2}


class TestPersistence:
    def test_survives_reopening_the_file(self, tmp_path, clock):
        path = tmp_path / "ngp.sqlite"
        first = Cache(path, now=clock)
        seed(first)
        first.mark("product", "C0")
        first.put("product", "C0", {"n": 1})
        first.close()

        second = Cache(path, now=clock)
        assert second.due("product", ttl_days=30) == ["C1", "C2"]
        assert second.get("product", "C0", ttl_days=30) == {"n": 1}
