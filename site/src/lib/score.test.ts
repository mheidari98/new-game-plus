import { describe, expect, it } from 'vitest';
import { finalScore, rank, type Weights } from './score';

// Mirrors crawler/weights.toml, which is copied into index.json as
// meta.weights. The browser reads weights from there, never from a constant
// of its own -- that is what stops the site and crawler drifting.
const weights: Weights = {
  final: { quality: 0.45, deal: 0.3, value: 0.25 },
  deal: { discount_depth: 0.6, price_anchor: 0.4 },
  adjust: {
    psplus_extra: 0.3,
    psplus_classics: 0.85,
    age_full_credit_years: 3,
    age_min_multiplier: 0.9,
    ps4_only: 0.96,
    thin_evidence: 0.8,
    no_evidence: 0.68,
  },
};

const game = (over: Partial<any> = {}) => ({
  quality: 80,
  discount_depth: 70,
  price_anchor: 60,
  price_cents: 1999,
  plus_extra: false,
  plus_classics: false,
  platforms: ['PS5'],
  release_year: 2024,
  evidence: 'high',
  ...over,
});

describe('finalScore', () => {
  it('blends quality, deal and value', () => {
    const s = finalScore(game(), weights, { hasPlusExtra: false }, 2026);
    expect(s).toBeGreaterThan(0);
    expect(s).toBeLessThanOrEqual(100);
  });

  it('is pure - same inputs give the same output', () => {
    const g = game();
    expect(finalScore(g, weights, { hasPlusExtra: false }, 2026))
      .toBe(finalScore(g, weights, { hasPlusExtra: false }, 2026));
  });

  it('ranks a better game above a worse one at the same price', () => {
    const good = finalScore(game({ quality: 90 }), weights, {}, 2026);
    const bad = finalScore(game({ quality: 40 }), weights, {}, 2026);
    expect(good).toBeGreaterThan(bad);
  });

  it('rewards a deeper discount', () => {
    const deep = finalScore(game({ discount_depth: 95 }), weights, {}, 2026);
    const shallow = finalScore(game({ discount_depth: 10 }), weights, {}, 2026);
    expect(deep).toBeGreaterThan(shallow);
  });
});

describe('PS+ Extra toggle', () => {
  it('demotes a game you can already play', () => {
    const g = game({ plus_extra: true });
    const without = finalScore(g, weights, { hasPlusExtra: false }, 2026);
    const with_ = finalScore(g, weights, { hasPlusExtra: true }, 2026);
    expect(with_).toBeLessThan(without);
  });

  it('leaves non-catalogue games untouched', () => {
    const g = game({ plus_extra: false });
    expect(finalScore(g, weights, { hasPlusExtra: true }, 2026))
      .toBe(finalScore(g, weights, { hasPlusExtra: false }, 2026));
  });

  it('applies the Classics multiplier only to Classics titles', () => {
    const g = game({ plus_classics: true });
    const on = finalScore(g, weights, { hasPlusExtra: true }, 2026);
    const plain = finalScore(game(), weights, { hasPlusExtra: true }, 2026);
    expect(on).toBeLessThan(plain);
  });
});

describe('adjustments', () => {
  it('discounts an older game', () => {
    const old = finalScore(game({ release_year: 2014 }), weights, {}, 2026);
    const fresh = finalScore(game({ release_year: 2025 }), weights, {}, 2026);
    expect(old).toBeLessThan(fresh);
  });

  it('penalises a PS4-only title', () => {
    const ps4 = finalScore(game({ platforms: ['PS4'] }), weights, {}, 2026);
    const ps5 = finalScore(game({ platforms: ['PS5'] }), weights, {}, 2026);
    expect(ps4).toBeLessThan(ps5);
  });

  it('penalises thin review evidence', () => {
    const thin = finalScore(game({ evidence: 'low' }), weights, {}, 2026);
    const solid = finalScore(game({ evidence: 'high' }), weights, {}, 2026);
    expect(thin).toBeLessThan(solid);
  });

  it('penalises absent evidence hardest', () => {
    const none = finalScore(game({ evidence: 'none' }), weights, {}, 2026);
    const low = finalScore(game({ evidence: 'low' }), weights, {}, 2026);
    expect(none).toBeLessThan(low);
  });

  it('never returns a negative score', () => {
    const s = finalScore(
      game({ quality: 0, discount_depth: 0, price_anchor: 0, evidence: 'none',
             plus_extra: true, release_year: 2005, platforms: ['PS4'] }),
      weights, { hasPlusExtra: true }, 2026);
    expect(s).toBeGreaterThanOrEqual(0);
  });
});

describe('weight sliders', () => {
  it('respects a caller-supplied weight override', () => {
    const qualityHeavy = { ...weights, final: { quality: 1, deal: 0, value: 0 } };
    const dealHeavy = { ...weights, final: { quality: 0, deal: 1, value: 0 } };
    const g = game({ quality: 90, discount_depth: 10, price_anchor: 10 });
    expect(finalScore(g, qualityHeavy, {}, 2026))
      .toBeGreaterThan(finalScore(g, dealHeavy, {}, 2026));
  });
});

describe('rank', () => {
  it('sorts descending by score', () => {
    const games = [game({ quality: 30 }), game({ quality: 90 }), game({ quality: 60 })];
    const out = rank(games, weights, {}, 2026);
    expect(out.map((g) => g.quality)).toEqual([90, 60, 30]);
  });

  it('handles an empty list', () => {
    expect(rank([], weights, {}, 2026)).toEqual([]);
  });
});
