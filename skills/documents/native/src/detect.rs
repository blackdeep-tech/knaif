//! Optional external-tool detection — port of `_deps.detect_external_tools`.
//!
//! documents degrades gracefully without these: an operation that needs a missing tool reports a
//! clear missing-dependency error rather than failing opaquely. The installer (Phase 8) and the
//! future UI reuse this to offer per-feature installs; PATH is never modified without consent.
//! Detection only — never launches the tool.

use std::path::PathBuf;

/// Which optional document backends are present on `PATH` (resolved absolute paths, or `None`).
/// `$KNAIF_<TOOL>_BIN` (e.g. `KNAIF_GS_BIN`) overrides detection for a custom install / tests.
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
        Self {
            ghostscript: which_any("KNAIF_GS_BIN", &["gs", "gswin64c", "gswin32c"]),
            libreoffice: which_any("KNAIF_SOFFICE_BIN", &["soffice", "libreoffice"]),
            tesseract: which_any("KNAIF_TESSERACT_BIN", &["tesseract"]),
        }
    }
}

/// Return the first of `names` found on `PATH`, honoring `$env_override` first (port of the
/// `shutil.which(a) or which(b) …` chain, plus a tool-specific env override).
fn which_any(env_override: &str, names: &[&str]) -> Option<PathBuf> {
    if let Some(raw) = std::env::var_os(env_override) {
        if !raw.is_empty() {
            return Some(PathBuf::from(raw));
        }
    }
    names.iter().find_map(|name| which(name))
}

/// Minimal `shutil.which`: scan `PATH` for `name` (trying the `PATHEXT` suffixes on Windows).
fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts = executable_extensions();
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let candidate = dir.join(format!("{name}{ext}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

/// Executable suffixes to try. On Windows, `PATHEXT` (e.g. `.EXE;.BAT`) plus a bare name; elsewhere
/// just the bare name.
fn executable_extensions() -> Vec<String> {
    if cfg!(windows) {
        let mut exts = vec![String::new()];
        if let Some(pathext) = std::env::var_os("PATHEXT") {
            exts.extend(
                pathext
                    .to_string_lossy()
                    .split(';')
                    .filter(|s| !s.is_empty())
                    .map(|s| s.to_lowercase()),
            );
        } else {
            exts.extend([".exe", ".bat", ".cmd"].map(String::from));
        }
        exts
    } else {
        vec![String::new()]
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
    fn which_finds_a_known_binary() {
        // The Rust toolchain guarantees `cargo` is on PATH in CI/dev; use it as a stable probe
        // that the PATH scan works (any real binary would do).
        let found = which("cargo")
            .or_else(|| which("sh"))
            .or_else(|| which("cmd"));
        assert!(found.is_some(), "expected to find cargo/sh/cmd on PATH");
    }

    #[test]
    fn missing_tool_is_none() {
        assert!(which("knaif-definitely-not-a-real-tool-xyz").is_none());
    }
}
