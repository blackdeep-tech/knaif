//! Skill external-dependency detection — the shared engine behind the installer component
//! tree (Phase 9) and the runtime `knaif setup`/doctor + execution preflight (Phase 8).
//!
//! Reads `dependencies.external_tools` from a bundle `skill.yaml` (the declarative source of
//! truth, identical for every runtime), probes `PATH` — then the install folders the entry
//! declares for this OS, since the Windows installers of Ghostscript/LibreOffice/Tesseract never
//! touch `PATH` and on macOS a LibreOffice cask lives inside its `.app` —
//! for each declared tool, and reports what is satisfied plus an actionable install hint. The
//! skills launch the binary the same lookup picks (`resolve_declared_*`), so found means used. **Detection only** — never launches a tool and
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
    /// Windows-only install facts: the winget package, the vendor's download page, and the
    /// folders the vendor's installer uses (most of which never put themselves on `PATH`).
    #[serde(default)]
    pub windows: WindowsInstall,
    /// macOS-only install facts: the Homebrew formula (or cask) and the folders searched after
    /// `PATH`.
    #[serde(default)]
    pub macos: MacosInstall,
}

/// The `macos:` block of an external tool in `skill.yaml`.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct MacosInstall {
    /// Homebrew formula (`brew install <name>`), or cask name when `cask` is set. The `.pkg`
    /// installer's options page installs the same name.
    #[serde(default)]
    pub brew: Option<String>,
    /// `brew` names a cask (`brew install --cask <name>`) — an `.app`, not a formula.
    #[serde(default)]
    pub cask: bool,
    /// Install folders searched after `PATH`, as absolute paths. Homebrew's `bin` is listed
    /// because a knaif started outside a login shell (the `.pkg` postinstall, a GUI) does not have
    /// it on `PATH`; a cask's binary sits inside its `.app` and is never on `PATH`.
    #[serde(default)]
    pub dirs: Vec<String>,
}

/// The `windows:` block of an external tool in `skill.yaml`.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct WindowsInstall {
    /// winget package id (`winget install -e --id <id>`); the installer's task must match it.
    #[serde(default)]
    pub winget: Option<String>,
    /// The vendor's download page, for a machine without winget.
    #[serde(default)]
    pub download: Option<String>,
    /// Install folders searched after `PATH`: `%VAR%` is expanded from the environment, `*`
    /// matches within one path component, and several matches are tried newest version first.
    #[serde(default)]
    pub dirs: Vec<String>,
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
        self.for_os(std::env::consts::OS)
    }

    /// The install hint for `os` (a [`std::env::consts::OS`] value), if declared.
    fn for_os(&self, os: &str) -> Option<&str> {
        match os {
            "windows" => self.windows.as_deref(),
            "macos" => self.macos.as_deref(),
            _ => self.linux.as_deref(),
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
    /// The declared install folders for this OS that exist on this machine.
    fn known_dirs(&self) -> Vec<PathBuf> {
        expand_dirs(self.declared_dirs(std::env::consts::OS))
    }

    /// The install folders declared for `os` (a [`std::env::consts::OS`] value); none on Linux,
    /// where package managers put every tool on `PATH`.
    fn declared_dirs(&self, os: &str) -> &[String] {
        match os {
            "windows" => &self.windows.dirs,
            "macos" => &self.macos.dirs,
            _ => &[],
        }
    }

    /// The binary to launch for this tool: the first of its commands to resolve, trying every
    /// command's `$KNAIF_<CMD>_BIN` override, then every command on `PATH`, then every command
    /// in the declared install folders. For `all_required` tools resolve each command instead.
    pub fn resolve_any(&self) -> Option<PathBuf> {
        self.resolve_any_in(&self.known_dirs())
    }

    fn resolve_any_in(&self, dirs: &[PathBuf]) -> Option<PathBuf> {
        self.commands
            .iter()
            .find_map(|c| env_override(c))
            .or_else(|| self.commands.iter().find_map(|c| which(c)))
            .or_else(|| self.commands.iter().find_map(|c| find_in_dirs(c, dirs)))
    }

    /// Probe the current environment for this tool.
    pub fn detect(&self) -> ToolStatus {
        let dirs = self.known_dirs();
        let mut found = Vec::new();
        let mut missing = Vec::new();
        for cmd in &self.commands {
            match resolve_command_in(cmd, &dirs) {
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
            install_hint: hint_for(
                self,
                std::env::consts::OS,
                cfg!(windows) && winget_available(),
            ),
        }
    }
}

/// What to tell the user to do about a missing tool on `os`. On Windows: the exact winget command
/// when winget is there, else the vendor's download page — a bare "winget" is no help on a machine
/// without it. On macOS: the exact `brew install` command. Otherwise, or with nothing
/// OS-specific declared, the per-OS channel hint.
fn hint_for(tool: &ExternalTool, os: &str, winget: bool) -> Option<String> {
    match os {
        "windows" => {
            if let (true, Some(id)) = (winget, &tool.windows.winget) {
                return Some(format!("winget install -e --id {id}"));
            }
            if let Some(url) = &tool.windows.download {
                return Some(format!("download from {url}"));
            }
        }
        "macos" => {
            if let Some(name) = &tool.macos.brew {
                let cask = if tool.macos.cask { "--cask " } else { "" };
                return Some(format!("brew install {cask}{name}"));
            }
        }
        _ => {}
    }
    tool.install.for_os(os).map(str::to_string)
}

/// Is winget usable here? It is an App Execution Alias (a zero-byte reparse point in
/// `WindowsApps`), so accept any entry by that name on `PATH`, not only a regular file.
fn winget_available() -> bool {
    which("winget").is_some()
        || std::env::var_os("PATH").is_some_and(|path| {
            std::env::split_paths(&path)
                .any(|dir| dir.join("winget.exe").symlink_metadata().is_ok())
        })
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
    let mut msg = format!(
        "The `{skill}` skill needs these tool(s), which knaif can't find on your PATH or in \
         their usual install folders:\n"
    );
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

/// The binary to launch for `tool_name` as declared in a `skill.yaml` (see
/// [`ExternalTool::resolve_any`]). `None` when the tool is undeclared or not found.
pub fn resolve_declared_tool(skill_yaml: &str, tool_name: &str) -> Option<PathBuf> {
    parse_external_tools(skill_yaml)
        .into_iter()
        .find(|t| t.name == tool_name)
        .and_then(|t| t.resolve_any())
}

/// The binary to launch for one command: override, `PATH`, then the install folders of the
/// `skill.yaml` entry that lists it. An undeclared command gets the override and `PATH` only.
pub fn resolve_declared_command(skill_yaml: &str, cmd: &str) -> Option<PathBuf> {
    let dirs = parse_external_tools(skill_yaml)
        .into_iter()
        .find(|t| t.commands.iter().any(|c| c == cmd))
        .map(|t| t.known_dirs())
        .unwrap_or_default();
    resolve_command_in(cmd, &dirs)
}

/// `$KNAIF_<CMD>_BIN`, when set and non-empty — returned verbatim, without probing.
fn env_override(cmd: &str) -> Option<PathBuf> {
    let raw = std::env::var_os(format!("KNAIF_{}_BIN", cmd.to_uppercase()))?;
    (!raw.is_empty()).then(|| PathBuf::from(raw))
}

/// Resolve a single command: `$KNAIF_<CMD>_BIN` override first, then a `PATH` scan, then `dirs`.
fn resolve_command_in(cmd: &str, dirs: &[PathBuf]) -> Option<PathBuf> {
    env_override(cmd)
        .or_else(|| which(cmd))
        .or_else(|| find_in_dirs(cmd, dirs))
}

/// The first of `dirs` holding `cmd` (under any executable suffix).
fn find_in_dirs(cmd: &str, dirs: &[PathBuf]) -> Option<PathBuf> {
    let exts = executable_extensions();
    dirs.iter().find_map(|dir| {
        exts.iter()
            .map(|ext| dir.join(format!("{cmd}{ext}")))
            .find(|candidate| is_executable(candidate))
    })
}

/// A file the OS would launch: any file on Windows (suffixes decide), a file with an execute
/// bit elsewhere. A bare-name launch skips a non-executable match and keeps searching, so the
/// lookup that replaces it must too.
fn is_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

/// Expand declared install folders to the directories that exist, in declaration order, the
/// matches of one wildcard pattern newest version first. A pattern naming an unset variable is
/// dropped rather than guessed at.
pub fn expand_dirs(patterns: &[String]) -> Vec<PathBuf> {
    patterns
        .iter()
        .filter_map(|p| expand_vars(p))
        .flat_map(|p| glob_dirs(&p))
        .collect()
}

/// Replace each `%NAME%` with that environment variable; `None` if one is unset or unclosed.
fn expand_vars(pattern: &str) -> Option<String> {
    let mut out = String::new();
    let mut rest = pattern;
    while let Some(start) = rest.find('%') {
        out.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        let end = after.find('%')?;
        let value = std::env::var_os(&after[..end])?;
        out.push_str(&value.to_string_lossy());
        rest = &after[end + 1..];
    }
    out.push_str(rest);
    Some(out)
}

/// The existing directories matching `pattern`, where `*` matches within one component. The
/// literal prefix before the first wildcard is used verbatim, so every root form (drive, UNC,
/// `\\?\`) survives; the rest is split on either slash. Several matches of one wildcard component
/// come newest version first.
fn glob_dirs(pattern: &str) -> Vec<PathBuf> {
    let Some(star) = pattern.find('*') else {
        let dir = PathBuf::from(pattern);
        return if dir.is_dir() { vec![dir] } else { Vec::new() };
    };
    let Some(sep) = pattern[..star].rfind(['\\', '/']) else {
        return Vec::new(); // a relative wildcard names no install folder
    };
    let prefix = &pattern[..sep];
    // Keep the separator for a bare root (`/gs*`) or a drive (`C:\gs*`, not drive-relative `C:`).
    let root = if prefix.is_empty() || prefix.ends_with(':') {
        PathBuf::from(&pattern[..=sep])
    } else {
        PathBuf::from(prefix)
    };
    let mut bases = vec![root];
    for part in pattern[sep + 1..]
        .split(['\\', '/'])
        .filter(|p| !p.is_empty())
    {
        if !part.contains('*') {
            bases = bases.into_iter().map(|b| b.join(part)).collect();
            continue;
        }
        let mut next = Vec::new();
        for base in &bases {
            let Ok(entries) = std::fs::read_dir(base) else {
                continue;
            };
            let mut names: Vec<String> = entries
                .filter_map(Result::ok)
                .filter(|e| e.path().is_dir())
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|name| wildcard_match(part, name))
                .collect();
            names.sort_by(|a, b| natural_cmp(b, a));
            next.extend(names.into_iter().map(|n| base.join(n)));
        }
        bases = next;
    }
    bases.into_iter().filter(|b| b.is_dir()).collect()
}

/// `*`-only glob over one path component, ignoring case (Windows paths are case-insensitive).
fn wildcard_match(pattern: &str, name: &str) -> bool {
    let pattern = pattern.to_lowercase();
    let name = name.to_lowercase();
    let pieces: Vec<&str> = pattern.split('*').collect();
    let (first, last) = (pieces[0], pieces[pieces.len() - 1]);
    if pieces.len() == 1 {
        return pattern == name;
    }
    if name.len() < first.len() + last.len() || !name.starts_with(first) || !name.ends_with(last) {
        return false;
    }
    let mut rest = &name[first.len()..name.len() - last.len()];
    for piece in &pieces[1..pieces.len() - 1] {
        match rest.find(piece) {
            Some(at) => rest = &rest[at + piece.len()..],
            None => return false,
        }
    }
    true
}

/// Order names so that digit runs compare as numbers (`gs10.05.1` > `gs9.56.1`) and text runs
/// ignore case, as the wildcard that selected them does (`GS10` > `gs9`).
fn natural_cmp(a: &str, b: &str) -> std::cmp::Ordering {
    let (a, b) = (a.to_lowercase(), b.to_lowercase());
    let (a, b) = (a.as_str(), b.as_str());
    fn runs(s: &str) -> Vec<(bool, &str)> {
        let mut out = Vec::new();
        let mut start = 0;
        let bytes = s.as_bytes();
        for i in 1..=bytes.len() {
            if i == bytes.len() || bytes[i].is_ascii_digit() != bytes[start].is_ascii_digit() {
                out.push((bytes[start].is_ascii_digit(), &s[start..i]));
                start = i;
            }
        }
        out
    }
    let (ra, rb) = (runs(a), runs(b));
    for ((da, sa), (db, sb)) in ra.iter().zip(rb.iter()) {
        let ord = if *da && *db {
            let (ta, tb) = (sa.trim_start_matches('0'), sb.trim_start_matches('0'));
            ta.len().cmp(&tb.len()).then_with(|| ta.cmp(tb))
        } else {
            sa.cmp(sb)
        };
        if ord != std::cmp::Ordering::Equal {
            return ord;
        }
    }
    ra.len().cmp(&rb.len())
}

/// Minimal `shutil.which`: scan `PATH` for `name` (trying `PATHEXT` suffixes on Windows).
fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts = executable_extensions();
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let candidate = dir.join(format!("{name}{ext}"));
            if is_executable(&candidate) {
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
            windows: WindowsInstall::default(),
            macos: MacosInstall::default(),
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
            windows: WindowsInstall::default(),
            macos: MacosInstall::default(),
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
            windows: WindowsInstall::default(),
            macos: MacosInstall::default(),
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

    #[test]
    fn parses_the_windows_block() {
        let yaml = "\
dependencies:
  external_tools:
    - name: ghostscript
      commands: [gs, gswin64c]
      windows:
        winget: ArtifexSoftware.GhostScript
        download: https://ghostscript.com/releases/gsdnld.html
        dirs: ['%ProgramFiles%\\gs\\gs*\\bin']
";
        let t = &parse_external_tools(yaml)[0];
        assert_eq!(
            t.windows.winget.as_deref(),
            Some("ArtifexSoftware.GhostScript")
        );
        assert_eq!(
            t.windows.download.as_deref(),
            Some("https://ghostscript.com/releases/gsdnld.html")
        );
        assert_eq!(t.windows.dirs, vec![r"%ProgramFiles%\gs\gs*\bin"]);
        // Absent block: nothing extra, and the entry still parses.
        assert!(parse_external_tools(FFMPEG_YAML)[0].windows.dirs.is_empty());
    }

    /// A fake executable named `cmd` in `dir`, spelled the way this platform's lookup finds it.
    fn fake_exe(dir: &Path, cmd: &str) -> PathBuf {
        std::fs::create_dir_all(dir).unwrap();
        let name = if cfg!(windows) {
            format!("{cmd}.exe")
        } else {
            cmd.to_string()
        };
        let path = dir.join(name);
        std::fs::write(&path, b"").unwrap();
        // The lookup only accepts what the OS would launch: off Windows, the execute bit.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        path
    }

    /// A declared folder, written the way `skill.yaml`'s `windows.dirs` does (backslashes) but with
    /// this platform's separator: these tests run everywhere, and a backslash is not a separator
    /// off Windows.
    fn declared(pattern: &str) -> String {
        pattern.replace('\\', std::path::MAIN_SEPARATOR_STR)
    }

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("knaif-deps-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_declared_folder_expands_its_variable_and_wildcard_newest_first() {
        let root = scratch("glob");
        // String order would put gs9 first; the newest version must win.
        fake_exe(&root.join("gs").join("gs9.56.1").join("bin"), "knaiftestgs");
        let newest = fake_exe(
            &root.join("gs").join("gs10.05.1").join("bin"),
            "knaiftestgs",
        );
        fake_exe(&root.join("gs").join("gs10.4.0").join("bin"), "knaiftestgs");
        std::fs::create_dir_all(root.join("gs").join("unrelated")).unwrap();
        std::env::set_var("KNAIF_TEST_GLOB_ROOT", &root);

        let dirs = expand_dirs(&[declared(r"%KNAIF_TEST_GLOB_ROOT%\gs\gs*\bin")]);
        assert_eq!(dirs.len(), 3, "{dirs:?}");
        assert_eq!(find_in_dirs("knaiftestgs", &dirs), Some(newest));
        std::env::remove_var("KNAIF_TEST_GLOB_ROOT");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_folder_naming_an_unset_variable_is_skipped() {
        assert!(expand_dirs(&[r"%KNAIF_TEST_SURELY_UNSET_VAR%\bin".to_string()]).is_empty());
        assert!(expand_dirs(&[r"%KNAIF_TEST_SURELY_UNSET_VAR".to_string()]).is_empty());
    }

    #[test]
    fn wildcards_match_within_one_component_case_insensitively() {
        assert!(wildcard_match(
            "Gyan.FFmpeg_*",
            "gyan.ffmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        ));
        assert!(wildcard_match("ffmpeg-*", "ffmpeg-7.1-full_build"));
        assert!(wildcard_match("gs*", "gs"));
        assert!(!wildcard_match("gs*", "ghostscript"));
        assert!(wildcard_match("*-*", "a-b"));
        assert!(!wildcard_match("a*b", "ac"));
    }

    #[test]
    fn natural_order_compares_digit_runs_as_numbers() {
        use std::cmp::Ordering::*;
        assert_eq!(natural_cmp("gs10.05.1", "gs9.56.1"), Greater);
        assert_eq!(natural_cmp("gs10.05.1", "gs10.4.0"), Greater);
        assert_eq!(natural_cmp("ffmpeg-7.1", "ffmpeg-7.1"), Equal);
    }

    #[test]
    fn the_known_folders_are_tried_after_path() {
        let root = scratch("after");
        let bin = fake_exe(&root.join("bin"), "knaiftestafter");
        std::env::set_var("KNAIF_TEST_AFTER_ROOT", &root);
        let dirs = vec![declared(r"%KNAIF_TEST_AFTER_ROOT%\bin")];
        assert_eq!(
            resolve_command_in("knaiftestafter", &expand_dirs(&dirs)),
            Some(bin)
        );
        // Nothing declared, nothing on PATH: not found.
        assert_eq!(resolve_command_in("knaiftestafter", &[]), None);
        std::env::remove_var("KNAIF_TEST_AFTER_ROOT");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn resolve_any_prefers_an_override_then_path_then_folders() {
        let root = scratch("any");
        let in_folder = fake_exe(&root.join("bin"), "knaiftestanyb");
        std::env::set_var("KNAIF_TEST_ANY_ROOT", &root);
        let tool = ExternalTool {
            name: "anytool".into(),
            required: false,
            all_required: false,
            commands: vec!["knaiftestanya".into(), "knaiftestanyb".into()],
            install: InstallHints::default(),
            windows: WindowsInstall {
                dirs: vec![declared(r"%KNAIF_TEST_ANY_ROOT%\bin")],
                ..WindowsInstall::default()
            },
            macos: MacosInstall::default(),
        };
        let dirs = expand_dirs(&tool.windows.dirs);
        assert_eq!(tool.resolve_any_in(&dirs), Some(in_folder));
        std::env::set_var("KNAIF_KNAIFTESTANYA_BIN", "/opt/override/anya");
        assert_eq!(
            tool.resolve_any_in(&dirs),
            Some(PathBuf::from("/opt/override/anya"))
        );
        std::env::remove_var("KNAIF_KNAIFTESTANYA_BIN");
        std::env::remove_var("KNAIF_TEST_ANY_ROOT");
        let _ = std::fs::remove_dir_all(&root);
    }

    fn hinted_tool(win: WindowsInstall, mac: MacosInstall) -> ExternalTool {
        ExternalTool {
            name: "tool".into(),
            required: true,
            all_required: false,
            commands: vec!["tool".into()],
            install: InstallHints {
                windows: Some("winget".into()),
                macos: Some("brew".into()),
                linux: Some("package_manager".into()),
            },
            windows: win,
            macos: mac,
        }
    }

    #[test]
    fn the_windows_hint_is_the_winget_command_else_the_download_page() {
        let win = WindowsInstall {
            winget: Some("Gyan.FFmpeg".into()),
            download: Some("https://ffmpeg.org/download.html".into()),
            dirs: vec![],
        };
        let tool = hinted_tool(win, MacosInstall::default());
        assert_eq!(
            hint_for(&tool, "windows", true).as_deref(),
            Some("winget install -e --id Gyan.FFmpeg")
        );
        assert_eq!(
            hint_for(&tool, "windows", false).as_deref(),
            Some("download from https://ffmpeg.org/download.html")
        );
        // Nothing Windows-specific declared: the plain channel hint, as before.
        let bare = hinted_tool(WindowsInstall::default(), MacosInstall::default());
        assert_eq!(hint_for(&bare, "windows", false).as_deref(), Some("winget"));
        // Off Windows the block is ignored.
        assert_eq!(
            hint_for(&tool, "linux", false).as_deref(),
            Some("package_manager")
        );
        assert_eq!(hint_for(&tool, "macos", false).as_deref(), Some("brew"));
    }

    #[test]
    fn the_macos_hint_is_the_brew_command() {
        let formula = MacosInstall {
            brew: Some("ffmpeg".into()),
            ..MacosInstall::default()
        };
        let tool = hinted_tool(WindowsInstall::default(), formula);
        assert_eq!(
            hint_for(&tool, "macos", false).as_deref(),
            Some("brew install ffmpeg")
        );
        // A cask (LibreOffice is an .app, not a formula) needs `--cask`.
        let cask = MacosInstall {
            brew: Some("libreoffice".into()),
            cask: true,
            ..MacosInstall::default()
        };
        let tool = hinted_tool(WindowsInstall::default(), cask);
        assert_eq!(
            hint_for(&tool, "macos", false).as_deref(),
            Some("brew install --cask libreoffice")
        );
        // The macOS block never leaks into another OS's hint.
        assert_eq!(hint_for(&tool, "windows", false).as_deref(), Some("winget"));
    }

    #[test]
    fn install_folders_are_the_ones_declared_for_the_running_os() {
        let tool = hinted_tool(
            WindowsInstall {
                dirs: vec![r"%ProgramFiles%\LibreOffice\program".into()],
                ..WindowsInstall::default()
            },
            MacosInstall {
                dirs: vec!["/Applications/LibreOffice.app/Contents/MacOS".into()],
                ..MacosInstall::default()
            },
        );
        assert_eq!(
            tool.declared_dirs("windows"),
            [r"%ProgramFiles%\LibreOffice\program".to_string()]
        );
        assert_eq!(
            tool.declared_dirs("macos"),
            ["/Applications/LibreOffice.app/Contents/MacOS".to_string()]
        );
        assert!(tool.declared_dirs("linux").is_empty());
    }

    #[test]
    fn the_macos_block_parses() {
        let yaml = "\
dependencies:
  external_tools:
    - name: libreoffice
      commands: [soffice]
      macos:
        brew: libreoffice
        cask: true
        dirs: [/Applications/LibreOffice.app/Contents/MacOS]
";
        let tools = parse_external_tools(yaml);
        assert_eq!(tools[0].macos.brew.as_deref(), Some("libreoffice"));
        assert!(tools[0].macos.cask);
        assert_eq!(
            tools[0].macos.dirs,
            ["/Applications/LibreOffice.app/Contents/MacOS".to_string()]
        );
        // Absent block: no formula, not a cask, no folders.
        let bare = parse_external_tools(
            "\
dependencies:
  external_tools:
    - name: x
      commands: [x]
",
        );
        assert!(bare[0].macos.brew.is_none() && !bare[0].macos.cask);
    }

    #[test]
    fn resolve_declared_finds_the_entry_that_lists_the_command() {
        std::env::set_var("KNAIF_KNAIFTESTDECLB_BIN", "/opt/fake/declb");
        let yaml = "\
dependencies:
  external_tools:
    - name: decl
      commands: [knaiftestdecla, knaiftestdeclb]
";
        assert_eq!(
            resolve_declared_tool(yaml, "decl"),
            Some(PathBuf::from("/opt/fake/declb"))
        );
        assert_eq!(
            resolve_declared_command(yaml, "knaiftestdeclb"),
            Some(PathBuf::from("/opt/fake/declb"))
        );
        // An undeclared command still gets the override and PATH; this one is on neither.
        assert_eq!(
            resolve_declared_command(yaml, "knaif-no-such-cmd-xyz"),
            None
        );
        assert_eq!(resolve_declared_tool(yaml, "undeclared"), None);
        std::env::remove_var("KNAIF_KNAIFTESTDECLB_BIN");
    }

    #[test]
    fn natural_order_ignores_case() {
        use std::cmp::Ordering::*;
        // A capitalised older folder must not sort above a newer lower-case one.
        assert_eq!(natural_cmp("GS10.05.1", "gs9.56.1"), Greater);
        assert_eq!(natural_cmp("gs10.05.1", "GS9.56.1"), Greater);
    }

    #[test]
    fn the_literal_prefix_before_a_wildcard_is_kept_verbatim() {
        let root = scratch("prefix");
        let bin = root.join("tools").join("v2").join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        // Written with the platform's own separators, so any root form (drive, UNC, `\\?\`)
        // reaches read_dir untouched instead of being split and rebuilt.
        let prefix = root.join("tools");
        let pattern = format!(
            "{}{}v*{}bin",
            prefix.display(),
            std::path::MAIN_SEPARATOR,
            std::path::MAIN_SEPARATOR
        );
        assert_eq!(glob_dirs(&pattern), vec![prefix.join("v2").join("bin")]);
        // No wildcard at all: the folder itself, when it exists.
        assert_eq!(glob_dirs(&bin.to_string_lossy()), vec![bin.clone()]);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(windows)]
    #[test]
    fn an_extended_length_root_survives_the_glob() {
        let root = scratch("verbatim");
        std::fs::create_dir_all(root.join("gs").join("gs10.1").join("bin")).unwrap();
        let pattern = format!(r"\\?\{}\gs\gs*\bin", root.display());
        let dirs = glob_dirs(&pattern);
        assert_eq!(dirs.len(), 1, "{dirs:?}");
        assert!(dirs[0].to_string_lossy().starts_with(r"\\?\"), "{dirs:?}");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn a_non_executable_file_does_not_count() {
        use std::os::unix::fs::PermissionsExt;
        let root = scratch("noexec");
        let plain = fake_exe(&root.join("a"), "knaiftestnoexec");
        std::fs::set_permissions(&plain, std::fs::Permissions::from_mode(0o644)).unwrap();
        let runnable = fake_exe(&root.join("b"), "knaiftestnoexec");
        std::fs::set_permissions(&runnable, std::fs::Permissions::from_mode(0o755)).unwrap();
        // The first folder's copy shadows nothing: the launch would fail with EACCES.
        assert_eq!(
            find_in_dirs("knaiftestnoexec", &[root.join("a"), root.join("b")]),
            Some(runnable)
        );
        let _ = std::fs::remove_dir_all(&root);
    }
}
