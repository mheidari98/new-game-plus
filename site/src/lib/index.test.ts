import { describe, expect, it, vi } from 'vitest';
import { artUrl, esc, esrbLabel, loadIndex, price } from './index';

describe('esc', () => {
  it('escapes the characters real store titles actually contain', () => {
    // 310 of 2,369 live names contain '&', 111 an apostrophe, 5 a double quote.
    expect(esc('"Edna & Harvey" Bundle')).toBe(
      '&quot;Edna &amp; Harvey&quot; Bundle',
    );
    expect(esc("Assassin's Creed")).toBe('Assassin&#39;s Creed');
  });

  it('escapes markup instead of letting the feed inject it', () => {
    expect(esc('<img src=x onerror=alert(1)>')).toBe(
      '&lt;img src=x onerror=alert(1)&gt;',
    );
  });

  it('renders a missing value as empty, not "null"', () => {
    expect(esc(null)).toBe('');
    expect(esc(undefined)).toBe('');
  });
});

describe('esrbLabel', () => {
  it('gives one label for the store\'s two codes for the same rating', () => {
    expect(esrbLabel('ESRB_MATURE')).toBe('Mature 17+');
    expect(esrbLabel('ESRB_MATURE_17')).toBe('Mature 17+');
  });

  it('labels the common ratings', () => {
    expect(esrbLabel('ESRB_TEEN')).toBe('Teen');
    expect(esrbLabel('ESRB_EVERYONE_10')).toBe('Everyone 10+');
  });

  it('falls back to the bare code rather than showing the prefix', () => {
    expect(esrbLabel('ESRB_E_T')).toBe('E T');
  });

  it('stays null when the store rated nothing', () => {
    expect(esrbLabel(null)).toBeNull();
  });
});

describe('loadIndex', () => {
  const el = () => ({ dataset: { src: '/index.json' }, innerHTML: '' }) as unknown as HTMLElement;
  const payload = {
    meta: { count: 1, generated_at: null, weights: {} },
    cols: {
      id: ['X'], name: ['A & B'], price_cents: [100], base_cents: [200],
      discount_pct: [50], is_free: [false], plus_extra: [false],
      plus_classics: [false], local_players: [null], dualsense: [false],
      release_year: [2024], quality: [80], discount_depth: [70],
      price_anchor: [60], genres: [[]], platforms: [[]], esrb: [null],
      psvr2: [null], evidence: [null],
    },
    dicts: {},
  };

  it('decodes what it fetched', async () => {
    vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => payload }));
    expect((await loadIndex(el()))!.rows[0].name).toBe('A & B');
  });

  it('says so instead of leaving "Loading…" up forever', async () => {
    vi.stubGlobal('fetch', async () => { throw new Error('offline'); });
    const app = el();
    expect(await loadIndex(app)).toBeNull();
    expect(app.innerHTML).toContain('Could not load the catalogue');
  });

  it('treats a 404 payload as a failure, not as an empty catalogue', async () => {
    vi.stubGlobal('fetch', async () => ({ ok: false, status: 404 }));
    const app = el();
    expect(await loadIndex(app)).toBeNull();
    expect(app.innerHTML).toContain('404');
  });
});

describe('price', () => {
  it('names zero rather than printing $0.00', () => {
    expect(price(0)).toBe('Free');
    expect(price(2999)).toBe('$29.99');
  });
});

describe('artUrl', () => {
  const ART =
    'https://image.api.playstation.com/vulcan/ap/rnd/202306/1219/1c7b75d8.png';

  it('asks the CDN for the size actually rendered, with BOTH dimensions', () => {
    // Measured against the live CDN: `?w=` alone snaps to a size ladder --
    // w=64, w=80 and w=120 all return the same 6,535 B file. Passing h as well
    // triggers a real resize: w=64&h=64 is 2,373 B. Sending w alone is the
    // difference between a 72px thumbnail costing 6.5 KB and costing 2.4 KB.
    expect(artUrl(ART, 72)).toBe(`${ART}?w=72&h=72`);
    expect(artUrl(ART, 440)).toBe(`${ART}?w=440&h=440`);
  });

  it('is null when the crawl has no art for that game', () => {
    // The caller renders no image at all rather than a broken one.
    expect(artUrl(null, 200)).toBeNull();
    expect(artUrl(undefined, 200)).toBeNull();
  });
});
