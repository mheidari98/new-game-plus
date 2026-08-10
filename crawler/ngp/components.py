"""Score components. Pure functions -- no I/O, no network, no clock.

Keep it that way. This is the part a reader needs to be able to audit, and
the crawler publishes these components rather than a final score so the
browser can recompute the ranking under the visitor's own assumptions
("I have PS+ Extra", weight sliders).

Two ideas do most of the work:

*Bayesian shrinkage* -- a 96 from three reviews is not better evidence than a
91 from 120, so scores are pulled toward a prior in proportion to how thin
the evidence is.

*A star curve* -- PSN ratings are heavily top-compressed, so a linear mapping
would call a 3.5-star game "70".
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

WEIGHTS_PATH = Path(__file__).resolve().parent.parent / "weights.toml"


@dataclass(frozen=True)
class Weights:
    quality: dict
    deal: dict
    final: dict
    adjust: dict

    @classmethod
    def defaults(cls) -> "Weights":
        return cls.load(WEIGHTS_PATH)

    @classmethod
    def load(cls, path) -> "Weights":
        raw = tomllib.loads(Path(path).read_text())
        return cls(quality=raw["quality"], deal=raw["deal"],
                   final=raw["final"], adjust=raw["adjust"])

    def as_dict(self) -> dict:
        """Copied verbatim into index.json so the browser cannot drift."""
        return {"quality": self.quality, "deal": self.deal,
                "final": self.final, "adjust": self.adjust}


@dataclass(frozen=True)
class Quality:
    score: float
    evidence: str          # high | medium | low | none
    parts: dict


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def shrink(score, count, prior_score, prior_weight):
    """Pull a score toward the prior when it rests on few observations."""
    count = max(0, count)
    return (score * count + prior_score * prior_weight) / (count + prior_weight)


def stars_to_100(average: float) -> float:
    """Map a PSN 0-5 star average onto 0-100.

    Piecewise because the data is top-compressed: nearly every competent game
    lands between 4.2 and 4.9, and 3.7 already signals real problems.
    """
    points = [(0.0, 0.0), (3.0, 30.0), (3.8, 52.0), (4.2, 66.0),
              (4.5, 78.0), (4.7, 87.0), (4.85, 94.0), (5.0, 100.0)]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if average <= x1:
            span = x1 - x0
            t = 0.0 if span == 0 else (average - x0) / span
            return y0 + t * (y1 - y0)
    return 100.0


def discount_depth(percent: int, weights: Weights) -> float:
    """Diminishing returns: 50% off is good, 90% off is not twice as good."""
    return _clamp(100.0 * (max(0, percent) / 100.0) ** weights.deal["discount_exponent"])


def price_anchor(base_cents: int, price_cents: int, weights: Weights) -> float:
    """Reward absolute money saved, log-scaled.

    Saving $45 on a $60 game is a materially different event from saving $4 on
    a $5 game, even though both are "75% off".
    """
    saved = max(0, base_cents - price_cents) / 100.0
    reference = weights.deal["anchor_reference_saved"]
    return _clamp(100.0 * math.log1p(saved) / math.log1p(reference))


def quality(critic_score, critic_count, star_average, star_count, weights: Weights) -> Quality:
    """Blend critic score and store stars, each shrunk toward its prior.

    Missing components are dropped and the rest renormalised, so a game with
    only one source is not dragged toward zero by the absent one.

    `critic_count=None` means "a score, of unknown depth" -- which is what
    Metacritic's search response gives, since the review count lives on the
    per-game page and fetching it would double the request budget. Such a
    score is weighted as if it rested on exactly `critic_prior_weight`
    reviews, so it lands halfway to the prior, and it can never on its own
    count as strong evidence.
    """
    q = weights.quality
    parts = []          # (weight, score)
    detail = {}
    has_critic = critic_score is not None and (critic_count is None or critic_count > 0)

    if has_critic:
        depth = q["critic_prior_weight"] if critic_count is None else critic_count
        shrunk = shrink(critic_score, depth,
                        q["critic_prior_score"], q["critic_prior_weight"])
        parts.append((q["critic"], shrunk))
        detail["critic"] = round(shrunk, 2)

    if star_average is not None and star_count:
        shrunk_stars = shrink(star_average, star_count,
                              q["star_prior_rating"], q["star_prior_weight"])
        scored = stars_to_100(shrunk_stars)
        parts.append((q["stars"], scored))
        detail["stars"] = round(scored, 2)

    if not parts:
        return Quality(0.0, "none", detail)

    total = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / total

    # "High" means either a deep critic consensus or two independent sources
    # with real volume behind one of them. A score whose depth we never
    # measured cannot reach it alone.
    many_stars = (star_count or 0) >= 2000
    if has_critic and ((critic_count or 0) >= 20 or many_stars):
        evidence = "high"
    elif has_critic or many_stars:
        evidence = "medium"
    else:
        evidence = "low"

    return Quality(_clamp(score), evidence, detail)
