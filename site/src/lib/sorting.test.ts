import { describe, expect, it } from 'vitest';
import { SORT_LABELS, sortRows } from './sorting';
import { DEFAULTS, SORT_COLUMNS, type ExploreState } from './urlstate';
import type { Row } from './index';
import type { Weights } from './score';

/** Shaped like meta.weights in index.json. Local to the test, not a second
 *  copy for the site to read -- the island takes these from the payload. */
const weights: Weights = {
  final: { quality: 0.45, deal: 0.3, value: 0.25 },
  deal: { discount_depth: 0.4, price_anchor: 0.25, vs_historical_min: 0.2, vs_typical_sale: 0.15 },
  adjust: {
    psplus_extra: 0.3, psplus_classics: 0.85, age_full_credit_years: 3,
    age_min_multiplier: 0.9, ps4_only: 0.96, thin_evidence: 0.8, no_evidence: 0.68,
  },
};

const row = (over: Partial<Row>): Row => ({
  id: 'x', name: 'x', price_cents: 1999, base_cents: 3999, discount_pct: 50,
  is_free: false, plus_extra: false, plus_classics: false, local_players: 1, dualsense: false,
  release_year: 2022, critic_score: null, vs_historical_min: null, vs_typical_sale: null,
  hours_main: null, splitscreen: null, perspective: null, quality: 70, discount_depth: 50,
  price_anchor: 50, genres: [], platforms: ['PS5'], esrb: null, psvr2: null,
  evidence: 'medium', ...over,
});

const state = (over: Partial<ExploreState>): ExploreState => ({ ...DEFAULTS, ...over });
const names = (rows: Row[]) => rows.map((r) => r.name);

describe('sortRows', () => {
  it('sorts by price ascending', () => {
    const rows = [row({ id: 'b', name: 'b', price_cents: 3000 }), row({ id: 'a', name: 'a', price_cents: 1000 })];
    expect(names(sortRows(rows, state({ sort: 'price', desc: false }), weights, new Map(), 2026)))
      .toEqual(['a', 'b']);
  });

  it('sorts by price descending', () => {
    const rows = [row({ id: 'a', name: 'a', price_cents: 1000 }), row({ id: 'b', name: 'b', price_cents: 3000 })];
    expect(names(sortRows(rows, state({ sort: 'price', desc: true }), weights, new Map(), 2026)))
      .toEqual(['b', 'a']);
  });

  it('breaks ties on popularity rank rather than leaving input order to chance', () => {
    // Every column here has integer-ish values over 12,732 rows, so ties are
    // everywhere. Relying on sort stability over the input array couples the
    // display order to whatever the filter happened to emit, which changes as
    // filters change -- rows appear to shuffle for no reason.
    const rows = [
      row({ id: 'unpopular', name: 'unpopular', price_cents: 1999 }),
      row({ id: 'popular', name: 'popular', price_cents: 1999 }),
    ];
    const rank = new Map([['popular', 3], ['unpopular', 900]]);
    expect(names(sortRows(rows, state({ sort: 'price', desc: false }), weights, rank, 2026)))
      .toEqual(['popular', 'unpopular']);
  });

  it('puts unknown hours last in both directions, never first', () => {
    // A null is "we did not measure this". Sorting it as 0 would make every
    // unknown-length game the shortest game on the site.
    const rows = [
      row({ id: 'unknown', name: 'unknown', hours_main: null }),
      row({ id: 'short', name: 'short', hours_main: 3 }),
    ];
    expect(names(sortRows(rows, state({ sort: 'hours', desc: false }), weights, new Map(), 2026)))
      .toEqual(['short', 'unknown']);
    expect(names(sortRows(rows, state({ sort: 'hours', desc: true }), weights, new Map(), 2026)))
      .toEqual(['short', 'unknown']);
  });

  it('puts an unknown player count last too', () => {
    const rows = [
      row({ id: 'unknown', name: 'unknown', local_players: null }),
      row({ id: 'two', name: 'two', local_players: 2 }),
    ];
    expect(names(sortRows(rows, state({ sort: 'players', desc: true }), weights, new Map(), 2026)))
      .toEqual(['two', 'unknown']);
  });

  it('sorts by name alphabetically, ignoring case', () => {
    const rows = [row({ id: 'z', name: 'Zelda' }), row({ id: 'a', name: 'astro bot' })];
    expect(names(sortRows(rows, state({ sort: 'name', desc: false }), weights, new Map(), 2026)))
      .toEqual(['astro bot', 'Zelda']);
  });

  it('ranks by score when no column is chosen, and the weights actually move it', () => {
    const cheapAndGood = row({ id: 'value', name: 'value', price_cents: 500, quality: 75, discount_depth: 10, price_anchor: 10 });
    const dearAndDeep = row({ id: 'deal', name: 'deal', price_cents: 6000, quality: 75, discount_depth: 99, price_anchor: 99 });
    const rows = [cheapAndGood, dearAndDeep];

    const byValue = { ...weights, final: { quality: 0, deal: 0, value: 1 } };
    const byDeal = { ...weights, final: { quality: 0, deal: 1, value: 0 } };
    expect(names(sortRows(rows, state({}), byValue, new Map(), 2026))[0]).toBe('value');
    expect(names(sortRows(rows, state({}), byDeal, new Map(), 2026))[0]).toBe('deal');
  });

  it('labels every sort column', () => {
    for (const column of SORT_COLUMNS) expect(SORT_LABELS[column]).toBeTruthy();
  });
});
