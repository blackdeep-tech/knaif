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
    /// The prompt generation this model was trained for (`contracts/runtime/prompt_generation.yaml`).
    /// `None`: trained before generations were counted. A CLI says when the model it runs was
    /// not trained for the generation this knaif serves.
    #[serde(default)]
    pub trained_for: Option<u32>,
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

    /// The model to get for *generation*: the recommendation when it was trained for it, else
    /// the first model (by name) that was.
    pub fn model_trained_for(&self, generation: u32) -> Option<&str> {
        let fits = |name: &str| self.get(name).and_then(|s| s.trained_for) == Some(generation);
        self.recommendations
            .cli
            .as_deref()
            .or(self.default_model())
            .filter(|n| fits(n))
            .or_else(|| {
                self.models
                    .iter()
                    .find(|(_, s)| s.trained_for == Some(generation))
                    .map(|(n, _)| n.as_str())
            })
    }
}

/// `generation` from `contracts/runtime/prompt_generation.yaml`: the served prompt this knaif
/// was built with. Mirrors Python `knaif.prompt_generation.declared`.
pub fn prompt_generation_from_yaml(text: &str) -> anyhow::Result<u32> {
    #[derive(Deserialize)]
    struct File {
        generation: u32,
    }
    let file: File = serde_yaml::from_str(text)?;
    Ok(file.generation)
}

/// One line for the terminal when a model was not trained for the generation this knaif serves,
/// else `None`. Word for word Python's `knaif.prompt_generation.note_for`; `pull` names the
/// model trained for it, when there is one.
pub fn trained_for_note(
    trained_for: Option<u32>,
    generation: u32,
    model: &str,
    pull: Option<&str>,
) -> Option<String> {
    if trained_for == Some(generation) {
        return None;
    }
    let when = match trained_for {
        Some(t) if t > generation => "a newer",
        _ => "an earlier",
    };
    // An older model is noted only once there is a model for this generation to pull: a release
    // that serves the shipped model on a newer prompt must not warn on every run until then
    // (owner, 2026-10-26). A model trained for a newer prompt always says so (knaif is behind).
    if when == "an earlier" && pull.is_none() {
        return None;
    }
    let mut note = format!(
        "note: {model} was trained for {when} prompt than this knaif serves \
         (generation {generation}); results may be worse."
    );
    if let Some(p) = pull.filter(|p| *p != model) {
        note.push_str(&format!(" The model for it: {p}."));
    }
    Some(note)
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

    #[test]
    fn parses_trained_for_and_finds_the_model_for_a_generation() {
        let m = Manifest::from_yaml(
            "models:\n  \
             old:\n    file: old.gguf\n  \
             pub-v3:\n    file: v3.gguf\n    trained_for: 1\n  \
             pub-v4:\n    file: v4.gguf\n    trained_for: 2\n\
             recommendations:\n  cli: pub-v3\n",
        )
        .unwrap();
        assert_eq!(m.get("old").unwrap().trained_for, None);
        assert_eq!(m.get("pub-v3").unwrap().trained_for, Some(1));
        // The recommendation first when it fits, else any model trained for the generation.
        assert_eq!(m.model_trained_for(1), Some("pub-v3"));
        assert_eq!(m.model_trained_for(2), Some("pub-v4"));
        assert_eq!(m.model_trained_for(3), None);
    }

    #[test]
    fn reads_the_prompt_generation_contract() {
        let text =
            "# which served prompt a model was trained for\ngeneration: 3\nfingerprint: \"ab\"\n";
        assert_eq!(prompt_generation_from_yaml(text).unwrap(), 3);
        assert!(prompt_generation_from_yaml("fingerprint: x\n").is_err());
    }

    #[test]
    fn the_note_matches_the_python_runtime_word_for_word() {
        // Twin of python/core/tests/test_prompt_generation.py::test_the_note_names_the_gap...
        assert_eq!(
            trained_for_note(None, 3, "knaif-qwen3-4b-v2", Some("knaif-made-up-model")).as_deref(),
            Some(
                "note: knaif-qwen3-4b-v2 was trained for an earlier prompt than this knaif serves \
                 (generation 3); results may be worse. The model for it: knaif-made-up-model."
            )
        );
        assert_eq!(
            trained_for_note(
                Some(3),
                3,
                "knaif-made-up-model",
                Some("knaif-made-up-model")
            ),
            None
        );
        assert_eq!(
            trained_for_note(Some(2), 3, "x", Some("y")).as_deref(),
            Some(
                "note: x was trained for an earlier prompt than this knaif serves (generation 3); \
                 results may be worse. The model for it: y."
            )
        );
        // An older model with nothing to pull says nothing (owner, 2026-10-26); a model trained
        // for a newer prompt than this knaif still says so.
        assert_eq!(trained_for_note(Some(2), 3, "x", None), None);
        assert_eq!(trained_for_note(None, 3, "x", None), None);
        assert!(trained_for_note(Some(4), 3, "x", None)
            .unwrap()
            .contains("a newer prompt"));
    }
}
