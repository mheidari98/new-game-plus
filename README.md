# new-game-plus

**You just bought a PS5. You don't have to buy games yet.**

There are **252 free-to-play games** on the US PlayStation Store and a **471-title PS+ Extra
catalogue**. This site tells you that first, and only then helps you shop.

A GitHub Action crawls the US PlayStation Store daily and publishes a static site to GitHub
Pages. No backend, no accounts, no server costs, ever.

## Status

Early. The crawler core is built and tested; the site is not up yet.

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
`site/src/lib/score.ts` are pure functions with no I/O.

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
pip install -r crawler/requirements.txt
pytest                      # 128 tests, no network required
```

Behind a restrictive network, route through a proxy:

```bash
python -m crawler.main --once --proxy http://127.0.0.1:2080
```

## Data sources

| Source | Auth | Used for |
|---|---|---|
| PlayStation Store GraphQL | none | catalogue, prices, genres, ESRB, features |
| playstation.com PS+ feeds | none | Extra / Classics / Monthly membership |
| Metacritic public backend | none | critic scores |
| IGDB | optional key | perspective, game modes, split-screen |

## Licence

MIT. Not affiliated with Sony Interactive Entertainment.
