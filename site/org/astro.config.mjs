// @ts-check
import { defineConfig } from "astro/config";

// knaif.org — the end-user product site. Light by default (see src/layouts/Base.astro).
export default defineConfig({
  site: "https://knaif.org",
  build: {
    // Amplify serves /skills/foo without a redirect only if the file is at
    // skills/foo/index.html; the default "file" format emits skills/foo.html.
    format: "directory",
  },
});
