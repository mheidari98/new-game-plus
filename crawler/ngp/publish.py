"""Write the published index.

The layout is measured, not stylistic. At 12,000 games:

    row-of-objects, with cover art   1,670,800 B gzipped   FAILS
    columnar, no cover art             573,422 B gzipped   passes
    columnar + int dicts               516,677 B gzipped   passes

Three fields are 59% of a naive payload: cover art, id and name. Cover art
alone is 27.1%, so it is not published -- art is fetched for the visible
viewport instead. Stripping the constant URL prefix was measured to save
0.3%, because the 48-hex asset hash is irreducible; there is no middle path.

Integer dictionaries barely help the bytes (gzip already back-references the
repeated strings) but make client-side filtering a bitmask compare instead of
a string compare, which is why they are here.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

# 800 KiB. Pages serves gzip for .json but neither brotli nor zstd, so this
# is the number that actually reaches a visitor.
GZIP_BUDGET_BYTES = 819_200

# Published per game. Deliberately excludes cover art and any precomputed
# final score -- the browser computes the ranking from the components.
_SCALARS = [
    "id", "name", "price_cents", "base_cents", "discount_pct", "is_free",
    "plus_extra", "plus_classics", "local_players", "dualsense",
    "release_year", "quality", "discount_depth", "price_anchor",
]
_DICTED = ["genres", "esrb", "platforms", "tier", "psvr2", "evidence"]
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
            # Copied verbatim so the browser reads weights from here rather
            # than its own constant. The two cannot drift.
            "weights": weights.as_dict(),
        },
        "dicts": dicts,
        "cols": cols,
    }


def write_index(games, weights, path, generated_at=None) -> dict:
    """Write index.json plus a precompressed .gz sidecar. Returns sizes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    body = json.dumps(build_index(games, weights, generated_at),
                      separators=(",", ":")).encode()
    path.write_bytes(body)

    packed = gzip.compress(body, 9)
    path.with_suffix(".json.gz").write_bytes(packed)

    return {"raw_bytes": len(body), "gzip_bytes": len(packed),
            "count": len(games), "over_budget": len(packed) > GZIP_BUDGET_BYTES}
