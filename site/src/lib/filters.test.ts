import { describe, expect, it } from 'vitest';
import { applyFilters, facetCounts, matches, playtimeKnown } from './filters';
import { DEFAULTS, type ExploreState } from './urlstate';
import type { Row } from './index';

const row = (over: Partial<Row>): Row => ({
  id: over.name ?? 'UP0000-X', name: 'Test Game', price_cents: 1999, base_cents: 3999,
  discount_pct: 50, is_free: false, plus_extra: false, plus_classics: false,
  local_players: 1, dualsense: false, release_year: 2022, critic_score: null,
  vs_historical_min: null, vs_typical_sale: null, hours_main: null,
  splitscreen: null, quality: 70, discount_depth: 50,
  price_anchor: 50, genres: ['Action'], platforms: ['PS5'], esrb: 'ESRB_TEEN',
  psvr2: null, evidence: 'medium', ...over,
});

const state = (over: Partial<ExploreState>): ExploreState => ({ ...DEFAULTS, ...over });
const names = (rows: Row[]) => rows.map((r) => r.name);

describe('the sparse-column policy', () => {
  const known = row({ name: 'Known', hours_main: 8 });
  const unknown = row({ name: 'Unknown', hours_main: null });

  it('never filters on playtime at all', () => {
    // hours_main is known for 301 of 2,369 games. A filter over it answers a
    // question about 13% of the catalogue while looking like it answers one
    // about all of it, so there is no such filter -- only a column and a sort.
    expect(names(applyFilters([known, unknown], state({})))).toEqual(['Known', 'Unknown']);
  });

  it('reports coverage so the UI can print it', () => {
    expect(playtimeKnown([known, unknown, unknown])).toBe(1);
  });
});

describe('quality gate', () => {
  it('filters on quality, which every row has', () => {
    const good = row({ name: 'good', quality: 82 });
    const poor = row({ name: 'poor', quality: 41 });
    expect(names(applyFilters([good, poor], state({ minQuality: 70 })))).toEqual(['good']);
  });

  it('does not consult critic_score, which only 47% of rows have', () => {
    // A "Metacritic >= 70" gate would silently drop the 1,249 unscored rows.
    const unscored = row({ name: 'unscored', quality: 82, critic_score: null });
    expect(applyFilters([unscored], state({ minQuality: 70 })).length).toBe(1);
  });
});

describe('matches', () => {
  it('treats an unknown player count as not couch co-op', () => {
    // null means the store did not say, which is not the same as one player,
    // but it is also not evidence of two.
    expect(matches(row({ local_players: null }), state({ players: '2' }))).toBe(false);
    expect(matches(row({ local_players: 4 }), state({ players: '2' }))).toBe(true);
  });

  it('reads the players facet as "at least"', () => {
    expect(matches(row({ local_players: 4 }), state({ players: '4' }))).toBe(true);
    expect(matches(row({ local_players: 2 }), state({ players: '4' }))).toBe(false);
  });

  it('matches the search case-insensitively', () => {
    expect(matches(row({ name: 'Gang Beasts' }), state({ q: 'gang' }))).toBe(true);
    expect(matches(row({ name: 'Gang Beasts' }), state({ q: 'GANG' }))).toBe(true);
  });

  it('filters free games on price, not on the is_free flag alone', () => {
    expect(matches(row({ price_cents: 0, is_free: false }), state({ price: 'free' }))).toBe(true);
  });

  it('applies the evidence floor', () => {
    expect(matches(row({ evidence: 'none' }), state({ minEvidence: 'low' }))).toBe(false);
    expect(matches(row({ evidence: 'none' }), state({ minEvidence: 'none' }))).toBe(true);
    expect(matches(row({ evidence: 'high' }), state({ minEvidence: 'low' }))).toBe(true);
  });

  it('reads the PS+ facet three ways', () => {
    const inExtra = row({ plus_extra: true });
    const outside = row({ plus_extra: false, plus_classics: false });
    expect(matches(inExtra, state({ plus: 'extra' }))).toBe(true);
    expect(matches(outside, state({ plus: 'extra' }))).toBe(false);
    expect(matches(outside, state({ plus: 'none' }))).toBe(true);
    expect(matches(inExtra, state({ plus: 'none' }))).toBe(false);
  });
});

describe('facetCounts', () => {
  const rows = [
    row({ name: 'a', platforms: ['PS5'], genres: ['Action'] }),
    row({ name: 'b', platforms: ['PS5'], genres: ['Puzzle'] }),
    row({ name: 'c', platforms: ['PS4'], genres: ['Action'] }),
  ];

  it('counts against the other active filters', () => {
    const counts = facetCounts(rows, state({ genre: 'Action' }), 'platform', ['PS5', 'PS4']);
    expect(counts.get('PS5')).toBe(1);
    expect(counts.get('PS4')).toBe(1);
  });

  it("excludes the facet's own selection, so alternatives are never all zero", () => {
    // Without the exclusion, asking "how many PS4 games?" while PS5 is
    // selected answers 0 for every alternative and the user hits a dead end.
    const counts = facetCounts(rows, state({ platform: 'PS5' }), 'platform', ['PS5', 'PS4']);
    expect(counts.get('PS4')).toBe(1);
  });
});
