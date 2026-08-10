# Contributing

## Setup

```bash
pip install -r crawler/requirements-dev.txt
pytest                      # 203 tests, no network

cd site && npm ci
npx vitest run              # 15 tests
npm run build
```

Nothing here needs a secret. IGDB is the only key the project will ever take, and its absence
must produce null columns rather than a failure.

## Rules that exist for a reason

Each of these is here because ignoring it caused a real bug.

**Never guess a persisted-query hash.** The store uses Apollo persisted queries with a
server-side allowlist. Hashes are pinned in `crawler/ngp/store.py`, captured from the public web
client. If they all fail, re-capture from browser DevTools. Probing an allowlist is
reconnaissance against a third party, not debugging.

**All outbound HTTP goes through `ngp/net.py`.** That is what makes the rate limit unbypassable.
No bare `httpx` or `requests` calls anywhere else.

**Guard every fuzzy match with `titles.numbers_compatible()`.** "Mortal Kombat 11" scores above
any sane threshold against "Mortal Kombat 1". There is currently no fuzzy path — PS+ matching is
exact conceptId/productId — and adding one without this guard will silently mismatch sequels.

**Take the lock in `ngp/cache.py`.** One SQLite connection is shared by five worker threads.
`sqlite3` serialises statements but not transactions, so an unguarded write raises "cannot start
a transaction within a transaction" and loses rows. Observed live. Every method that touches the
database takes `self._lock`; new ones must too.

**Keep the scoring layer pure.** `crawler/ngp/components.py` and `site/src/lib/score.ts` take
data in and return numbers. No network, no filesystem, no clock. A reader auditing the ranking
should have to read only those two files.

**Weights live in one file.** `crawler/weights.toml` is copied verbatim into `index.json`, and
the browser reads them from there rather than from a constant of its own. Do not add a second
copy — one copy at runtime is what stops the site and crawler drifting.

**Say when data is missing.** Price history starts empty and the site says so. Do not fill a gap
with a plausible number; a confident wrong recommendation is worse than an honest gap.

**`.npmrc` pins the public registry.** Do not remove it. Without it, a contributor whose global
npm points at a private mirror bakes that hostname into `package-lock.json`, which breaks CI and
leaks internal infrastructure into a public repo. This has already happened once.

## Testing

Test-first. If a test passes the moment you write it, you are testing existing behaviour rather
than driving new behaviour — either delete it or mutate the implementation to prove the test can
fail.

The suites run with no network. Anything that needs the live store belongs in a manual run:

```bash
python crawler/main.py --once --limit 25 -v
```

## Rate limiting

`ngp/ratelimit.py` uses AIMD — it creeps the rate up while the store is happy and halves it on a
refusal, remembering the refused rate as a ceiling so the ramp stops just short of it. Simulated
over the 40,460-request cold start, that converges in 1–4 refusals; plain AIMD without the
remembered ceiling produced hundreds.

If you raise the ceiling, raise the worker count with it: `workers ≈ ceiling × latency`, and
measured latency is ~0.8 s. Extra workers past that just queue on the limiter.
