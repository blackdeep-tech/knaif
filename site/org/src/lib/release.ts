// Typed access to the published-release snapshot.
//
// Produced by scripts/release_data.py from the GitHub Releases API, AFTER a release is
// published (RELEASE.md §5). Never derived from the version in Cargo.toml: the bump lands
// before the assets exist, so a derived URL would 404 for as long as that gap lasts.
import release from "../../../data/release.json";
import platformsDoc from "../../../data/site-data.json";

export interface Asset {
  name: string;
  url: string;
  size_bytes: number;
}

export interface Release {
  schema_version: number;
  tag: string;
  version: string;
  published_at: string;
  release_page: string;
  releases_page: string;
  checksums_url: string;
  assets: Record<string, Record<string, Asset>>;
}

export const rel = release as Release;

/** The support matrix from contracts/release/platforms.yaml, carried in site-data.json. */
export interface PlatformArtifact {
  artifact: string;
  kind: string;
  recommended?: boolean;
  notes?: string;
}

export interface Platform {
  id: string;
  name: string;
  arch?: string;
  status: "supported" | "planned";
  requires: string | null;
  known_good?: string[];
  known_bad?: { distro: string; reason: string }[];
  artifacts?: PlatformArtifact[];
  warnings?: { id: string; text: string }[];
  notes?: string;
}

const doc = platformsDoc.platforms as {
  platforms: Platform[];
  gpu: {
    default: { backends: string[]; text: string };
    optional: {
      id: string;
      name: string;
      install_command: string;
      approx_size: string;
      requires: string;
      text: string;
    }[];
  };
  external_tools: { text: string };
  model: { text: string };
};

export const platforms = doc.platforms;
export const gpu = doc.gpu;
export const externalTools = doc.external_tools;
export const modelNote = doc.model;

export const supported = platforms.filter((p) => p.status === "supported");
export const planned = platforms.filter((p) => p.status !== "supported");

export function assetsFor(platformId: string): Record<string, Asset> {
  return rel.assets[platformId] ?? {};
}

/** Prefer the artifact the platform marks `recommended:`, falling back to whatever exists.
 *  Keeping the preference in the contract rather than here means the download button and
 *  the support table cannot disagree about which artifact is the primary one. */
export function primaryAsset(platform: Platform): Asset | undefined {
  const assets = assetsFor(platform.id);
  const preferred = platform.artifacts?.find((a) => a.recommended)?.kind;
  return (preferred && assets[preferred]) || Object.values(assets)[0];
}

export const mb = (bytes: number) => `${(bytes / 1_000_000).toFixed(1)} MB`;

export const publishedOn = new Date(rel.published_at).toLocaleDateString("en-GB", {
  day: "numeric",
  month: "long",
  year: "numeric",
});
