"""Measure the IGDB join against a ground truth instead of estimating it.

Wikidata publishes a crosswalk: P12332 is a PlayStation Store concept id and
P5794 is an IGDB game identifier. One SPARQL query returns every item that
carries both -- 6,182 pairs today -- which is large enough to report a real
precision and recall figure for the matcher rather than a plausible-sounding
one.

    python crawler/match.py --measure-accuracy [--proxy http://127.0.0.1:2080]

Two things this found that the design did not know:

* **P5794 stores a slug, not a numeric id** (`jurassic-world-evolution-3`),
  so the comparison is on slugs and the IGDB query has to ask for one.
* The pair count drifts -- 6,083 when this was planned, 6,182 today. Like
  every other count in this project, it is read, never asserted.

Without IGDB credentials this still reports the crosswalk's size and how much
of our catalogue it covers, and says plainly that the rest needs a key. It is
a measurement tool, not a test: it never fails a build.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlencode

from ngp.igdb import PAGE, Igdb
from ngp.net import HttpClient
from ngp.ratelimit import AdaptiveLimiter

REPO = Path(__file__).resolve().parent.parent

SPARQL = "https://query.wikidata.org/sparql"
CROSSWALK = """
SELECT ?concept ?igdb WHERE { ?item wdt:P12332 ?concept ; wdt:P5794 ?igdb . }
"""


def crosswalk(http) -> dict[str, str]:
    """`{psn_concept_id: igdb_slug}` from Wikidata."""
    url = f"{SPARQL}?{urlencode({'query': CROSSWALK, 'format': 'json'})}"
    payload = http.get_json(url, headers={"accept": "application/sparql-results+json"})
    return {row["concept"]["value"]: row["igdb"]["value"]
            for row in payload["results"]["bindings"]}


def our_concepts(db) -> set[str]:
    """Concept ids we have crawled. `index.json` publishes product ids, so the
    crawl cache is the only place the concept ids survive."""
    if not Path(db).exists():
        return set()
    with sqlite3.connect(str(db)) as conn:
        return {str(row[0]) for row in conn.execute("SELECT concept_id FROM concept")}


def measure(http, truth, ours) -> None:
    print(f"crosswalk: {len(truth)} PSN concept -> IGDB slug pairs on Wikidata")
    if ours:
        overlap = truth.keys() & ours
        print(f"our catalogue: {len(ours)} concepts, {len(overlap)} of them in the "
              f"crosswalk ({100 * len(overlap) / len(ours):.1f}%)")
    else:
        print("our catalogue: no crawl cache found, so coverage is unknown "
              "(run a crawl first)")

    igdb = Igdb(http)
    if not igdb.configured:
        print("\nIGDB_CLIENT_ID / IGDB_CLIENT_SECRET are unset, so the join itself "
              "cannot be measured.\nThe uid format is still unverified: it may hold "
              "a concept id, a product id or an npTitleId,\nand ngp/igdb.py "
              "classifies it at runtime rather than assuming one.")
        return

    source = igdb.source_id()
    if source is None:
        print("\nIGDB has no PlayStation Store external-game source; no join to measure.")
        return
    index, shape = igdb.psn_index(source)
    print(f"\nIGDB: {len(index)} PlayStation Store links, uid shape {shape or 'unrecognised'}")
    if shape != "concept_id":
        print("uids are not concept ids, so they cannot be compared against P12332 "
              "directly; measure the fallback name matcher instead.")
        return

    # Ground truth is a slug, so ask IGDB for slugs and compare those.
    checkable = {cid: index[cid] for cid in truth if cid in index}
    slugs = {}
    ids = sorted(set(checkable.values()))
    for start in range(0, len(ids), PAGE):
        batch = ids[start:start + PAGE]
        for row in igdb.query("games", (
                "fields id,slug; where id = "
                f"({','.join(str(i) for i in batch)}); limit {PAGE};")):
            slugs[row["id"]] = row.get("slug")

    agree = sum(1 for cid, gid in checkable.items() if slugs.get(gid) == truth[cid])
    print(f"joined {len(checkable)} of {len(truth)} known pairs "
          f"(recall {100 * len(checkable) / len(truth):.1f}%)")
    if checkable:
        print(f"of those, {agree} point at the same game "
              f"(precision {100 * agree / len(checkable):.1f}%)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measure-accuracy", action="store_true")
    parser.add_argument("--proxy", help="e.g. http://127.0.0.1:2080")
    parser.add_argument("--cache", default="data/cache/ngp.sqlite")
    args = parser.parse_args(argv)
    if not args.measure_accuracy:
        parser.print_help()
        return 0

    with HttpClient(limiter=AdaptiveLimiter(start=1.0, ceiling=4.0),
                    proxy=args.proxy) as http:
        measure(http, crosswalk(http), our_concepts(REPO / args.cache))
    return 0


if __name__ == "__main__":
    sys.exit(main())
