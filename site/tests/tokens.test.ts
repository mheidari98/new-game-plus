import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/** The tokens file is the only place a colour is defined, so these are the two
 *  things a reviewer cannot check by eye: that the light and dark palettes
 *  define the same names, and that the pairs actually used together meet AA.
 *  Lives outside src/ because it reads a file as text rather than importing it. */
const css = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8');

/** Pull one `selector { ... }` block's custom properties into a map. */
function block(startsWith: string): Record<string, string> {
  const at = css.indexOf(startsWith);
  if (at === -1) throw new Error(`no block starting "${startsWith}"`);
  const open = css.indexOf('{', at);
  const body = css.slice(open + 1, css.indexOf('}', open));
  const out: Record<string, string> = {};
  for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    out[name] = value.trim();
  }
  return out;
}

const srgb = (channel: number) =>
  channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;

/** WCAG relative luminance. Hex only -- the tokens are all hex by rule. */
function luminance(hex: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) throw new Error(`not a 6-digit hex colour: ${hex}`);
  const n = parseInt(m[1], 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => srgb(c / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const light = block(':root {');
const darkMedia = block(':root:not([data-theme=light])');
const darkExplicit = block(':root[data-theme=dark]');

describe('tokens', () => {
  it('defines every light colour token in both dark blocks', () => {
    const names = Object.keys(light).filter((n) =>
      /^--(bg|surface|line|fg|muted|accent|on-accent|good|warn)/.test(n));
    expect(names.length).toBeGreaterThan(5);
    expect(names.filter((n) => !(n in darkMedia))).toEqual([]);
    expect(names.filter((n) => !(n in darkExplicit))).toEqual([]);
  });

  it('keeps the two dark blocks identical, so the toggle cannot drift from the media query', () => {
    expect(darkExplicit).toEqual(darkMedia);
  });

  // 4.5:1 is AA for body text; 3:1 is AA for large text and UI boundaries.
  const pairs: [string, string, number][] = [
    ['--fg', '--bg', 4.5],
    ['--fg', '--surface', 4.5],
    ['--muted', '--bg', 4.5],
    ['--muted', '--surface', 4.5],
    ['--accent', '--bg', 4.5],
    ['--accent', '--surface', 4.5],
    ['--good', '--bg', 4.5],
    ['--warn', '--bg', 4.5],
    ['--on-accent', '--accent', 4.5],
    ['--line', '--bg', 1.2],
  ];

  describe.each([['light', light], ['dark', darkMedia]] as const)('%s theme', (_name, theme) => {
    it.each(pairs)('%s on %s meets %s:1', (fg, bg, min) => {
      expect(contrast(theme[fg], theme[bg])).toBeGreaterThanOrEqual(min);
    });
  });
});
