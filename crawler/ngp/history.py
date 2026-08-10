"""Append-only price history, partitioned by month.

The only thing here that accumulates: a bug elsewhere costs a re-crawl, a bug
here loses data permanently. Wayback cannot bootstrap it -- measured median of
2 captures per product across its whole lifetime.

Rows are written only when a price moves: writing every price every day is
~4,300 rows of which ~900 carry information. One file per month, because a
single append-only file re-stores the whole blob in every commit (~1.8 GB of
git objects in year one) and a file per day would hit the 3,000-entries-per-
directory limit in year nine. The month is in the path, so a row carries only
the day.

    python crawler/ngp/history.py --report          # what we actually have
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

HEADER = ["day", "id", "price", "base"]

Observation = tuple[date, int, int]


@dataclass(frozen=True)
class Summary:
    count: int
    min_cents: int
    typical_sale_cents: int | None


def month_path(root, when: date) -> Path:
    return Path(root) / "prices" / f"{when.year:04d}" / f"{when.month:02d}.csv"


def load(root) -> dict[str, list[Observation]]:
    """Every observation, keyed by product id, oldest first.

    A missing directory is an empty history, not an error: fresh clone, or a
    first run before the data branch exists.
    """
    series: dict[str, list[Observation]] = {}
    prices = Path(root) / "prices"
    if not prices.is_dir():
        return series
    for path in sorted(prices.glob("*/*.csv")):
        year, month = int(path.parent.name), int(path.stem)
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                when = date(year, month, int(row["day"]))
                series.setdefault(row["id"], []).append(
                    (when, int(row["price"]), int(row["base"])))
    for observations in series.values():
        observations.sort()
    return series


def _changes(series, priced):
    """Which of `priced` differ from the last thing recorded for them.

    `priced` is `(product_id, price_cents, base_cents)`. A None price is an
    "Unavailable" product; it is skipped rather than recorded as zero.
    """
    return [
        (pid, price, base)
        for pid, price, base in priced
        if price is not None and base is not None
        and (pid not in series or series[pid][-1][1:] != (price, base))
    ]


def _write(root, changed, when: date) -> None:
    path = month_path(root, when)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(HEADER)
        writer.writerows((when.day, pid, price, base) for pid, price, base in changed)


def append_changes(root, priced, when: date) -> int:
    """Append the prices that moved. Returns the number of rows written."""
    return record(root, priced, when)[1]


def record(root, priced, when: date) -> tuple[dict[str, list[Observation]], int]:
    """Append today's changes and return the whole history *including* them.

    Deciding "did this price move?" in one place is what stops the stored file
    and the scored series disagreeing. Today counts toward the history it is
    scored against, or a genuine all-time low would score as ordinary.
    """
    series = load(root)
    changed = _changes(series, priced)
    if changed:
        _write(root, changed, when)
    for pid, price, base in changed:
        series.setdefault(pid, []).append((when, price, base))
    return series, len(changed)


def summarise(series, min_observations: int) -> Summary | None:
    """What this history supports saying, or None when it supports nothing.

    Below the floor the caller drops the history components and renormalises:
    a confident number from three data points is worse than an honest gap.
    """
    if not series or len(series) < min_observations:
        return None
    # A sale is an observation below that day's list price. Full-price days
    # are not sales and would drag the median toward the list price.
    sales = sorted(price for _, price, base in series if price < base)
    return Summary(
        count=len(series),
        min_cents=min(price for _, price, _ in series),
        typical_sale_cents=sales[(len(sales) - 1) // 2] if sales else None,
    )


def _report(root, min_observations: int) -> str:
    series = load(root)
    if not series:
        return f"no usable price history yet ({Path(root)/'prices'} is empty)"
    usable = {pid: s for pid, s in series.items()
              if summarise(s, min_observations) is not None}
    days = sorted({o[0] for observations in series.values() for o in observations})
    lines = [
        f"{len(series)} products, {sum(len(s) for s in series.values())} observations",
        f"{days[0]} to {days[-1]}, {len(days)} days with at least one change",
        f"{len(usable)} products have the {min_observations} observations needed to score",
    ]
    if not usable:
        lines.append("no usable price history yet -- history components stay dropped")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--root", default="history")
    parser.add_argument("--min-observations", type=int, default=4)
    args = parser.parse_args()
    print(_report(args.root, args.min_observations))
