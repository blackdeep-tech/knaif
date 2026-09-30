//! Which external binary a skill launches — the same lookup `knaif skills deps` reports.
//!
//! A skill passes its own bundle's `skill.yaml` (embedded at compile time with `include_str!`,
//! so no call site needs the bundle path) and gets back `$KNAIF_<CMD>_BIN`, else the `PATH`
//! hit, else — on Windows — the first declared install folder holding the command. Launching a
//! bare name instead would let `skills deps` say OK for a tool the run then cannot start.
//!
//! Known limit (1.2.0): `skills deps` and the run preflight read the bundle on disk, the launch
//! reads the copy compiled in. They are the same file as shipped; editing an installed bundle's
//! `windows.dirs`, or pointing `KNAIF_SKILLS_ROOT` at a different tree, can make them disagree.
//! Passing the bundle path to every launch site removes that (1.2.1).

use std::path::PathBuf;

/// The binary for one command (e.g. `ffprobe`), or the bare name when nothing resolves, so the
/// launch fails with the usual not-found error and its install message.
pub fn command_bin(skill_yaml: &str, cmd: &str) -> PathBuf {
    knaif_core::resolve_declared_command(skill_yaml, cmd).unwrap_or_else(|| PathBuf::from(cmd))
}

/// The binary for a tool declared with alias commands (e.g. `ghostscript`: gs / gswin64c), or
/// `None` when none of them resolves.
pub fn tool_bin(skill_yaml: &str, tool_name: &str) -> Option<PathBuf> {
    knaif_core::resolve_declared_tool(skill_yaml, tool_name)
}
