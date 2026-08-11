import { describe, expect, it } from 'vitest';
import { coveredBy, evaluate, TIERS, type Pick, type Tier } from './plusmath';

const tier = (id: 'essential' | 'extra' | 'premium', yearlyCents = 13499): Tier => ({
  ...TIERS.find((t) => t.id === id)!,
  yearlyCents,
});

const pick = (over: Partial<Pick> = {}): Pick => ({
  id: 'UP1-X_00-A',
  name: 'A Game',
  price_cents: 5999,
  plus_extra: false,
  plus_classics: false,
  ...over,
});

describe('coverage', () => {
  it('counts nothing as covered by Essential', () => {
    // Essential is online play plus monthly games that rotate. It is not a
    // catalogue, so no wishlist entry can be "already included".
    expect(coveredBy('essential', pick({ plus_extra: true }))).toBe(false);
  });

  it('counts Extra catalogue games under Extra', () => {
    expect(coveredBy('extra', pick({ plus_extra: true }))).toBe(true);
  });

  it('does not count Classics under Extra', () => {
    expect(coveredBy('extra', pick({ plus_classics: true }))).toBe(false);
  });

  it('counts both catalogues under Premium', () => {
    expect(coveredBy('premium', pick({ plus_classics: true }))).toBe(true);
    expect(coveredBy('premium', pick({ plus_extra: true }))).toBe(true);
  });
});

describe('evaluate', () => {
  it('adds up what the covered games cost today', () => {
    const out = evaluate(
      [pick({ plus_extra: true, price_cents: 3999 }),
       pick({ id: 'b', plus_extra: true, price_cents: 1999 })],
      tier('extra'),
    );
    expect(out.coveredCents).toBe(5998);
    expect(out.uncovered).toHaveLength(0);
  });

  it('ignores games you would still have to buy', () => {
    const out = evaluate(
      [pick({ plus_extra: true, price_cents: 3999 }), pick({ id: 'b', price_cents: 6999 })],
      tier('extra'),
    );
    expect(out.coveredCents).toBe(3999);
    expect(out.uncovered.map((g) => g.id)).toEqual(['b']);
  });

  it('gives a free catalogue game no credit', () => {
    // You were never going to pay for it, so it cannot justify a subscription.
    const out = evaluate([pick({ plus_extra: true, price_cents: 0 })], tier('extra'));
    expect(out.coveredCents).toBe(0);
    expect(out.worthIt).toBe(false);
  });

  it('is worth it exactly when the covered games cost more than the sub', () => {
    const under = evaluate([pick({ plus_extra: true, price_cents: 13499 })], tier('extra', 13499));
    const over = evaluate([pick({ plus_extra: true, price_cents: 13500 })], tier('extra', 13499));
    expect(under.worthIt).toBe(false);   // breaking even is not worth it
    expect(over.worthIt).toBe(true);
  });

  it('reports the shortfall as a negative net', () => {
    const out = evaluate([pick({ plus_extra: true, price_cents: 3999 })], tier('extra', 13499));
    expect(out.netCents).toBe(-9500);
  });

  it('follows the price the visitor actually typed', () => {
    const list = [pick({ plus_extra: true, price_cents: 9999 })];
    expect(evaluate(list, tier('extra', 13499)).worthIt).toBe(false);
    expect(evaluate(list, tier('extra', 5999)).worthIt).toBe(true);
  });

  it('says no on an empty list', () => {
    const out = evaluate([], tier('extra'));
    expect(out.worthIt).toBe(false);
    expect(out.netCents).toBe(-13499);
  });

  it('is pure - same inputs give the same output', () => {
    const list = [pick({ plus_extra: true })];
    expect(evaluate(list, tier('extra'))).toEqual(evaluate(list, tier('extra')));
  });
});

describe('what the list costs', () => {
  // The verdict answers "is the subscription worth it". These answer the
  // question people ask next, which is "so what am I actually spending".
  const list = () => [
    pick({ id: 'a', plus_extra: true, price_cents: 3999 }),
    pick({ id: 'b', price_cents: 4999 }),
    pick({ id: 'c', plus_extra: true, price_cents: 2999 }),
  ];

  it('totals every game on the list at today s price', () => {
    expect(evaluate(list(), tier('extra')).listCents).toBe(11997);
  });

  it('splits that total into covered and still-to-buy', () => {
    const out = evaluate(list(), tier('extra'));
    expect(out.coveredCents).toBe(6998);
    expect(out.uncoveredCents).toBe(4999);
  });

  it('counts a free game as zero rather than dropping it', () => {
    const out = evaluate([pick({ price_cents: 0 })], tier('extra'));
    expect(out.listCents).toBe(0);
    expect(out.uncovered).toHaveLength(1);
  });

  it('is zero on an empty list', () => {
    const out = evaluate([], tier('extra'));
    expect(out.listCents).toBe(0);
    expect(out.uncoveredCents).toBe(0);
  });

  it('moves a game between the halves when the tier changes', () => {
    const classics = [pick({ plus_classics: true, price_cents: 1999 })];
    expect(evaluate(classics, tier('extra')).uncoveredCents).toBe(1999);
    expect(evaluate(classics, tier('premium')).coveredCents).toBe(1999);
  });
});
