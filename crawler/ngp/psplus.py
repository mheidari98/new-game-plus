"""PlayStation Plus catalogue membership.

Extra and Classics come from the store's own product grid (categoryGridRetrieve
-- the same GraphQL operation and pinned hash already used for
deals/all_games/free_to_play in store.py), via the category ids
`plus_extra`/`plus_classics` in categories.json. This replaced the AEM feed at
GAMESLIST_URL below for those two tiers on 2026-09-22: that feed started
truncating its answer to ~55 of ~500 entries on 2026-09-19 and never
recovered, while the grid -- backed by the same live product database as
pricing -- was unaffected throughout, discovered the same sanctioned way as
every other category (a public browse page, not guessed).

Monthly Essentials has no store-category equivalent -- it is a rotating claim
list, not a static catalogue -- so it still reads the AEM feed.

Grid rows carry an exact productId but no conceptId. AEM rows carry both.
PlusIndex copes with either shape; see its docstring.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

GAMESLIST_URL = "https://www.playstation.com/bin/imagic/gameslist"

# Only Monthly is still read from here. Kept as a mapping (rather than a bare
# URL) in case Extra or Classics ever has to fall back to the AEM feed again --
# ubisoft-classics-list is a strict SUBSET of plus-games-list and must never be
# unioned in if that happens; it double-counted 68 entries (+10.6%) last time.
LISTS = {
    "monthly": "plus-monthly-games-list",
}


@dataclass(frozen=True)
class PlusEntry:
    list_name: str
    concept_id: str | None
    product_id: str | None
    name: str
    devices: list
    release_date: str | None

    @property
    def in_extra(self) -> bool:
        """Monthly Essentials are claimable and keepable by every tier, so they
        rank as already-owned like Extra."""
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


def fetch_catalogue(store, category_id, list_name) -> list[PlusEntry]:
    """Page a PS+ store category (categoryGridRetrieve) to exhaustion.

    Rows carry an exact productId but no conceptId, so `concept_id` is left
    None; PlusIndex buckets by product_id when it is absent (see its
    docstring), and matching on an exact SKU is what this project already
    prefers over matching on a concept.

    Deliberately does not catch anything: a failure on page 2 must not
    silently publish page 1's partial answer as if it were the whole
    catalogue -- that undercounts the same way the truncated AEM feed did.
    fetch_all is what decides how to react to a failure here.
    """
    entries, offset = [], 0
    while True:
        page = store.grid_page(category_id, offset=offset)
        entries.extend(
            PlusEntry(
                list_name=list_name,
                concept_id=None,
                product_id=row["id"],
                name=row.get("name") or "",
                # Order is not normalised upstream, same as the AEM feed.
                devices=sorted(row.get("platforms") or []),
                release_date=None,
            )
            for row in page.products
        )
        offset += len(page.products)
        if page.is_last or not page.products:
            break
    return entries


def fetch_all(http, store, *, extra_category_id, classics_category_id,
              locale="en-us") -> dict[str, list[PlusEntry]]:
    """Extra and Classics from the store grid, Monthly from the AEM feed.

    Nothing here raises. A source that fails or comes back thin publishes
    empty (or whatever partial answer another source failing does not
    affect); resolve() is what decides whether the result is too little to
    trust and needs the last-good snapshot instead.
    """
    out = {}
    for key, category_id in (("extra", extra_category_id), ("classics", classics_category_id)):
        try:
            out[key] = fetch_catalogue(store, category_id, key)
        except Exception as exc:
            log.warning("ps+ %s category unreadable, publishing it empty: %s", key, exc)
            out[key] = []

    try:
        payload = http.get_json(
            f"{GAMESLIST_URL}?locale={locale}&categoryList={LISTS['monthly']}",
            headers={"accept": "application/json"},
        )
        out["monthly"] = parse_feed(payload, "monthly")
    except Exception as exc:
        # Nice to have, so no raise -- but an empty catalogue publishes every
        # game as not-in-it, which is a confident wrong answer were it load
        # bearing. It logs instead so a moved URL is still visible.
        log.warning("ps+ monthly list unreadable, publishing it empty: %s", exc)
        out["monthly"] = []
    return out


class PlusIndex:
    """Exact-id lookup over the catalogues.

    conceptId is NOT unique within an AEM feed (18 conceptIds cover 38 Extra
    entries, e.g. the TimeSplitters trilogy), so entries are bucketed rather
    than keyed 1:1. productId is unique everywhere.

    Grid-sourced entries (fetch_catalogue) have no conceptId at all. Bucketing
    them under a shared `None` key would collapse extra_count to 1 regardless
    of how many products there really are -- exactly the truncation this
    index exists to catch, self-inflicted. So the bucket key falls back to a
    namespaced product_id when concept_id is absent: still one bucket per
    product, but a prefix that a real conceptId (Sony's are bare numbers) can
    never collide with, however product ids happen to be shaped.
    """

    _PREFERENCE = {"extra": 0, "monthly": 1, "classics": 2}

    def __init__(self, catalogues: dict[str, list[PlusEntry]]):
        self._by_concept: dict[str, list[PlusEntry]] = {}
        self._by_product: dict[str, PlusEntry] = {}
        for entries in catalogues.values():
            for e in entries:
                key = e.concept_id or f"product:{e.product_id}"
                self._by_concept.setdefault(key, []).append(e)
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

    @property
    def extra_count(self) -> int:
        """Distinct catalogue entries (concepts, or products where there is no
        concept) with at least one Extra (or Monthly) membership."""
        return sum(any(e.in_extra for e in v) for v in self._by_concept.values())

    def __len__(self):
        return len(self._by_concept)


def _write_snapshot(path, catalogues, today: date) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_on": today.isoformat(),
        "catalogues": {k: [asdict(e) for e in v] for k, v in catalogues.items()},
    }
    # Replaced whole, so a run killed mid-write cannot leave half a snapshot.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def _read_snapshot(path):
    """(catalogues, saved_on) or None. Anything unreadable is None: a snapshot
    is a convenience, and a bad one must not take the crawl down with it."""
    try:
        raw = json.loads(Path(path).read_text())
        return (
            {k: [PlusEntry(**e) for e in v] for k, v in raw["catalogues"].items()},
            date.fromisoformat(raw["saved_on"]),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def resolve(catalogues, snapshot_path, today: date, *, floor, max_age_days):
    """Pick the catalogue to publish against: (PlusIndex, stale_since | None).

    fetch_all never raises -- a source that fails or comes back thin just
    publishes less. So this is the only place that decides whether "less" is
    too little: a live catalogue under `floor` is replaced by the last healthy
    one, and the caller is told how old it is. A degraded source never
    overwrites the snapshot, and a snapshot that is itself under the floor or
    past `max_age_days` is not trusted -- the live index comes back and the
    publish guard blocks, exactly as if there were no snapshot at all.
    """
    live = PlusIndex(catalogues)
    if live.extra_count >= floor:
        _write_snapshot(snapshot_path, catalogues, today)
        return live, None

    saved = _read_snapshot(snapshot_path)
    if saved is not None:
        old_catalogues, saved_on = saved
        old = PlusIndex(old_catalogues)
        age = (today - saved_on).days
        if old.extra_count >= floor and age <= max_age_days:
            log.warning(
                "PS+ Extra catalogue returned %d entries (floor %d); using the snapshot "
                "from %s (%d days old, %d entries)",
                live.extra_count, floor, saved_on, age, old.extra_count)
            return old, saved_on
        log.warning("PS+ snapshot from %s is unusable (%d days old, %d entries)",
                    saved_on, age, old.extra_count)
    else:
        log.warning("PS+ Extra catalogue returned %d entries (floor %d) and there is "
                    "no snapshot to fall back on", live.extra_count, floor)
    return live, None
