"""PlayStation Plus catalogue feeds.

Every entry carries both a conceptId and a productId, which makes matching
against store products exact -- no fuzzy title matching anywhere in this path.
Field shapes below were measured against the live feeds.
"""

from datetime import date

import pytest

from ngp.psplus import (
    LISTS,
    PlusFeedUnavailable,
    PlusIndex,
    fetch_all,
    parse_feed,
    resolve,
)


def entry(concept_id, name="A Game", product_id=None, devices=("PS5",)):
    return {
        "conceptId": concept_id,
        "name": name,
        "nameEn": name,
        "productId": product_id or f"UP0000-CUSA00000_00-{concept_id:016d}",
        "device": list(devices),
        "genre": ["Action"],
        "releaseDate": "2023-01-01T00:00:00Z",
        "ageRating": {"authority": "ESRB", "name": "ESRB_TEEN", "descriptors": []},
    }


def feed(*entries):
    """The response is 27 alphabetical buckets, not a flat list."""
    return [{"catalogKey": "A", "count": len(entries), "games": list(entries)}]


class TestParsing:
    def test_flattens_alphabetical_buckets(self):
        payload = [
            {"catalogKey": "A", "count": 1, "games": [entry(1, "Alpha")]},
            {"catalogKey": "B", "count": 2, "games": [entry(2, "Beta"), entry(3, "Gamma")]},
        ]
        assert len(parse_feed(payload, "extra")) == 3

    def test_keeps_concept_and_product_ids(self):
        got = parse_feed(feed(entry(228903, "A Hat in Time")), "extra")[0]
        assert got.concept_id == "228903"
        assert got.product_id.startswith("UP0000-")

    def test_concept_id_is_stringified(self):
        # The feed sends ints; the store sends strings. Normalise on the way in
        # or every comparison silently fails.
        assert parse_feed(feed(entry(123)), "extra")[0].concept_id == "123"

    def test_devices_are_sorted(self):
        # Order is not normalised upstream: 11 entries are ['PS5','PS4'].
        got = parse_feed(feed(entry(1, devices=("PS5", "PS4"))), "extra")[0]
        assert got.devices == ["PS4", "PS5"]

    def test_empty_payload_is_empty(self):
        assert parse_feed([], "extra") == []

    def test_entry_without_concept_id_is_skipped(self):
        payload = feed({"name": "Broken", "productId": "X"}, entry(5))
        assert len(parse_feed(payload, "extra")) == 1


class TestIndexLookup:
    """Matching is by exact id. conceptId is NOT unique within a feed (18
    conceptIds cover 38 entries in Extra), but productId is."""

    def test_finds_by_concept_id(self):
        idx = PlusIndex({"extra": parse_feed(feed(entry(42)), "extra")})
        assert idx.lookup(concept_id="42").list_name == "extra"

    def test_finds_by_product_id(self):
        e = entry(42, product_id="UP1234-CUSA00001_00-ABCDEFGHIJKLMNOP")
        idx = PlusIndex({"extra": parse_feed(feed(e), "extra")})
        got = idx.lookup(product_id="UP1234-CUSA00001_00-ABCDEFGHIJKLMNOP")
        assert got.concept_id == "42"

    def test_unknown_id_is_none(self):
        idx = PlusIndex({"extra": parse_feed(feed(entry(42)), "extra")})
        assert idx.lookup(concept_id="999") is None

    def test_extra_wins_over_classics_for_the_same_concept(self):
        # Extra is the cheaper tier, so it is the more useful answer.
        idx = PlusIndex({
            "classics": parse_feed(feed(entry(7)), "classics"),
            "extra": parse_feed(feed(entry(7)), "extra"),
        })
        assert idx.lookup(concept_id="7").list_name == "extra"

    def test_duplicate_concept_ids_do_not_lose_entries(self):
        idx = PlusIndex({"extra": parse_feed(
            feed(entry(10, "TimeSplitters 1"), entry(10, "TimeSplitters 2")), "extra")})
        assert idx.lookup(concept_id="10") is not None


class TestUnion:
    """ubisoft-classics-list is a strict SUBSET of plus-games-list. Unioning
    it in double-counts 68 entries (+10.6% phantom catalogue)."""

    def test_ubisoft_list_is_excluded_from_the_catalogue(self):
        assert "ubisoft" not in LISTS

    def test_catalogue_lists_are_the_three_real_tiers(self):
        assert set(LISTS) == {"extra", "classics", "monthly"}


class TestTransientFailure:
    """Measured: the feed 404'd from a US runner on one run and returned 471
    entries on the next, minutes later. An empty Extra catalogue would mark
    every game as not-in-PS+, which is worse than no data at all -- so it must
    fail loudly rather than degrade."""

    def test_empty_extra_catalogue_raises(self):
        class DeadHttp:
            def get_json(self, url, headers=None):
                return []
        with pytest.raises(PlusFeedUnavailable):
            fetch_all(DeadHttp(), sleep=lambda _: None)

    def test_transient_error_on_extra_raises(self):
        class BrokenHttp:
            def get_json(self, url, headers=None):
                raise RuntimeError("HTTP 404")
        with pytest.raises(PlusFeedUnavailable):
            fetch_all(BrokenHttp(), sleep=lambda _: None)

    def test_a_missing_optional_list_does_not_raise(self):
        # Classics and Monthly are nice to have; Extra is load-bearing.
        class PartialHttp:
            def get_json(self, url, headers=None):
                if "plus-games-list" in url:
                    return feed(entry(1), entry(2))
                raise RuntimeError("HTTP 500")
        got = fetch_all(PartialHttp(), sleep=lambda _: None)
        assert len(got["extra"]) == 2
        assert got["classics"] == []

    def test_a_missing_optional_list_is_logged(self, caplog):
        # Silently empty Classics publishes every game as not-in-Classics,
        # which is a confident wrong answer. A moved URL must be visible.
        class PartialHttp:
            def get_json(self, url, headers=None):
                if "plus-games-list" in url:
                    return feed(entry(1))
                raise RuntimeError("HTTP 404")
        with caplog.at_level("WARNING", logger="ngp.psplus"):
            fetch_all(PartialHttp(), sleep=lambda _: None)
        logged = caplog.text
        assert "classics" in logged and "monthly" in logged
        assert "HTTP 404" in logged

    def test_extra_is_asked_again_before_the_run_is_abandoned(self):
        # A 404 here costs the whole crawl, and the feed has answered on the
        # next try. net.py cannot do this: a 404 elsewhere is a real absence.
        class FlakyHttp:
            attempts = 0

            def get_json(self, url, headers=None):
                if "plus-games-list" not in url:
                    return feed(entry(9))
                FlakyHttp.attempts += 1
                if FlakyHttp.attempts < 3:
                    raise RuntimeError("HTTP 404")
                return feed(entry(1))

        got = fetch_all(FlakyHttp(), sleep=lambda _: None)
        assert len(got["extra"]) == 1
        assert FlakyHttp.attempts == 3

    def test_an_empty_extra_response_is_retried_too(self):
        # 200-with-nothing is the same outage wearing a different status.
        class EmptyThenFull:
            attempts = 0

            def get_json(self, url, headers=None):
                if "plus-games-list" not in url:
                    return feed(entry(9))
                EmptyThenFull.attempts += 1
                return [] if EmptyThenFull.attempts < 2 else feed(entry(1))

        got = fetch_all(EmptyThenFull(), sleep=lambda _: None)
        assert len(got["extra"]) == 1

    def test_the_optional_lists_are_not_retried(self):
        # Spending retries on data the run does not need is just load.
        class ClassicsDown:
            attempts = 0

            def get_json(self, url, headers=None):
                if "plus-games-list" in url:
                    return feed(entry(1))
                ClassicsDown.attempts += 1
                raise RuntimeError("HTTP 500")

        got = fetch_all(ClassicsDown(), sleep=lambda _: None)
        assert got["classics"] == []
        assert ClassicsDown.attempts == 2      # classics and monthly, once each


class TestTierFlags:
    def test_extra_membership_is_reported(self):
        idx = PlusIndex({"extra": parse_feed(feed(entry(1)), "extra")})
        assert idx.lookup(concept_id="1").in_extra is True

    def test_classics_is_not_extra(self):
        # A Premium Classics title is not covered by an Extra subscription.
        idx = PlusIndex({"classics": parse_feed(feed(entry(1)), "classics")})
        got = idx.lookup(concept_id="1")
        assert got.in_extra is False
        assert got.in_classics is True

    def test_monthly_counts_as_extra_for_ranking(self):
        # Essential monthlies are claimable and keepable by every tier.
        idx = PlusIndex({"monthly": parse_feed(feed(entry(1)), "monthly")})
        assert idx.lookup(concept_id="1").in_extra is True


class TestExtraCount:
    """The publish gate needs the size of the Extra catalogue. It counts
    concepts, not entries -- 18 conceptIds cover 38 Extra entries."""

    def test_counts_concepts_in_extra(self):
        idx = PlusIndex({"extra": parse_feed(feed(entry(1), entry(2)), "extra")})
        assert idx.extra_count == 2

    def test_classics_only_concepts_are_not_counted(self):
        idx = PlusIndex({
            "extra": parse_feed(feed(entry(1)), "extra"),
            "classics": parse_feed(feed(entry(2)), "classics"),
        })
        assert idx.extra_count == 1
        assert len(idx) == 2

    def test_duplicate_concept_ids_count_once(self):
        idx = PlusIndex({"extra": parse_feed(
            feed(entry(10, "TimeSplitters 1"), entry(10, "TimeSplitters 2")), "extra")})
        assert idx.extra_count == 1

    def test_monthly_counts_as_extra(self):
        idx = PlusIndex({"monthly": parse_feed(feed(entry(1)), "monthly")})
        assert idx.extra_count == 1

    def test_empty_catalogue_is_zero(self):
        assert PlusIndex({"extra": []}).extra_count == 0


class TestDegradedFeedFallback:
    """Measured 2026-09-19: the Extra feed stopped 404ing and started answering
    200 with 55 of ~500 entries, three runs in a row. Emptiness checks pass a
    truncated catalogue, and publishing it marks ~90% of Extra as not-in-PS+.
    So a catalogue under the floor is replaced by the last healthy one -- said
    out loud via the stale date -- and never overwrites it."""

    FLOOR = 3
    MAX_AGE = 30
    TODAY = date(2026, 9, 21)

    @staticmethod
    def catalogues(n_extra, first=1, classics=()):
        return {
            "extra": parse_feed(feed(*(entry(i) for i in range(first, first + n_extra))), "extra"),
            "classics": parse_feed(feed(*(entry(i) for i in classics)), "classics"),
            "monthly": [],
        }

    def run(self, live, path, today=None):
        return resolve(live, path, today or self.TODAY,
                       floor=self.FLOOR, max_age_days=self.MAX_AGE)

    def test_healthy_feed_is_used_as_is_and_saved(self, tmp_path):
        path = tmp_path / "plus" / "last_good.json"
        index, stale_since = self.run(self.catalogues(5), path)
        assert index.extra_count == 5
        assert stale_since is None
        assert path.exists()

    def test_degraded_feed_falls_back_to_the_last_good_snapshot(self, tmp_path):
        path = tmp_path / "last_good.json"
        self.run(self.catalogues(6, classics=(100,)), path, today=date(2026, 9, 18))
        index, stale_since = self.run(self.catalogues(2, first=900), path)
        assert index.extra_count == 6
        assert stale_since == date(2026, 9, 18)
        # The snapshot answers exact lookups like the live feed does.
        assert index.lookup(concept_id="1").in_extra is True
        assert index.lookup(concept_id="100").in_classics is True
        assert index.lookup(concept_id="900") is None

    def test_degraded_feed_never_overwrites_the_snapshot(self, tmp_path):
        path = tmp_path / "last_good.json"
        self.run(self.catalogues(6), path, today=date(2026, 9, 18))
        before = path.read_text()
        self.run(self.catalogues(2, first=900), path)
        assert path.read_text() == before

    def test_degraded_feed_with_no_snapshot_is_returned_for_the_guard_to_block(self, tmp_path):
        index, stale_since = self.run(self.catalogues(2), tmp_path / "missing.json")
        assert index.extra_count == 2
        assert stale_since is None

    def test_a_snapshot_past_the_age_limit_is_not_trusted(self, tmp_path):
        path = tmp_path / "last_good.json"
        self.run(self.catalogues(6), path, today=date(2026, 8, 1))
        index, stale_since = self.run(self.catalogues(2, first=900), path)
        assert index.extra_count == 2
        assert stale_since is None

    def test_a_snapshot_exactly_at_the_age_limit_is_still_used(self, tmp_path):
        path = tmp_path / "last_good.json"
        self.run(self.catalogues(6), path, today=date(2026, 8, 22))
        _, stale_since = self.run(self.catalogues(2, first=900), path)
        assert stale_since == date(2026, 8, 22)

    @pytest.mark.parametrize("junk", ["", "not json", "[]", '{"saved_on": "nope"}',
                                      '{"saved_on": "2026-09-18", "catalogues": 5}'])
    def test_an_unreadable_snapshot_counts_as_missing(self, tmp_path, junk):
        path = tmp_path / "last_good.json"
        path.write_text(junk)
        index, stale_since = self.run(self.catalogues(2), path)
        assert index.extra_count == 2
        assert stale_since is None

    def test_a_degraded_snapshot_is_never_used_as_a_fallback(self, tmp_path):
        # Guards against a snapshot that was itself under the floor (e.g. one
        # seeded by hand): falling back to it would just relabel the truncation.
        path = tmp_path / "last_good.json"
        path.write_text('{"saved_on": "2026-09-18", "catalogues": {"extra": []}}')
        index, stale_since = self.run(self.catalogues(2), path)
        assert index.extra_count == 2
        assert stale_since is None
