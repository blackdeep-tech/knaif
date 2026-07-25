//! Skill discovery + metadata, read from the canonical bundle `skill.yaml`.
//!
//! The declarative YAML at a bundle's top is the single source of truth, readable by every
//! runtime — so the native CLI lists skills straight from `skills/<name>/skill.yaml` (the
//! same files the Python loader reads and the Phase 3 `runtimes:` metadata lives in), rather
//! than duplicating a list into Rust. Compile-time skill *selection* for a shipped binary is
//! a later concern (release manifest, Phase 9).

use std::path::{Path, PathBuf};

use serde::Deserialize;

/// Metadata surfaced for `knaif skills list`.
#[derive(Debug, Clone)]
pub struct SkillMeta {
    pub name: String,
    pub description: String,
    /// `status: stale` in skill.yaml (partial / under rebuild — hidden by default).
    pub stale: bool,
    /// `runtimes.native.status` (e.g. "supported"), if declared.
    pub native_status: Option<String>,
    /// `runtimes.native.crate` — the crate implementing the native skill.
    pub native_crate: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RawSkill {
    name: String,
    #[serde(default)]
    description: String,
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    runtimes: Option<RawRuntimes>,
}

#[derive(Debug, Deserialize)]
struct RawRuntimes {
    #[serde(default)]
    native: Option<RawNative>,
}

#[derive(Debug, Deserialize)]
struct RawNative {
    #[serde(default)]
    status: Option<String>,
    // `crate` is a Rust keyword; the YAML key stays `crate`.
    #[serde(rename = "crate", default)]
    krate: Option<String>,
}

/// Locate the `skills/` bundle directory.
///
/// Resolution order: `$KNAIF_SKILLS_ROOT` → a dev checkout (walk up from the current directory to
/// the first `skills/` dir holding a bundle, mirroring Python `_discover_skills_root`) → an
/// installed layout (`skills/` beside the executable, or one level up from a `bin/` dir). Note the
/// shipped binary still reads skill *data* (`skill.yaml`, `tools.yaml`, profiles, …) from this
/// on-disk bundle; only the handler *code* is compiled in.
pub fn resolve_skills_root() -> Option<PathBuf> {
    if let Ok(env) = std::env::var("KNAIF_SKILLS_ROOT") {
        if !env.is_empty() {
            return Some(PathBuf::from(env));
        }
    }
    // Dev checkout: walk up from the current dir for a `skills/` bundle dir.
    if let Ok(start) = std::env::current_dir() {
        for ancestor in start.ancestors() {
            let candidate = ancestor.join("skills");
            if contains_bundle(&candidate) {
                return Some(candidate);
            }
        }
    }
    // Installed layout: `skills/` ships beside the executable — resolve relative to it so a
    // packaged `knaif` run from any directory still finds its bundles.
    let exe = std::env::current_exe().ok()?;
    skills_root_near(exe.parent()?)
}

/// Find a `skills/` bundle dir relative to the executable's directory: beside it, or one level up
/// (the `<install>/bin/knaif` + `<install>/skills` layout). Returns the first that holds a bundle.
fn skills_root_near(exe_dir: &Path) -> Option<PathBuf> {
    let mut candidates = vec![exe_dir.join("skills")];
    if let Some(parent) = exe_dir.parent() {
        candidates.push(parent.join("skills"));
    }
    candidates.into_iter().find(|c| contains_bundle(c))
}

fn contains_bundle(dir: &Path) -> bool {
    if !dir.is_dir() {
        return false;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return false;
    };
    entries
        .flatten()
        .any(|e| e.path().join("skill.yaml").is_file())
}

/// Read every bundle's `skill.yaml` under `root`, sorted by directory name.
///
/// Skills with `status: stale` are excluded unless `include_stale` is set. Malformed or
/// unreadable manifests are skipped rather than aborting the listing.
pub fn list_skills(root: &Path, include_stale: bool) -> Vec<SkillMeta> {
    let mut dirs: Vec<PathBuf> = match std::fs::read_dir(root) {
        Ok(entries) => entries
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect(),
        Err(_) => return Vec::new(),
    };
    dirs.sort();

    let mut out = Vec::new();
    for dir in dirs {
        let manifest = dir.join("skill.yaml");
        let Ok(text) = std::fs::read_to_string(&manifest) else {
            continue;
        };
        let Ok(raw) = serde_yaml::from_str::<RawSkill>(&text) else {
            continue;
        };
        let stale = raw.status.as_deref() == Some("stale");
        if stale && !include_stale {
            continue;
        }
        let (native_status, native_crate) = match raw.runtimes.and_then(|r| r.native) {
            Some(n) => (n.status, n.krate),
            None => (None, None),
        };
        out.push(SkillMeta {
            name: raw.name,
            description: raw.description,
            stale,
            native_status,
            native_crate,
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn write_skill(root: &Path, name: &str, body: &str) {
        let dir = root.join(name);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("skill.yaml"), body).unwrap();
    }

    #[test]
    fn lists_active_skills_sorted_and_reads_native_metadata() {
        let tmp = std::env::temp_dir().join(format!("knaif_skills_{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        write_skill(
            &tmp,
            "ffmpeg",
            "name: ffmpeg\ndescription: media\nruntimes:\n  native:\n    status: supported\n    crate: knaif-skill-ffmpeg\n",
        );
        write_skill(&tmp, "io", "name: io\ndescription: files\nstatus: stale\n");

        let active = list_skills(&tmp, false);
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].name, "ffmpeg");
        assert_eq!(active[0].native_status.as_deref(), Some("supported"));
        assert_eq!(
            active[0].native_crate.as_deref(),
            Some("knaif-skill-ffmpeg")
        );

        let all = list_skills(&tmp, true);
        assert_eq!(all.len(), 2);
        assert!(all.iter().any(|s| s.name == "io" && s.stale));

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn skills_root_resolves_beside_and_above_exe() {
        // Simulate an installed layout: <root>/bin/knaif(.exe) with <root>/skills/<bundle>.
        let root = std::env::temp_dir().join(format!("knaif_near_{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let bin = root.join("bin");
        fs::create_dir_all(&bin).unwrap();
        write_skill(
            &root.join("skills"),
            "ffmpeg",
            "name: ffmpeg\ndescription: media\n",
        );

        // exe in <root>/bin → finds ../skills.
        assert_eq!(skills_root_near(&bin), Some(root.join("skills")));
        // Flat layout: exe beside skills/ → finds ./skills.
        assert_eq!(skills_root_near(&root), Some(root.join("skills")));
        // No bundle anywhere near → None.
        assert_eq!(skills_root_near(&bin.join("nested")), None);

        let _ = fs::remove_dir_all(&root);
    }
}
