"""Metacritic critic scores. One request per game.

The metascore is inline in the search response, so fetching the per-game page
would buy only the user score and the review count at double the request
budget. It is not fetched, which means the review *count* is unknown --
`components.quality` shrinks an unknown-depth score against a fixed
conservative weight rather than pretending it is deep.

Matching is the hard part, not fetching. Measured against the live backend:

* **A "(YYYY)" suffix is a disambiguator, not part of the title.** Searching
  "Silent Hill 2" returns `Silent Hill 2 (2001)` above the 2024 remake. Left
  in place, that 2001 reads as a version number and `numbers_compatible`
  discards *both* candidates.
* **Numbering must agree.** Mandatory for every fuzzy match in this project.
* **Platform and year break the remaining ties**, in that order of cheapness:
  our catalogue is PS4/PS5 only, so a PS2-only entry is almost never the row
  we are pricing.

Metacritic 403s on the literal User-Agent `Python-urllib/3.11` and on nothing
else; `net.HttpClient` sends a real one, so there is nothing to do here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

from .titles import normalize_title, numbers_compatible, similarity, split_year

log = logging.getLogger(__name__)

# Public key embedded in metacritic.com's own pages for its read-only backend.
BACKEND = "https://backend.metacritic.com"
API_KEY = "1MOZgmNFxvmljaQR1X9KAij9Mo4xAY3u"
GAME_TYPE = 13

# Below this the best candidate is a different game. 0.72 is inherited from the
# predecessor project, where it was tuned against real store names.
MIN_CONFIDENCE = 0.72

_PS_CURRENT = ("playstation 5", "playstation 4")


@dataclass(frozen=True)
class Critic:
    score: float            # 0-100 metascore
    title: str              # what we actually matched, for auditing
    year: int | None
    confidence: float
    url: str


class Metacritic:
    def __init__(self, http, limit: int = 8):
        self._http = http
        self._limit = limit

    def _search(self, query: str) -> list[dict]:
        url = (
            f"{BACKEND}/finder/metacritic/search/{quote(query)}/web"
            f"?apiKey={API_KEY}&limit={self._limit}&mcoTypeId={GAME_TYPE}&offset=0"
            f"&componentName=search&componentDisplayName=Search"
            f"&componentType=SearchResults"
        )
        payload = self._http.get_json(url, headers={"accept": "application/json"})
        items = ((payload or {}).get("data") or {}).get("items") or []
        return [i for i in items if i.get("typeId") == GAME_TYPE]

    def lookup(self, name: str | None, release_year: int | None = None) -> Critic | None:
        """Best critic score for a store title, or None if nothing matches."""
        query = normalize_title(name or "")
        if not query:
            return None
        try:
            candidates = self._search(query)
        except Exception as exc:      # single-source: degrade to null, never fail the run
            log.debug("metacritic search failed for %r: %s", name, exc)
            return None

        scored = []
        for item in candidates:
            title, suffix_year = split_year(item.get("title") or "")
            if not numbers_compatible(query, title):
                continue
            score = (item.get("criticScoreSummary") or {}).get("score")
            if not score:             # None = unreleased, 0 = no reviews at all
                continue
            confidence = similarity(query, title)
            if confidence < MIN_CONFIDENCE:
                continue
            year = item.get("premiereYear") or suffix_year
            scored.append((confidence + _bonus(item, year, release_year),
                           confidence, title, year, score, item.get("slug")))

        if not scored:
            return None
        _, confidence, title, year, score, slug = max(scored)
        return Critic(
            score=float(score),
            title=title,
            year=year,
            confidence=confidence,
            url=f"https://www.metacritic.com/game/{slug}/",
        )


def _bonus(item: dict, year: int | None, release_year: int | None) -> float:
    """Tie-breaks, worth less than the title similarity they adjust.

    Store release dates are re-release dates for remasters, so the year is a
    nudge rather than a filter -- but it is the stronger of the two, because
    the platform hint alone cannot separate two PS5 entries.
    """
    bonus = 0.0
    platforms = {(p.get("name") or "").lower() for p in item.get("platforms") or []}
    if platforms & set(_PS_CURRENT):
        bonus += 0.05
    if release_year and year:
        bonus += 0.20 if abs(year - release_year) <= 1 else -0.20
    return bonus
