/**
 * Ranking, computed in the browser.
 *
 * The crawler publishes score *components*; this combines them. That is what
 * makes the "I have PS+ Extra" toggle and the weight sliders real rather than
 * cosmetic, and it means the ranking can be checked by hand.
 *
 * Weights come from index.json (meta.weights), which is a verbatim copy of
 * crawler/weights.toml. Never hardcode them here -- one copy at runtime is
 * what stops the site and crawler drifting.
 *
 * Pure: no fetch, no DOM, no Date.now().
 */

export interface Weights {
  final: { quality: number; deal: number; value: number };
  deal: { discount_depth: number; price_anchor: number };
  adjust: {
    psplus_extra: number;
    psplus_classics: number;
    age_full_credit_years: number;
    age_min_multiplier: number;
    ps4_only: number;
    thin_evidence: number;
    no_evidence: number;
  };
}

export interface Game {
  quality: number;
  discount_depth: number;
  price_anchor: number;
  price_cents: number;
  plus_extra: boolean;
  plus_classics: boolean;
  platforms: string[];
  release_year: number | null;
  evidence: 'high' | 'medium' | 'low' | 'none' | string;
}

export interface Prefs {
  /** Owning Extra makes a catalogue game much less worth buying. */
  hasPlusExtra?: boolean;
}

const clamp = (x: number) => Math.max(0, Math.min(100, x));

/** Enjoyment per dollar. Quality is squared because a 90 is much more than
 *  1.4x as worth playing as a 65; +4 keeps sub-$1 items sane. */
function valueScore(game: Game): number {
  const q = game.quality / 100;
  return clamp((q * q * 60 * 100) / (game.price_cents / 100 + 4));
}

export function finalScore(
  game: Game,
  weights: Weights,
  prefs: Prefs,
  currentYear: number,
): number {
  const deal =
    weights.deal.discount_depth * game.discount_depth +
    weights.deal.price_anchor * game.price_anchor;

  const base =
    weights.final.quality * game.quality +
    weights.final.deal * deal +
    weights.final.value * valueScore(game);

  const a = weights.adjust;
  let multiplier = 1;

  if (prefs.hasPlusExtra) {
    // You can already play it, so paying is only worth it to keep it after
    // it rotates out of the catalogue.
    if (game.plus_extra) multiplier *= a.psplus_extra;
    else if (game.plus_classics) multiplier *= a.psplus_classics;
  }

  if (game.release_year) {
    const age = currentYear - game.release_year;
    if (age > a.age_full_credit_years) {
      // A big percentage off an eight-year-old game means less.
      const past = Math.min(1, (age - a.age_full_credit_years) / 7);
      multiplier *= 1 - (1 - a.age_min_multiplier) * past;
    }
  }

  if (game.platforms?.length && !game.platforms.includes('PS5')) {
    multiplier *= a.ps4_only;
  }

  if (game.evidence === 'none') multiplier *= a.no_evidence;
  else if (game.evidence === 'low') multiplier *= a.thin_evidence;

  return clamp(base * multiplier);
}

export function rank<T extends Game>(
  games: T[],
  weights: Weights,
  prefs: Prefs,
  currentYear: number,
): T[] {
  return [...games].sort(
    (a, b) =>
      finalScore(b, weights, prefs, currentYear) -
      finalScore(a, weights, prefs, currentYear),
  );
}
