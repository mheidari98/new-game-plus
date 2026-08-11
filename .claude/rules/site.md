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
absent from it: it was 27% of the payload, and stripping the URL prefix saves only 0.3% because
the asset hash is irreducible.

Art ships as two files from one publish step. `src/art.json` is keyed by product id and lives
outside `public/`, so it is build input and never served — the prerendered `/game/<id>/` pages
import it and emit `<img>` directly, costing the client nothing. `public/art.json` is the served
manifest and is a **positional array over index.json's rows**, because the browser already holds
the ids: measured on 2,000 live rows, `{id: url}` is 51.5 B gzipped an entry against 35.3 B for
the array, so 640 KB becomes 439 KB. The constant `https://image.api.playstation.com/` prefix is
stripped and re-added client-side only where a value has no scheme.

That join is positional and nothing else checks it, so the explorer refuses the file unless
`art.length === rows.length`: a short array shifts every cover after the gap onto the wrong game,
which looks like working software. Both files are written from the same `games` list in the same
publish step, which is what makes the invariant hold.

`artUrl(url, width)` is mandatory — the raw asset is 403,800 B and `?w=200` is 12,679 B. Every
`<img>` carries `width`/`height` so the box is reserved and nothing shifts.

Two plain `<script>` islands — the explorer at `/` and `/plus/`, plus `/game/` for the long tail
— and no framework runtime. Adding one would undo the reason the site loads instantly.

The explorer renders in batches of `BATCH` rows and appends with `insertAdjacentHTML`, never by
rebuilding the tbody. A full repaint is for a filter, sort or weight change only; the one other
caller is cover art landing after first paint. An `IntersectionObserver` on the "show more"
button does the appending, and it is **re-armed after every append** — the callback fires on a
change of intersection, so a button still on screen after a batch would never fire again and the
list would stall.

Nav is two tabs. `/explore/` and `/deals/` are redirects that forward `location.search`, because
every shareable URL the explorer produces carries its filters in the query string and a meta
refresh cannot.

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
