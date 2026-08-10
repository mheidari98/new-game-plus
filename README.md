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

Two things genuinely are not served elsewhere:

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
| Published payload | 2,369 games → 97 KB gzipped · **12,732 games → 342 KB, 42% of the 800 KB budget** |
| Site build | **2,374 pages in 2.0 s** |
| A full crawl | ~7,000 requests, 0 retries, 0 refusals, limiter settled at its 6.00 req/s ceiling |

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
make test                    # 289 pytest + 44 vitest + invariants, no network
make check                   # invariants only, ~1s
```

A full crawl, which does hit the live store:

```bash
python crawler/main.py --once -v            # the deals set, ~7,000 requests
python crawler/main.py --backfill --cap 3000 -v   # extend to the whole catalogue
python crawler/ngp/history.py --report      # what price history we actually have
python crawler/match.py --measure-accuracy  # IGDB join vs the Wikidata crosswalk
```

Behind a restrictive network, route through a proxy. PlayStation and Metacritic answer
directly; HowLongToBeat and IGDB need it:

```bash
python crawler/main.py --once --proxy http://127.0.0.1:2080
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
