/** Explorer state <-> query string.
 *
 * Short keys and omitted defaults, because the point of this module is that a
 * filtered, re-weighted view can be pasted into a message and still look like
 * a link rather than a payload. It also turns every preset into a plain
 * <a href> that works before the island has run.
 *
 * Every value is validated on the way in: these URLs get hand-edited,
 * truncated by chat apps, and occasionally crafted. Nothing here trusts input.
 *
 * Pure: no DOM, no history, no clock. The island owns pushState/replaceState.
 */

export interface ExploreState {
  q: string;
  price: string;
  plus: string;
  players: string;
  esrb: string;
  platform: string;
  genre: string;
  /** The "well-reviewed" facet. Reads `quality` (100% covered), never
   *  `critic_score` (47%) -- a critic threshold would silently exclude the
   *  1,249 rows Metacritic never scored. */
  minQuality: number;
  minEvidence: string;
  sort: string;
  desc: boolean;
  hasPlus: boolean;
  w: { quality: number; deal: number; value: number };
}

/** The sort column is echoed into markup, so it is an allowlist rather than a
 *  free string. `hours` is here deliberately: playtime is 12.7% covered, which
 *  is enough to sort by and far too little to filter on. */
export const SORT_COLUMNS = [
  'score', 'name', 'price', 'off', 'quality', 'players', 'hours',
] as const;

export const DEFAULTS: ExploreState = {
  q: '', price: '', plus: '', players: '', esrb: '', platform: '', genre: '',
  minQuality: 0, minEvidence: 'low',
  sort: 'score', desc: true, hasPlus: false,
  // -1 is "not set in the URL", replaced on load by meta.weights from
  // index.json. Never seed real weights here -- a second copy of weights.toml
  // is how the site and the crawler come to disagree, and check_invariants.py
  // fails the build for it.
  w: { quality: -1, deal: -1, value: -1 },
};

const KEYS: Record<string, keyof ExploreState> = {
  q: 'q', price: 'price', plus: 'plus', players: 'players', esrb: 'esrb',
  platform: 'platform', genre: 'genre', evidence: 'minEvidence', sort: 'sort',
};

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

export function toSearch(state: ExploreState): string {
  const p = new URLSearchParams();
  for (const [key, field] of Object.entries(KEYS)) {
    const value = state[field];
    if (value !== DEFAULTS[field]) p.set(key, String(value));
  }
  if (state.minQuality !== DEFAULTS.minQuality) p.set('minq', String(state.minQuality));
  if (state.desc !== DEFAULTS.desc) p.set('dir', 'asc');
  if (state.hasPlus !== DEFAULTS.hasPlus) p.set('mine', '1');
  for (const [key, field] of [['wq', 'quality'], ['wd', 'deal'], ['wv', 'value']] as const) {
    const value = state.w[field];
    // Whole percents: a drag emits dozens of values and 0.3333333333 in a URL
    // helps nobody.
    if (value >= 0) p.set(key, String(Math.round(value * 100)));
  }
  return p.toString();
}

export function fromSearch(search: string): ExploreState {
  const p = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const str = (key: string, fallback: string) => p.get(key) ?? fallback;
  const num = (key: string, fallback: number) => {
    const raw = p.get(key);
    if (raw === null) return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  };
  const weight = (key: string) => {
    const raw = p.get(key);
    if (raw === null) return -1;
    const n = Number(raw);
    return Number.isFinite(n) ? clamp(n / 100, 0, 1) : -1;
  };

  const sort = str('sort', DEFAULTS.sort);
  return {
    q: str('q', DEFAULTS.q),
    price: str('price', DEFAULTS.price),
    plus: str('plus', DEFAULTS.plus),
    players: str('players', DEFAULTS.players),
    esrb: str('esrb', DEFAULTS.esrb),
    platform: str('platform', DEFAULTS.platform),
    genre: str('genre', DEFAULTS.genre),
    minQuality: clamp(num('minq', DEFAULTS.minQuality), 0, 100),
    minEvidence: str('evidence', DEFAULTS.minEvidence),
    sort: (SORT_COLUMNS as readonly string[]).includes(sort) ? sort : DEFAULTS.sort,
    desc: p.get('dir') !== 'asc',
    hasPlus: p.get('mine') === '1',
    w: { quality: weight('wq'), deal: weight('wd'), value: weight('wv') },
  };
}
