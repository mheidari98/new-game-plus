"""PlayStation Store GraphQL client.

The storefront uses Apollo *persisted queries*: it sends a sha256 hash instead
of the query text and the server keeps an allowlist, so an arbitrary query is
rejected with "Query not whitelisted". We reuse the hashes the public web
client ships with, pinned below.

NEVER brute-force or probe for hashes. If every pinned hash fails, re-capture
them from browser DevTools traffic. Guessing at Sony's allowlist is
reconnaissance against a third party, not debugging.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

GRAPHQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
STORE_WEB = "https://store.playstation.com"

MAX_PAGE_SIZE = 1000        # size=2000 -> "must be less than or equal to 1000"
MAX_WINDOW = 10000          # offset + size > 10000 -> "Incorrect offset/limit"

OPERATIONS = {
    "categoryGridRetrieve": "4e41660b6732f35c99fc5541926b7502a09557924e8c2cfebd1beb1a5c8c8f81",
    "wcaProductStarRatingRetrive": "799fa113378f699281e0eda3154c54e03d763f6a98ad9a1378d58b1c2cb76cec",
    "metGetProductById": "a128042177bd93dd831164103d53b73ef790d56f51dae647064cb8f9d9fc9d1a",
    "metGetPricingDataByConceptId": "abcb311ea830e679fe2b697a27f755764535d825b24510ab1239a4ca3092bd09",
}

# Older but still-registered revisions, tried automatically.
FALLBACK_HASHES = {
    "categoryGridRetrieve": ["4ce7d410a4db2c8b635a48c1dcec375906ff63b19dadd87e073f8fd0c0481d35"],
    "wcaProductStarRatingRetrive": ["cedd370c39e89da20efa7b2e55710e88cb6e6843cc2f8203f7e73ba4751e7253"],
}

# The facet key uses a hyphen; the payload value uses an underscore. Feeding a
# product's own classification back into filterBy otherwise returns zero rows.
_FACET_ALIASES = {"ADD_ON_PACK": "ADD-ON_PACK"}

_MONEY = re.compile(r"(\d[\d,]*)(?:\.(\d{1,2}))?")


class PersistedQueryStale(RuntimeError):
    """No known hash for an operation is accepted any more."""


def money_to_cents(text) -> int | None:
    """Grid prices are formatted strings only -- there is no numeric field."""
    if not text:
        return None
    if "free" in str(text).lower():
        return 0
    m = _MONEY.search(str(text))
    if not m:
        return None                       # "Unavailable"
    return int(m.group(1).replace(",", "")) * 100 + int((m.group(2) or "0").ljust(2, "0"))


def normalise_classification(value: str) -> str:
    """Map a payload classification onto its filterBy facet key."""
    return _FACET_ALIASES.get(value, value)


def _alias_filter(clause: str) -> str:
    """Alias the value half of a "facet:VALUE" clause, leaving the name alone."""
    name, _, value = clause.partition(":")
    return f"{name}:{normalise_classification(value)}" if value else clause


@dataclass
class GridPage:
    products: list = field(default_factory=list)
    concepts: list | None = None
    total: int = 0
    is_last: bool = True
    facets: list = field(default_factory=list)

    def product_to_concept(self) -> dict[str, str]:
        """Concept grids carry every product id under its concept, so 17 bulk
        requests replace ~4,300 per-product lookups."""
        return {
            p["id"]: c["id"]
            for c in (self.concepts or [])
            for p in (c.get("products") or [])
        }

    def facet_values(self, name) -> list[tuple[str, int]]:
        """`[(key, count)]` for one facet, empty if it was not requested.

        Read rather than hardcoded: the store gained an 11th price bucket
        between this project's design and its build.
        """
        for facet in self.facets:
            if facet.get("name") == name:
                return [(v["key"], v.get("count") or 0)
                        for v in facet.get("values") or []]
        return []


class StoreClient:
    def __init__(self, http, locale="en-US"):
        self._http = http
        self._locale = locale
        self._hashes = dict(OPERATIONS)

    def _headers(self):
        return {
            "accept": "application/json",
            # Required even on GET: Apollo's CSRF guard returns a gzipped 400
            # without it, which looks like a binary garbage response.
            "content-type": "application/json",
            "x-psn-store-locale-override": self._locale,
            "origin": STORE_WEB,
            "referer": STORE_WEB + "/",
        }

    def call(self, operation, variables) -> dict:
        for sha in [self._hashes[operation]] + FALLBACK_HASHES.get(operation, []):
            params = {
                "operationName": operation,
                "variables": json.dumps(variables, separators=(",", ":")),
                "extensions": json.dumps(
                    {"persistedQuery": {"version": 1, "sha256Hash": sha}},
                    separators=(",", ":"),
                ),
            }
            payload = self._http.get_json(
                f"{GRAPHQL_URL}?{urlencode(params)}", headers=self._headers()
            )
            errors = payload.get("errors") or []
            if errors:
                message = json.dumps(errors)[:300]
                if "PersistedQueryNotFound" in message or "not whitelisted" in message:
                    continue
                raise RuntimeError(f"{operation} failed: {message}")
            self._hashes[operation] = sha      # remember what worked
            return payload.get("data") or {}
        raise PersistedQueryStale(
            f"no working hash for {operation}; re-capture from DevTools, do not guess"
        )

    def grid_page(self, category_id, *, offset=0, size=MAX_PAGE_SIZE, sort_by=None,
                  filter_by=(), facet_options=(), baseline_total=None) -> GridPage:
        if size > MAX_PAGE_SIZE:
            raise ValueError(f"page size {size} exceeds the server limit of {MAX_PAGE_SIZE}")
        if offset + size > MAX_WINDOW:
            raise ValueError(
                f"offset+size {offset + size} exceeds the {MAX_WINDOW} window; "
                "slice by a disjoint facet instead"
            )

        data = self.call("categoryGridRetrieve", {
            "id": category_id,
            "pageArgs": {"size": size, "offset": offset},
            "sortBy": sort_by,
            "filterBy": [_alias_filter(f) for f in filter_by],
            # Facet counts are only returned for the facets you ask for.
            "facetOptions": list(facet_options),
        })
        grid = data.get("categoryGridRetrieve")
        if grid is None:
            raise RuntimeError(f"category {category_id} returned no grid")

        info = grid.get("pageInfo") or {}
        total = int(info.get("totalCount") or 0)

        # An unknown facet NAME is silently ignored (returns everything); an
        # unknown VALUE returns nothing. Guard both, not just "did it change".
        if filter_by and baseline_total is not None:
            if total >= baseline_total:
                raise ValueError(f"filter {list(filter_by)} was ignored: still {total} rows")
            if total == 0:
                raise ValueError(f"filter {list(filter_by)} matched no rows; bad facet value?")

        return GridPage(
            products=grid.get("products") or [],
            concepts=grid.get("concepts"),
            total=total,
            is_last=bool(info.get("isLast", True)),
            facets=grid.get("facetOptions") or [],
        )

    def product(self, product_id) -> dict:
        """Genres, ESRB, publisher, compatibilityNotices AND the concept id.

        metGetConceptById is redundant: conceptRetrieve is exactly this minus
        the `concept` key, so this one call covers both.
        """
        return (self.call("metGetProductById", {"productId": product_id})
                .get("productRetrieve") or {})

    def stars(self, product_id) -> dict:
        """The only source of the PSN star rating."""
        return (self.call("wcaProductStarRatingRetrive", {"productId": product_id})
                .get("productRetrieve") or {})
