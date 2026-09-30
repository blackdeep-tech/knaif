// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

// knaif.org — the end-user product site. Light by default (see src/layouts/Base.astro).
export default defineConfig({
  site: "https://knaif.org",
  // knaif.dev gets a sitemap for free because Starlight bundles this integration; a plain
  // Astro app does not, so .org shipped without one and /sitemap-index.xml 404'd in
  // production. `site:` above is what makes the emitted URLs apex-canonical.
  integrations: [sitemap()],
  build: {
    // Amplify serves /skills/foo without a redirect only if the file is at
    // skills/foo/index.html; the default "file" format emits skills/foo.html.
    format: "directory",
  },
});
