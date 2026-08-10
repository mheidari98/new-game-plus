# AGENTS.md

Crawls the US PlayStation Store daily and publishes a static site to GitHub Pages.
No backend, no accounts, no secrets required.

## Commands

```bash
make setup      # install python + node deps
make test       # pytest + vitest + invariant checks (no network)
make check      # invariants only, ~1s
make crawl      # live crawl, hits the real store
make site       # build the static site
```

Run `make test` before committing. `python crawler/main.py --once --limit 25 -v` is the fast way
to exercise the live path without a full crawl.

## Enforced invariants

`scripts/check_invariants.py` fails the build on these. Read it before working around one —
each exists because the violation is silent and expensive.

1. Only `ngp/net.py` may import an HTTP library. Anything else bypasses the rate limiter.
2. `crawler/ngp/components.py` and `site/src/lib/score.ts` stay pure — no I/O, clock or randomness.
3. `score.ts` reads weights from `index.json`, never hardcodes them.
4. Any fuzzy matching must call `titles.numbers_compatible()`.
5. Every `Cache` method touching the database takes `self._lock`.
6. Persisted-query hashes are literals, never generated.
7. `package-lock.json` resolves only through `registry.npmjs.org`.

Rule 4 covers `titles.similarity` as well as `difflib`/`rapidfuzz`: a file that ranks candidates
by name must call `numbers_compatible()` in the same file.

## Pitfalls

These cost real debugging time. None are obvious from reading the code.

**The store API**

- `content-type: application/json` is required **even on GET**. Without it Apollo's CSRF guard
  returns a gzipped 400 that reads as binary garbage.
- **Never brute-force or probe persisted-query hashes.** They are pinned in `store.py`, captured
  from the public web client. If they all fail, re-capture from browser DevTools. Probing an
  allowlist is reconnaissance against a third party, not debugging.
- Page size caps at 1000; `offset + size` must be ≤ 10000. Past that, slice by a disjoint facet.
- An unknown facet **name** is silently ignored and returns the whole catalogue; an unknown facet
  **value** returns zero rows. Guard both — "did the count change?" passes the second case while
  zeroing the dataset.
- The facet key is `ADD-ON_PACK` (hyphen); the payload value is `ADD_ON_PACK` (underscore).
  Feeding a product's own classification back into `filterBy` matches nothing.
- PS5/`All games` are **concept** grids (`concepts[]`); PS4/`All deals` are **product** grids
  (`products[]`). Handle both shapes.
- `sortingOptions` in the response is not an exhaustive allowlist. Read back `sortedBy.name`.
- Category counts drift daily — "All deals" moved 4335 → 4337 in one day. Never assert a constant.

**Data semantics**

- `NO_OF_PLAYERS` counts players **on one console**, so `>= 2` is the couch co-op filter. It is
  the project's differentiator and needs no third-party source.
- `OFFLINE_PLAY_MODE` does **not** mean multiplayer. It appears on single-player-only titles and
  is absent from 4-player couch games. Never combine the two fields.
- PS VR2 ships under codenames: `CAESAR_HEADSET` is the headset, `ASTON_CONTROLLER` the
  controllers. There is no literal "PSVR2" key.
- Install size, 60/120fps, VRR, ray tracing and HDR **do not exist** anywhere in the store API or
  on the public product page. Do not add fields for them.
- `ubisoft-classics-list` is a strict subset of `plus-games-list`. Unioning it double-counts 68
  entries.
- The PS+ feed 404s intermittently. An empty Extra catalogue must raise, never degrade — it would
  mark the entire store as not-in-PS+.
- Free games come from the free-to-play category, not `price == 0` in the deals grid; the latter
  returns cosmetic bundles.

**Third-party sources**

- Metacritic's **search** response carries the metascore but not the review count. Pass
  `critic_count=None`, never a made-up number: `quality()` treats None as "unknown depth".
- A third-party title ending `(2001)` is disambiguating a remake. Split the year off before
  `numbers_compatible()`, or the guard reads it as a version number and rejects both entries.
- Character similarity alone matches "Bean Beasts" to "Gang Beasts" at 0.82. `titles.similarity`
  takes the worse of character and whole-word agreement for that reason.
- **HowLongToBeat's endpoint moves every 2–3 months.** Nothing is pinned. Find the search call by
  its shape — it is the only `fetch` sending `x-auth-token` — never by picking the most common
  `/api/…` string, because `_buildManifest.js` lists every route on the site including
  `/api/admin/panel`. Auth is `GET <endpoint>/init` → `{token, hpKey, hpVal}`, sent as three
  headers *and* injected into the body under the dynamic `hpKey`.
- IGDB's `uid` format is still unverified against the live API. `igdb.py` classifies it at
  runtime; do not pin one of the three candidates.
- Wikidata's P5794 holds an IGDB **slug**, not a numeric id.

**Everything else**

- Strip trademark symbols **before** NFKD, or `™` becomes the letters "TM".
- Price buckets are read from the facet response, never hardcoded: there are 11 now, and "Free"
  (`0-0`) is a subset of "Under $1.99" (`0-199`), so the sweep dedupes by concept id.
- Astro's `base` has no trailing slash, so `${BASE_URL}index.json` renders as
  `/repoindex.json`. `astro.config.mjs` normalises it.
- Cover art is not published in `index.json` — it was 27% of the payload.

## Conventions

- Test first. If a test passes the moment you write it, you are describing existing behaviour;
  mutate the implementation to prove the test can fail.
- Say when data is missing. Price history starts empty and the site says so. A confident wrong
  number is worse than an honest gap.
- The crawler publishes score *components*; the browser computes the ranking. Do not move scoring
  server-side — the PS+ toggle and weight sliders depend on it.
- Prefer deleting scope to adding it.

## Design notes

`docs/` is not published. Rate limiting is AIMD (`ngp/ratelimit.py`): it creeps up while the
store is happy and halves on a refusal, remembering the refused rate as a ceiling. Plain AIMD
without that memory generated hundreds of 429s per crawl instead of one or two.

Everything optional degrades to a null column. Missing evidence is dropped and the remaining
weights renormalise — in `components.quality` and again in `score.ts`'s `dealScore` — so a game
is never marked down for data *we* do not have. A null and a zero are different claims.

`ngp/history.py` is the only thing here that accumulates. Every other source is re-fetched, so a
bug elsewhere costs a re-crawl and a bug there loses data permanently.

**Pacing is per host.** Each host in `PACED_HOSTS` gets its own `AdaptiveLimiter`, because they
are different companies and a refusal from one says nothing about the other. Do not go back to
one shared bucket: it made Metacritic's 5,000 requests queue behind the store's.

**Do not split a source into its own sequential pass.** Per-host limiters only pay off if the
hosts are exercised at the same time. `_enrich` deliberately does store detail, stars and
Metacritic in *one* task per concept — as two passes their rates apply end to end and each host
idles through the other's phase. It also keeps Metacritic's year disambiguation working, since
the release date it needs is fetched moments earlier in the same task.

Worker count comes from `net.workers_for(ceiling, task_requests, host_requests)` — never a
literal. A worker blocked on Metacritic is not fetching from the store, so a multi-host task
needs a wider pool than `ceiling × latency`. Measured latency is ~0.8s.
