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
  // Astro's default compression deletes a line break between text and an inline tag, so
  // "We\n<a>" shipped as "We<a>" — seven glued words across the pages. Prose here is
  // written with wrapped lines, so keep the whitespace; the size cost is negligible.
  compressHTML: false,
  build: {
    // Amplify serves /skills/foo without a redirect only if the file is at
    // skills/foo/index.html; the default "file" format emits skills/foo.html.
    format: "directory",
  },
});
