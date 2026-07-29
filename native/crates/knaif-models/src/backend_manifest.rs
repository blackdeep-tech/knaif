//! The language-neutral backend manifest (`contracts/backends/backend-manifest.yaml`).
//!
//! Records the opt-in loadable GPU backend payloads (today: CUDA) that `knaif backend install`
//! fetches into the directory the runtime scans. Read by [`BackendStore`](crate::BackendStore).
//!
//! Deliberately **stricter than the model manifest**. A model is one platform-independent GGUF whose
//! store keys on its own filename, so a knaif upgrade with an unchanged recommendation re-downloads
//! nothing. A backend payload is the opposite: it is many files, it is per-platform, its `ggml` lib
//! is ABI-coupled to the exe that loads it, and an older payload under a newer binary must be
//! **refused** rather than tolerated. [`BackendManifest::knaif_version`] is that binding.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;

use crate::manifest::is_real;

/// One file in a payload: what to fetch, what it must hash to, and where it lands.
#[derive(Debug, Clone, Deserialize)]
pub struct BackendFile {
    /// Filename in the backends directory. Also the staged name — payload files are flat, because
    /// that is what `load_backends_from_path` scans.
    pub name: String,
    /// Download URL. `None`/empty/`"TODO"` means not yet published (install refuses).
    #[serde(default)]
    pub url: Option<String>,
    /// Expected SHA-256 (hex). `None`/empty/`"TODO"` means unverifiable (install refuses — unlike
    /// the model store, which tolerates an unchecksummed pull).
    #[serde(default)]
    pub sha256: Option<String>,
    #[serde(default)]
    pub size_bytes: Option<u64>,
    /// Which release tag this asset rides — `product` (ABI-coupled to this knaif release) or a
    /// toolkit-keyed tag like `redist-cuda-13.3` shared across releases. Informational: the store
    /// acts on `url`. Recorded so a reader can see the split without diffing URLs.
    #[serde(default)]
    pub tag: Option<String>,
}

impl BackendFile {
    /// The URL if it is real (present, non-empty, not the `TODO` placeholder).
    pub fn real_url(&self) -> Option<&str> {
        is_real(self.url.as_deref())
    }

    /// The checksum if it is real.
    pub fn real_sha256(&self) -> Option<&str> {
        is_real(self.sha256.as_deref())
    }
}

/// The file set for one `<os>-<arch>` target.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct BackendPlatform {
    #[serde(default)]
    pub files: Vec<BackendFile>,
}

/// What a payload needs from the machine before it is worth offering. Read by the first-run nudge so
/// the driver floor is declared beside the payload rather than hard-coded in the gate.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct BackendRequires {
    /// GPU vendor this payload targets (e.g. `nvidia`).
    #[serde(default)]
    pub vendor: Option<String>,
    /// Minimum GPU driver major version. R580+ is the CUDA 13 floor; the presence of `nvcuda.dll` /
    /// `libcuda.so` alone is not sufficient, because an older driver loads the lib and then fails.
    #[serde(default)]
    pub min_driver: Option<u32>,
    /// CUDA toolkit the redist half was built from (e.g. `13.3`) — also the `redist-cuda-*` tag.
    #[serde(default)]
    pub cuda_toolkit: Option<String>,
}

/// Whether a payload's assets are actually uploaded. An `Unpublished` entry is refused by
/// `backend install` rather than fetching a `TODO` URL.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PublishStatus {
    #[default]
    Unpublished,
    Published,
}

/// How strongly to offer this payload, expressed as data.
///
/// The offer has two strengths — on some hardware the payload is what makes the product usable,
/// on the rest it is an optimisation — and which is which is a **measurement**, not a fact about
/// the architecture. The defect behind the strong case is in a llama.cpp/driver code path and may
/// be fixed upstream, at which point a list baked into the code would start lying in the other
/// direction. Keeping it here means re-measuring when the llama.cpp pin moves is one edit.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct BackendNudge {
    /// Compute capabilities (`"12.0"`) where the *fallback* backend has been measured to be
    /// inadequate rather than merely slower. Sourced from `docs/PERFORMANCE.md` §2.
    #[serde(default)]
    pub vulkan_inadequate_compute_caps: Vec<String>,
}

/// One installable backend payload (e.g. `cuda`).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct BackendSpec {
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub status: PublishStatus,
    #[serde(default)]
    pub requires: BackendRequires,
    #[serde(default)]
    pub nudge: BackendNudge,
    /// Keyed `<os>-<arch>`, matching what `installers/package.sh` names its artifacts.
    #[serde(default)]
    pub platforms: BTreeMap<String, BackendPlatform>,
}

impl BackendSpec {
    /// The file list for `platform` (`"linux-x64"`), if this payload targets it.
    pub fn platform(&self, platform: &str) -> Option<&BackendPlatform> {
        self.platforms.get(platform)
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct BackendManifest {
    /// The knaif release this manifest ships inside. Stamped into the install receipt so the loader
    /// can refuse a payload left behind by a previous release — `~/.knaif/backends` sits outside the
    /// install dir by design, survives an upgrade, and is scanned first.
    #[serde(default)]
    pub knaif_version: Option<String>,
    #[serde(default)]
    pub backends: BTreeMap<String, BackendSpec>,
}

impl BackendManifest {
    pub fn from_yaml(text: &str) -> anyhow::Result<Self> {
        Ok(serde_yaml::from_str(text)?)
    }

    pub fn load(path: &Path) -> anyhow::Result<Self> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("reading backend manifest {}: {e}", path.display()))?;
        Self::from_yaml(&text)
    }

    pub fn get(&self, name: &str) -> Option<&BackendSpec> {
        self.backends.get(name)
    }
}

/// This build's `<os>-<arch>` platform key, matching the manifest's `platforms:` keys and the
/// artifact names `installers/package.sh` produces.
pub fn current_platform() -> String {
    // `consts::OS` already matches package.sh's names (`windows`, `linux`, `macos`); only the arch
    // spelling differs, because the artifacts use the short forms.
    let os = std::env::consts::OS;
    let arch = match std::env::consts::ARCH {
        "x86_64" => "x64",
        "aarch64" => "arm64",
        other => other,
    };
    format!("{os}-{arch}")
}

#[cfg(test)]
mod tests {
    use super::*;

    const MANIFEST: &str = r#"
knaif_version: "1.1.0"
backends:
  cuda:
    description: "NVIDIA CUDA offload"
    status: unpublished
    requires:
      vendor: nvidia
      min_driver: 580
      cuda_toolkit: "13.3"
    platforms:
      linux-x64:
        files:
          - name: libggml-cuda.so
            tag: product
            url: TODO
            sha256: TODO
          - name: libcudart.so.13
            tag: redist-cuda-13.3
            url: "https://example.test/libcudart.so.13"
            sha256: "aa"
            size_bytes: 12
"#;

    #[test]
    fn parses_files_requires_and_the_version_binding() {
        let m = BackendManifest::from_yaml(MANIFEST).unwrap();
        assert_eq!(m.knaif_version.as_deref(), Some("1.1.0"));

        let cuda = m.get("cuda").unwrap();
        assert_eq!(cuda.status, PublishStatus::Unpublished);
        assert_eq!(cuda.requires.min_driver, Some(580));
        assert_eq!(cuda.requires.vendor.as_deref(), Some("nvidia"));

        let linux = cuda.platform("linux-x64").unwrap();
        assert_eq!(linux.files.len(), 2);
        assert_eq!(linux.files[0].tag.as_deref(), Some("product"));
        // The TODO placeholder is not a real URL, exactly as in the model manifest.
        assert_eq!(linux.files[0].real_url(), None);
        assert_eq!(linux.files[0].real_sha256(), None);
        assert_eq!(
            linux.files[1].real_url(),
            Some("https://example.test/libcudart.so.13")
        );
        assert_eq!(linux.files[1].size_bytes, Some(12));
    }

    #[test]
    fn unknown_platform_is_none_not_an_error() {
        // A payload simply may not target every OS — that is a "not available here" answer, not a
        // parse failure.
        let m = BackendManifest::from_yaml(MANIFEST).unwrap();
        assert!(m.get("cuda").unwrap().platform("macos-arm64").is_none());
        assert!(m.get("rocm").is_none());
    }

    #[test]
    fn status_defaults_to_unpublished_when_absent() {
        // The safe default: an entry that forgot to declare a status must not be fetchable.
        let m = BackendManifest::from_yaml("backends:\n  x:\n    platforms: {}\n").unwrap();
        assert_eq!(m.get("x").unwrap().status, PublishStatus::Unpublished);
    }

    #[test]
    fn current_platform_is_os_dash_short_arch() {
        let p = current_platform();
        assert!(
            p.contains('-') && !p.contains("x86_64") && !p.contains("aarch64"),
            "expected `<os>-<x64|arm64>`, got {p}"
        );
    }
}
