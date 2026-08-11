/** Explorer filtering and facet counting.
 *
 * The rule this module enforces: a filter over a sparse column must not
 * silently delete the rows we have no data for. `hours_main` is known for 301
 * of 2,369 games, so there is no playtime filter at all -- it is a column and
 * a sort. The same reasoning picks `quality` (100%) over `critic_score` (47%)
 * for the well-reviewed gate.
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

const PRICE_MAX: Record<string, number> = {
  free: 0, under5: 500, under10: 1000, under20: 2000, under40: 4000,
};

export function matches(row: Row, state: ExploreState): boolean {
  if (state.q && !row.name.toLowerCase().includes(state.q.toLowerCase())) return false;

  if (state.price === 'sale' && row.discount_pct <= 0) return false;
  const maxPrice = PRICE_MAX[state.price];
  if (maxPrice !== undefined && row.price_cents > maxPrice) return false;

  if (state.plus === 'extra' && !row.plus_extra) return false;
  if (state.plus === 'classics' && !row.plus_classics) return false;
  if (state.plus === 'none' && (row.plus_extra || row.plus_classics)) return false;

  // null means the store did not say, which is not evidence of two players.
  if (state.players && (row.local_players ?? 0) < Number(state.players)) return false;

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
    // The probe state is built once per value, not once per row -- inside the
    // callback it was ~178,000 spreads per render at full catalogue size.
    const probe = { ...base, [facet]: value } as ExploreState;
    let n = 0;
    for (const row of pool) if (matches(row, probe)) n++;
    counts.set(value, n);
  }
  return counts;
}

/** How many rows carry a playtime, so the UI can say so rather than let a
 *  sparse column look like a complete one. */
export const playtimeKnown = (rows: Row[]) =>
  rows.reduce((n, row) => n + (row.hours_main === null ? 0 : 1), 0);
