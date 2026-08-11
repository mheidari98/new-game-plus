/** Decode the columnar index.json back into row objects.
 *
 * Columnar with integer dictionaries because at 12k games a row-of-objects
 * layout is 1.67 MB gzipped and blows the budget; this is 440 KB. Decoding is
 * one pass and costs a few ms.
 */
import type { Game, Weights } from './score';

export interface Row extends Game {
  id: string;
  name: string;
  base_cents: number;
  discount_pct: number;
  is_free: boolean;
  genres: string[];
  esrb: string | null;
  local_players: number | null;
  psvr2: string | null;
  dualsense: boolean;
  /** Metascore, or null when nothing matched. Published so the quality
   *  component can be checked against its source by hand. */
  critic_score: number | null;
  /** Best-effort and usually null: HowLongToBeat main-story hours, and
   *  IGDB's split-screen flag and player perspective. */
  hours_main: number | null;
  splitscreen: boolean | null;
  perspective: string | null;
}

export interface Index {
  meta: { count: number; generated_at: string | null; weights: Weights };
  rows: Row[];
}

export function decode(payload: any): Index {
  const { cols, dicts, meta } = payload;
  const n = meta.count;
  const lookup = (field: string, v: any) =>
    v === null || v === undefined ? null : dicts[field][v];

  const rows: Row[] = [];
  for (let i = 0; i < n; i++) {
    rows.push({
      id: cols.id[i],
      name: cols.name[i],
      price_cents: cols.price_cents[i],
      base_cents: cols.base_cents[i],
      discount_pct: cols.discount_pct[i],
      is_free: cols.is_free[i],
      plus_extra: cols.plus_extra[i],
      plus_classics: cols.plus_classics[i],
      local_players: cols.local_players[i],
      dualsense: cols.dualsense[i],
      release_year: cols.release_year[i],
      critic_score: cols.critic_score?.[i] ?? null,
      vs_historical_min: cols.vs_historical_min?.[i] ?? null,
      vs_typical_sale: cols.vs_typical_sale?.[i] ?? null,
      hours_main: cols.hours_main?.[i] ?? null,
      splitscreen: cols.splitscreen?.[i] ?? null,
      perspective: lookup('perspective', cols.perspective?.[i] ?? null),
      quality: cols.quality[i],
      discount_depth: cols.discount_depth[i],
      price_anchor: cols.price_anchor[i],
      genres: (cols.genres[i] ?? []).map((g: number) => dicts.genres[g]),
      platforms: (cols.platforms[i] ?? []).map((p: number) => dicts.platforms[p]),
      esrb: lookup('esrb', cols.esrb[i]),
      psvr2: lookup('psvr2', cols.psvr2[i]),
      evidence: lookup('evidence', cols.evidence[i]) ?? 'none',
    });
  }
  return { meta, rows };
}

export const price = (cents: number) =>
  cents === 0 ? 'Free' : `$${(cents / 100).toFixed(2)}`;

export const storeUrl = (id: string) =>
  `https://store.playstation.com/en-us/product/${id}`;

/** Escape before interpolating store text into HTML. Not optional: of 2,369
 *  live rows, 310 names contain '&', 111 an apostrophe and 5 a double quote
 *  (e.g. '"Edna & Harvey" Bundle'). */
export const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

/** The store publishes both ESRB_MATURE (349 rows) and ESRB_MATURE_17 (126)
 *  for the same rating. Unknown codes -- the store also emits dual ratings like
 *  ESRB_E_T on bundles -- fall through to the raw code, minus its prefix. */
const ESRB: Record<string, string> = {
  ESRB_EVERYONE: 'Everyone',
  ESRB_EVERYONE_10: 'Everyone 10+',
  ESRB_TEEN: 'Teen',
  ESRB_MATURE: 'Mature 17+',
  ESRB_MATURE_17: 'Mature 17+',
  ESRB_RATING_PENDING: 'Rating pending',
};

export const esrbLabel = (esrb: string | null): string | null =>
  esrb ? (ESRB[esrb] ?? esrb.replace('ESRB_', '').replaceAll('_', ' ')) : null;

/** Sony's CDN resizes on request, but `?w=` ALONE SNAPS TO A SIZE LADDER --
 *  measured, `w=64`, `w=80` and `w=120` all return the same 6,535 B file, so
 *  asking for a smaller width buys nothing. Passing `h` as well triggers a real
 *  resize: `w=64&h=64` is 2,373 B, a 64% saving, and returns the exact
 *  dimensions rendered. The CDN also negotiates AVIF off the `Accept` header
 *  every modern browser already sends, for a further 19-35% at no cost to us.
 *
 *  MASTER art is square (1024x1024 on all 22 sampled), so one number is enough.
 *  Null art means the crawl has not reached that game yet, and the caller
 *  renders no image rather than a placeholder. */
export const artUrl = (url: string | null | undefined, size: number) =>
  url ? `${url}?w=${size}&h=${size}` : null;

/** Fetch and decode the payload an island points at. Every island used to
 *  hand-roll this without a catch, so a failed fetch left "Loading…" up
 *  forever. Returns null once the element says so. */
export async function loadIndex(el: HTMLElement): Promise<Index | null> {
  try {
    const res = await fetch(el.dataset.src!);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return decode(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="note">Could not load the catalogue (${esc(
      err instanceof Error ? err.message : err,
    )}). Reload to try again.</p>`;
    return null;
  }
}
