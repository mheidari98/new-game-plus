"""Write the published index.

The layout is measured, not stylistic. At 12,000 games, gzipped: row-of-objects
with cover art 1,670,800 B (over budget), columnar without cover art 573,422 B,
columnar + int dicts 516,677 B. Cover art, id and name are 59% of a naive
payload -- art alone is 27.1%, so it is not published and is fetched per
viewport instead. Stripping the constant URL prefix saves only 0.3%: the 48-hex
asset hash is irreducible. Int dicts barely help the bytes (gzip already
back-references the repeats) but make client-side filtering a bitmask compare.

Art URLs go to a separate build-only file -- see `save_art`.
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
_DICTED = ["genres", "esrb", "platforms", "psvr2", "evidence", "perspective"]
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


# How many rows get a *served* art manifest. Rows are in popularity order, so
# the head is what anyone actually browses. Measured: an art URL costs 34.6 B
# gzipped that gzip cannot shrink (the 48-hex asset hash is random), so the
# whole catalogue would be 428 KB -- which is why art is not in index.json at
# all. 2,000 rows is ~69 KB, fetched only when a page wants thumbnails.
ART_HEAD_ROWS = 2000


def save_art_head(path, games) -> tuple[int, int]:
    """Art URLs for the popular head, served to the browser.

    Separate from `save_art`, which writes the *build-time* file: pages that
    render at build time bake in an <img> and cost the client nothing, but the
    runtime islands (`/explore/`, `/plus/`) have no such luxury and would
    otherwise show a catalogue of text. This file is fetched after first paint,
    so it never delays the page.

    Returns `(rows_with_art, gzip_bytes)` so the caller can log both.
    """
    art = {g["id"]: g["art"] for g in games[:ART_HEAD_ROWS] if g.get("art")}
    body = json.dumps(art, separators=(",", ":"), sort_keys=True).encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return len(art), len(gzip.compress(body, 9))


def save_art(path, games) -> int:
    """Cover-art URLs, keyed by product id, for the site build to read.

    Kept out of index.json on measurement: a URL costs 34.6 B gzipped per row
    that gzip cannot shrink -- the 48-hex asset hash is random -- which is
    428 KB at the full catalogue and puts the payload over budget. This file
    is build input only. It is written outside `public/`, so the static pages
    bake `<img>` tags in at build time and no browser ever downloads it.
    """
    art = {g["id"]: g["art"] for g in games if g.get("art")}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(art, separators=(",", ":"), sort_keys=True))
    return len(art)


def save(path, body: bytes, packed: bytes) -> None:
    """index.json plus a precompressed .gz sidecar."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.with_suffix(".json.gz").write_bytes(packed)
