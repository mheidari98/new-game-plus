# new-game-plus

**You just bought a PS5. You don't have to buy games yet.**

There are **252 free-to-play games** on the US PlayStation Store and a **471-title PS+ Extra
catalogue**. This site tells you that first, and only then helps you shop.

A GitHub Action crawls the US PlayStation Store daily and publishes a static site to GitHub
Pages. No backend, no accounts, no server costs, ever.

**Live: https://mheidari98.github.io/new-game-plus/**

## Status

Milestones 1–6 are built. The crawler runs daily; the site has a starter guide, a free-games
list, a PS+ break-even calculator, a filterable explorer and a page per game. Price history
started accumulating on 2026-08-10, so for now every game says *no usable price history yet* and
the two history terms are dropped from the ranking rather than guessed at.

Two things are deliberately incomplete, and both say so in place rather than pretending:

- **IGDB has never run against the live API.** No Twitch credential existed while it was
  written. Split-screen and perspective are null without one, and `uid`'s format is classified
  at runtime rather than assumed, so the first live run is its real test.
- **The full catalogue arrives over several days.** `--backfill` enumerates all 12,736 concepts
  in 50 requests, but enriching them is capped per run, so the site fills in popularity-first.

## What makes it different

Most PlayStation price trackers are mature and good at what they do — PSPrices has covered
console prices since 2014, PlatPrices has price history back to 2020 and already ships a PS+
break-even calculator, Deku Deals covers PlayStation with alerts. This project does not try to
beat them at price history, and says so.

Three things genuinely are not served elsewhere:

**Local co-op filtering over the priced catalogue.** Co-Optimus has the co-op data but no prices
or PS+ status; the price trackers have prices but no co-op data at all. Sony publishes a
`NO_OF_PLAYERS` field on every product that nobody reads — so "2+ players on one couch, under
$20, not already in PS+" is a query you currently cannot run anywhere.

**Auditable, tunable ranking.** The crawler publishes score *components*; your browser computes
the final score. That makes the "I have PS+ Extra" toggle and the weight sliders real rather than
cosmetic, and it means you can check the maths. `crawler/ngp/components.py` and
`site/src/lib/score.ts` are pure functions with no I/O, and every game's page prints the
components its ranking came from.

**Saying when we do not know.** A missing component is dropped and the remaining weights
renormalise, in the crawler and again in the browser — a game is never marked down for data
*we* are missing. "No usable price history yet" is printed rather than implied, a Metacritic
score of unknown depth can never count as strong evidence, and `null` split-screen means IGDB
did not say, not that the game lacks it.

## "Is PS+ worth it for you"

**[PlatPrices](https://platprices.com) shipped this first**, and covers more regions than this
does. It is here anyway because the difference is one of kind rather than of feature count:
this version runs entirely in your browser against a list kept in `localStorage`, needs no
account, and the whole computation is [one short pure file](site/src/lib/plusmath.ts) you can
read in a minute. It counts today's price rather than the list price, gives a free-to-play
catalogue game no credit, and counts nothing at all as covered by Essential — its monthly games
rotate and are not a catalogue.

## Measured, not estimated

| | |
|---|---|
| Catalogue | 4,337 deals products → 2,741 game-like, plus 252 free-to-play; **12,736 concepts** with `--backfill` |
| Enumerating all of it | **50 requests** — price-bucket facet slicing, because pagination stops at 10,000 |
| Published payload | **12,732 games → 392,801 B gzipped, 48% of the 800 KB budget** |
| Site build | **2,374 pages in 2.0 s** |
| Full backfill run | **15,048 requests, 0 retries, 0 refusals**, 55 min |

## Why the crawl takes what it takes

Measured on the run above, which is what the pacing was then tuned against:

| Phase | Requests | Wall clock |
|---|---|---|
| Enumeration + price-bucket sweep | 50 | 3 min |
| Store detail + stars, then Metacritic | 15,048 | **45 min** |
| HowLongToBeat | 416 | 8 min |

Three things were wrong with that, and none of them was the request count:

1. **One token bucket for every host.** Metacritic's 5,000 requests queued behind the store's
   10,048 for no reason — they are different companies. Each host now has its own
   `AdaptiveLimiter`, so a refusal from one no longer slows the other either.
2. **Per-host pacing pays nothing if the hosts take turns.** As two sequential passes the rates
   applied end to end. `_enrich` now does store detail, stars and Metacritic in *one* task per
   concept, so both rates apply at once and the phase costs as long as the busier host alone.
   It also fixed a latent accuracy problem: Metacritic's year disambiguation needs the release
   date, which is now fetched moments earlier in the same task.
3. **HowLongToBeat ran last, alone.** It has its own client and its own deliberately slow
   limiter, so it now runs in a thread beside the store passes and its 8 minutes vanish inside
   theirs.

Measured after, on the enrichment phase alone so the comparison is like for like:
**4.77 → 11.28 requests/second, 2.4×**. Whole-run wall clock went 55.6 → 15.5 min.

The ceiling was raised from 6 to **12 req/s per host** on evidence rather than optimism: two
production runs totalling ~20,000 requests both settled at exactly 6.00 with **zero refusals**,
which means 6 was a cap we chose, not a wall the store pushed back with. At 12 both hosts again
settled at exactly 12.00 with zero refusals, so the store's real limit is *still* above what we
ask of it. The binding constraint now is the worker pool, not the store. Finding the real one is
what the AIMD limiter is for — the first 429 or 403 halves the rate and pins the ceiling at 0.9×
the refused rate for the rest of the run, so overshooting costs a handful of refusals rather than
an IP. Every run prints the rate each host settled at, and the wall if it found one; if a run
ever reports a wall below 12, lower `--max-rate` to it.

Worker count is derived, never a literal — `net.workers_for(ceiling, task_requests,
host_requests)`. Raising the ceiling without widening the pool buys nothing.

That is not a hypothetical: the first run at the new ceiling had the store at **7.5 req/s against
its own 12.00 ceiling**, because the pool was sized from a per-request latency of 0.8 s measured
*single-threaded*. Under fifteen-way concurrency on one HTTP/2 connection a request occupies its
worker for **1.33 s** — measured as 8,211 requests through 15 workers in 728 s — so the pool was
38% too small. `MEASURED_LATENCY_S` now carries the concurrent figure.

Widening the pool cannot make the crawl less polite, which is what makes this safe to tune: the
limiter caps the rate per host regardless of how many threads are queued on it. Workers only
decide whether we reach the rate we already chose. `MAX_WORKERS` is a guard against a typo in
`--max-rate`, not a politeness limit.

## Design constraints

- **All outbound HTTP goes through one client** (`ngp/net.py`) so pacing cannot be bypassed.
- **Adaptive rate limiting** (`ngp/ratelimit.py`): AIMD, the control law behind TCP congestion
  avoidance. It finds the fastest rate the store actually tolerates and backs off before
  repeated refusals become a ban. Measured: converges in 1–4 refusals out of 40,000 requests.
- **Never guess persisted-query hashes.** The store uses Apollo persisted queries. Hashes are
  pinned from the public web client. If they all fail, re-capture from DevTools — probing an
  allowlist is reconnaissance, not debugging.
- **Say when data is missing.** Price history starts empty and the site says so rather than
  inventing confidence.
- **Runs with zero secrets.** IGDB is the only optional key, and its absence produces null
  columns, never a failure.

## Development

```bash
make setup
make test                    # 340 pytest + 64 vitest + invariants, no network
make check                   # invariants only, ~1s
```

A full crawl, which does hit the live store:

```bash
python crawler/main.py -v                   # the deals set, ~7,000 requests
python crawler/main.py --backfill --cap 3000 -v   # extend to the whole catalogue
python crawler/ngp/history.py --report      # what price history we actually have
python crawler/match.py --measure-accuracy  # IGDB join vs the Wikidata crosswalk
```

Behind a restrictive network, route through a proxy. PlayStation and Metacritic answer
directly; HowLongToBeat and IGDB need it:

```bash
python crawler/main.py --proxy http://127.0.0.1:2080
```

## Data sources

| Source | Auth | Used for | Missing means |
|---|---|---|---|
| PlayStation Store GraphQL | none | catalogue, prices, genres, ESRB, features, player counts | the run aborts |
| playstation.com PS+ feeds | none | Extra / Classics / Monthly membership | the run aborts |
| Metacritic public backend | none | critic scores | thinner evidence, lower confidence |
| HowLongToBeat | none | main-story hours, cost per hour | null column |
| IGDB | optional key | split-screen, player perspective | null columns |
| Wikidata SPARQL | none | ground truth for measuring the IGDB join | no measurement |

Only the first two can fail a run. Their absence makes the output *wrong* — an empty PS+ feed
would mark the whole store as not-in-PS+ — where the others only make it thinner.

Metacritic's search response carries the metascore but not the review count, and the per-game
page would double the request budget for it. So the count is genuinely unknown, and
`components.quality` weights such a score as if it rested on exactly `critic_prior_weight`
reviews: it can never on its own count as strong evidence.

HowLongToBeat's `robots.txt` disallows `/api` for every user-agent and Ziff Davis's terms
prohibit automated retrieval. That was weighed and accepted. What it means in the code is that
playtime is a nullable column on a 180-day TTL, fetched on its own much slower limiter, that can
never fail a run — and the whole integration is removable in one commit.

## Licence

MIT. Not affiliated with Sony Interactive Entertainment.
