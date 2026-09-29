//! Optional external-tool detection — port of `_deps.detect_external_tools`.
//!
//! documents degrades gracefully without these: an operation that needs a missing tool reports a
//! clear missing-dependency error rather than failing opaquely. The installer (Phase 8) and the
//! future UI reuse this to offer per-feature installs; PATH is never modified without consent.
//! Detection only — never launches the tool.
//!
//! The lookup is `knaif skills deps`'s own (`knaif_skill_api::tools`), fed this bundle's
//! `skill.yaml`: `$KNAIF_<CMD>_BIN`, then `PATH`, then (Windows) the install folders declared
//! there. None of the three vendors' Windows installers adds itself to `PATH`.

use std::path::PathBuf;

/// This bundle's `skill.yaml`, whose `external_tools` entries say where each tool may live.
const SKILL_YAML: &str = include_str!("../../skill.yaml");

/// Which optional document backends are present (resolved paths, or `None`). A command's
/// `$KNAIF_<CMD>_BIN` (e.g. `KNAIF_GS_BIN`) overrides detection for a custom install / tests.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ExternalTools {
    /// Ghostscript (`gs` / `gswin64c` / `gswin32c`) — PDF compression.
    pub ghostscript: Option<PathBuf>,
    /// LibreOffice (`soffice` / `libreoffice`) — office-document conversion.
    pub libreoffice: Option<PathBuf>,
    /// Tesseract (`tesseract`) — OCR.
    pub tesseract: Option<PathBuf>,
}

impl ExternalTools {
    /// Detect all three from the current environment.
    pub fn detect() -> Self {
        use knaif_skill_api::tools::tool_bin;
        Self {
            ghostscript: tool_bin(SKILL_YAML, "ghostscript"),
            libreoffice: tool_bin(SKILL_YAML, "libreoffice"),
            tesseract: tool_bin(SKILL_YAML, "tesseract"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_override_wins() {
        // A tool-specific override is returned verbatim without touching PATH.
        std::env::set_var("KNAIF_GS_BIN", "/custom/gs");
        let tools = ExternalTools::detect();
        assert_eq!(tools.ghostscript, Some(PathBuf::from("/custom/gs")));
        std::env::remove_var("KNAIF_GS_BIN");
    }

    #[test]
    fn detect_asks_for_the_tools_skill_yaml_declares() {
        // A rename on either side would make that tool silently undetectable.
        let doc: serde_yaml::Value = serde_yaml::from_str(SKILL_YAML).unwrap();
        let names: Vec<&str> = doc["dependencies"]["external_tools"]
            .as_sequence()
            .unwrap()
            .iter()
            .filter_map(|t| t["name"].as_str())
            .collect();
        assert_eq!(names, ["ghostscript", "libreoffice", "tesseract"]);
    }
}
