"""PlayStation Plus catalogue feeds.

Extra and Classics come from the store's own product grid (categoryGridRetrieve,
the same operation and pinned hash used for deals/all_games/free_to_play);
Monthly Essentials still comes from the AEM feed (bin/imagic/gameslist), which
has no store-category equivalent since it is a rotating claim list, not a
static catalogue. Grid rows carry an exact productId but no conceptId; AEM
entries carry both. Field shapes below were measured against the live sources.
"""

from datetime import date

import pytest

from ngp.psplus import (
    LISTS,
    PlusEntry,
    PlusIndex,
    fetch_all,
    fetch_catalogue,
    parse_feed,
    resolve,
)
from ngp.store import GridPage


def product(id, name="A Game", platforms=("PS5",)):
    return {"id": id, "name": name, "platforms": list(platforms)}


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


class TestFetchCatalogue:
    """Extra and Classics are ordinary store categories now: paged with
    categoryGridRetrieve like deals/all_games/free_to_play, to exhaustion."""

    class FakeStore:
        def __init__(self, pages):
            self.pages = pages      # list of GridPage, one per offset/1000
            self.calls = []

        def grid_page(self, category_id, *, offset=0, size=None):
            self.calls.append((category_id, offset))
            i = offset // 1000
            return self.pages[i] if i < len(self.pages) else GridPage()

    def test_reads_id_name_and_platforms_off_each_row(self):
        store = self.FakeStore([GridPage(
            products=[product("UP1", "Astro Bot", ("PS5", "PS4"))], is_last=True)])
        got = fetch_catalogue(store, "cat1", "extra")
        assert len(got) == 1
        assert got[0].product_id == "UP1"
        assert got[0].name == "Astro Bot"
        assert got[0].list_name == "extra"
        # Order is not normalised upstream -- same discipline as the AEM feed.
        assert got[0].devices == ["PS4", "PS5"]

    def test_has_no_concept_id(self):
        # The grid answers about products, not concepts -- see
        # TestProductOnlyEntries for how PlusIndex copes with that.
        store = self.FakeStore([GridPage(products=[product("UP1")], is_last=True)])
        assert fetch_catalogue(store, "cat1", "extra")[0].concept_id is None

    def test_pages_to_exhaustion(self):
        page1 = GridPage(products=[product(f"UP{i}") for i in range(1000)], is_last=False)
        page2 = GridPage(products=[product("UP1000")], is_last=True)
        store = self.FakeStore([page1, page2])
        got = fetch_catalogue(store, "cat1", "extra")
        assert len(got) == 1001
        assert store.calls == [("cat1", 0), ("cat1", 1000)]

    def test_a_single_page_stops_without_a_second_request(self):
        store = self.FakeStore([GridPage(products=[product("UP1")], is_last=True)])
        fetch_catalogue(store, "cat1", "extra")
        assert store.calls == [("cat1", 0)]

    def test_a_failure_mid_page_does_not_publish_a_partial_answer(self):
        # A category that dies on page 2 must not silently return page 1's
        # partial list as if it were the whole catalogue -- that undercounts
        # exactly like the truncated AEM feed did.
        class DyingStore:
            def grid_page(self, category_id, *, offset=0, size=None):
                if offset == 0:
                    return GridPage(products=[product("UP1")], is_last=False)
                raise RuntimeError("HTTP 500")
        with pytest.raises(RuntimeError):
            fetch_catalogue(DyingStore(), "cat1", "extra")


class TestFetchAll:
    """Nothing here raises. resolve() is what decides whether a thin or empty
    catalogue is too little to trust (see TestDegradedFeedFallback below) --
    fetch_all's job is just to try each source, log what failed, and hand
    back whatever it has."""

    class OkStore:
        def grid_page(self, category_id, *, offset=0, size=None):
            return GridPage(products=[product("UP1")], is_last=True)

    class BrokenStore:
        def grid_page(self, category_id, *, offset=0, size=None):
            raise RuntimeError("HTTP 500")

    class MonthlyHttp:
        def get_json(self, url, headers=None):
            return feed(entry(9))

    class DeadHttp:
        def get_json(self, url, headers=None):
            raise RuntimeError("HTTP 404")

    def _fetch(self, http, store):
        return fetch_all(http, store,
                          extra_category_id="cat-extra", classics_category_id="cat-classics")

    def test_extra_and_classics_come_from_their_own_categories(self):
        got = self._fetch(self.MonthlyHttp(), self.OkStore())
        assert len(got["extra"]) == 1
        assert len(got["classics"]) == 1
        assert len(got["monthly"]) == 1

    def test_a_broken_category_does_not_raise_and_publishes_empty(self):
        got = self._fetch(self.MonthlyHttp(), self.BrokenStore())
        assert got["extra"] == []
        assert got["classics"] == []
        assert len(got["monthly"]) == 1        # unaffected by the store failing

    def test_a_dead_monthly_feed_does_not_raise_either(self):
        got = self._fetch(self.DeadHttp(), self.OkStore())
        assert got["monthly"] == []
        assert len(got["extra"]) == 1          # unaffected by the AEM feed failing

    def test_everything_failing_still_returns_cleanly(self):
        assert self._fetch(self.DeadHttp(), self.BrokenStore()) == {
            "extra": [], "classics": [], "monthly": []}

    def test_failures_are_logged(self, caplog):
        with caplog.at_level("WARNING", logger="ngp.psplus"):
            self._fetch(self.DeadHttp(), self.BrokenStore())
        logged = caplog.text
        assert "extra" in logged and "classics" in logged and "monthly" in logged


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


class TestProductOnlyEntries:
    """Grid-sourced entries carry no conceptId (TestFetchCatalogue). PlusIndex
    must still count and look them up correctly -- bucketing every one of
    them under the same missing key would collapse extra_count to 1 and make
    the publish guard block a perfectly healthy catalogue."""

    @staticmethod
    def entry_no_concept(product_id, list_name="extra", name="A Game"):
        return PlusEntry(list_name=list_name, concept_id=None, product_id=product_id,
                         name=name, devices=["PS5"], release_date=None)

    def test_each_product_gets_its_own_bucket(self):
        idx = PlusIndex({"extra": [
            self.entry_no_concept("UP1"), self.entry_no_concept("UP2"),
            self.entry_no_concept("UP3"),
        ]})
        assert idx.extra_count == 3

    def test_lookup_by_product_id_still_works(self):
        idx = PlusIndex({"extra": [self.entry_no_concept("UP1")]})
        assert idx.lookup(product_id="UP1").in_extra is True

    def test_a_product_id_never_matches_as_a_concept_id(self):
        idx = PlusIndex({"extra": [self.entry_no_concept("UP1")]})
        assert idx.lookup(concept_id="UP1") is None

    def test_mixes_cleanly_with_concept_keyed_entries(self):
        # Extra (grid, no conceptId) and Monthly (AEM, has conceptId) coexist
        # in the same index without one kind interfering with the other.
        idx = PlusIndex({
            "extra": [self.entry_no_concept("UP1")],
            "monthly": parse_feed(feed(entry(1)), "monthly"),
        })
        assert idx.extra_count == 2
        assert idx.lookup(product_id="UP1").in_extra is True
        assert idx.lookup(concept_id="1").in_extra is True


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
