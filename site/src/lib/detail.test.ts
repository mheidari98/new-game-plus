import { describe, expect, it } from 'vitest';
import { detailHtml, gameUrl, PRERENDERED } from './detail';
import type { Row } from './index';

const game = (over: Partial<Row> = {}): Row => ({
  id: 'UP9000-PPSA26344_00-GHOST2SHIP000000',
  name: 'A Game',
  price_cents: 2999,
  base_cents: 5999,
  discount_pct: 50,
  is_free: false,
  plus_extra: false,
  plus_classics: false,
  platforms: ['PS5'],
  genres: ['Action'],
  esrb: 'M',
  local_players: null,
  psvr2: null,
  dualsense: false,
  tier: 'premium',
  release_year: 2024,
  critic_score: null,
  hours_main: null,
  splitscreen: null,
  perspective: null,
  vs_historical_min: null,
  vs_typical_sale: null,
  quality: 80,
  discount_depth: 70,
  price_anchor: 60,
  evidence: 'high',
  ...over,
});

describe('gameUrl', () => {
  it('links popular games at their prerendered path', () => {
    expect(gameUrl('/ngp/', 'X', 0)).toBe('/ngp/game/X/');
  });

  it('sends the long tail to the client-rendered page', () => {
    // Prerendering 12,000 pages risks the 10-minute Pages deployment
    // timeout, and the tail needs no data the browser has not already loaded.
    expect(gameUrl('/ngp/', 'X', PRERENDERED)).toBe('/ngp/game/?id=X');
  });
});

describe('detailHtml', () => {
  it('escapes the name rather than trusting the store feed', () => {
    const html = detailHtml(game({ name: '<script>alert(1)</script>' }), 2026);
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
  });

  it('omits a fact it has no value for', () => {
    expect(detailHtml(game({ hours_main: null }), 2026)).not.toContain('Main story');
  });

  it('says there is no price history rather than implying a floor', () => {
    // Our history starts when the project did; a blank here would read as
    // "never been cheaper".
    const html = detailHtml(game(), 2026);
    expect(html).toContain('no usable price history yet');
  });

  it('shows the history comparison once it exists', () => {
    const html = detailHtml(game({ vs_historical_min: 100 }), 2026);
    expect(html).toContain('100 / 100');
  });

  it('shows cost per hour only when both halves are known', () => {
    expect(detailHtml(game({ hours_main: 20 }), 2026)).toContain('Cost per hour');
    expect(detailHtml(game(), 2026)).not.toContain('Cost per hour');
  });

  it('distinguishes "no split screen" from "nobody said"', () => {
    expect(detailHtml(game({ splitscreen: false }), 2026)).toContain('Split screen');
    expect(detailHtml(game({ splitscreen: null }), 2026)).not.toContain('Split screen');
  });

  it('calls out couch co-op only at two or more players', () => {
    expect(detailHtml(game({ local_players: 4 }), 2026)).toContain('couch co-op');
    expect(detailHtml(game({ local_players: 1 }), 2026)).not.toContain('couch co-op');
  });

  it('marks a catalogue game as already included', () => {
    expect(detailHtml(game({ plus_extra: true }), 2026)).toContain('PS+ Extra');
  });

  it('mentions the age discount only for an older game', () => {
    expect(detailHtml(game({ release_year: 2014 }), 2026)).toContain('12-year-old');
    expect(detailHtml(game({ release_year: 2025 }), 2026)).not.toContain('year-old');
  });

  it('is pure - same inputs give the same output', () => {
    expect(detailHtml(game(), 2026)).toBe(detailHtml(game(), 2026));
  });
});
