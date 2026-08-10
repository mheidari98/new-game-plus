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

**Everything else**

- Strip trademark symbols **before** NFKD, or `™` becomes the letters "TM".
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

If you raise the rate ceiling, raise the worker count with it: `workers ≈ ceiling × latency`,
and measured latency is ~0.8s. Extra workers past that just queue on the limiter.
