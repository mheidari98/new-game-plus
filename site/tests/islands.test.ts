import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/** Payload strings written into an island's HTML must go through esc(). Of
 *  2,369 live rows, 310 names contain '&', 111 an apostrophe and 5 a double
 *  quote -- '"Edna & Harvey" Bundle' used to terminate the remove button's
 *  aria-label. The .astro frontmatter escapes itself; the <script> does not.
 *  Lives outside src/pages/ because Astro would build a .ts file there as a
 *  route. */
describe.each(['deals.astro', 'plus.astro'])('%s island', (file) => {
  it('escapes every payload string it interpolates', () => {
    const src = readFileSync(new URL(`../src/pages/${file}`, import.meta.url), 'utf8');
    const island = src.slice(src.indexOf('<script>'));
    const raw = [...island.matchAll(/\$\{[^}]*?\b[gr]\.(?:name|id|genres|psvr2)\b[^}]*\}/g)]
      .map((m) => m[0])
      .filter((expr) => !expr.includes('esc('));
    expect(raw).toEqual([]);
  });
});
