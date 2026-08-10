import { defineConfig } from 'astro/config';

// Astro does not add a trailing slash to `base`, so `${BASE_URL}index.json`
// would render as "/new-game-plusindex.json" and 404 every link on Pages.
// Normalise here rather than depending on how the env var was written.
const base = process.env.PAGES_BASE
  ? process.env.PAGES_BASE.replace(/\/?$/, '/')
  : undefined;

export default defineConfig({
  site: process.env.PAGES_URL,
  base,
  build: { format: 'directory' },
});
