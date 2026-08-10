/**
 * "Is PlayStation Plus worth it for you", from your own list of games.
 *
 * PlatPrices shipped this idea first, with region and tier selection. The
 * difference here is not a longer feature list: this runs entirely in your
 * browser against a list kept in localStorage, with no account, and the
 * arithmetic is one short file anyone can read.
 *
 * Pure: no fetch, no DOM, no Date.now().
 */

export type TierId = 'essential' | 'extra' | 'premium';

export interface Tier {
  id: TierId;
  label: string;
  /** US list price for 12 months, in cents. Editable in the UI: plenty of
   *  people buy discounted cards, and a list price would quietly go stale. */
  yearlyCents: number;
  note: string;
}

export const TIERS: Tier[] = [
  {
    id: 'essential',
    label: 'Essential',
    yearlyCents: 7999,
    note: 'Online play and the monthly games. No back catalogue, so nothing on your list is covered.',
  },
  {
    id: 'extra',
    label: 'Extra',
    yearlyCents: 13499,
    note: 'Adds the Game Catalog — the several hundred PS4 and PS5 games marked "PS+ Extra" here.',
  },
  {
    id: 'premium',
    label: 'Premium',
    yearlyCents: 15999,
    note: 'Adds the Classics Catalog on top of Extra.',
  },
];

export interface Pick {
  id: string;
  name: string;
  /** Today's price. The honest counterfactual is what you would pay instead
   *  of subscribing, not the list price you were never going to pay. */
  price_cents: number;
  plus_extra: boolean;
  plus_classics: boolean;
}

export interface Verdict {
  covered: Pick[];
  uncovered: Pick[];
  /** What the covered games would cost you today, buying them outright. */
  coveredCents: number;
  costCents: number;
  /** Positive means the subscription pays for itself against this list. */
  netCents: number;
  worthIt: boolean;
}

export function coveredBy(tier: TierId, game: Pick): boolean {
  // Essential's monthly games rotate and are not a catalogue, so no game on a
  // wishlist can be counted as covered by it.
  if (tier === 'essential') return false;
  if (tier === 'extra') return game.plus_extra;
  return game.plus_extra || game.plus_classics;
}

export function evaluate(picks: Pick[], tier: Tier): Verdict {
  const covered: Pick[] = [];
  const uncovered: Pick[] = [];
  for (const game of picks) {
    (coveredBy(tier.id, game) ? covered : uncovered).push(game);
  }
  // A free catalogue game contributes nothing, which needs no special case:
  // its price is zero.
  const coveredCents = covered.reduce((n, g) => n + Math.max(0, g.price_cents), 0);
  return {
    covered,
    uncovered,
    coveredCents,
    costCents: tier.yearlyCents,
    netCents: coveredCents - tier.yearlyCents,
    worthIt: coveredCents > tier.yearlyCents,
  };
}
