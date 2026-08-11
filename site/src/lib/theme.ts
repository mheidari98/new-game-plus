/** Theme preference resolution. Pure: the caller reads localStorage and the
 *  media query and passes what it found, so this stays testable. */

export type Theme = 'light' | 'dark';

/** A stored value that is not exactly 'light' or 'dark' is treated as absent.
 *  localStorage survives deploys, so a value written by an older version of
 *  the site must never wedge the theme at something unrenderable. */
export const resolveTheme = (stored: string | null, prefersDark: boolean): Theme =>
  stored === 'light' || stored === 'dark' ? stored : prefersDark ? 'dark' : 'light';

export const nextTheme = (current: Theme): Theme =>
  current === 'dark' ? 'light' : 'dark';
