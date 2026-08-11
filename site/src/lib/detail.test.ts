import { describe, expect, it } from 'vitest';
import { detailHtml, gameUrl, plusVerdict, PRERENDERED } from './detail';
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

describe('plusVerdict', () => {
  it('tells someone not to buy a game already in the Extra catalogue', () => {
    const out = plusVerdict(game({ plus_extra: true, price_cents: 5999 }), 13499);
    expect(out).toMatch(/already/i);
    // It must not pitch a subscription at someone who is asking about a game
    // the subscription already covers.
    expect(out).not.toMatch(/costs? about/i);
  });

  it('says how many games like this one match a year of Extra', () => {
    const out = plusVerdict(game({ plus_extra: false, price_cents: 6999 }), 13499);
    expect(out).toMatch(/\$134\.99/);
    expect(out).toMatch(/\b2 games\b/);
  });

  it('makes no subscription argument for a free game', () => {
    // A free-to-play game gives a subscription no credit; an argument built on
    // it would be a pitch dressed as arithmetic.
    expect(plusVerdict(game({ price_cents: 0, is_free: true }), 13499)).toBe('');
  });

  it('says one game, not 1 games', () => {
    expect(plusVerdict(game({ price_cents: 13499 }), 13499)).toMatch(/\b1 game\b/);
  });
});

describe('price history honesty', () => {
  it('says we lack observations, not that there were no discounts', () => {
    // vs_historical_min is null on observation COUNT (< 4), not on absence of
    // discounts, so "no discounts yet" would be a claim we cannot support.
    const html = detailHtml(game({ vs_historical_min: null }), 2026);
    expect(html).toMatch(/not enough recorded prices/i);
    expect(html).not.toMatch(/no usable price history/i);
  });
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
    // "never been cheaper". The wording names the observation count rather
    // than claiming there were no discounts -- see plusVerdict's neighbour test.
    const html = detailHtml(game(), 2026);
    expect(html).toContain('not enough recorded prices yet to say');
  });

  it('says so for a row built without the history fields at all', () => {
    // The Game interface types these as optional; only decode() coalesces them
    // to null, so an === null check renders "NaN / 100" here.
    const html = detailHtml(game({ vs_historical_min: undefined, vs_typical_sale: undefined }), 2026);
    expect(html).not.toContain('NaN');
    expect(html).toContain('not enough recorded prices yet to say');
  });

  it('spells the ESRB rating the same way the listing pages do', () => {
    expect(detailHtml(game({ esrb: 'ESRB_MATURE_17' }), 2026)).toContain('Mature 17+');
    expect(detailHtml(game({ esrb: 'ESRB_MATURE_17' }), 2026)).not.toContain('ESRB_');
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

describe('cover art on the game page', () => {
  const ART =
    'https://image.api.playstation.com/vulcan/ap/rnd/202306/1219/1c7b75d8.png';

  it('renders a sized image when the build supplies one', () => {
    const html = detailHtml(game(), 2026, ART);
    // Both dimensions, and the & is entity-escaped because the URL is
    // interpolated into an attribute. `?w=` alone would snap to the CDN's size
    // ladder and ship a larger file than the one rendered -- see artUrl.
    expect(html).toContain(`src="${ART}?w=440&amp;h=440"`);
    // width/height reserve the box so the text below does not jump.
    expect(html).toMatch(/width="440"\s+height="440"/);
  });

  it('renders no image at all when art is absent', () => {
    // The long-tail page has no art.json, so this is its normal state.
    expect(detailHtml(game(), 2026, null)).not.toContain('<img');
    expect(detailHtml(game(), 2026)).not.toContain('<img');
  });

  it('escapes the art URL like every other interpolated value', () => {
    const html = detailHtml(game(), 2026, 'https://img/x.png?a=1&b=2');
    expect(html).toContain('&amp;b=2');
  });
});
