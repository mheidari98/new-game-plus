/** Decode the columnar index.json back into row objects.
 *
 * The payload is columnar with integer dictionaries because at 12k games a
 * row-of-objects layout is 1.67 MB gzipped and blows the budget, while this
 * is 440 KB. Decoding is one pass and costs a few ms.
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
  tier: string | null;
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
      tier: lookup('tier', cols.tier[i]),
      evidence: lookup('evidence', cols.evidence[i]) ?? 'none',
    });
  }
  return { meta, rows };
}

export const price = (cents: number) =>
  cents === 0 ? 'Free' : `$${(cents / 100).toFixed(2)}`;

export const storeUrl = (id: string) =>
  `https://store.playstation.com/en-us/product/${id}`;
