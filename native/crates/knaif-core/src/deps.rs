//! Skill external-dependency detection — the shared engine behind the installer component
//! tree (Phase 9) and the runtime `knaif setup`/doctor + execution preflight (Phase 8).
//!
//! Reads `dependencies.external_tools` from a bundle `skill.yaml` (the declarative source of
//! truth, identical for every runtime), probes `PATH` for each declared tool, and reports what
//! is satisfied plus the per-OS install hint. **Detection only** — never launches a tool and
//! never modifies `PATH`. Third-party tools are installed via their own installers / package
//! managers, never bundled (owner decision 2026-07-07).

use std::path::{Path, PathBuf};

use serde::Deserialize;

/// One declared external tool (an entry in `dependencies.external_tools` in `skill.yaml`).
///
/// A single entry maps to one installer component; its `commands` are that one vendor
/// package's binaries/aliases.
#[derive(Debug, Clone, Deserialize)]
pub struct ExternalTool {
    /// Human/component name (e.g. `ffmpeg`, `ghostscript`).
    pub name: String,
    /// Mandatory for the skill to function → an unmet `required` tool blocks execution and is a
    /// mandatory installer sub-dependency. `false` → optional (per-feature checkbox).
    #[serde(default)]
    pub required: bool,
    /// When `true`, **every** command must resolve — the commands are distinct binaries that are
    /// all needed (e.g. ffmpeg + ffprobe). Default `false` → the commands are alternative
    /// names/aliases for one binary and **any one** satisfies (e.g. gs / gswin64c / gswin32c).
    #[serde(default)]
    pub all_required: bool,
    /// Command names to probe on `PATH`.
    #[serde(default)]
    pub commands: Vec<String>,
    /// Per-OS install channel hint.
    #[serde(default)]
    pub install: InstallHints,
}

/// Per-OS install channel hint (`install: {windows, macos, linux}` in skill.yaml).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct InstallHints {
    #[serde(default)]
    pub windows: Option<String>,
    #[serde(default)]
    pub macos: Option<String>,
    #[serde(default)]
    pub linux: Option<String>,
}

impl InstallHints {
    /// The install hint for the OS this binary is running on, if declared.
    pub fn current(&self) -> Option<&str> {
        if cfg!(windows) {
            self.windows.as_deref()
        } else if cfg!(target_os = "macos") {
            self.macos.as_deref()
        } else {
            self.linux.as_deref()
        }
    }
}

/// Detection status of one declared tool.
#[derive(Debug, Clone)]
pub struct ToolStatus {
    pub name: String,
    pub required: bool,
    /// Usable now: all commands resolved (`all_required`) or at least one did (any-of).
    pub satisfied: bool,
    /// Commands that resolved, paired with their absolute path, in declaration order.
    pub found: Vec<(String, PathBuf)>,
    /// Commands that did not resolve.
    pub missing: Vec<String>,
    /// Install hint for the current OS, if declared.
    pub install_hint: Option<String>,
}

impl ExternalTool {
    /// Probe the current environment for this tool.
    pub fn detect(&self) -> ToolStatus {
        let mut found = Vec::new();
        let mut missing = Vec::new();
        for cmd in &self.commands {
            match resolve_command(cmd) {
                Some(path) => found.push((cmd.clone(), path)),
                None => missing.push(cmd.clone()),
            }
        }
        let satisfied = if self.commands.is_empty() {
            false
        } else if self.all_required {
            missing.is_empty()
        } else {
            !found.is_empty()
        };
        ToolStatus {
            name: self.name.clone(),
            required: self.required,
            satisfied,
            found,
            missing,
            install_hint: self.install.current().map(str::to_string),
        }
    }
}

/// Parse the declared external tools from a `skill.yaml` string. Unreadable/malformed → empty.
pub fn parse_external_tools(skill_yaml: &str) -> Vec<ExternalTool> {
    #[derive(Deserialize)]
    struct RawManifest {
        #[serde(default)]
        dependencies: Option<RawDeps>,
    }
    #[derive(Deserialize)]
    struct RawDeps {
        #[serde(default)]
        external_tools: Vec<ExternalTool>,
    }
    serde_yaml::from_str::<RawManifest>(skill_yaml)
        .ok()
        .and_then(|m| m.dependencies)
        .map(|d| d.external_tools)
        .unwrap_or_default()
}

/// Read `<bundle>/skill.yaml` and parse its external tools (empty if the file is unreadable).
pub fn load_external_tools(bundle_dir: &Path) -> Vec<ExternalTool> {
    match std::fs::read_to_string(bundle_dir.join("skill.yaml")) {
        Ok(text) => parse_external_tools(&text),
        Err(_) => Vec::new(),
    }
}

/// Detect every declared tool for a skill bundle, in declaration order.
pub fn detect_skill_deps(bundle_dir: &Path) -> Vec<ToolStatus> {
    load_external_tools(bundle_dir)
        .iter()
        .map(ExternalTool::detect)
        .collect()
}

/// The `required` tools that are declared but not satisfied — these block skill execution.
pub fn unmet_required(statuses: &[ToolStatus]) -> Vec<&ToolStatus> {
    statuses
        .iter()
        .filter(|s| s.required && !s.satisfied)
        .collect()
}

/// A user-facing preflight message when a skill can't execute because a required tool is missing,
/// or `None` when every required tool is present. Names each blocking tool, its missing
/// command(s), and the current-OS install hint — and states that knaif never modifies `PATH`.
pub fn missing_required_message(skill: &str, statuses: &[ToolStatus]) -> Option<String> {
    let unmet = unmet_required(statuses);
    if unmet.is_empty() {
        return None;
    }
    let mut msg = format!("The `{skill}` skill needs these tool(s), which aren't on your PATH:\n");
    for status in unmet {
        let cmds = status.missing.join(", ");
        match &status.install_hint {
            Some(hint) => {
                msg.push_str(&format!("  - {} ({cmds}) — install: {hint}\n", status.name))
            }
            None => msg.push_str(&format!("  - {} ({cmds})\n", status.name)),
        }
    }
    msg.push_str(
        "Install it, then re-run. Or use --dry-run to preview the command without executing. \
         (knaif never changes your PATH.)",
    );
    Some(msg)
}

/// Resolve a single command: `$KNAIF_<CMD>_BIN` override first, else a `PATH` scan.
fn resolve_command(cmd: &str) -> Option<PathBuf> {
    let env_key = format!("KNAIF_{}_BIN", cmd.to_uppercase());
    if let Some(raw) = std::env::var_os(&env_key) {
        if !raw.is_empty() {
            return Some(PathBuf::from(raw));
        }
    }
    which(cmd)
}

/// Minimal `shutil.which`: scan `PATH` for `name` (trying `PATHEXT` suffixes on Windows).
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

/// Executable suffixes to try. Windows: `PATHEXT` (+ bare name); elsewhere just the bare name.
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

    const FFMPEG_YAML: &str = "\
name: ffmpeg
dependencies:
  external_tools:
    - name: ffmpeg
      required: true
      all_required: true
      commands: [ffmpeg, ffprobe]
      install: { windows: winget, macos: brew, linux: package_manager }
";

    const DOCUMENTS_YAML: &str = "\
name: documents
dependencies:
  external_tools:
    - name: ghostscript
      required: false
      commands: [gs, gswin64c, gswin32c]
      install: { windows: winget, macos: brew, linux: package_manager }
";

    #[test]
    fn parses_ffmpeg_all_required_entry() {
        let tools = parse_external_tools(FFMPEG_YAML);
        assert_eq!(tools.len(), 1);
        let t = &tools[0];
        assert_eq!(t.name, "ffmpeg");
        assert!(t.required);
        assert!(t.all_required);
        assert_eq!(t.commands, vec!["ffmpeg", "ffprobe"]);
    }

    #[test]
    fn parses_documents_optional_alias_entry() {
        let tools = parse_external_tools(DOCUMENTS_YAML);
        assert_eq!(tools.len(), 1);
        let t = &tools[0];
        assert_eq!(t.name, "ghostscript");
        assert!(!t.required);
        assert!(!t.all_required); // default: any-of aliases
        assert_eq!(t.commands.len(), 3);
    }

    #[test]
    fn missing_dependencies_section_is_empty() {
        assert!(parse_external_tools("name: bare\n").is_empty());
        assert!(parse_external_tools("::: not yaml :::").is_empty());
    }

    #[test]
    fn any_of_satisfied_when_a_single_alias_resolves() {
        // Distinct fake command names so this test can't collide with another test's env.
        std::env::set_var("KNAIF_KNAIFTESTALIASB_BIN", "/opt/fake/aliasb");
        let tool = ExternalTool {
            name: "aliastool".into(),
            required: false,
            all_required: false,
            commands: vec![
                "knaiftestaliasa".into(),
                "knaiftestaliasb".into(),
                "knaiftestaliasc".into(),
            ],
            install: InstallHints::default(),
        };
        let status = tool.detect();
        assert!(status.satisfied, "any-of: one resolved alias satisfies");
        assert_eq!(status.found.len(), 1);
        assert_eq!(status.found[0].0, "knaiftestaliasb");
        assert_eq!(status.missing.len(), 2);
        std::env::remove_var("KNAIF_KNAIFTESTALIASB_BIN");
    }

    #[test]
    fn all_required_needs_every_command() {
        std::env::set_var("KNAIF_KNAIFTESTALLA_BIN", "/opt/fake/alla");
        let tool = ExternalTool {
            name: "pairtool".into(),
            required: true,
            all_required: true,
            commands: vec!["knaiftestalla".into(), "knaiftestallb".into()],
            install: InstallHints::default(),
        };
        // Only one of two present → not satisfied.
        let partial = tool.detect();
        assert!(!partial.satisfied, "all_required: one of two is not enough");

        // Both present → satisfied.
        std::env::set_var("KNAIF_KNAIFTESTALLB_BIN", "/opt/fake/allb");
        let full = tool.detect();
        assert!(full.satisfied, "all_required: both present satisfies");
        assert!(full.missing.is_empty());

        std::env::remove_var("KNAIF_KNAIFTESTALLA_BIN");
        std::env::remove_var("KNAIF_KNAIFTESTALLB_BIN");
    }

    #[test]
    fn empty_commands_never_satisfied() {
        let tool = ExternalTool {
            name: "empty".into(),
            required: false,
            all_required: false,
            commands: vec![],
            install: InstallHints::default(),
        };
        assert!(!tool.detect().satisfied);
    }

    #[test]
    fn install_hint_reports_current_os() {
        let hints = InstallHints {
            windows: Some("winget".into()),
            macos: Some("brew".into()),
            linux: Some("package_manager".into()),
        };
        let expected = if cfg!(windows) {
            "winget"
        } else if cfg!(target_os = "macos") {
            "brew"
        } else {
            "package_manager"
        };
        assert_eq!(hints.current(), Some(expected));
    }

    #[test]
    fn unmet_required_filters_to_blocking_tools() {
        let statuses = vec![
            ToolStatus {
                name: "req-missing".into(),
                required: true,
                satisfied: false,
                found: vec![],
                missing: vec!["x".into()],
                install_hint: None,
            },
            ToolStatus {
                name: "req-ok".into(),
                required: true,
                satisfied: true,
                found: vec![],
                missing: vec![],
                install_hint: None,
            },
            ToolStatus {
                name: "opt-missing".into(),
                required: false,
                satisfied: false,
                found: vec![],
                missing: vec!["y".into()],
                install_hint: None,
            },
        ];
        let unmet = unmet_required(&statuses);
        assert_eq!(unmet.len(), 1);
        assert_eq!(unmet[0].name, "req-missing");
    }

    #[test]
    fn no_message_when_required_tools_satisfied() {
        let statuses = vec![ToolStatus {
            name: "ffmpeg".into(),
            required: true,
            satisfied: true,
            found: vec![],
            missing: vec![],
            install_hint: Some("winget".into()),
        }];
        assert!(missing_required_message("ffmpeg", &statuses).is_none());
        // No declared tools at all (e.g. documents) is also unblocked.
        assert!(missing_required_message("documents", &[]).is_none());
    }

    #[test]
    fn message_names_skill_tool_and_install_hint() {
        let statuses = vec![
            ToolStatus {
                name: "ffmpeg".into(),
                required: true,
                satisfied: false,
                found: vec![],
                missing: vec!["ffmpeg".into(), "ffprobe".into()],
                install_hint: Some("winget".into()),
            },
            // An unmet OPTIONAL tool must not appear in the blocking message.
            ToolStatus {
                name: "tesseract".into(),
                required: false,
                satisfied: false,
                found: vec![],
                missing: vec!["tesseract".into()],
                install_hint: Some("winget".into()),
            },
        ];
        let msg = missing_required_message("ffmpeg", &statuses).expect("required tool is missing");
        assert!(msg.contains("ffmpeg"));
        assert!(msg.contains("ffprobe"));
        assert!(msg.contains("winget"));
        assert!(msg.to_lowercase().contains("path"));
        assert!(!msg.contains("tesseract"), "optional tool must not block");
    }
}
