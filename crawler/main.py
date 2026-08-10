"""Crawl the US PlayStation Store and publish the site index.

    python crawler/main.py                       # the deals set
    python crawler/main.py --limit 50            # quick iteration
    python crawler/main.py --backfill --cap 5000 # extend to the full catalogue
    python crawler/main.py --proxy http://127.0.0.1:2080

Runs with zero secrets. Enrichment is resumable: a run that dies leaves its
rows unstamped and the next one continues from the TTL cursor.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ngp import history
from ngp.cache import Cache
from ngp.components import Weights, discount_depth, price_anchor, price_vs, quality
from ngp.features import decode_features
from ngp.guard import PublishBlocked, check_publishable
from ngp.hltb import HowLongToBeat, SearchFailed
from ngp.igdb import Igdb
from ngp.net import HttpClient, workers_for
from ngp.psplus import PlusIndex, fetch_all
from ngp.publish import render, save, save_art
from ngp.ratelimit import AdaptiveLimiter, RateLimitExceeded
from ngp.ratings import Metacritic
from ngp.store import MAX_WINDOW, StoreClient, money_to_cents

log = logging.getLogger("ngp")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent          # so --out/--cache are the same wherever you run from

# match.py reads the same file: a moved default there would silently report
# zero IGDB coverage instead of failing.
CACHE_DB = "data/cache/ngp.sqlite"

CATEGORIES = json.loads((HERE / "categories.json").read_text())
GAME_CLASSES = ["FULL_GAME", "GAME_BUNDLE", "PREMIUM_EDITION"]

# The grid's default order is best-selling, so enumeration order is popularity
# order and the enrichment cursor gets its priority for free.
SALES30 = {"name": "sales30", "isAscending": False}

PRICE_FACET = "webBasePrice"

# Cover art, best role first. MASTER is the square tile the store itself leads
# with and was the only role present on all 18 products sampled across the
# popularity range, so the rest are insurance. LOGO and SCREENSHOT are never
# box art. The URL carries a 48-hex asset hash, so it cannot be derived from a
# product id -- it is fetched or it is nothing.
ART_ROLES = ("MASTER", "GAMEHUB_COVER_ART", "PORTRAIT_BANNER")

# Catalogue-only concepts queue behind everything in the deals grid: the deals
# set is what the site leads with, and it is at most ~4,500 rows.
CATALOGUE_RANK_BASE = 100_000

# Hosts paced independently. They are different companies with different
# infrastructure, and making Metacritic's ~5,000 requests queue behind the
# store's token bucket cost ~14 minutes a run for nothing.
PACED_HOSTS = ("playstation.com", "metacritic.com")


def crawl(args):
    weights = Weights.defaults()
    limiters = {host: AdaptiveLimiter(start=args.rate, ceiling=args.max_rate)
                for host in PACED_HOSTS}
    # A task makes 3 requests -- product, stars, Metacritic -- of which 2 are
    # paced against the store, so the pool has to be wider than the store's
    # rate alone would suggest.
    workers = workers_for(args.max_rate, task_requests=3, host_requests=2)
    state_dir = (REPO / args.cache).parent
    state_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(REPO / args.cache)
    if args.refresh:
        log.info("refresh: %d %s stamps cleared, so every concept is due again",
                 cache.clear_stamps(args.refresh), args.refresh)
    last_good = state_dir / "last_good.json"
    try:
        previous = json.loads(last_good.read_text())["game_count"]
    except Exception:
        previous = None
    log.info("pacing: %d workers, each host ramping from %.2f toward %.2f req/s",
             workers, args.rate, args.max_rate)

    with HttpClient(limiter=AdaptiveLimiter(start=args.rate, ceiling=args.max_rate),
                    limiters=limiters, proxy=args.proxy,
                    direct_hosts=list(PACED_HOSTS)) as http:
        store = StoreClient(http)

        log.info("fetching PS+ catalogues")
        plus = PlusIndex(fetch_all(http))
        log.info("PS+ index: %d concepts", len(plus))

        log.info("enumerating deals")
        baseline = store.grid_page(CATEGORIES["deals"], size=1).total
        products, offset = [], 0
        while offset < baseline:
            page = store.grid_page(
                CATEGORIES["deals"], offset=offset, size=1000, sort_by=SALES30,
                filter_by=[f"storeDisplayClassification:{c}" for c in GAME_CLASSES],
                baseline_total=baseline if offset == 0 else None,
            )
            if not page.products:
                break
            products.extend(page.products)
            offset += len(page.products)
            if page.is_last:
                break
        log.info("deals: %d products, %d game-like", baseline, len(products))

        # The curated free-to-play category, not "price == 0 in the deals
        # grid" -- that returns cosmetic bundles like "War Thunder Cyberpunk
        # Airforce Snail Bundle" rather than games anyone would recommend.
        # One request for the whole thing.
        free, _ = _rows_from_concepts(
            store.grid_page(CATEGORIES["free_to_play"], size=1000,
                            sort_by=SALES30).concepts)
        for row in free:
            # Free by definition, so an absent price is a gap in the grid
            # rather than an unknown.
            row["price"] = row["price"] or {"basePrice": "Free",
                                            "discountedPrice": "Free", "isFree": True}
        log.info("free-to-play: %d concepts", len(free))
        products.extend(free)

        log.info("mapping products to concepts")
        # Bulk product->concept from the concept grids: 17 requests, not 4,336.
        concept_of = {}
        for key in ("all_games", "all_ps5_games"):
            offset = 0
            while offset < MAX_WINDOW:
                page = store.grid_page(CATEGORIES[key], offset=offset, size=1000)
                if not page.concepts:
                    break
                concept_of.update(page.product_to_concept())
                offset += len(page.concepts)
                if page.is_last:
                    break

        rows = {}
        for rank, prod in enumerate(products):
            # Concept-grid rows already know their concept. Otherwise use the
            # bulk map, falling back to the product id so an unmapped row still
            # gets a stable key.
            cid = prod.get("_concept_id") or concept_of.get(prod["id"]) or prod["id"]
            rows.setdefault(cid, {"concept_id": cid, "rank": rank, "product": prod})
        cache.upsert_concepts([
            {"concept_id": cid, "rank": r["rank"], "product_id": r["product"]["id"]}
            for cid, r in rows.items()
        ])

        # HowLongToBeat has its own client, its own much slower limiter and its
        # own host, so it has no reason to wait for the store passes. Run it
        # alongside them and its ~9 minutes disappear inside theirs.
        playtime = threading.Thread(target=_enrich_playtime,
                                    args=(cache, rows, args), daemon=True)
        if args.playtime:
            playtime.start()

        if args.backfill:
            # `rows` is extended in place, so publishing needs no second code
            # path. Concepts already in the deals set keep their deals rank --
            # they are the ones the site leads with.
            found, unreleased = catalogue_concepts(store, CATEGORIES["all_games"])
            added = []
            for index, row in enumerate(found):
                cid = row["_concept_id"]
                if cid in rows:
                    continue
                rows[cid] = {"concept_id": cid, "rank": CATALOGUE_RANK_BASE + index,
                             "product": row}
                added.append(rows[cid])
            cache.upsert_concepts([
                {"concept_id": r["concept_id"], "rank": r["rank"],
                 "product_id": r["product"]["id"]} for r in added
            ])
            log.info("catalogue: +%d concepts beyond the deals set, %d unreleased "
                     "concepts skipped (no product to price or enrich)",
                     len(added), unreleased)

        enriched, failed, matched, attempted = _enrich(
            store, Metacritic(http), cache, rows, args, workers)
        log.info("metacritic: %d of %d looked up matched", matched, attempted)
        if attempted > 20 and matched == 0:
            log.warning("metacritic matched nothing in %d lookups; "
                        "quality scores will rest on store stars alone", attempted)

        _enrich_editorial(Igdb(http), cache, rows, args)

    if args.playtime:
        playtime.join()          # its rows have to be cached before assembly

    games = _assemble(rows, cache, plus, weights, args)

    now = datetime.now(timezone.utc)
    written, scored = _score_against_history(REPO / args.history, games, now.date(), weights)
    log.info("price history: %d rows written, %d of %d games have enough to score",
             written, scored, len(games))
    # A real seasonal sale does move thousands of prices at once, so this is
    # worth saying out loud but is not worth refusing to publish over.
    if written > 10_000:
        log.warning("%d price changes in one day; expected ~900. Real sales do this, "
                    "but check the crawl did not re-key every product", written)

    # Render, then check, then write. Nothing degraded reaches the disk.
    body, packed, stats = render(games, weights, generated_at=now.isoformat())
    check_publishable(
        game_count=len(games),
        previous_game_count=previous,
        plus_extra_count=plus.extra_count,
        gzip_bytes=stats["gzip_bytes"],
        enrichment_attempted=enriched + failed,
        enrichment_failed=failed,
        filtered_counts={"game_classes": len(products)},
        baseline_count=baseline,
    )
    save(REPO / args.out, body, packed)
    with_art = save_art(REPO / args.art, games)
    last_good.write_text(json.dumps({"game_count": len(games)}))
    log.info("cover art: %d of %d games", with_art, len(games))

    log.info("published %d games: %d B raw, %d B gzipped (%.0f%% of budget)",
             stats["count"], stats["raw_bytes"], stats["gzip_bytes"],
             100 * stats["gzip_bytes"] / 819_200)
    # Print what each host actually tolerated: the discovered wall is the
    # only honest input to tuning --max-rate for the next run.
    log.info("http: %s | %s", http.stats, " | ".join(
        f"{host} settled at {lim.rate:.2f} req/s"
        f"{'' if lim.refusals == 0 else f' after {lim.refusals} refusals, wall at {lim.effective_ceiling:.2f}'}"
        for host, lim in limiters.items()))
    cache.close()
    return stats


def _rows_from_concepts(concepts):
    """Flatten concept-grid rows into the product shape the rest of the crawl
    speaks. Price and name sit on the concept; the product id has to be pulled
    out of products[], and platforms are absent until the detail call.

    A concept with no products is unreleased: nothing to price, nothing to
    enrich. Returns those separately rather than silently dropping them.
    """
    rows, unreleased = [], 0
    for concept in concepts or []:
        products = concept.get("products") or []
        if not products:
            unreleased += 1
            continue
        rows.append({
            "id": products[0]["id"],
            "name": concept.get("name"),
            "platforms": [],
            "price": concept.get("price") or {},
            "_concept_id": concept["id"],
        })
    return rows, unreleased


def catalogue_concepts(store, category):
    """Every concept in a category, escaping the 10,000-row pagination window.

    `offset + size` is capped at 10,000, but a *filtered* grid paginates
    within its own filtered total, so slicing by price bucket reaches the rest
    of a ~12.8k catalogue. Three things this must not assume:

    * **The bucket list is not a constant.** It is read from the facet
      response; the store gained an 11th bucket after this was designed.
    * **The buckets are not disjoint.** "Free" (`0-0`) is a subset of
      "Under $1.99" (`0-199`), so concepts are deduped by id rather than
      counted by summing.
    * **Unpriced concepts are in no bucket at all** -- `price` is null on
      unreleased titles -- so this enumeration is a lower bound, and the
      shortfall is returned rather than glossed over.
    """
    head = store.grid_page(category, size=1, facet_options=[PRICE_FACET])
    buckets = head.facet_values(PRICE_FACET)
    if not buckets:
        raise RuntimeError(f"category {category} exposes no {PRICE_FACET} facet to slice by")

    rows, unreleased = {}, 0
    for key, count in buckets:
        if not count:
            continue
        if count > MAX_WINDOW:
            raise RuntimeError(
                f"price bucket {key} holds {count} concepts, past the {MAX_WINDOW} "
                "window; it needs slicing by a second facet")
        offset = 0
        while offset < count:
            page = store.grid_page(
                category, offset=offset, size=1000, sort_by=SALES30,
                filter_by=[f"{PRICE_FACET}:{key}"],
                baseline_total=head.total if offset == 0 else None)
            if not page.concepts:
                break
            found, skipped = _rows_from_concepts(page.concepts)
            unreleased += skipped
            for row in found:
                rows.setdefault(row["_concept_id"], row)
            offset += len(page.concepts)
            if page.is_last:
                break
    return list(rows.values()), unreleased


def _enrich(store, metacritic, cache, rows, args, workers):
    """Everything a concept needs in one task: store detail, stars, Metacritic.

    Merged because the hosts are paced independently: as two passes the rates
    apply end to end -- 10,000 store requests, *then* 5,000 Metacritic ones --
    and each host idles through the other's phase. It also keeps Metacritic's
    year disambiguation working (2001 Silent Hill 2 vs the 2024 remake): the
    release date comes from the detail fetched moments earlier in this task.

    Each source keeps its own TTL, so a concept can be due for one and fresh
    for the other.
    """
    limit = args.limit or args.cap
    due_detail = cache.due("product", ttl_days=args.ttl, limit=limit)
    due_critic = cache.due("critic", ttl_days=args.critic_ttl, limit=limit)
    detail_set, critic_set = set(due_detail), set(due_critic)
    # Each source is capped on its own, and the union is NOT capped again.
    # Capping the union lets the cheap source crowd out the expensive one: a
    # live run spent its whole budget on 5,000 Metacritic lookups and got
    # through only 2,629 store details. Since each task does only the work
    # that concept is actually due for, the uncapped union costs exactly what
    # the two separate passes used to -- at most `limit` of each.
    todo = sorted(detail_set | critic_set,
                  key=lambda cid: rows[cid]["rank"] if cid in rows else CATALOGUE_RANK_BASE)
    log.info("enriching %d concepts of %d (%d need detail, %d need a critic score)",
             len(todo), len(rows), len(due_detail), len(due_critic))

    def one(cid):
        """`(details fetched, details failed, critic scores matched)`, 0 or 1 each."""
        row = rows.get(cid)
        if not row:
            return 0, 0, 0
        if cid in detail_set and not _fetch_detail(store, cache, cid, row):
            # No critic lookup: matching on the grid name alone loses the year.
            return 0, 1, 0
        critic = (cid in critic_set
                  and _fetch_critic(metacritic, cache, cid, row, args.ttl))
        return int(cid in detail_set), 0, int(bool(critic))

    ok = fail = matched = 0
    # pool.map yields in the main thread, so the counters need no lock.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fetched, failed, hit in pool.map(one, todo):
            ok += fetched
            fail += failed
            matched += hit
    return ok, fail, matched, len(due_critic)


def _fetch_detail(store, cache, cid, row):
    """`metGetProductById` plus the star rating. Two operations, not three:
    `metGetConceptById` returns the same fields minus the concept id."""
    try:
        detail = store.product(row["product"]["id"])
        stars = store.stars(row["product"]["id"])
        feats = decode_features(detail.get("compatibilityNotices"))
        rating = stars.get("starRating") or {}
        images = {m.get("role"): m.get("url") for m in (detail.get("media") or [])
                  if m.get("type") == "IMAGE" and m.get("url")}
        cache.put("product", cid, {
            "name": detail.get("name") or row["product"].get("name"),
            # Concept grids do not carry platforms, so for anything found
            # by the catalogue sweep this is the only source.
            "platforms": detail.get("platforms") or [],
            "genres": [g.get("value") for g in
                       (detail.get("combinedLocalizedGenres") or [])],
            "esrb": (detail.get("contentRating") or {}).get("name"),
            "release": detail.get("releaseDate"),
            "local_players": feats.local_players,
            "dualsense": feats.dualsense_haptics,
            "psvr2": feats.psvr2,
            "star_average": rating.get("averageRating"),
            "star_count": rating.get("totalRatingsCount"),
            "art": next((images[r] for r in ART_ROLES if r in images), None),
        })
        cache.mark("product", cid)
        return True
    except RateLimitExceeded:
        raise           # the circuit breaker, not one row's failure
    except Exception as exc:
        log.warning("enrich %s failed: %s", cid, exc)
        return False


def _fetch_critic(metacritic, cache, cid, row, ttl):
    """One Metacritic search. A miss is stored exactly like a hit, or every
    run re-asks about the same few hundred titles that will never have one."""
    name, year = _known_name_and_year(cache, cid, row, ttl)
    if not name:
        return None
    try:
        critic = metacritic.lookup(name, year)
    except RateLimitExceeded:
        raise           # the circuit breaker, not one row's failure
    except Exception as exc:
        log.warning("metacritic %s failed: %s", cid, exc)
        return None
    cache.put("critic", cid, {"score": critic.score, "title": critic.title,
                              "url": critic.url} if critic else {})
    cache.mark("critic", cid)
    return critic is not None


def _known_name_and_year(cache, cid, row, ttl):
    """What the third-party matchers key on: the store's name and release year
    if we have the detail, else the grid row's name."""
    detail = cache.get("product", cid, ttl_days=ttl) or {}
    return (detail.get("name") or (row and row["product"].get("name")),
            _release_year(detail.get("release")))


def _enrich_playtime(cache, rows, args):
    """HowLongToBeat, on its own client and its own much slower limiter.

    Deliberately not sharing the store's 6 req/s: this is a source whose terms
    we are already stretching, and playtime does not change, so a 180-day TTL
    plus a per-run cap means the catalogue warms over weeks and then costs
    almost nothing. Single-threaded for the same reason.
    """
    todo = cache.due("playtime", ttl_days=args.playtime_ttl, limit=args.playtime_cap)
    if not todo:
        return 0

    with HttpClient(limiter=AdaptiveLimiter(start=0.5, ceiling=1.0),
                    proxy=args.proxy) as http:
        hltb = HowLongToBeat(http)
        if not hltb.available:
            # The endpoint moves every two or three months. That is the normal
            # state, not an incident: playtime stays null and the run goes on.
            log.warning("hltb unavailable this run; playtime stays null")
            return 0

        found = 0
        for cid in todo:
            name, year = _known_name_and_year(cache, cid, rows.get(cid), args.ttl)
            if not name:
                continue
            try:
                play = hltb.lookup(name, year)
            except SearchFailed as exc:
                # Not an answer. Leaving the row unstamped is the retry: the
                # next run's `due` picks it up again.
                log.warning("hltb lookup failed for %s: %s", cid, exc)
                continue
            cache.put("playtime", cid,
                      {"hours_main": play.main_hours, "title": play.title,
                       "hltb_id": play.hltb_id} if play else {})
            cache.mark("playtime", cid)
            found += bool(play)

    log.info("playtime: %d of %d looked up matched", found, len(todo))
    return found


def _enrich_editorial(igdb, cache, rows, args):
    """IGDB split-screen and perspective. ~26 requests for the whole
    catalogue, or nothing at all without a key."""
    if not igdb.configured:
        log.info("igdb: no credentials, split-screen and perspective stay null")
        return 0
    todo = cache.due("igdb", ttl_days=args.igdb_ttl)
    if not todo:
        return 0

    source = igdb.source_id()
    index, shape = igdb.psn_index(source) if source else ({}, None)
    if shape is None:
        # The uid is not any identifier we hold. Name matching is the
        # documented fallback, and it is a different enough problem that
        # guessing here would be worse than shipping nulls.
        log.warning("igdb: uid shape unrecognised, so there is no exact join; "
                    "split-screen and perspective stay null")
        return 0

    # Our side of the join, in whatever identifier IGDB turned out to hold.
    matched = {}
    for cid in todo:
        product_id = rows[cid]["product"]["id"] if cid in rows else ""
        if shape == "concept_id":
            key = str(cid)
        elif shape == "product_id":
            key = product_id
        else:   # UP9000-PPSA26344_00-GHOST2SHIP000000 -> the npTitleId in the middle
            key = (product_id.split("-") + [""])[1]
        if key in index:
            matched[cid] = index[key]
    editorial = igdb.editorial(matched.values())

    for cid in todo:
        found = editorial.get(matched.get(cid))
        cache.put("igdb", cid, {
            "splitscreen": found.splitscreen, "perspective": found.perspective,
            "hours_main": round(found.time_to_beat_seconds / 3600.0, 1)
            if found.time_to_beat_seconds else None,
        } if found else {})
        cache.mark("igdb", cid)
    log.info("igdb: %d of %d concepts joined on %s", len(matched), len(todo), shape)
    return len(matched)


def _release_year(value):
    year = (value or "")[:4]
    return int(year) if year.isdigit() else None


def _assemble(rows, cache, plus, weights, args):
    """Join crawl output into publishable rows. No I/O beyond the cache."""
    ttl, critic_ttl = args.ttl, args.critic_ttl
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
        play = cache.get("playtime", cid, ttl_days=args.playtime_ttl) or {}
        editorial = cache.get("igdb", cid, ttl_days=args.igdb_ttl) or {}
        member = plus.lookup(concept_id=cid, product_id=prod["id"])
        # None, not 0: the search response carries the metascore but not the
        # review count, and quality() shrinks an unknown depth conservatively.
        q = quality(critic.get("score"), None, detail.get("star_average"),
                    detail.get("star_count") or 0, weights)
        discount = int(round((base_cents - price_cents) / base_cents * 100)) if base_cents else 0

        games.append({
            "id": prod["id"],
            "name": detail.get("name") or prod.get("name") or "",
            # Payload wins for display; the detail is the fallback for rows
            # that came from a concept grid, which carries no platforms.
            "platforms": sorted(prod.get("platforms") or detail.get("platforms") or []),
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
            "critic_score": critic.get("score"),
            # HowLongToBeat first, IGDB's time-to-beat as the substitute. Both
            # are best-effort and either may be absent.
            "hours_main": play.get("hours_main") or editorial.get("hours_main"),
            "splitscreen": editorial.get("splitscreen"),
            "perspective": editorial.get("perspective"),
            "quality": round(q.score, 1),
            "discount_depth": round(discount_depth(discount, weights), 1),
            "price_anchor": round(price_anchor(base_cents, price_cents, weights), 1),
            "evidence": q.evidence,
            # Not published in index.json -- see publish.save_art.
            "art": detail.get("art"),
        })
    return games


def _score_against_history(root, games, today, weights):
    """Record today's prices and score each game against everything recorded.

    Both components stay None until a game has `min_price_observations`
    behind it. Null is dropped by the browser and the remaining deal weights
    renormalise -- a game is never marked down for history *we* do not have.
    """
    series, written = history.record(
        root, [(g["id"], g["price_cents"], g["base_cents"]) for g in games], today)
    floor = weights.deal["min_price_observations"]

    scored = 0
    for game in games:
        summary = history.summarise(series.get(game["id"], []), floor)
        if summary:
            scored += 1
        low = price_vs(game["price_cents"], summary.min_cents) if summary else None
        typical = (price_vs(game["price_cents"], summary.typical_sale_cents)
                   if summary else None)
        game["vs_historical_min"] = None if low is None else round(low, 1)
        game["vs_typical_sale"] = None if typical is None else round(typical, 1)
    return written, scored


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backfill", action="store_true", help="extend to the full catalogue")
    p.add_argument("--limit", type=int, help="stop after N enrichments (iteration)")
    p.add_argument("--cap", type=int, default=25_000, help="max enrichments per run")
    p.add_argument("--ttl", type=float, default=30, help="detail cache TTL, days")
    p.add_argument("--critic-ttl", type=float, default=14,
                   help="Metacritic cache TTL, days")
    p.add_argument("--no-playtime", dest="playtime", action="store_false",
                   help="skip HowLongToBeat entirely")
    p.add_argument("--playtime-cap", type=int, default=400,
                   help="max HowLongToBeat lookups per run")
    p.add_argument("--playtime-ttl", type=float, default=180,
                   help="playtime cache TTL, days -- it does not change")
    p.add_argument("--igdb-ttl", type=float, default=90,
                   help="IGDB cache TTL, days")
    p.add_argument("--rate", type=float, default=1.0,
                   help="starting req/s, per host; AIMD ramps up from here")
    # 6.0 was never a measured wall. Two production runs -- 4,764 and 15,048
    # requests -- both settled at exactly 6.00 with zero refusals, which says
    # the store's real threshold is somewhere above it and unknown. The
    # limiter is built to find that: the first 429/403 halves the rate and
    # pins the ceiling at 0.9x the refused rate for the rest of the run, so
    # overshooting costs a handful of refusals rather than an IP. Lower this
    # if a run ever reports a discovered wall below it.
    p.add_argument("--max-rate", type=float, default=12.0,
                   help="ceiling req/s, per host")
    # --ttl 0 looks like it would do this and must not be used for it: it also
    # expires the payload `_assemble` reads back, so every game publishes with
    # no genres, no rating and quality 0, and the publish gate passes.
    p.add_argument("--refresh", metavar="SOURCE",
                   help="re-fetch one source (product, critic, playtime, igdb) "
                        "whatever its TTL, for when it starts capturing a new field")
    p.add_argument("--proxy", help="e.g. http://127.0.0.1:2080")
    p.add_argument("--cache", default=CACHE_DB)
    p.add_argument("--history", default="history",
                   help="price-history checkout (the `data` branch in CI)")
    p.add_argument("--out", default="site/public/index.json")
    # Outside public/ on purpose: build input, never served to a browser.
    p.add_argument("--art", default="site/src/art.json")
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
