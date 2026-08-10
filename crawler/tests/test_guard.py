"""Failure policy.

A stale-but-correct site beats a fresh-but-wrong one. Pages keeps the last
successful deployment when a run fails, so aborting is cheap and publishing
garbage is not.
"""

import pytest

from ngp.guard import PublishBlocked, check_publishable


def state(**over):
    base = dict(
        game_count=2740,
        previous_game_count=2740,
        plus_extra_count=471,
        gzip_bytes=440_645,
        enrichment_attempted=1000,
        enrichment_failed=10,
        filtered_counts={"FULL_GAME": 2005},
        baseline_count=4337,
    )
    base.update(over)
    return base


class TestCatalogueSize:
    """'All deals' is a dynamic category and drifts a few items per day --
    4335 -> 4336 -> 4337 over one day, measured. A few percent is normal;
    ten percent is not."""

    def test_a_normal_day_publishes(self):
        check_publishable(**state(game_count=2745))

    def test_small_drift_is_fine(self):
        check_publishable(**state(game_count=2700))

    def test_a_ten_percent_drop_blocks(self):
        with pytest.raises(PublishBlocked, match="shrank"):
            check_publishable(**state(game_count=2400))

    def test_a_collapse_blocks(self):
        with pytest.raises(PublishBlocked, match="shrank"):
            check_publishable(**state(game_count=100))

    def test_growth_is_never_blocked(self):
        check_publishable(**state(game_count=5000))

    def test_first_run_has_no_baseline_to_compare(self):
        check_publishable(**state(previous_game_count=None, game_count=2740))


class TestPlusCatalogue:
    """An empty Extra catalogue marks every game as not-in-PS+ -- a wrong
    answer on every row, which is worse than no answer."""

    def test_empty_extra_blocks(self):
        with pytest.raises(PublishBlocked, match="PS\\+"):
            check_publishable(**state(plus_extra_count=0))

    def test_implausibly_small_extra_blocks(self):
        with pytest.raises(PublishBlocked, match="PS\\+"):
            check_publishable(**state(plus_extra_count=12))

    def test_a_normal_catalogue_passes(self):
        check_publishable(**state(plus_extra_count=471))


class TestPayloadBudget:
    def test_over_budget_blocks(self):
        with pytest.raises(PublishBlocked, match="budget"):
            check_publishable(**state(gzip_bytes=900_000))

    def test_under_budget_passes(self):
        check_publishable(**state(gzip_bytes=500_000))


class TestEnrichmentHealth:
    def test_high_failure_rate_blocks(self):
        with pytest.raises(PublishBlocked, match="enrichment"):
            check_publishable(**state(enrichment_attempted=1000, enrichment_failed=250))

    def test_ordinary_failures_pass(self):
        check_publishable(**state(enrichment_attempted=1000, enrichment_failed=30))

    def test_no_enrichment_attempted_is_not_a_division_error(self):
        check_publishable(**state(enrichment_attempted=0, enrichment_failed=0))


class TestFacetSanity:
    """Two opposite silent failures: an unknown facet name returns the whole
    catalogue, an unknown value returns nothing."""

    def test_a_zeroed_facet_blocks(self):
        with pytest.raises(PublishBlocked, match="facet"):
            check_publishable(**state(filtered_counts={"FULL_GAME": 0}))

    def test_a_facet_matching_everything_blocks(self):
        with pytest.raises(PublishBlocked, match="facet"):
            check_publishable(**state(filtered_counts={"FULL_GAME": 4337},
                                      baseline_count=4337))


class TestReporting:
    def test_the_error_says_what_to_do(self):
        with pytest.raises(PublishBlocked) as exc:
            check_publishable(**state(game_count=100))
        message = str(exc.value)
        assert "2740" in message and "100" in message
