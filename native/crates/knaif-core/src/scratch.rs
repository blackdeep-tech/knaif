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

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::OnceLock;
use std::time::{Duration, SystemTime};

use regex::Regex;
use serde_json::Value;

use crate::clarify_gate::{looks_like_filename, mention_count, FileKinds, TERMINAL_TOOLS};
use crate::registry::Produces;
use crate::sandbox::resolve_real;

/// How old a `run-*` folder must be before a new run may assume its owner is gone.
pub const STALE_AFTER_SECS: u64 = 24 * 3600;

/// `$KNAIF_SCRATCH_ROOT`, else `<temp>/knaif`; always absolute (Python `scratch_root`).
pub fn scratch_root() -> PathBuf {
    let cwd = std::env::current_dir().unwrap_or_default();
    scratch_root_from(
        std::env::var_os("KNAIF_SCRATCH_ROOT").filter(|v| !v.is_empty()),
        &cwd,
    )
}

/// [`scratch_root`] for a given setting: a relative root is joined to `cwd`. Left relative, a
/// run scratch path stops matching itself once a tool resolves it against the sandbox, and a
/// refined chain's results go with the scratch (2026-10-20).
fn scratch_root_from(value: Option<std::ffi::OsString>, cwd: &Path) -> PathBuf {
    match value {
        Some(root) if Path::new(&root).is_absolute() => PathBuf::from(root),
        Some(root) => cwd.join(root),
        None => std::env::temp_dir().join("knaif"),
    }
}

/// Whether the scratch is kept when the run ends (`KNAIF_DEBUG` or `KNAIF_KEEP_SCRATCH`).
pub fn keep_scratch() -> bool {
    ["KNAIF_DEBUG", "KNAIF_KEEP_SCRATCH"]
        .iter()
        .any(|k| std::env::var_os(k).is_some_and(|v| !v.is_empty()))
}

// ── keep every step's output (`--keep-intermediates` and its request words) ──────────────────

/// Python `knaif.scratch._KEEP_NEGATION`.
const KEEP_NEGATION: &str = r"\b(?:don['’]?t|do\s+not|never|no\s+need\s+to)\s+(?:keep|leave)\b";

/// Python `knaif.scratch._KEEP_PATTERNS`, the same patterns: a keep verb next to a scope
/// (intermediate files, everything, all the files, every step). `save` is never a keep verb.
const KEEP_PATTERNS: [&str; 9] = [
    // en
    concat!(
        r"\b(?:keep|leave)\s+(?:(?:all|of|the|my|these|those|any)\s+)*",
        r"(?:intermediate\w*|in-between\s+files?|everything)\b"
    ),
    concat!(
        r"\b(?:keep|leave)\s+(?:all\s+(?:of\s+)?(?:the\s+)?|every\s+|each\s+)",
        r"(?:files?|outputs?|results?|versions?|steps?)\b"
    ),
    r"\b(?:keep|leave)\s+(?:them|these|those)\s+all\b",
    // de: either order
    concat!(
        r"\b(?:behalt\w*|behält|beibehalten|aufbewahren|aufheben)\s+(?:\S+\s+){0,1}?",
        r"(?:zwischen\w*|alles|alle\s+dateien)\b"
    ),
    concat!(
        r"\b(?:zwischen\w*|alles|alle\s+dateien)\s+(?:\S+\s+){0,1}?",
        r"(?:behalt\w*|behält|beibehalten|aufbewahren|aufheben)\b"
    ),
    // es, fr
    concat!(
        r"\b(?:conserv\w*|manten\w*|mantén|deja\w*|gard\w*)\s+(?:\S+\s+){0,2}?",
        r"(?:intermedi\w*|interm[ée]diaires?|todo|tout|todos\s+los\s+archivos|",
        r"tous\s+les\s+fichiers)\b"
    ),
    // bg
    r"\b(?:запаз\w*|остав\w*)\s+(?:\S+\s+){0,1}?(?:междинн\w*|всичко)\b",
    // zh: written without spaces
    r"保留.{0,2}?(?:中间|所有|全部)",
    r"(?:全部|都|所有)保留",
];

/// True when the request asks to keep every step's output ("keep the intermediate files",
/// "keep everything", "保留中间文件"): the same as `--keep-intermediates`. A negated
/// English request keeps the default. Port of Python `knaif.scratch.wants_everything_kept`;
/// contract: `contracts/parity/keep_request_cases.json`.
pub fn wants_everything_kept(utterance: &str) -> bool {
    static RES: OnceLock<(Regex, Vec<Regex>)> = OnceLock::new();
    let (negation, patterns) = RES.get_or_init(|| {
        (
            Regex::new(KEEP_NEGATION).expect("keep negation pattern"),
            KEEP_PATTERNS
                .iter()
                .map(|p| Regex::new(p).expect("keep pattern"))
                .collect(),
        )
    });
    let lower = utterance.to_lowercase();
    let text = negation.replace_all(&lower, " ");
    patterns.iter().any(|p| p.is_match(&text))
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
/// Each tool's declared `produces:`, for the keep rule. Mirrors Python
/// `CommandAgent.route_kinds`.
pub fn produced_kinds(registry: &crate::registry::Registry) -> HashMap<String, Produces> {
    registry
        .iter()
        .filter_map(|(name, td)| td.produces.clone().map(|p| (name.clone(), p)))
        .collect()
}

fn extension_of(path: &str) -> String {
    let name = path.rsplit(['/', '\\']).next().unwrap_or(path);
    match name.rfind('.') {
        Some(dot) if dot > 0 => name[dot + 1..].to_lowercase(),
        _ => String::new(),
    }
}

/// The kind of file *step* writes: its tool's `produces:`, else its explicit output's.
/// Port of Python `knaif.agent._written_kind`.
///
/// The declaration is the skill's word and the output extension is the model's, so the
/// declaration decides when both exist (owner, 2026-10-15): `extract_audio` writing
/// `clip_audio.mp4` still writes audio. A `{arg: name}` declaration reads that argument;
/// without a usable value, and for a tool declaring nothing, the output's extension decides.
fn written_kind(
    step: &Value,
    file_kinds: &FileKinds,
    produces: &HashMap<String, Produces>,
) -> Option<String> {
    let args = step.get("args");
    let declared = step
        .get("tool")
        .and_then(Value::as_str)
        .and_then(|tool| produces.get(tool));
    match declared {
        Some(Produces::Kind(kind)) => return Some(kind.clone()),
        Some(Produces::Arg { arg }) => {
            let value = args
                .and_then(|a| a.get(arg))
                .and_then(Value::as_str)
                .map(|v| v.trim_start_matches('.').to_lowercase())
                .filter(|v| !v.trim().is_empty());
            if let Some(kind) = value.and_then(|v| file_kinds.get(v.as_str())) {
                return Some(kind.clone());
            }
        }
        // A fixed format is read by the result set only; the keep rule decides as before.
        Some(Produces::Format { .. }) | None => {}
    }
    let out = args.and_then(|a| a.get("output")).and_then(Value::as_str)?;
    let ext = extension_of(out);
    if ext.is_empty() {
        return None;
    }
    file_kinds.get(&ext).cloned()
}

/// The extension *step* writes, for the request signal: its explicit output's, else the value
/// of its `produces: {arg}` argument; `None` when unknown. Port of Python
/// `knaif.agent._written_extension`.
fn written_extension(step: &Value, produces: &HashMap<String, Produces>) -> Option<String> {
    let args = step.get("args");
    if let Some(out) = args.and_then(|a| a.get("output")).and_then(Value::as_str) {
        let ext = extension_of(out);
        if !ext.is_empty() {
            return Some(ext);
        }
    }
    let tool = step.get("tool").and_then(Value::as_str)?;
    match produces.get(tool)? {
        Produces::Arg { arg } => args?
            .get(arg)?
            .as_str()
            .map(|v| v.trim().trim_start_matches('.').to_lowercase())
            .filter(|v| !v.is_empty()),
        Produces::Kind(_) | Produces::Format { .. } => None,
    }
}

/// Extensions the request names as bare words: `to mp4`, `as .gif`, or Chinese without spaces
/// (`转换为mp4`); never the extension inside a filename (`clip.mp4`). Extensions are ASCII in
/// every language, so this needs no word lists. Port of Python `knaif.agent._named_formats`,
/// with the boundaries of [`mention_count`]; a leading dot counts when nothing name-like
/// (a letter, digit or `_` in any script) precedes it.
pub fn named_formats(utterance: &str, file_kinds: &FileKinds) -> HashSet<String> {
    file_kinds
        .keys()
        .map(|ext| ext.to_lowercase())
        .filter(|ext| format_mentions(utterance, ext) > 0)
        .collect()
}

/// How many times the request names the format `ext` as a word ([`named_formats`]). Port of
/// Python `knaif.agent._format_mentions`.
pub fn format_mentions(utterance: &str, ext: &str) -> usize {
    let u = utterance.to_lowercase();
    let e = ext.to_lowercase();
    if e.is_empty() {
        return 0;
    }
    let mut count = 0;
    let mut i = 0;
    while i < u.len() {
        if u[i..].starts_with(&e) {
            let prev = u[..i].chars().next_back();
            let bare_ok = prev.is_none_or(|c| !(is_word(c) || c == '.' || c == '-'));
            let dotted_ok = prev == Some('.')
                && u[..i - 1]
                    .chars()
                    .next_back()
                    .is_none_or(|c| !(c.is_alphanumeric() || c == '_'));
            let mut after = u[i + e.len()..].chars();
            let after_ok = match after.next() {
                Some(c) if is_word(c) || c == '-' => false,
                Some('.') => !after.next().is_some_and(is_word),
                _ => true,
            };
            if (bare_ok || dotted_ok) && after_ok {
                count += 1;
            }
        }
        i += u[i..].chars().next().map_or(1, char::len_utf8);
    }
    count
}

fn is_word(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// True when the user asked for *intermediate* although they never named the file. The keep
/// rule (owner, 2026-10-15): one rule, two deterministic signals, no tool named in core. Port of
/// Python `knaif.agent._asked_for`.
///
/// * **Tool signal:** every reader makes a *new thing* from it: a file of another kind
///   (`file_kinds`, what a reader writes per [`written_kind`]), or a tool in `derives`
///   (`tools.yaml` `derives: true`: a piece, a page range, a join). Not when every reader is
///   in `joining`: pieces made for a join are on the way (owner, 2026-10-20).
/// * **Request signal:** the user named its format as a bare word ([`named_formats`]), the
///   format is not the producer's own input extension, and no reader writes that format again.
///
/// Otherwise a reader refines it, and it goes to the scratch.
#[allow(clippy::too_many_arguments)]
fn asked_for(
    intermediate: &str,
    producer: &Value,
    readers: &[&Value],
    utterance: &str,
    file_kinds: &FileKinds,
    produces: &HashMap<String, Produces>,
    derives: &HashSet<String>,
    joining: &HashSet<String>,
) -> bool {
    let own_ext = extension_of(intermediate);
    let own_kind = file_kinds.get(&own_ext);
    let mut seen = HashSet::new();
    let readers: Vec<&Value> = readers
        .iter()
        .copied()
        .filter(|s| seen.insert(*s as *const Value)) // a step reading through two args counts once
        .collect();
    if readers.is_empty() {
        return false;
    }
    let makes_new_thing = |step: &Value| -> bool {
        if step
            .get("tool")
            .and_then(Value::as_str)
            .is_some_and(|t| derives.contains(t))
        {
            return true;
        }
        match (own_kind, written_kind(step, file_kinds, produces)) {
            (Some(own), Some(kind)) => &kind != own,
            _ => false,
        }
    };
    let only_joins = readers.iter().all(|s| {
        s.get("tool")
            .and_then(Value::as_str)
            .is_some_and(|t| joining.contains(t))
    });
    if !only_joins && readers.iter().all(|s| makes_new_thing(s)) {
        return true;
    }
    if own_ext.is_empty() || !named_formats(utterance, file_kinds).contains(&own_ext) {
        return false;
    }
    let names_the_source = producer
        .get("args")
        .and_then(Value::as_object)
        .is_some_and(|args| {
            args.iter()
                .filter(|(k, _)| *k != "output")
                .any(|(_, v)| match v {
                    Value::String(s) => extension_of(s) == own_ext,
                    Value::Array(items) => items
                        .iter()
                        .filter_map(Value::as_str)
                        .any(|s| extension_of(s) == own_ext),
                    _ => false,
                })
        });
    if names_the_source {
        return false;
    }
    readers
        .iter()
        .all(|s| written_extension(s, produces).is_some_and(|w| w != own_ext))
}

pub fn deriving_tools(registry: &crate::registry::Registry) -> HashSet<String> {
    registry
        .iter()
        .filter(|(_, td)| td.derives)
        .map(|(name, _)| name.clone())
        .collect()
}

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
/// nothing reads later (a deliverable); an output the user named in `utterance`; an output the
/// user asked for without naming it ([`asked_for`], the keep rule); everything, when there is no
/// utterance. Returns a new plan. Contract:
/// `contracts/parity/scratch_routing_cases.json`.
#[allow(clippy::too_many_arguments)]
pub fn route_intermediates_to_scratch(
    plan: &[Value],
    work_dir: &Path,
    utterance: Option<&str>,
    resolution_dir: &Path,
    joining_tools: &HashSet<String>,
    file_kinds: &FileKinds,
    produces: &HashMap<String, Produces>,
    derives: &HashSet<String>,
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
        // The keep rule: an intermediate the user asked for (every reader makes a new thing
        // from it, or the user named its format) stays where the plan put it.
        let reader_steps: Vec<&Value> = readers.iter().map(|(i, _, _)| &plan[*i]).collect();
        if asked_for(
            &out,
            &plan[idx],
            &reader_steps,
            utterance,
            file_kinds,
            produces,
            derives,
            joining_tools,
        ) {
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

    /// A relative `KNAIF_SCRATCH_ROOT` once lost a refined chain's results (Python side,
    /// in a run-order A/B, 2026-10-20): the root is made absolute against the working folder.
    #[test]
    fn a_relative_scratch_root_is_made_absolute() {
        let cwd = Path::new("/work/here");
        assert_eq!(
            scratch_root_from(Some("rel/scratch".into()), cwd),
            cwd.join("rel/scratch")
        );
        let abs = std::env::temp_dir().join("abs");
        assert_eq!(scratch_root_from(Some(abs.clone().into()), cwd), abs);
        assert_eq!(
            scratch_root_from(None, cwd),
            std::env::temp_dir().join("knaif")
        );
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
    fn named_formats_match_python_boundaries() {
        let kinds: FileKinds = [
            ("mp4", "video"),
            ("gif", "video"),
            ("mov", "video"),
            ("mp3", "audio"),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        let f = |u: &str| {
            let mut v: Vec<String> = named_formats(u, &kinds).into_iter().collect();
            v.sort();
            v
        };
        assert_eq!(
            f("convert clip.mov to mp4 and make a gif preview"),
            ["gif", "mp4"]
        );
        assert!(f("trim clip.mp4 to 5 seconds").is_empty());
        assert_eq!(f("save it as .gif"), ["gif"]);
        assert_eq!(f("将clip.mov转换为mp4并制作gif预览"), ["gif", "mp4"]);
        assert_eq!(f("конвертирай видео.mov в MP4"), ["mp4"]);
        assert!(f("an mp4-like file and a mp3_x").is_empty());
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
