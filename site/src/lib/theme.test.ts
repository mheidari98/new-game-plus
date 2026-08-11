import { describe, expect, it } from 'vitest';
import { nextTheme, resolveTheme } from './theme';

describe('resolveTheme', () => {
  it('honours an explicit stored choice over the system preference', () => {
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });

  it('falls back to the system preference when nothing is stored', () => {
    expect(resolveTheme(null, true)).toBe('dark');
    expect(resolveTheme(null, false)).toBe('light');
  });

  it('treats a corrupt stored value as nothing stored', () => {
    // localStorage is user-writable and survives deploys, so a value written
    // by an older version of the site must not wedge the theme.
    expect(resolveTheme('purple', true)).toBe('dark');
    expect(resolveTheme('', false)).toBe('light');
  });

  it('toggles', () => {
    expect(nextTheme('light')).toBe('dark');
    expect(nextTheme('dark')).toBe('light');
  });
});
