/** Column sorting for the explorer.
 *
 * Two rules that are not obvious:
 *
 * * **Ties break on popularity rank.** Prices, discounts and rounded scores
 *   collide constantly across 12,732 rows. Leaning on sort stability would
 *   couple display order to whatever order the filter emitted, and that order
 *   changes as filters change -- rows would appear to shuffle for no reason.
 * * **Nulls sort last in both directions.** A null is "we did not measure
 *   this". Treating it as zero would put every game of unknown length at the
 *   top of "shortest first", which is a confident claim we cannot support.
 *
 * Pure: no DOM, no clock -- the year is a parameter, as in score.ts.
 */
import type { Row } from './index';
import { finalScore, type Weights } from './score';
import type { ExploreState } from './urlstate';

export const SORT_LABELS: Record<string, string> = {
  score: 'Score', name: 'Game', price: 'Price', off: 'Off',
  quality: 'Quality', players: 'Players', hours: 'Hours',
};

/** Built once. Passing a locale and options to localeCompare defeats V8's
 *  cached-collator fast path and resolves a fresh collator per comparison:
 *  measured 590 ms to sort 12,732 names, against 17 ms reusing this. */
const COLLATOR = new Intl.Collator('en', { sensitivity: 'base' });

/** null sorts last regardless of direction: it means "we did not measure
 *  this", so it must never win a ranking. */
const nullsLast = (a: number | null, b: number | null, direction: number): number =>
  a === null && b === null ? 0 : a === null ? 1 : b === null ? -1 : direction * (a - b);

export function sortRows(
  rows: Row[],
  state: ExploreState,
  weights: Weights,
  rankOf: Map<string, number>,
  currentYear: number,
): Row[] {
  const direction = state.desc ? -1 : 1;

  // Scored once per row, not once per comparison: a comparator that calls
  // finalScore does O(n log n) of it -- 310,418 calls to order 12,732 rows.
  const scores = state.sort === 'score'
    ? new Map(rows.map((r) => [r, finalScore(r, weights, { hasPlusExtra: state.hasPlus }, currentYear)]))
    : null;

  const compare = (a: Row, b: Row): number => {
    switch (state.sort) {
      case 'name': return direction * COLLATOR.compare(a.name, b.name);
      case 'price': return direction * (a.price_cents - b.price_cents);
      case 'off': return direction * (a.discount_pct - b.discount_pct);
      case 'quality': return direction * (a.quality - b.quality);
      case 'players': return nullsLast(a.local_players, b.local_players, direction);
      case 'hours': return nullsLast(a.hours_main, b.hours_main, direction);
      default: return direction * (scores!.get(a)! - scores!.get(b)!);
    }
  };

  const rank = (row: Row) => rankOf.get(row.id) ?? Number.MAX_SAFE_INTEGER;
  return [...rows].sort((a, b) => compare(a, b) || rank(a) - rank(b));
}
