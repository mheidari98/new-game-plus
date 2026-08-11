/** Explorer filtering, facet counting and coverage reporting.
 *
 * The rule this module exists to enforce: a filter over a sparse column must
 * not silently delete the rows we have no data for. `hours_main` is known for
 * 301 of 2,369 games, so the old "under N hours" filter answered a question
 * about 13% of the catalogue while looking like it answered one about all of
 * it. There is no playtime filter here at all -- playtime is a column and a
 * sort option, which is what a 12.7%-covered field can honestly support.
 *
 * The same reasoning picks `quality` (100%) over `critic_score` (47%) for the
 * well-reviewed gate.
 *
 * Pure: no DOM, no fetch, no clock.
 */
import type { Row } from './index';
import type { ExploreState } from './urlstate';

/** Ordered worst to best, so a floor is an index comparison. */
const EVIDENCE_ORDER = ['none', 'low', 'medium', 'high'];
const evidenceRank = (value: string) => {
  const at = EVIDENCE_ORDER.indexOf(value);
  return at === -1 ? 0 : at;   // an unknown label is treated as no evidence
};

/** Sony's NO_OF_PLAYERS carries a 99 sentinel on two live rows. Anything above
 *  this is not a real couch, it is a missing value wearing a number. The real
 *  distribution is 1, 2, 3, 4, 5, 6, 8, 10, 11 -- 16 leaves generous headroom. */
const MAX_REAL_PLAYERS = 16;
export const playerCount = (raw: number | null): number | null =>
  raw === null || raw > MAX_REAL_PLAYERS ? null : raw;

const PRICE_BOUNDS: Record<string, (cents: number) => boolean> = {
  free: (c) => c === 0,
  under5: (c) => c <= 500,
  under10: (c) => c <= 1000,
  under20: (c) => c <= 2000,
  under40: (c) => c <= 4000,
};

export function matches(row: Row, state: ExploreState): boolean {
  if (state.q && !row.name.toLowerCase().includes(state.q.toLowerCase())) return false;

  if (state.price === 'sale') {
    if (row.discount_pct <= 0) return false;
  } else if (state.price && !(PRICE_BOUNDS[state.price]?.(row.price_cents) ?? true)) {
    return false;
  }

  if (state.plus === 'extra' && !row.plus_extra) return false;
  if (state.plus === 'classics' && !row.plus_classics) return false;
  if (state.plus === 'none' && (row.plus_extra || row.plus_classics)) return false;

  if (state.players) {
    // null means the store did not say. That is not evidence of two players,
    // so it cannot satisfy a couch filter.
    const actual = playerCount(row.local_players);
    if (actual === null || actual < Number(state.players)) return false;
  }

  if (state.esrb && row.esrb !== state.esrb) return false;
  if (state.platform && !row.platforms.includes(state.platform)) return false;
  if (state.genre && !row.genres.includes(state.genre)) return false;

  // No playtime filter by design -- see the module comment.

  if (state.minQuality && row.quality < state.minQuality) return false;

  if (evidenceRank(row.evidence) < evidenceRank(state.minEvidence)) return false;

  return true;
}

export const applyFilters = (rows: Row[], state: ExploreState): Row[] =>
  rows.filter((row) => matches(row, state));

/** How many rows each value of one facet would yield, given every *other*
 *  active filter. The facet's own selection is cleared first: without that,
 *  every alternative inside the facet the user is currently touching reads
 *  zero, which is the dead end faceted search exists to prevent. */
export function facetCounts(
  rows: Row[],
  state: ExploreState,
  facet: keyof ExploreState,
  values: string[],
): Map<string, number> {
  const base = { ...state, [facet]: '' } as ExploreState;
  const pool = applyFilters(rows, base);
  const counts = new Map<string, number>();
  for (const value of values) {
    counts.set(value, pool.filter((row) => matches(row, { ...base, [facet]: value })).length);
  }
  return counts;
}

/** What share of the rows actually carry a field, so the UI can say so rather
 *  than let a sparse column look like a complete one. */
export function coverage(
  rows: Row[],
  field: 'hours_main' | 'local_players' | 'splitscreen',
): { known: number; total: number } {
  return {
    known: rows.reduce((n, row) => n + (row[field] === null ? 0 : 1), 0),
    total: rows.length,
  };
}
