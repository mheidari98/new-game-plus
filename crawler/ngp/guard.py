"""Failure policy: refuse to publish a degraded index.

GitHub Pages keeps the last successful deployment when a run fails, so
aborting costs one stale day and publishing garbage costs trust. Every
threshold here is set from a measured baseline rather than intuition.
"""

from __future__ import annotations

from ngp.publish import GZIP_BUDGET_BYTES

# "All deals" is a dynamic category: it drifted 4335 -> 4336 -> 4337 over one
# day during investigation. A few percent is weather; ten percent is a fault.
MIN_CATALOGUE_RATIO = 0.90
# The Extra catalogue has sat around 471 entries. Anything near zero means the
# feed failed rather than the catalogue emptying.
MIN_PLUS_EXTRA = 100
MAX_ENRICHMENT_FAILURE_RATIO = 0.20


class PublishBlocked(RuntimeError):
    """The run must not publish. Keep the last good deploy and raise an issue."""


def check_publishable(
    *,
    game_count,
    previous_game_count,
    plus_extra_count,
    gzip_bytes,
    enrichment_attempted,
    enrichment_failed,
    filtered_counts=None,
    baseline_count=None,
):
    if previous_game_count:
        floor = previous_game_count * MIN_CATALOGUE_RATIO
        if game_count < floor:
            raise PublishBlocked(
                f"catalogue shrank from {previous_game_count} to {game_count} "
                f"(floor {floor:.0f}); crawl likely truncated"
            )

    if plus_extra_count < MIN_PLUS_EXTRA:
        raise PublishBlocked(
            f"PS+ Extra catalogue has {plus_extra_count} entries (expected ~471); "
            "publishing would mark every game as not-in-PS+"
        )

    if gzip_bytes > GZIP_BUDGET_BYTES:
        raise PublishBlocked(
            f"index is {gzip_bytes} B gzipped, over the {GZIP_BUDGET_BYTES} B budget"
        )

    if enrichment_attempted:
        ratio = enrichment_failed / enrichment_attempted
        if ratio > MAX_ENRICHMENT_FAILURE_RATIO:
            raise PublishBlocked(
                f"enrichment failed on {enrichment_failed}/{enrichment_attempted} "
                f"({ratio:.0%}); source may be blocking us"
            )

    # An unknown facet NAME is silently ignored and returns everything; an
    # unknown VALUE returns nothing. Catch both, not just "did it change".
    for name, count in (filtered_counts or {}).items():
        if count == 0:
            raise PublishBlocked(f"facet {name} matched no rows; bad value?")
        if baseline_count and count >= baseline_count:
            raise PublishBlocked(f"facet {name} matched everything; filter ignored?")
