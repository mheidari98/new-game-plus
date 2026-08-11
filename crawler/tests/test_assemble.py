"""Joining the crawl's row map into publishable rows.

The hazard here is not arithmetic, it is identity. Sony sells several products
under one *concept*: concept 234689 is "It Takes Two" at $39.99 and also
"Hazelight Bundle" at 67% off, and the crawl meets them through two different
grids. Keying the published row by concept and keeping whichever product
arrived first meant the deals grid's bundle silently spoke for the game -- 758
priced products were unreachable, and searching the site for "It Takes Two"
returned nothing while the game sat in the index under another name.

Enrichment stays keyed by concept, because that is what costs requests.
Publishing fans back out to every product we actually saw a price for.
"""

from types import SimpleNamespace

import pytest

from main import _assemble, _place
from ngp.cache import Cache
from ngp.components import Weights


def cached(payloads=None):
    """The real Cache, in memory. It defaults to `:memory:` and does no I/O, so
    a fake would only differ by ignoring `ttl_days` and drifting."""
    cache = Cache()
    for (source, key), body in (payloads or {}).items():
        cache.put(source, key, body)
    return cache


class NoPlus:
    def lookup(self, *, concept_id=None, product_id=None):
        return None


def product(pid, name, base="$39.99", discounted=None, **price):
    return {"id": pid, "name": name,
            "price": {"basePrice": base, "discountedPrice": discounted or base, **price}}


@pytest.fixture
def args():
    return SimpleNamespace(ttl=30, critic_ttl=14, playtime_ttl=180, igdb_ttl=90)


@pytest.fixture
def weights():
    return Weights.defaults()


def assemble(rows, cache, args, weights, plus=None):
    return _assemble(rows, cache, plus or NoPlus(), weights, args)


class TestSiblingProducts:
    """Concept 234689, reproduced."""

    @pytest.fixture
    def concept_with_two_skus(self):
        return {
            "234689": {
                "concept_id": "234689",
                "rank": 130,
                # What the deals grid handed us: the bundle, on sale.
                "product": product("UP0006-PPSA02424_00-HAZELIGHTBUNDLE5",
                                   "Hazelight Bundle", base="$59.99",
                                   discounted="$19.79"),
                # What the catalogue sweep handed us: the concept's own product.
                "siblings": [product("UP0006-PPSA02342_00-ITTAKESTWORETAIL",
                                     "It Takes Two PS4 & PS5", base="$39.99")],
            }
        }

    def test_publishes_both_priced_products(self, concept_with_two_skus, args, weights):
        games = assemble(concept_with_two_skus, cached(), args, weights)
        assert [g["name"] for g in games] == ["Hazelight Bundle", "It Takes Two PS4 & PS5"]

    def test_each_product_keeps_its_own_price_and_discount(
            self, concept_with_two_skus, args, weights):
        bundle, game = assemble(concept_with_two_skus, cached(), args, weights)
        assert (bundle["price_cents"], bundle["base_cents"], bundle["discount_pct"]) \
            == (1979, 5999, 67)
        # The bundle's 67% must not be attributed to the game, which is not on sale.
        assert (game["price_cents"], game["base_cents"], game["discount_pct"]) \
            == (3999, 3999, 0)

    def test_a_sibling_does_not_inherit_the_enriched_products_name(
            self, concept_with_two_skus, args, weights):
        # The detail call is made once, for the row's primary product, so its
        # name describes that product alone. Letting a sibling take it renames
        # every edition of a game after whichever one got enriched.
        cache = cached({("product", "234689"): {"name": "Hazelight Bundle",
                                                "genres": ["Adventure"]}})
        _, game = assemble(concept_with_two_skus, cache, args, weights)
        assert game["name"] == "It Takes Two PS4 & PS5"

    def test_siblings_share_the_concepts_enrichment(
            self, concept_with_two_skus, args, weights):
        # Same game, one detail fetch. Split-screen, genres and rating are
        # properties of the game, not of which SKU you buy.
        cache = cached({("product", "234689"): {
            "genres": ["Adventure"], "esrb": "ESRB_TEEN", "local_players": 2,
            "art": "https://image.api.playstation.com/cdn/UP0006/x.png"}})
        bundle, game = assemble(concept_with_two_skus, cache, args, weights)
        for row in (bundle, game):
            assert row["genres"] == ["Adventure"]
            assert row["esrb"] == "ESRB_TEEN"
            assert row["local_players"] == 2
            assert row["art"] == "https://image.api.playstation.com/cdn/UP0006/x.png"

    def test_ps_plus_membership_is_asked_per_product(
            self, concept_with_two_skus, args, weights):
        # A catalogue can carry the base game without the bundle, so the
        # question has to be asked of each product rather than answered once.
        asked = []

        class Plus:
            def lookup(self, *, concept_id=None, product_id=None):
                asked.append(product_id)
                return None

        assemble(concept_with_two_skus, cached(), args, weights, plus=Plus())
        assert asked == ["UP0006-PPSA02424_00-HAZELIGHTBUNDLE5",
                         "UP0006-PPSA02342_00-ITTAKESTWORETAIL"]


class TestDroppedRows:
    def test_an_unavailable_sibling_does_not_take_the_concept_with_it(self, args, weights):
        rows = {"1": {"concept_id": "1", "rank": 0,
                      "product": product("P-A", "Playable"),
                      "siblings": [product("P-B", "Delisted", base="Unavailable")]}}
        games = assemble(rows, cached(), args, weights)
        assert [g["name"] for g in games] == ["Playable"]

    def test_an_unavailable_primary_does_not_take_its_siblings_with_it(self, args, weights):
        rows = {"1": {"concept_id": "1", "rank": 0,
                      "product": product("P-A", "Delisted", base="Unavailable"),
                      "siblings": [product("P-B", "Playable")]}}
        games = assemble(rows, cached(), args, weights)
        assert [g["name"] for g in games] == ["Playable"]

    def test_a_product_claimed_by_two_concepts_is_published_once(self, args, weights):
        # product_to_concept is built from two overlapping grids, so the same
        # product id can arrive under two concept keys. Publishing it twice
        # would double-count it in every total on the site.
        shared = product("P-SHARED", "Shared")
        rows = {"1": {"concept_id": "1", "rank": 0, "product": shared, "siblings": []},
                "2": {"concept_id": "2", "rank": 1, "product": shared, "siblings": []}}
        games = assemble(rows, cached(), args, weights)
        assert [g["id"] for g in games] == ["P-SHARED"]


class TestPlace:
    """`_place` owns the row shape and the dedupe for both callers.

    It is called from two passes -- the deals grid and the catalogue sweep --
    and they used to disagree about what "already have it" meant, so the deals
    pass could record the same product twice and `_assemble` carried a second
    dedupe to mop it up.
    """

    def test_first_product_of_a_concept_opens_a_row(self):
        rows = {}
        assert _place(rows, "c1", product("P-A", "A"), 7) == "row"
        assert rows["c1"] == {"concept_id": "c1", "rank": 7,
                              "product": product("P-A", "A"), "siblings": []}

    def test_a_different_product_of_a_held_concept_becomes_a_sibling(self):
        rows = {}
        _place(rows, "c1", product("P-A", "A"), 0)
        assert _place(rows, "c1", product("P-B", "B"), 1) == "sibling"
        assert [s["id"] for s in rows["c1"]["siblings"]] == ["P-B"]

    def test_the_rank_of_the_first_product_wins(self):
        # Deals-set rows lead the site, and a later catalogue rank must not
        # demote a concept the deals grid already ranked.
        rows = {}
        _place(rows, "c1", product("P-A", "A"), 3)
        _place(rows, "c1", product("P-B", "B"), 100_000)
        assert rows["c1"]["rank"] == 3

    def test_the_same_product_twice_is_recorded_once(self):
        rows = {}
        _place(rows, "c1", product("P-A", "A"), 0)
        assert _place(rows, "c1", product("P-A", "A"), 1) == "duplicate"
        assert rows["c1"]["siblings"] == []

    def test_a_product_already_a_sibling_is_not_added_again(self):
        # The deals grid and the catalogue sweep overlap, so both passes can
        # offer the same secondary SKU.
        rows = {}
        _place(rows, "c1", product("P-A", "A"), 0)
        _place(rows, "c1", product("P-B", "B"), 1)
        assert _place(rows, "c1", product("P-B", "B"), 2) == "duplicate"
        assert len(rows["c1"]["siblings"]) == 1


class TestNames:
    def test_runs_of_whitespace_are_collapsed(self, args, weights):
        # The concept grid returns "It Takes Two  PS4 & PS5" with a double
        # space, which renders as a gap and breaks exact-match search.
        rows = {"1": {"concept_id": "1", "rank": 0,
                      "product": product("P-A", "It Takes Two  PS4 & PS5"),
                      "siblings": []}}
        assert assemble(rows, cached(), args, weights)[0]["name"] \
            == "It Takes Two PS4 & PS5"
