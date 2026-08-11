/**
 * One game's page body, as a string of HTML.
 *
 * A string rather than an Astro component because it is rendered twice --
 * prerendered at build time for the popular games, in the browser for the long
 * tail -- and two implementations of one page would drift.
 *
 * Pure: no fetch, no DOM, no Date.now(). The current year is a parameter.
 */
import type { Row } from './index';
import { artUrl, esc, esrbLabel, price, storeUrl } from './index';

export { esc };

/** How many games get a prerendered page. Covers the whole deals-plus-free set
 *  (~2,993 rows), which is everything the site links to directly; the tail
 *  renders in the browser from data it already has.
 *
 *  Build time is not the constraint -- 2,005 pages took 1.4 s locally -- the
 *  10-minute Pages *deployment* timeout is, and 3,000 files is far inside it.
 *  A 12,000-page deploy is still unmeasured, hence not simply "all of them".
 */
export const PRERENDERED = 3000;

/** Index order is popularity order -- the store's default sort is sales30 --
 *  so a row's position decides whether it has a prerendered page. */
export const gameUrl = (base: string, id: string, rank: number) =>
  rank < PRERENDERED ? `${base}game/${id}/` : `${base}game/?id=${encodeURIComponent(id)}`;

const row = (label: string, value: string | null) =>
  value ? `<div class="fact"><dt>${esc(label)}</dt><dd>${value}</dd></div>` : '';

/** US list price for 12 months of Extra, mirroring TIERS in plusmath.ts. One
 *  literal rather than an import, because plusmath.ts models the whole tier
 *  picker and this page only ever asks about Extra. */
const PLUS_EXTRA_YEARLY_CENTS = 13499;

/** "Should I buy this, or subscribe?" answered where the decision is made.
 *
 *  Uses today's price, because the honest counterfactual is what you would pay
 *  instead of subscribing -- not a list price you were never going to pay. A
 *  free game returns nothing: it gives a subscription no credit, and an
 *  argument built on it would be a pitch rather than arithmetic.
 */
export function plusVerdict(game: Row, yearlyCents: number): string {
  if (game.price_cents === 0) return '';
  if (game.plus_extra) {
    return 'You can already play this with PS Plus Extra — it is in the catalogue today. '
      + 'Buying it only makes sense if you want to keep it after it rotates out.';
  }
  const games = Math.ceil(yearlyCents / game.price_cents);
  return `At ${price(game.price_cents)}, ${games} game${games === 1 ? '' : 's'} like this `
    + `costs about what a year of PS Plus Extra does (${price(yearlyCents)}).`;
}

const EVIDENCE: Record<string, string> = {
  high: 'a critic score and a lot of player ratings',
  medium: 'one source, or player ratings alone',
  low: 'thin - few ratings and no critic score',
  none: 'nothing at all, so the score is not meaningful',
};

/** `art` is a parameter because it does not travel in index.json: the build
 *  reads it from src/art.json and the browser-rendered tail has none, so the
 *  long-tail page passes null and simply shows no image. */
export function detailHtml(
  game: Row,
  currentYear: number,
  art: string | null = null,
): string {
  const off = game.discount_pct > 0;
  const players = game.local_players;
  const esrb = esrbLabel(game.esrb);

  return `
    ${art ? `<img class="cover" src="${esc(artUrl(art, 440))}" alt=""
       width="440" height="440" loading="eager" decoding="async" />` : ''}
    <h1>${esc(game.name)}</h1>
    <p class="price">
      ${game.price_cents === 0
        ? '<span class="free">Free</span>'
        : `<strong>${price(game.price_cents)}</strong>` +
          (off ? ` <s>${price(game.base_cents)}</s> <span class="off">−${game.discount_pct}%</span>` : '')}
      ${game.plus_extra ? '<span class="tag in">included with PS+ Extra</span>' : ''}
      ${game.plus_classics ? '<span class="tag in">included with PS+ Premium Classics</span>' : ''}
    </p>

    <dl class="facts">
      ${row('Platforms', game.platforms.length ? esc(game.platforms.join(', ')) : null)}
      ${row('Released', game.release_year ? String(game.release_year) : null)}
      ${row('Genres', game.genres.length ? esc(game.genres.join(', ')) : null)}
      ${row('Rated', esrb && esc(esrb))}
      ${row('Players on one console', players ? `${players}${players >= 2 ? ' — couch co-op' : ''}` : null)}
      ${row('Split screen', game.splitscreen === null ? null : game.splitscreen ? 'yes' : 'no')}
      ${row('Perspective', game.perspective ? esc(game.perspective) : null)}
      ${row('Main story', game.hours_main ? `about ${game.hours_main} hours` : null)}
      ${row('Cost per hour', game.hours_main && game.price_cents
        ? price(Math.round(game.price_cents / game.hours_main)) : null)}
      ${row('DualSense haptics', game.dualsense ? 'yes' : null)}
      ${row('PS VR2', game.psvr2 ? esc(game.psvr2) : null)}
    </dl>

    ${plusVerdict(game, PLUS_EXTRA_YEARLY_CENTS)
      ? `<p class="note">${plusVerdict(game, PLUS_EXTRA_YEARLY_CENTS)}</p>` : ''}

    <h2>Why it ranks where it does</h2>
    <dl class="facts">
      ${row('Quality', `${Math.round(game.quality)} / 100`)}
      ${row('Metacritic', game.critic_score ? String(game.critic_score) : null)}
      ${row('Evidence behind that', esc(EVIDENCE[game.evidence] ?? game.evidence))}
      ${row('Discount depth', `${Math.round(game.discount_depth)} / 100`)}
      ${row('Money saved', `${Math.round(game.price_anchor)} / 100`)}
      ${row('Against its lowest recorded price', game.vs_historical_min == null
        ? 'not enough recorded prices yet to say' : `${Math.round(game.vs_historical_min)} / 100`)}
      ${row('Against its typical sale price', game.vs_typical_sale == null
        ? 'not enough recorded prices yet to say' : `${Math.round(game.vs_typical_sale)} / 100`)}
    </dl>
    <p class="note">
      These are the published components. The final ranking is computed in your
      browser from them and the weights in the same file, so you can check it.
      ${game.release_year && currentYear - game.release_year > 3
        ? `A discount on a ${currentYear - game.release_year}-year-old game counts for a little less.`
        : ''}
    </p>

    <p><a class="buy" href="${storeUrl(game.id)}">View on the PlayStation Store →</a></p>`;
}
