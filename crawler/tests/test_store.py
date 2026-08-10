"""PlayStation Store GraphQL client.

The store uses Apollo persisted queries: an arbitrary query is rejected, so
operation hashes are pinned. Every quirk asserted here was measured against
the live US store.
"""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from ngp.store import (
    MAX_PAGE_SIZE,
    MAX_WINDOW,
    PersistedQueryStale,
    StoreClient,
    money_to_cents,
    normalise_classification,
)


class FakeHttp:
    """Stands in for HttpClient, recording requests and replaying payloads."""

    def __init__(self, *payloads):
        self.queue = list(payloads)
        self.calls = []

    def get_json(self, url, headers=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return self.queue.pop(0) if self.queue else {"data": {}}

    def last_params(self):
        q = parse_qs(urlsplit(self.calls[-1]["url"]).query)
        return {k: v[0] for k, v in q.items()}


def grid_payload(products=None, concepts=None, total=0, is_last=True, facets=None):
    return {
        "data": {
            "categoryGridRetrieve": {
                "products": products or [],
                "concepts": concepts,
                "pageInfo": {"totalCount": total, "isLast": is_last},
                "facetOptions": facets or [],
            }
        }
    }


def product(pid="UP1-X_00-A", name="A Game", price="$59.99",
            discounted="$29.99", discount="-50%", cls="FULL_GAME"):
    return {
        "id": pid, "name": name, "npTitleId": pid.split("-")[1],
        "storeDisplayClassification": cls, "platforms": ["PS5"],
        "price": {"basePrice": price, "discountedPrice": discounted,
                  "discountText": discount, "isFree": False},
        "media": [{"type": "IMAGE", "role": "MASTER", "url": "https://img/x.png"}],
    }


class TestMoneyParsing:
    """The grid carries NO numeric price field -- only formatted strings.
    4,311 of 4,336 match the money pattern; the rest are these two cases."""

    def test_parses_dollars_and_cents(self):
        assert money_to_cents("$59.99") == 5999

    def test_parses_thousands_separator(self):
        assert money_to_cents("$1,299.00") == 129900

    def test_unavailable_is_none(self):
        assert money_to_cents("Unavailable") is None

    def test_free_is_zero(self):
        assert money_to_cents("Free") == 0

    def test_none_is_none(self):
        assert money_to_cents(None) is None

    def test_whole_dollars(self):
        assert money_to_cents("$20") == 2000


class TestClassificationAlias:
    """The facet KEY is ADD-ON_PACK (hyphen); the payload VALUE is
    ADD_ON_PACK (underscore). Feeding the payload value back into filterBy
    silently returns zero rows. Sole exception among 18 classes."""

    def test_payload_value_maps_to_facet_key(self):
        assert normalise_classification("ADD_ON_PACK") == "ADD-ON_PACK"

    def test_other_classes_pass_through(self):
        assert normalise_classification("FULL_GAME") == "FULL_GAME"
        assert normalise_classification("GAME_BUNDLE") == "GAME_BUNDLE"

    def test_filter_value_is_aliased_on_the_wire(self):
        # Passing a product's own classification straight through would send
        # ADD_ON_PACK and silently match zero rows.
        http = FakeHttp(grid_payload(products=[product()], total=179))
        StoreClient(http).grid_page(
            "cat-1", filter_by=["storeDisplayClassification:ADD_ON_PACK"],
            baseline_total=4336)
        sent = json.loads(http.last_params()["variables"])["filterBy"]
        assert sent == ["storeDisplayClassification:ADD-ON_PACK"]

    def test_filter_names_are_left_alone(self):
        http = FakeHttp(grid_payload(products=[product()], total=1387))
        StoreClient(http).grid_page(
            "cat-1", filter_by=["productGenres:ACTION"], baseline_total=4336)
        sent = json.loads(http.last_params()["variables"])["filterBy"]
        assert sent == ["productGenres:ACTION"]


class TestRequestShape:
    def test_sends_content_type_even_on_get(self):
        # Apollo's CSRF guard returns a gzipped 400 without it, which reads
        # as binary garbage rather than a clear error.
        http = FakeHttp(grid_payload(total=1))
        StoreClient(http).grid_page("cat-1")
        assert http.calls[0]["headers"]["content-type"] == "application/json"

    def test_sends_locale_override(self):
        http = FakeHttp(grid_payload(total=1))
        StoreClient(http).grid_page("cat-1")
        assert http.calls[0]["headers"]["x-psn-store-locale-override"] == "en-US"

    def test_sends_the_pinned_hash(self):
        http = FakeHttp(grid_payload(total=1))
        StoreClient(http).grid_page("cat-1")
        ext = json.loads(http.last_params()["extensions"])
        assert len(ext["persistedQuery"]["sha256Hash"]) == 64

    def test_passes_category_and_paging_in_variables(self):
        http = FakeHttp(grid_payload(total=1))
        StoreClient(http).grid_page("cat-1", offset=100, size=50)
        v = json.loads(http.last_params()["variables"])
        assert v["id"] == "cat-1"
        assert v["pageArgs"] == {"size": 50, "offset": 100}


class TestPagingLimits:
    """Measured: size caps at 1000, and offset + size must be <= 10000."""

    def test_rejects_oversized_page(self):
        with pytest.raises(ValueError, match="1000"):
            StoreClient(FakeHttp()).grid_page("cat-1", size=MAX_PAGE_SIZE + 1)

    def test_rejects_paging_past_the_window(self):
        with pytest.raises(ValueError, match="10000"):
            StoreClient(FakeHttp()).grid_page("cat-1", offset=MAX_WINDOW, size=1)

    def test_allows_exactly_the_window_edge(self):
        http = FakeHttp(grid_payload(total=10000))
        StoreClient(http).grid_page("cat-1", offset=MAX_WINDOW - 1, size=1)
        assert len(http.calls) == 1


class TestGridShapes:
    """PS5 and PS4 are asymmetric: PS5/All-games are CONCEPT grids, PS4 and
    All-deals are PRODUCT grids. Code must handle both."""

    def test_reads_a_product_grid(self):
        http = FakeHttp(grid_payload(products=[product()], total=1))
        page = StoreClient(http).grid_page("cat-1")
        assert page.products[0]["id"] == "UP1-X_00-A"
        assert page.total == 1

    def test_reads_a_concept_grid(self):
        concepts = [{"id": "10002456", "name": "BG3",
                     "products": [{"id": "UP1-X_00-A"}, {"id": "UP1-X_00-B"}]}]
        http = FakeHttp(grid_payload(concepts=concepts, total=1))
        page = StoreClient(http).grid_page("cat-1")
        assert page.concepts[0]["id"] == "10002456"

    def test_concept_grid_yields_product_to_concept_pairs(self):
        # 17 bulk requests build this map; a per-product lookup would be 4,336.
        concepts = [{"id": "C1", "products": [{"id": "P1"}, {"id": "P2"}]},
                    {"id": "C2", "products": [{"id": "P3"}]}]
        http = FakeHttp(grid_payload(concepts=concepts, total=2))
        page = StoreClient(http).grid_page("cat-1")
        assert page.product_to_concept() == {"P1": "C1", "P2": "C1", "P3": "C2"}

    def test_concept_with_no_products_is_tolerated(self):
        # 70 of 6,987 PS5 concepts have an empty products[] (unreleased).
        http = FakeHttp(grid_payload(concepts=[{"id": "C1", "products": []}], total=1))
        assert StoreClient(http).grid_page("cat-1").product_to_concept() == {}


class TestFacetGuard:
    """Two opposite silent failures: an unknown facet NAME is ignored and
    returns the whole catalogue; an unknown VALUE returns zero rows. The
    naive guard ('did the count change?') passes the second while zeroing
    the dataset."""

    def test_rejects_a_filter_that_returned_everything(self):
        http = FakeHttp(grid_payload(total=10000))
        client = StoreClient(http)
        with pytest.raises(ValueError, match="ignored"):
            client.grid_page("cat-1", filter_by=["bogusFacet:X"], baseline_total=10000)

    def test_rejects_a_filter_that_returned_nothing(self):
        http = FakeHttp(grid_payload(total=0))
        client = StoreClient(http)
        with pytest.raises(ValueError, match="no rows"):
            client.grid_page("cat-1", filter_by=["ageRating:NOPE"], baseline_total=10000)

    def test_accepts_a_filter_that_narrowed_the_set(self):
        http = FakeHttp(grid_payload(products=[product()], total=1387))
        page = StoreClient(http).grid_page(
            "cat-1", filter_by=["productGenres:ACTION"], baseline_total=4336)
        assert page.total == 1387


class TestHashFallback:
    """Hashes rotate. Older still-registered revisions are tried before
    giving up -- and we never guess or probe for new ones."""

    def test_falls_back_when_the_primary_hash_is_rejected(self):
        stale = {"errors": [{"message": "PersistedQueryNotFound"}]}
        http = FakeHttp(stale, grid_payload(total=7))
        assert StoreClient(http).grid_page("cat-1").total == 7
        assert len(http.calls) == 2

    def test_raises_when_every_hash_is_rejected(self):
        stale = {"errors": [{"message": "PersistedQueryNotFound"}]}
        http = FakeHttp(*[stale] * 6)
        with pytest.raises(PersistedQueryStale):
            StoreClient(http).grid_page("cat-1")

    def test_a_working_hash_is_remembered_for_later_calls(self):
        stale = {"errors": [{"message": "PersistedQueryNotFound"}]}
        http = FakeHttp(stale, grid_payload(total=1), grid_payload(total=1))
        client = StoreClient(http)
        client.grid_page("cat-1")
        before = len(http.calls)
        client.grid_page("cat-1")
        assert len(http.calls) == before + 1, "should not retry the dead hash"

    def test_other_graphql_errors_are_not_swallowed(self):
        http = FakeHttp({"errors": [{"message": "Incorrect offset/limit"}]})
        with pytest.raises(RuntimeError, match="Incorrect offset"):
            StoreClient(http).grid_page("cat-1")
