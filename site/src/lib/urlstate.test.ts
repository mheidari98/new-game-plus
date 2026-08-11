import { describe, expect, it } from 'vitest';
import { DEFAULTS, fromSearch, toSearch, type ExploreState } from './urlstate';

const withState = (over: Partial<ExploreState>): ExploreState => ({ ...DEFAULTS, ...over });

describe('toSearch', () => {
  it('is empty for the default view, so an unfiltered link stays clean', () => {
    expect(toSearch(DEFAULTS)).toBe('');
  });

  it('omits defaults and keeps only what changed', () => {
    expect(toSearch(withState({ players: '2', sort: 'price' }))).toBe('players=2&sort=price');
  });

  it('round-trips every field', () => {
    const state = withState({
      q: 'gang beasts', price: 'under20', plus: 'extra', players: '4', esrb: 'ESRB_EVERYONE',
      platform: 'PS5', genre: 'Action', minQuality: 70,
      minEvidence: 'medium', sort: 'quality', desc: false, hasPlus: true,
      w: { quality: 0.5, deal: 0.3, value: 0.2 },
    });
    expect(fromSearch(toSearch(state))).toEqual(state);
  });

  it('rounds weights to whole percents so a slider drag cannot produce a 17-character number', () => {
    const search = toSearch(withState({ w: { quality: 0.333333, deal: 0.333333, value: 0.333334 } }));
    expect(search).not.toMatch(/\d{4,}/);
  });
});

describe('fromSearch', () => {
  it('returns the defaults for an empty search', () => {
    expect(fromSearch('')).toEqual(DEFAULTS);
    expect(fromSearch('?')).toEqual(DEFAULTS);
  });

  it('ignores unknown keys rather than throwing', () => {
    expect(fromSearch('utm_source=twitter&players=2').players).toBe('2');
  });

  it('falls back to the default when a number is not a number', () => {
    // These URLs get hand-edited and pasted into chat apps that mangle them.
    expect(fromSearch('minq=banana').minQuality).toBe(DEFAULTS.minQuality);
    expect(fromSearch('wq=nope').w.quality).toBe(DEFAULTS.w.quality);
  });

  it('has no hours filter at all', () => {
    // hours_main is known for 301 of 2,369 games. A filter over it silently
    // hides 87% of the catalogue, so it is a column and a sort, never a facet.
    expect('maxHours' in fromSearch('hours=12')).toBe(false);
  });

  it('rejects a sort column that does not exist', () => {
    expect(fromSearch('sort=drop%20table').sort).toBe(DEFAULTS.sort);
  });

  it('clamps weights into 0..1 so a crafted link cannot invert the ranking', () => {
    const state = fromSearch('wq=500&wd=-20');
    expect(state.w.quality).toBeLessThanOrEqual(1);
    expect(state.w.deal).toBeGreaterThanOrEqual(0);
  });

  it('clamps the quality floor into 0..100', () => {
    expect(fromSearch('minq=9999').minQuality).toBe(100);
    expect(fromSearch('minq=-5').minQuality).toBe(0);
  });
});
