import { defineConfig } from 'astro/config';

// site/base come from the Actions Pages deploy; locally they are unset.
export default defineConfig({
  site: process.env.PAGES_URL,
  base: process.env.PAGES_BASE,
  build: { format: 'directory' },
});
