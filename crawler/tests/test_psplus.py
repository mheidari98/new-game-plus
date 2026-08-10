"""PlayStation Plus catalogue feeds.

Every entry carries both a conceptId and a productId, which makes matching
against store products exact -- no fuzzy title matching anywhere in this path.
Field shapes below were measured against the live feeds.
"""

import pytest

from ngp.psplus import (
    LISTS,
    PlusFeedUnavailable,
    PlusIndex,
    fetch_all,
    parse_feed,
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
