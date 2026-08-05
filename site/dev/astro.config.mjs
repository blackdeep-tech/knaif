// @ts-check
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

// knaif.dev — the developer site. Dark by default (Starlight's defaultLocale theme is set
// via customCss + the tokens' data-theme contract; see src/styles/theme.css).
//
// Five tracks, split by AUDIENCE rather than topic: A–D serve someone extending knaif with
// a new skill; E serves someone putting natural language on their own CLI, who may never
// write a knaif skill at all. Plan §5.
export default defineConfig({
  site: "https://knaif.dev",
  integrations: [
    starlight({
      title: "knaif",
      description:
        "Build skills for knaif, or put a natural-language front end on your own CLI.",
      logo: {
        src: "./src/assets/wordmark.svg",
        replacesTitle: true,
      },
      favicon: "/favicon.ico",
      customCss: [
        "knaif-shared/fonts.css",
        "knaif-shared/tokens.css",
        "./src/styles/theme.css",
      ],
      components: {
        // knaif.dev opens dark by default (plan §10). Upstream falls back to the OS
        // preference, which would render a light .dev for anyone whose laptop is in
        // light mode. A `head:` script cannot fix it — Starlight injects its provider
        // after custom head tags. See the file for the one-line diff.
        ThemeProvider: "./src/components/ThemeProvider.astro",
      },
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/blackdeep-tech/knaif",
        },
      ],
      // Starlight >= 0.39 removed `label` alongside `autogenerate`; a group needs an
      // `items` array wrapping the autogenerate config.
      sidebar: [
        { label: "Start here", items: [{ autogenerate: { directory: "start" } }] },
        { label: "The knaif SDK", items: [{ autogenerate: { directory: "sdk" } }] },
        { label: "Author a skill", items: [{ autogenerate: { directory: "author" } }] },
        { label: "Evaluate a skill", items: [{ autogenerate: { directory: "evaluate" } }] },
        { label: "Fine-tuning", items: [{ autogenerate: { directory: "fine-tuning" } }] },
        { label: "Python to native", items: [{ autogenerate: { directory: "native" } }] },
      ],
    }),
  ],
});
