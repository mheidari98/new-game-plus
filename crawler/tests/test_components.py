"""The scoring layer. Pure functions, no I/O -- a reader must be able to audit it.

The crawler publishes these *components*; the browser combines them with the
weights carried in the same file. So these tests pin the components, not the
final ranking.
"""

import pytest

from ngp.components import (
    Weights,
    discount_depth,
    price_anchor,
    quality,
    shrink,
    stars_to_100,
)


@pytest.fixture
def w():
    return Weights.defaults()


class TestShrinkage:
    """A score resting on few observations is pulled toward the prior."""

    def test_many_observations_barely_move(self, w):
        assert shrink(90.0, 1000, prior_score=72.0, prior_weight=8.0) == pytest.approx(89.86, abs=0.1)

    def test_few_observations_are_pulled_hard(self, w):
        # 100/100 from a single critic is not better evidence than a solid 90.
        assert shrink(100.0, 1, prior_score=72.0, prior_weight=8.0) < 80

    def test_zero_observations_give_the_prior(self, w):
        assert shrink(100.0, 0, prior_score=72.0, prior_weight=8.0) == 72.0

    def test_a_lone_perfect_score_loses_to_a_well_reviewed_90(self, w):
        lone = shrink(100.0, 1, prior_score=72.0, prior_weight=8.0)
        solid = shrink(90.0, 120, prior_score=72.0, prior_weight=8.0)
        assert solid > lone


class TestStarCurve:
    """PSN stars are heavily top-compressed: almost every competent game sits
    between 4.2 and 4.9, and 3.7 already signals real problems. Linear
    avg/5*100 would call a 3.5-star game '70'."""

    def test_top_of_scale_is_100(self):
        assert stars_to_100(5.0) == 100.0

    def test_bottom_of_scale_is_zero(self):
        assert stars_to_100(0.0) == 0.0

    def test_three_point_five_is_well_below_seventy(self):
        assert stars_to_100(3.5) < 50

    def test_curve_is_monotonic(self):
        vals = [stars_to_100(x / 10) for x in range(0, 51)]
        assert vals == sorted(vals)

    def test_spreads_the_range_where_data_actually_lives(self):
        # 4.2 -> 4.7 is the band most games occupy; it must not be flat.
        assert stars_to_100(4.7) - stars_to_100(4.2) > 15


class TestDiscountDepth:
    def test_no_discount_is_zero(self, w):
        assert discount_depth(0, w) == 0.0

    def test_full_discount_is_100(self, w):
        assert discount_depth(100, w) == pytest.approx(100.0)

    def test_ninety_off_is_not_twice_fifty_off(self, w):
        assert discount_depth(90, w) < 2 * discount_depth(50, w)

    def test_negative_is_clamped(self, w):
        assert discount_depth(-10, w) == 0.0


class TestPriceAnchor:
    """Saving $45 on a $60 game is a different event from saving $4 on a $5
    game, even though both are 75% off."""

    def test_no_saving_is_zero(self, w):
        assert price_anchor(1999, 1999, w) == 0.0

    def test_bigger_saving_scores_higher(self, w):
        assert price_anchor(6999, 1999, w) > price_anchor(999, 499, w)

    def test_is_log_scaled_not_linear(self, w):
        # Doubling the saving must add less than double the score.
        small = price_anchor(2000, 1000, w)     # saved $10
        big = price_anchor(3000, 1000, w)       # saved $20
        assert big < 2 * small


class TestQuality:
    def test_uses_critic_and_stars_together(self, w):
        got = quality(critic_score=90, critic_count=100,
                      star_average=4.6, star_count=5000, weights=w)
        assert 60 < got.score < 100
        assert got.evidence == "high"

    def test_stars_alone_still_scores(self, w):
        got = quality(critic_score=None, critic_count=0,
                      star_average=4.6, star_count=5000, weights=w)
        assert got.score > 0
        assert got.evidence == "medium"

    def test_no_evidence_at_all_is_flagged(self, w):
        got = quality(critic_score=None, critic_count=0,
                      star_average=None, star_count=0, weights=w)
        assert got.evidence == "none"
        assert got.score == 0.0

    def test_thin_star_evidence_is_flagged_low(self, w):
        got = quality(critic_score=None, critic_count=0,
                      star_average=4.9, star_count=12, weights=w)
        assert got.evidence == "low"

    def test_missing_component_renormalises_rather_than_penalising(self, w):
        # A game with only stars must not be dragged toward zero by the
        # absent critic term.
        both = quality(90, 100, 4.6, 5000, w).score
        stars_only = quality(None, 0, 4.6, 5000, w).score
        assert stars_only > both * 0.5

    def test_is_pure(self, w):
        # Same inputs, same output, no hidden state.
        a = quality(85, 50, 4.4, 900, w).score
        b = quality(85, 50, 4.4, 900, w).score
        assert a == b


class TestUnknownCriticDepth:
    """Metacritic's search response carries the metascore but not the review
    count, and fetching the count would double the request budget. So the
    count is genuinely unknown, and `None` says so rather than guessing."""

    def test_unknown_depth_shrinks_halfway_to_the_prior(self, w):
        # Weighted as if backed by exactly `critic_prior_weight` reviews, i.e.
        # a 50/50 blend with the prior -- the conservative reading.
        got = quality(critic_score=90, critic_count=None,
                      star_average=None, star_count=0, weights=w)
        assert got.parts["critic"] == pytest.approx(81.0)

    def test_unknown_depth_scores_below_a_well_reviewed_identical_score(self, w):
        unknown = quality(90, None, None, 0, w).score
        known = quality(90, 120, None, 0, w).score
        assert unknown < known

    def test_unknown_depth_alone_is_never_high_evidence(self, w):
        # We cannot claim depth we did not measure.
        got = quality(90, None, 4.9, 12, w)
        assert got.evidence == "medium"

    def test_a_critic_score_plus_a_mass_of_stars_is_high_evidence(self, w):
        # Two independent sources, one of them with real volume behind it.
        assert quality(90, None, 4.6, 5000, w).evidence == "high"

    def test_a_missing_score_is_still_missing(self, w):
        assert quality(None, None, None, 0, w).evidence == "none"


class TestWeightsLoading:
    def test_defaults_load_from_the_toml(self, w):
        assert w.final["quality"] == 0.45
        assert w.adjust["psplus_extra"] == 0.30

    def test_round_trips_to_a_plain_dict_for_publishing(self, w):
        # The whole file is copied into index.json so the browser cannot drift.
        as_dict = w.as_dict()
        assert as_dict["final"]["quality"] == 0.45
        assert "quality" in as_dict and "deal" in as_dict
