"""PlayStation Plus catalogue feeds.

The public PS+ page renders client-side from an AEM endpoint. Every entry
carries a conceptId *and* a productId, so matching against store products is
exact -- there is no fuzzy title matching anywhere in this path, which is the
single biggest source of wrong answers avoided.

Tier mapping, from the page's own bundle:
  plus-games-list      Extra Game Catalog
  plus-classics-list   Premium Classics (disjoint from Extra)
  plus-monthly-games-list  this month's Essential games
  ubisoft-classics-list    a strict SUBSET of Extra -- never union it in,
                           it double-counts 68 entries (+10.6%)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

GAMESLIST_URL = "https://www.playstation.com/bin/imagic/gameslist"

LISTS = {
    "extra": "plus-games-list",
    "classics": "plus-classics-list",
    "monthly": "plus-monthly-games-list",
}


class PlusFeedUnavailable(RuntimeError):
    """The Extra catalogue could not be read. Never publish without it."""


@dataclass(frozen=True)
class PlusEntry:
    list_name: str
    concept_id: str
    product_id: str | None
    name: str
    devices: list
    release_date: str | None

    @property
    def in_extra(self) -> bool:
        """Monthly Essentials are claimable and keepable by every tier, so
        for ranking purposes they count as already-owned like Extra."""
        return self.list_name in ("extra", "monthly")

    @property
    def in_classics(self) -> bool:
        return self.list_name == "classics"


def parse_feed(payload, list_name) -> list[PlusEntry]:
    """Flatten the 27 alphabetical buckets into entries."""
    out = []
    for bucket in payload or []:
        for game in bucket.get("games") or []:
            concept_id = game.get("conceptId")
            if concept_id is None:
                continue
            out.append(PlusEntry(
                list_name=list_name,
                # The feed sends ints, the store sends strings. Normalise here
                # or every downstream comparison silently fails.
                concept_id=str(concept_id),
                product_id=game.get("productId"),
                name=game.get("name") or game.get("nameEn") or "",
                # Order is not normalised upstream: 11 entries are ['PS5','PS4'].
                devices=sorted(game.get("device") or []),
                release_date=game.get("releaseDate"),
            ))
    return out


# The Extra feed 404s intermittently: it went down on one US-runner crawl and
# answered with 471 entries minutes later. net.py cannot retry this for us --
# a 404 anywhere else is a real absence, not a blip -- and losing it costs the
# whole run, so it is asked again here before the crawl is abandoned.
EXTRA_ATTEMPTS = 4
EXTRA_BACKOFF_SECONDS = 20


def fetch_all(http, locale="en-us", sleep=time.sleep) -> dict[str, list[PlusEntry]]:
    """One request per list -- the endpoint ignores every batching attempt.

    Raises if the Extra catalogue cannot be read. A silent empty list would
    mark the entire store as not-in-PS+, which is a wrong answer everywhere
    and worse than no answer.
    """
    def fetch(category):
        return http.get_json(
            f"{GAMESLIST_URL}?locale={locale}&categoryList={category}",
            headers={"accept": "application/json"},
        )

    out = {}
    for key, category in LISTS.items():
        if key != "extra":
            try:
                out[key] = parse_feed(fetch(category), key)
            except Exception:
                out[key] = []       # Classics and Monthly are nice to have
            continue

        problem = "came back empty"
        for attempt in range(EXTRA_ATTEMPTS):
            if attempt:
                sleep(EXTRA_BACKOFF_SECONDS)
            try:
                out[key] = parse_feed(fetch(category), key)
            except Exception as exc:
                problem, out[key] = str(exc), []
            if out[key]:
                break
        if not out[key]:
            raise PlusFeedUnavailable(
                f"Extra catalogue unreadable after {EXTRA_ATTEMPTS} attempts: {problem}")
    return out


class PlusIndex:
    """Exact-id lookup over the catalogues.

    conceptId is NOT unique within a feed (18 conceptIds cover 38 Extra
    entries, e.g. the TimeSplitters trilogy), so entries are bucketed.
    productId is unique.
    """

    _PREFERENCE = {"extra": 0, "monthly": 1, "classics": 2}

    def __init__(self, catalogues: dict[str, list[PlusEntry]]):
        self._by_concept: dict[str, list[PlusEntry]] = {}
        self._by_product: dict[str, PlusEntry] = {}
        for entries in catalogues.values():
            for e in entries:
                self._by_concept.setdefault(e.concept_id, []).append(e)
                if e.product_id:
                    self._by_product[e.product_id] = e

    def lookup(self, *, concept_id=None, product_id=None) -> PlusEntry | None:
        if concept_id and (found := self._by_concept.get(str(concept_id))):
            # Extra is the cheaper tier, so it is the more useful answer when
            # a concept appears in more than one catalogue.
            return min(found, key=lambda e: self._PREFERENCE.get(e.list_name, 9))
        if product_id:
            return self._by_product.get(product_id)
        return None

    def __len__(self):
        return len(self._by_concept)
