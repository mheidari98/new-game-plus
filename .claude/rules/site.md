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

Art lives in `src/art.json` instead — outside `public/`, so it is build input and never served.
Pages that render at build time (`/`, `/free/`, the prerendered `/game/<id>/`) import it and emit
`<img>` directly, costing the client nothing. The runtime islands have no art: `/plus/` and the
long-tail `/game/?id=` would need the whole 428 KB file to show a handful of thumbnails, so they
show none. `artUrl(url, width)` is mandatory — the raw asset is 403,800 B and `?w=200` is 12,679 B.
Every `<img>` carries `width`/`height` so the box is reserved and nothing shifts.

Three plain `<script>` islands — `/deals/`, `/plus/` and `/game/` — and no framework runtime.
Adding one would undo the reason the site loads instantly.

`src/lib/detail.ts` returns a game page as an HTML *string* because that page is rendered twice:
prerendered at build time for the first `PRERENDERED` rows, and in the browser for the tail. One
implementation, so the two cannot drift. It escapes its inputs.

Row order in `index.json` is popularity order (the store's default sort is `sales30`), and that
order is what decides whether a game has a prerendered page. Capture the rank *before* sorting
rows for display.

A component that is `null` means "no evidence" and must be dropped, letting the remaining weights
renormalise — see `dealScore`. Scoring it zero marks a game down for data we do not have.

Astro's `base` has no trailing slash — `astro.config.mjs` normalises it. Verify any link change
with `PAGES_BASE=/new-game-plus npm run build` and check the rendered `href`, because it works
locally at root and breaks only once deployed.
