---
paths:
  - "site/**/*.ts"
  - "site/**/*.astro"
  - "site/*.mjs"
---

# Site

`src/lib/score.ts` is pure: no fetch, no DOM, no `Date.now()`. The current year is a parameter.
It reads weights from `index.json` (`meta.weights`), which is a verbatim copy of
`crawler/weights.toml`. Never add a second copy — one copy at runtime is what stops the site
and crawler drifting.

`index.json` is columnar with integer dictionaries because a row-of-objects layout at 12k games
is 1.67 MB gzipped and blows the budget. `src/lib/index.ts` decodes it. Cover art is deliberately
absent: it was 27% of the payload, and stripping the URL prefix saves only 0.3% because the asset
hash is irreducible.

One client island, on `/deals/`. Everything else is static. Adding a framework runtime would
undo the reason the site loads instantly.

Astro's `base` has no trailing slash — `astro.config.mjs` normalises it. Verify any link change
with `PAGES_BASE=/new-game-plus npm run build` and check the rendered `href`, because it works
locally at root and breaks only once deployed.
