"""Write the published index.

The layout is measured, not stylistic. At 12,000 games, gzipped: row-of-objects
with cover art 1,670,800 B (over budget), columnar without cover art 573,422 B,
columnar + int dicts 516,677 B. Cover art, id and name are 59% of a naive
payload -- art alone is 27.1%, so it is not published and is fetched per
viewport instead. Stripping the constant URL prefix saves only 0.3%: the 48-hex
asset hash is irreducible. Int dicts barely help the bytes (gzip already
back-references the repeats) but make client-side filtering a bitmask compare.

Art URLs go to separate files -- see `save_art`.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

# 800 KiB. Pages serves gzip for .json but neither brotli nor zstd, so this is
# the number that actually reaches a visitor.
GZIP_BUDGET_BYTES = 819_200

# No cover art and no precomputed final score -- the browser ranks from the
# components.
_SCALARS = [
    "id", "name", "price_cents", "base_cents", "discount_pct", "is_free",
    "plus_extra", "plus_classics", "local_players", "dualsense",
    "release_year", "critic_score", "quality", "discount_depth", "price_anchor",
    # Null until we have recorded enough prices; the browser drops a null term
    # and renormalises, so nothing is marked down for data we lack.
    "vs_historical_min", "vs_typical_sale",
    # Best-effort third-party columns. Null is the normal state for both.
    "hours_main", "splitscreen",
]
_DICTED = ["genres", "esrb", "platforms", "psvr2", "evidence"]
_MULTI = {"genres", "platforms"}      # lists per row; the rest are single values


def build_index(games, weights, generated_at=None) -> dict:
    cols = {name: [g.get(name) for g in games] for name in _SCALARS}
    dicts = {}

    for field in _DICTED:
        vocabulary = []
        encoded = []
        for game in games:
            value = game.get(field)
            if field in _MULTI:
                ids = []
                for item in value or []:
                    if item not in vocabulary:
                        vocabulary.append(item)
                    ids.append(vocabulary.index(item))
                encoded.append(ids)
            elif value is None:
                encoded.append(None)
            else:
                if value not in vocabulary:
                    vocabulary.append(value)
                encoded.append(vocabulary.index(value))
        cols[field] = encoded
        dicts[field] = vocabulary

    return {
        "meta": {
            "count": len(games),
            "generated_at": generated_at,
            # Copied verbatim: the browser reads weights from here, never from
            # its own constant, so the two cannot drift.
            "weights": weights.as_dict(),
        },
        "dicts": dicts,
        "cols": cols,
    }


def render(games, weights, generated_at=None) -> tuple[bytes, bytes, dict]:
    """Serialise the index without writing it anywhere.

    Separate from `save` so the guard can check the real sizes and refuse
    before anything lands on disk: a stale correct site beats a fresh wrong one.
    """
    body = json.dumps(build_index(games, weights, generated_at),
                      separators=(",", ":")).encode()
    packed = gzip.compress(body, 9)
    return body, packed, {
        "raw_bytes": len(body), "gzip_bytes": len(packed), "count": len(games),
        "over_budget": len(packed) > GZIP_BUDGET_BYTES}


# Every sampled asset is on this host, so it is a constant the served manifest
# does not have to repeat 12,000 times. A URL that is not on it is kept whole
# and the client re-adds the host only where there is none.
ART_HOST = "https://image.api.playstation.com/"


def _write(path, body: bytes) -> int:
    """Write, creating the directory, and return the gzipped size.

    Level 6, not 9: this number is only reported, never stored. On a 1 MB body
    level 9 costs 37 ms against 9 ms for a 4% better estimate that nobody acts
    on, and Pages compresses on the fly at about level 6 anyway -- so 6 is both
    the cheaper measurement and the more honest one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return len(gzip.compress(body, 6))


def save_art(path, games) -> tuple[int, int]:
    """Cover-art URLs keyed by product id. Returns `(rows, gzip_bytes)`.

    The build-input file, which lives outside `public/` so it is never served.
    The static pages read it to bake in `<img>` tags at no cost to the client,
    and they look a game up by id -- hence a mapping rather than an array.
    """
    art = {g["id"]: g["art"] for g in games if g.get("art")}
    body = json.dumps(art, separators=(",", ":"), sort_keys=True).encode()
    return len(art), _write(path, body)


def save_art_index(path, games) -> int:
    """The served manifest: one entry per index row, in row order. Gzip bytes.

    The explorer reads this against an index it already holds, so keying it by
    product id repeats an identifier the browser has in front of it. Measured
    on 2,000 live rows: `{id: url}` is 51.5 B gzipped an entry, this is 35.3 B
    -- 640 KB against 439 KB over the catalogue, for one fetch either way.

    The join is positional and there is nothing else to check it against, so
    the array is always `len(games)` long, nulls included. It is written from
    the same `games` list as index.json in the same publish step; that, and
    only that, is what stops the two drifting. How many rows carry art is
    `save_art`'s answer, not this one's.
    """
    body = json.dumps(
        [u[len(ART_HOST):] if u and u.startswith(ART_HOST) else u or None
         for u in (g.get("art") for g in games)],
        separators=(",", ":"),
    ).encode()
    return _write(path, body)


def save(path, body: bytes, packed: bytes) -> None:
    """index.json plus a precompressed .gz sidecar.

    Not `_write`: this one stores the compressed bytes rather than measuring
    them, so it takes the level-9 artifact `render` already produced.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.with_suffix(".json.gz").write_bytes(packed)
