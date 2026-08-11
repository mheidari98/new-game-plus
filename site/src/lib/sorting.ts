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
import { playerCount } from './filters';
import { finalScore, type Weights } from './score';
import type { ExploreState } from './urlstate';

export const SORT_LABELS: Record<string, string> = {
  score: 'Score', name: 'Game', price: 'Price', off: 'Off',
  quality: 'Quality', players: 'Players', hours: 'Hours',
};

/** null sorts last regardless of direction, so it is separated from the
 *  numeric comparison rather than folded into it. */
const nullsLast = (a: number | null, b: number | null, compare: () => number): number => {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return compare();
};

export function sortRows(
  rows: Row[],
  state: ExploreState,
  weights: Weights,
  rankOf: Map<string, number>,
  currentYear: number,
): Row[] {
  const prefs = { hasPlusExtra: state.hasPlus };
  const direction = state.desc ? -1 : 1;

  const compare = (a: Row, b: Row): number => {
    switch (state.sort) {
      case 'name':
        return direction * a.name.localeCompare(b.name, 'en', { sensitivity: 'base' });
      case 'price':
        return direction * (a.price_cents - b.price_cents);
      case 'off':
        return direction * (a.discount_pct - b.discount_pct);
      case 'quality':
        return direction * (a.quality - b.quality);
      case 'players': {
        const [x, y] = [playerCount(a.local_players), playerCount(b.local_players)];
        return nullsLast(x, y, () => direction * ((x as number) - (y as number)));
      }
      case 'hours':
        return nullsLast(a.hours_main, b.hours_main,
          () => direction * ((a.hours_main as number) - (b.hours_main as number)));
      default:
        return direction * (finalScore(a, weights, prefs, currentYear)
                          - finalScore(b, weights, prefs, currentYear));
    }
  };

  const rank = (row: Row) => rankOf.get(row.id) ?? Number.MAX_SAFE_INTEGER;
  return [...rows].sort((a, b) => compare(a, b) || rank(a) - rank(b));
}
