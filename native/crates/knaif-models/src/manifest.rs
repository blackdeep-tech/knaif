//! The language-neutral model manifest (`contracts/models/model-manifest.yaml`).
//!
//! Records the models the runtime knows about (file, download URL, SHA-256, license,
//! skill compatibility) and per-surface recommendations. Read by `ModelStore` and by any
//! model-management UI; the Rust runtime and Python dev path share the same file.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;

/// One model's record in the manifest.
#[derive(Debug, Clone, Deserialize)]
pub struct ModelSpec {
    /// GGUF filename as stored on disk in the model store.
    pub file: String,
    /// Download URL. `None`/empty/"TODO" means not yet hosted (pull is unavailable).
    #[serde(default)]
    pub url: Option<String>,
    /// Expected SHA-256 (hex). `None`/empty/"TODO" means unverifiable.
    #[serde(default)]
    pub sha256: Option<String>,
    #[serde(default)]
    pub size_bytes: Option<u64>,
    #[serde(default)]
    pub license: Option<String>,
    /// Skills this model is a good fit for (informational).
    #[serde(default)]
    pub skills: Vec<String>,
    /// Provenance: which fine-tune run produced this release (e.g. `sft-v3-flat`). Informational —
    /// keeps the public release version decoupled from the internal FT-cycle number.
    #[serde(default)]
    pub training_run: Option<String>,
}

/// Per-surface model recommendations (not hard requirements).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Recommendations {
    #[serde(default)]
    pub desktop: Option<String>,
    #[serde(default)]
    pub cli: Option<String>,
    #[serde(default)]
    pub mobile: Option<String>,
    /// Fallback default when no surface-specific recommendation applies.
    #[serde(default)]
    pub default: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct Manifest {
    #[serde(default)]
    pub models: BTreeMap<String, ModelSpec>,
    #[serde(default)]
    pub recommendations: Recommendations,
}

impl Manifest {
    pub fn from_yaml(text: &str) -> anyhow::Result<Self> {
        Ok(serde_yaml::from_str(text)?)
    }

    pub fn load(path: &Path) -> anyhow::Result<Self> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("reading manifest {}: {e}", path.display()))?;
        Self::from_yaml(&text)
    }

    pub fn get(&self, name: &str) -> Option<&ModelSpec> {
        self.models.get(name)
    }

    /// Manifest-level default (recommendations.default, else recommendations.cli).
    pub fn default_model(&self) -> Option<&str> {
        self.recommendations
            .default
            .as_deref()
            .or(self.recommendations.cli.as_deref())
    }
}

/// A declared value is "real" when it is present, non-empty, and not the `TODO` placeholder.
pub(crate) fn is_real(value: Option<&str>) -> Option<&str> {
    value.filter(|s| !s.is_empty() && *s != "TODO")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_training_run_and_tolerates_its_absence() {
        let m = Manifest::from_yaml(
            "models:\n  \
             pub-v1:\n    file: knaif-x-v1.gguf\n    training_run: sft-v3-flat\n    skills: [ffmpeg]\n  \
             legacy:\n    file: y.gguf\n",
        )
        .unwrap();
        assert_eq!(
            m.get("pub-v1").unwrap().training_run.as_deref(),
            Some("sft-v3-flat")
        );
        // absent training_run is fine (defaults to None) — an unknown `source:` key is ignored too
        assert_eq!(m.get("legacy").unwrap().training_run, None);
    }
}
