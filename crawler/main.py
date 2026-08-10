"""Crawl the US PlayStation Store and publish the site index.

    python -m main --once                    # deals only, the M1 shape
    python -m main --once --limit 50         # quick iteration
    python -m main --backfill --cap 5000     # extend to the full catalogue
    python -m main --proxy http://127.0.0.1:2080

Runs with zero secrets. Enrichment is resumable: a run that dies leaves its
rows unstamped and the next one continues from the TTL cursor.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ngp.cache import Cache
from ngp.components import Weights, discount_depth, price_anchor, quality
from ngp.features import decode_features
from ngp.guard import PublishBlocked, check_publishable
from ngp.net import HttpClient
from ngp.psplus import PlusIndex, fetch_all
from ngp.publish import write_index
from ngp.ratelimit import AdaptiveLimiter
from ngp.ratings import Metacritic
from ngp.store import StoreClient, money_to_cents

log = logging.getLogger("ngp")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent          # so --out/--cache are the same wherever you run from

CATEGORIES = json.loads((HERE / "categories.json").read_text())
GAME_CLASSES = ["FULL_GAME", "GAME_BUNDLE", "PREMIUM_EDITION"]

# Workers x latency must not exceed the limiter's ceiling or they just queue.
# Measured latency ~0.8s, ceiling 6 req/s -> 5.
WORKERS = 5


def tier_of(base_cents):
    """Premium / mid / indie, from launch price."""
    if base_cents is None:
        return None
    if base_cents >= 5999:
        return "premium"
    return "mid" if base_cents >= 1999 else "indie"


def crawl(args):
    weights = Weights.defaults()
    limiter = AdaptiveLimiter(start=args.rate, ceiling=args.max_rate)
    state_dir = (REPO / args.cache).parent
    state_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(REPO / args.cache)
    previous = _read_previous_count(state_dir / "last_good.json")

    with HttpClient(limiter=limiter, proxy=args.proxy,
                    direct_hosts=["playstation.com", "metacritic.com"]) as http:
        store = StoreClient(http)

        log.info("fetching PS+ catalogues")
        plus = PlusIndex(fetch_all(http))
        log.info("PS+ index: %d concepts", len(plus))

        log.info("enumerating deals")
        baseline = store.grid_page(CATEGORIES["deals"], size=1).total
        products = _enumerate(store, CATEGORIES["deals"], baseline)
        log.info("deals: %d products, %d game-like", baseline, len(products))

        # The curated free-to-play category, not "price == 0 in the deals
        # grid" -- that returns cosmetic bundles like "War Thunder Cyberpunk
        # Airforce Snail Bundle" rather than games anyone would recommend.
        # One request for the whole thing.
        free = _free_to_play(store)
        log.info("free-to-play: %d concepts", len(free))
        products.extend(free)

        log.info("mapping products to concepts")
        concept_of = _concept_map(store)

        rows = _rank_and_seed(cache, products, concept_of)
        todo = cache.due("product", ttl_days=args.ttl, limit=args.limit or args.cap)
        log.info("enriching %d of %d", len(todo), len(rows))

        enriched, failed = _enrich(store, cache, todo, rows, args.ttl)

        matched, attempted = _enrich_critics(
            Metacritic(http), cache, rows, args.ttl, args.critic_ttl,
            args.limit or args.cap)
        log.info("metacritic: %d of %d looked up matched", matched, attempted)
        if attempted > 20 and matched == 0:
            log.warning("metacritic matched nothing in %d lookups; "
                        "quality scores will rest on store stars alone", attempted)

    games = _assemble(rows, cache, plus, weights, args.ttl, args.critic_ttl)
    out = REPO / args.out
    stats = write_index(games, weights, out,
                        generated_at=datetime.now(timezone.utc).isoformat())

    check_publishable(
        game_count=len(games),
        previous_game_count=previous,
        plus_extra_count=sum(1 for e in plus._by_concept.values()
                             if any(x.in_extra for x in e)),
        gzip_bytes=stats["gzip_bytes"],
        enrichment_attempted=enriched + failed,
        enrichment_failed=failed,
        filtered_counts={"game_classes": len(products)},
        baseline_count=baseline,
    )
    (state_dir / "last_good.json").write_text(json.dumps({"game_count": len(games)}))

    log.info("published %d games: %d B raw, %d B gzipped (%.0f%% of budget)",
             stats["count"], stats["raw_bytes"], stats["gzip_bytes"],
             100 * stats["gzip_bytes"] / 819_200)
    log.info("http: %s | limiter settled at %.2f req/s",
             http.stats, limiter.rate)
    cache.close()
    return stats


def _enumerate(store, category, baseline):
    """Walk the deals grid, games only."""
    out, offset = [], 0
    while offset < baseline:
        page = store.grid_page(
            category, offset=offset, size=1000,
            sort_by={"name": "sales30", "isAscending": False},
            filter_by=[f"storeDisplayClassification:{c}" for c in GAME_CLASSES],
            baseline_total=baseline if offset == 0 else None,
        )
        if not page.products:
            break
        out.extend(page.products)
        offset += len(page.products)
        if page.is_last:
            break
    return out


def _free_to_play(store):
    """The curated F2P catalogue, flattened into product-shaped rows.

    It is a CONCEPT grid, so price and name sit on the concept and the
    product id has to be pulled out of products[].
    """
    page = store.grid_page(CATEGORIES["free_to_play"], size=1000,
                           sort_by={"name": "sales30", "isAscending": False})
    rows = []
    for concept in page.concepts or []:
        products = concept.get("products") or []
        if not products:
            continue                       # unreleased; nothing to price
        rows.append({
            "id": products[0]["id"],
            "name": concept.get("name"),
            "platforms": [],               # filled from the product detail
            "price": concept.get("price") or {"basePrice": "Free",
                                              "discountedPrice": "Free",
                                              "isFree": True},
            "_concept_id": concept["id"],
        })
    return rows


def _concept_map(store):
    """Bulk product->concept from the concept grids: 17 requests, not 4,336."""
    mapping = {}
    for key in ("all_games", "all_ps5_games"):
        offset = 0
        while offset < 10_000:
            page = store.grid_page(CATEGORIES[key], offset=offset, size=1000)
            if not page.concepts:
                break
            mapping.update(page.product_to_concept())
            offset += len(page.concepts)
            if page.is_last:
                break
    return mapping


def _rank_and_seed(cache, products, concept_of):
    """Grid order is sales30, so enumeration order is popularity order."""
    rows = {}
    for rank, prod in enumerate(products):
        pid = prod["id"]
        # Concept-grid rows already know their concept. Otherwise use the bulk
        # map, falling back to the product id so an unmapped row still gets a
        # stable key.
        cid = prod.get("_concept_id") or concept_of.get(pid) or pid
        rows.setdefault(cid, {"concept_id": cid, "rank": rank, "product": prod})
    cache.upsert_concepts([
        {"concept_id": cid, "rank": r["rank"], "product_id": r["product"]["id"]}
        for cid, r in rows.items()
    ])
    return rows


def _enrich(store, cache, todo, rows, ttl):
    """Per-concept detail. Threads share one limiter, so the aggregate rate
    is governed exactly as it would be single-threaded."""
    ok = fail = 0

    def one(cid):
        row = rows.get(cid)
        if not row:
            return None
        try:
            detail = store.product(row["product"]["id"])
            stars = store.stars(row["product"]["id"])
            feats = decode_features(detail.get("compatibilityNotices"))
            rating = stars.get("starRating") or {}
            cache.put("product", cid, {
                "name": detail.get("name") or row["product"].get("name"),
                "genres": [g.get("value") for g in
                           (detail.get("combinedLocalizedGenres") or [])],
                "esrb": (detail.get("contentRating") or {}).get("name"),
                "release": detail.get("releaseDate"),
                "local_players": feats.local_players,
                "dualsense": feats.dualsense_haptics,
                "psvr2": feats.psvr2,
                "star_average": rating.get("averageRating"),
                "star_count": rating.get("totalRatingsCount"),
            })
            cache.mark("product", cid)
            return True
        except Exception as exc:
            log.warning("enrich %s failed: %s", cid, exc)
            return False

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for result in pool.map(one, todo):
            if result is True:
                ok += 1
            elif result is False:
                fail += 1
    return ok, fail


def _enrich_critics(metacritic, cache, rows, ttl, critic_ttl, limit):
    """Metacritic, one request per concept, on its own TTL.

    A miss is stored exactly like a hit. Otherwise every run re-asks about the
    same few hundred obscure titles that will never have a Metacritic entry.
    """
    todo = cache.due("critic", ttl_days=critic_ttl, limit=limit)

    def one(cid):
        row = rows.get(cid)
        detail = cache.get("product", cid, ttl_days=ttl) or {}
        name = detail.get("name") or (row and row["product"].get("name"))
        if not name:
            return None
        try:
            critic = metacritic.lookup(name, _release_year(detail.get("release")))
        except Exception as exc:
            log.warning("metacritic %s failed: %s", cid, exc)
            return None
        cache.put("critic", cid, {"score": critic.score, "title": critic.title,
                                  "url": critic.url} if critic else {})
        cache.mark("critic", cid)
        return critic is not None

    matched = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for result in pool.map(one, todo):
            if result:
                matched += 1
    return matched, len(todo)


def _release_year(value):
    year = (value or "")[:4]
    return int(year) if year.isdigit() else None


def _assemble(rows, cache, plus, weights, ttl, critic_ttl):
    """Join crawl output into publishable rows. No I/O beyond the cache."""
    games = []
    for cid, row in rows.items():
        prod = row["product"]
        price = prod.get("price") or {}
        base_cents = money_to_cents(price.get("basePrice"))
        price_cents = money_to_cents(price.get("discountedPrice"))
        if base_cents is None or price_cents is None:
            continue                      # "Unavailable"

        detail = cache.get("product", cid, ttl_days=ttl) or {}
        critic = cache.get("critic", cid, ttl_days=critic_ttl) or {}
        member = plus.lookup(concept_id=cid, product_id=prod["id"])
        # None, not 0: the search response carries the metascore but not the
        # review count, and quality() shrinks an unknown depth conservatively.
        q = quality(critic.get("score"), None, detail.get("star_average"),
                    detail.get("star_count") or 0, weights)
        discount = int(round((base_cents - price_cents) / base_cents * 100)) if base_cents else 0

        games.append({
            "id": prod["id"],
            "name": detail.get("name") or prod.get("name") or "",
            "platforms": sorted(prod.get("platforms") or []),
            "price_cents": price_cents,
            "base_cents": base_cents,
            "discount_pct": discount,
            "is_free": bool(price.get("isFree")),
            "plus_extra": bool(member and member.in_extra),
            "plus_classics": bool(member and member.in_classics),
            "genres": detail.get("genres") or [],
            "esrb": detail.get("esrb"),
            "local_players": detail.get("local_players"),
            "psvr2": detail.get("psvr2"),
            "dualsense": bool(detail.get("dualsense")),
            "release_year": _release_year(detail.get("release")),
            "tier": tier_of(base_cents),
            "critic_score": critic.get("score"),
            "quality": round(q.score, 1),
            "discount_depth": round(discount_depth(discount, weights), 1),
            "price_anchor": round(price_anchor(base_cents, price_cents, weights), 1),
            "evidence": q.evidence,
        })
    return games


def _read_previous_count(path):
    try:
        return json.loads(Path(path).read_text())["game_count"]
    except Exception:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once", action="store_true", help="crawl the deals set")
    p.add_argument("--backfill", action="store_true", help="extend to the full catalogue")
    p.add_argument("--limit", type=int, help="stop after N enrichments (iteration)")
    p.add_argument("--cap", type=int, default=25_000, help="max enrichments per run")
    p.add_argument("--ttl", type=float, default=30, help="detail cache TTL, days")
    p.add_argument("--critic-ttl", type=float, default=14,
                   help="Metacritic cache TTL, days")
    p.add_argument("--rate", type=float, default=1.0, help="starting req/s")
    p.add_argument("--max-rate", type=float, default=6.0, help="ceiling req/s")
    p.add_argument("--proxy", help="e.g. http://127.0.0.1:2080")
    p.add_argument("--cache", default="data/cache/ngp.sqlite")
    p.add_argument("--out", default="site/public/index.json")
    p.add_argument("-v", "--verbose", action="count", default=0)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        crawl(args)
    except PublishBlocked as exc:
        log.error("NOT PUBLISHING: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
