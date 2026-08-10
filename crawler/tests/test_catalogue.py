"""Enumerating the full catalogue by price-bucket slicing.

The store caps pagination at `offset + size <= 10000` while the catalogue is
~12.8k concepts, so the only way through is to page each price bucket inside
its own filtered total. Every hazard asserted here was measured live.
"""

import pytest

from main import catalogue_concepts
from ngp.store import GridPage


class FakeStore:
    """Serves a facet head request and then one page per (bucket, offset)."""

    def __init__(self, buckets, pages, total=10_000):
        self.buckets = buckets
        self.pages = pages          # {(bucket_key, offset): [concept, ...]}
        self.total = total
        self.calls = []

    def grid_page(self, category, *, offset=0, size=1000, sort_by=None,
                  filter_by=(), facet_options=(), baseline_total=None):
        self.calls.append({"offset": offset, "filter_by": list(filter_by),
                           "facet_options": list(facet_options),
                           "baseline_total": baseline_total})
        if facet_options:
            return GridPage(total=self.total, facets=[{
                "name": "webBasePrice",
                "values": [{"key": k, "count": c} for k, c in self.buckets],
            }])
        key = filter_by[0].split(":", 1)[1]
        concepts = self.pages.get((key, offset), [])
        held = sum(c for k, c in self.buckets if k == key)
        return GridPage(concepts=concepts, total=held,
                        is_last=offset + len(concepts) >= held)


def concept(cid, name=None, products=("UP1-X_00-A",)):
    return {
        "id": cid,
        "name": name or f"Game {cid}",
        "price": {"basePrice": "$19.99", "discountedPrice": "$19.99"},
        "products": [{"id": p} for p in products],
    }


class TestBucketSweep:
    def test_collects_every_bucket(self):
        store = FakeStore(
            buckets=[("0-199", 2), ("200-499", 1)],
            pages={("0-199", 0): [concept("1"), concept("2")],
                   ("200-499", 0): [concept("3")]},
        )
        rows, _ = catalogue_concepts(store, "cat")
        assert sorted(r["_concept_id"] for r in rows) == ["1", "2", "3"]

    def test_reads_the_bucket_list_from_the_response(self):
        # Never hardcoded: the store gained an 11th price bucket between this
        # project's design and its build.
        store = FakeStore(buckets=[("42-99", 1)], pages={("42-99", 0): [concept("1")]})
        catalogue_concepts(store, "cat")
        assert [c["filter_by"] for c in store.calls[1:]] == [["webBasePrice:42-99"]]

    def test_dedupes_overlapping_buckets(self):
        # "Free" (0-0) is a strict subset of "Under $1.99" (0-199), so summing
        # the bucket counts double-counts and the sweep must key by id.
        store = FakeStore(
            buckets=[("0-0", 1), ("0-199", 2)],
            pages={("0-0", 0): [concept("1")],
                   ("0-199", 0): [concept("1"), concept("2")]},
        )
        rows, _ = catalogue_concepts(store, "cat")
        assert sorted(r["_concept_id"] for r in rows) == ["1", "2"]

    def test_pages_within_a_bucket(self):
        store = FakeStore(
            buckets=[("0-199", 3)],
            pages={("0-199", 0): [concept("1"), concept("2")],
                   ("0-199", 2): [concept("3")]},
        )
        rows, _ = catalogue_concepts(store, "cat")
        assert len(rows) == 3

    def test_skips_empty_buckets_without_a_request(self):
        store = FakeStore(buckets=[("8000-9999", 0), ("0-199", 1)],
                          pages={("0-199", 0): [concept("1")]})
        catalogue_concepts(store, "cat")
        assert all("8000-9999" not in c["filter_by"][0] for c in store.calls[1:])

    def test_reports_unreleased_concepts_rather_than_dropping_them_silently(self):
        # price is null on unreleased titles, so they sit in no bucket at all
        # and this enumeration is a lower bound. Say so, do not gloss it.
        store = FakeStore(
            buckets=[("0-199", 2)],
            pages={("0-199", 0): [concept("1"), concept("2", products=())]},
        )
        rows, unreleased = catalogue_concepts(store, "cat")
        assert len(rows) == 1
        assert unreleased == 1


class TestSweepGuards:
    def test_refuses_a_bucket_bigger_than_the_pagination_window(self):
        # Paging it would silently stop at 10,000 and look like success.
        store = FakeStore(buckets=[("0-199", 10_001)], pages={})
        with pytest.raises(RuntimeError, match="window"):
            catalogue_concepts(store, "cat")

    def test_refuses_a_category_with_no_price_facet(self):
        store = FakeStore(buckets=[], pages={})
        with pytest.raises(RuntimeError, match="facet"):
            catalogue_concepts(store, "cat")

    def test_hands_the_grid_the_baseline_so_it_can_check_the_filter_took_effect(self):
        # An unknown facet name is silently ignored and returns the whole
        # catalogue; an unknown value returns nothing. grid_page catches both,
        # but only when it is given the unfiltered total to compare against.
        store = FakeStore(buckets=[("0-199", 3)], total=9_999,
                          pages={("0-199", 0): [concept("1"), concept("2")],
                                 ("0-199", 2): [concept("3")]})
        catalogue_concepts(store, "cat")
        first, second = store.calls[1], store.calls[2]
        assert first["baseline_total"] == 9_999
        assert second["baseline_total"] is None   # only the first page needs it
