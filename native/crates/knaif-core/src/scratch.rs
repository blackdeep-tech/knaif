//! The per-run scratch folder, and routing chain intermediates into it.
//!
//! Port of Python `knaif/scratch.py` and `CommandAgent._route_intermediates_to_scratch`. Every
//! run that executes gets a fresh `<temp>/knaif/run-<pid>-<hex>/`; a chain's intermediate, a
//! preview clip, and an output awaiting verification go there, so the user's folder only ever
//! receives the outputs they asked for.
//!
//! Lifetime rules (identical in both runtimes):
//! * **removed when the run ends**, on success and on failure (the [`RunScratch`] guard's `Drop`);
//! * **kept under `KNAIF_DEBUG` or `KNAIF_KEEP_SCRATCH`** (the eval lane grades intermediates);
//! * **a dry run creates nothing**: the path still exists as a name commands can render;
//! * **stale `run-*` folders are swept at start**, once older than [`STALE_AFTER_SECS`] (a run
//!   killed hard cannot remove its own; a newer one may belong to another live process).
//!
//! `KNAIF_SCRATCH_ROOT` overrides the root.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime};

use serde_json::Value;

use crate::clarify_gate::{looks_like_filename, mention_count, TERMINAL_TOOLS};
use crate::sandbox::resolve_real;

/// How old a `run-*` folder must be before a new run may assume its owner is gone.
pub const STALE_AFTER_SECS: u64 = 24 * 3600;

/// `$KNAIF_SCRATCH_ROOT`, else `<temp>/knaif`.
pub fn scratch_root() -> PathBuf {
    match std::env::var_os("KNAIF_SCRATCH_ROOT").filter(|v| !v.is_empty()) {
        Some(root) => PathBuf::from(root),
        None => std::env::temp_dir().join("knaif"),
    }
}

/// Whether the scratch is kept when the run ends (`KNAIF_DEBUG` or `KNAIF_KEEP_SCRATCH`).
pub fn keep_scratch() -> bool {
    ["KNAIF_DEBUG", "KNAIF_KEEP_SCRATCH"]
        .iter()
        .any(|k| std::env::var_os(k).is_some_and(|v| !v.is_empty()))
}

/// Remove `run-*` folders under `root` last modified before `now - STALE_AFTER_SECS`.
pub fn sweep_stale(root: &Path, now: SystemTime) -> Vec<PathBuf> {
    let Some(cutoff) = now.checked_sub(Duration::from_secs(STALE_AFTER_SECS)) else {
        return Vec::new();
    };
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut removed = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let is_run = path
            .file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|n| n.starts_with("run-"));
        let old = entry
            .metadata()
            .and_then(|m| m.modified())
            .is_ok_and(|t| t < cutoff);
        if is_run && path.is_dir() && old && std::fs::remove_dir_all(&path).is_ok() {
            removed.push(path);
        }
    }
    removed
}

/// The skill's `scratch_intermediates:` (absent = false). The same key Python reads
/// (`knaif.skill.Skill.load`), so both runtimes route the same skills. A non-boolean is an error.
pub fn load_scratch_intermediates(skill_yaml: &Path) -> anyhow::Result<bool> {
    let text = std::fs::read_to_string(skill_yaml)?;
    let doc: serde_yaml::Value = serde_yaml::from_str(&text)?;
    match doc.get("scratch_intermediates") {
        None | Some(serde_yaml::Value::Null) => Ok(false),
        Some(serde_yaml::Value::Bool(b)) => Ok(*b),
        Some(other) => anyhow::bail!("scratch_intermediates must be true or false, got {other:?}"),
    }
}

/// One run's scratch folder. The path is fixed at construction, so a plan can be rewritten to
/// name it before anything executes; [`RunScratch::enter`] creates it (unless dry run) and the
/// guard's `Drop` removes it whatever happened (unless kept).
#[derive(Debug)]
pub struct RunScratch {
    path: PathBuf,
    dry_run: bool,
    entered: bool,
}

impl RunScratch {
    /// A run folder under [`scratch_root`].
    pub fn new(dry_run: bool) -> Self {
        Self::in_root(&scratch_root(), dry_run)
    }

    /// A run folder under `root`.
    pub fn in_root(root: &Path, dry_run: bool) -> Self {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let nanos = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        let tag = (u64::from(nanos) ^ (n << 32)) & 0xffff_ffff;
        Self {
            path: root.join(format!("run-{}-{tag:08x}", std::process::id())),
            dry_run,
            entered: false,
        }
    }

    /// Sweep stale folders and create this run's, unless this is a dry run.
    pub fn enter(&mut self) -> std::io::Result<()> {
        if !self.dry_run {
            if let Some(root) = self.path.parent() {
                sweep_stale(root, SystemTime::now());
            }
            std::fs::create_dir_all(&self.path)?;
            self.entered = true;
        }
        Ok(())
    }

    /// This run's folder (a name only, in a dry run).
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Is `p` inside this run's folder?
    pub fn contains(&self, p: &Path) -> bool {
        let cwd = std::env::current_dir().unwrap_or_default();
        resolve_real(p, &cwd).starts_with(resolve_real(&self.path, &cwd))
    }
}

impl Drop for RunScratch {
    fn drop(&mut self) {
        if self.entered && !keep_scratch() {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}

/// Case-folded on Windows, where paths ignore case (Python `os.path.normcase`).
fn path_key(p: &Path) -> String {
    let s = p.to_string_lossy().into_owned();
    if cfg!(windows) {
        s.replace('/', "\\").to_lowercase()
    } else {
        s
    }
}

/// The tools whose several inputs become ONE output (`joins_inputs: true` in `tools.yaml`).
pub fn joining_tools(registry: &crate::registry::Registry) -> HashSet<String> {
    registry
        .iter()
        .filter(|(_, td)| td.joins_inputs)
        .map(|(name, _)| name.clone())
        .collect()
}

/// Does this step's `output` name a batch rather than one file? A glob always fans out; several
/// input files do unless the tool joins them (`joining`). Port of Python's routing rule.
fn fans_out(step: &Value, joining: &HashSet<String>) -> bool {
    let Some(args) = step.get("args").and_then(Value::as_object) else {
        return false;
    };
    let is_glob = |s: &str| s.contains('*') || s.contains('?');
    let mut files: Vec<&str> = Vec::new();
    for (k, v) in args {
        if k == "output" {
            continue;
        }
        let values: Vec<&str> = match v {
            Value::String(s) => vec![s.as_str()],
            Value::Array(items) => items.iter().filter_map(Value::as_str).collect(),
            _ => Vec::new(),
        };
        files.extend(
            values
                .into_iter()
                .filter(|s| looks_like_filename(s) || is_glob(s)),
        );
    }
    if files.iter().any(|f| is_glob(f)) {
        return true;
    }
    let joins = step
        .get("tool")
        .and_then(Value::as_str)
        .is_some_and(|t| joining.contains(t));
    files.len() > 1 && !joins
}

fn is_ref(value: &str) -> bool {
    !value.is_empty() && !value.starts_with('$')
}

/// Move each chain intermediate into `work_dir`, with every later step that reads it.
///
/// An intermediate is a step's `output` that a later step reads. It keeps its name and its place
/// relative to the user's folder (`videos/x.mp4` -> `work_dir/videos/x.mp4`), so every rendered
/// argv still compares equal by basename across runtimes. Left where the plan put them: an output
/// nothing reads later (a deliverable); an output the user named in `utterance`; everything,
/// when there is no utterance. Returns a new plan. Contract:
/// `contracts/parity/scratch_routing_cases.json`.
pub fn route_intermediates_to_scratch(
    plan: &[Value],
    work_dir: &Path,
    utterance: Option<&str>,
    resolution_dir: &Path,
    joining_tools: &HashSet<String>,
) -> Vec<Value> {
    let mut plan = plan.to_vec();
    let Some(utterance) = utterance else {
        return plan;
    };
    let key = |value: &str| path_key(&resolve_real(Path::new(value), resolution_dir));
    let cwd = std::env::current_dir().unwrap_or_default();
    let base = resolve_real(resolution_dir, &cwd);
    let is_terminal = |step: &Value| {
        step.get("tool")
            .and_then(Value::as_str)
            .is_some_and(|t| TERMINAL_TOOLS.contains(&t))
    };

    for idx in 0..plan.len() {
        if is_terminal(&plan[idx]) {
            continue;
        }
        let Some(out) = plan[idx]["args"]
            .get("output")
            .and_then(Value::as_str)
            .map(str::to_string)
        else {
            continue;
        };
        if !is_ref(&out) || !looks_like_filename(&out) {
            continue;
        }
        let name = out.rsplit(['/', '\\']).next().unwrap_or(&out).to_string();
        if mention_count(utterance, &name) > 0 {
            continue;
        }
        // A producer that fans out writes one file per input under this one name, and only one
        // of them can be what a later step reads: the rest are deliverables (ffmpeg_134#2,
        // ffmpeg_114#2). A joining tool's several inputs make one file (concat, ffmpeg_243#0).
        if fans_out(&plan[idx], joining_tools) {
            continue;
        }
        let target = key(&out);

        // (step index, arg name, list position) of every later reader.
        let mut readers: Vec<(usize, String, Option<usize>)> = Vec::new();
        for (later_idx, later) in plan.iter().enumerate().skip(idx + 1) {
            if is_terminal(later) {
                continue;
            }
            let Some(args) = later.get("args").and_then(Value::as_object) else {
                continue;
            };
            for (k, v) in args {
                if k == "output" {
                    continue;
                }
                match v {
                    Value::String(s) if is_ref(s) && key(s) == target => {
                        readers.push((later_idx, k.clone(), None));
                    }
                    Value::Array(items) => {
                        for (i, item) in items.iter().enumerate() {
                            if item.as_str().is_some_and(|s| is_ref(s) && key(s) == target) {
                                readers.push((later_idx, k.clone(), Some(i)));
                            }
                        }
                    }
                    _ => {}
                }
            }
            // A later step writing the same file again ends this intermediate's readers.
            if args
                .get("output")
                .and_then(Value::as_str)
                .is_some_and(|o| is_ref(o) && key(o) == target)
            {
                break;
            }
        }
        if readers.is_empty() {
            continue;
        }

        let resolved = resolve_real(Path::new(&out), resolution_dir);
        let moved = match resolved.strip_prefix(&base) {
            Ok(rel) => work_dir.join(rel),
            Err(_) => work_dir.join(&name),
        };
        let moved = Value::String(moved.to_string_lossy().into_owned());
        plan[idx]["args"]["output"] = moved.clone();
        for (later_idx, k, pos) in readers {
            match pos {
                None => plan[later_idx]["args"][&k] = moved.clone(),
                Some(i) => plan[later_idx]["args"][&k][i] = moved.clone(),
            }
        }
    }
    plan
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("knaif-scratch-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn a_run_folder_exists_while_held_and_is_removed_after() {
        let root = tmp("lifecycle");
        let path = {
            let mut s = RunScratch::in_root(&root, false);
            s.enter().unwrap();
            assert!(s.path().is_dir());
            assert!(s
                .path()
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("run-"));
            s.path().to_path_buf()
        };
        if !keep_scratch() {
            assert!(!path.exists());
        }
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_dry_run_creates_nothing() {
        let root = tmp("dry").join("never");
        let mut s = RunScratch::in_root(&root, true);
        s.enter().unwrap();
        assert!(!s.path().exists());
        assert!(!root.exists());
    }

    #[test]
    fn two_runs_never_share_a_folder() {
        let root = tmp("unique");
        let a = RunScratch::in_root(&root, true);
        let b = RunScratch::in_root(&root, true);
        assert_ne!(a.path(), b.path());
    }

    #[test]
    fn only_old_run_folders_are_swept() {
        let root = tmp("sweep");
        for d in ["run-old", "run-new", "not-a-run"] {
            std::fs::create_dir_all(root.join(d)).unwrap();
        }
        let later = SystemTime::now() + Duration::from_secs(STALE_AFTER_SECS + 60);
        // Everything is "old" a day from now; only run-* folders may go.
        let removed = sweep_stale(&root, later);
        assert_eq!(removed.len(), 2);
        assert!(root.join("not-a-run").exists());
        // Nothing is old right now.
        std::fs::create_dir_all(root.join("run-fresh")).unwrap();
        assert!(sweep_stale(&root, SystemTime::now()).is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn concat_declares_that_it_joins_its_inputs() {
        let tools = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg/tools.yaml");
        let registry = crate::registry::load_registry(&tools).unwrap();
        assert!(registry["concat_video"].joins_inputs);
        assert!(!registry["extract_audio"].joins_inputs);
        assert_eq!(
            joining_tools(&registry),
            ["concat_video".to_string()].into()
        );
    }

    #[test]
    fn the_opt_in_is_read_from_skill_yaml() {
        let root = tmp("optin");
        let yes = root.join("yes.yaml");
        let no = root.join("no.yaml");
        std::fs::write(&yes, "name: x\nscratch_intermediates: true\n").unwrap();
        std::fs::write(&no, "name: x\n").unwrap();
        assert!(load_scratch_intermediates(&yes).unwrap());
        assert!(!load_scratch_intermediates(&no).unwrap());
        std::fs::write(&no, "name: x\nscratch_intermediates: maybe\n").unwrap();
        assert!(
            load_scratch_intermediates(&no).is_err(),
            "a non-boolean is an error, as in Python"
        );
        let ffmpeg =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg/skill.yaml");
        assert!(load_scratch_intermediates(&ffmpeg).unwrap());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn contains_tells_scratch_paths_from_others() {
        let root = tmp("contains");
        let mut s = RunScratch::in_root(&root, false);
        s.enter().unwrap();
        assert!(s.contains(&s.path().join("a/b.mp4")));
        assert!(!s.contains(&root.join("elsewhere.mp4")));
        drop(s);
        let _ = std::fs::remove_dir_all(&root);
    }
}
