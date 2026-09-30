// Required since Astro 5: without this, Starlight finds no `docs` collection, builds only
// 404.html, and STILL EXITS 0 — a silent empty site. `site-check` in the justfile and the
// page-count assertion in the Amplify build spec exist because of this failure mode.
import { defineCollection } from "astro:content";
import { docsLoader } from "@astrojs/starlight/loaders";
import { docsSchema } from "@astrojs/starlight/schema";

export const collections = {
  docs: defineCollection({ loader: docsLoader(), schema: docsSchema() }),
};
