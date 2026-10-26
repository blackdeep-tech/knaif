//! What a plan will leave: the result set every preview ends with. Port of Python
//! `knaif/result_set.py`; both read `contracts/parity/result_set_cases.json`.
//!
//! After linking, the per-file lift and scratch routing, the plan says which files reach the
//! user's folder; core computes them and the preview ends with them (owner, 2026-10-15): "leaves
//! beach_small.mp4", "leaves 5 mp4 and 5 mp3". A step's files: its explicit `output` (one per
//! input file it runs over, one for a join; a name only when it runs over one file and the output
//! is no pattern), else what its tool's `produces:` names (an `{arg}` value is an extension, a
//! kind is shown as a kind), else each input's own extension. Outputs in the run scratch, and
//! steps of readonly or control tools, leave nothing. Up to three files, all named, are listed by
//! name; otherwise counted by type, first seen first. A named file whose extension disagrees with
//! its tool's declaration is flagged. Pure: the caller expands globs.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::Path;

use serde_json::Value;

use crate::batch_chain::INPUT_KEYS;
use crate::registry::Produces;

/// Tools that steer the run and write nothing.
const CONTROL_TOOLS: [&str; 4] = ["clarify", "reject", "done", "wait_for_confirmation"];

/// One file a plan leaves: its name when the plan gives it, its type either way.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResultFile {
    pub tool: String,
    pub name: Option<String>,
    pub ext: Option<String>,
    pub kind: Option<String>,
    pub note: Option<String>,
}

impl ResultFile {
    /// `(singular, plural)` of its type: `mp4`, or `audio file(s)`, or `file(s)`.
    fn label(&self) -> (String, String) {
        if let Some(ext) = &self.ext {
            return (ext.clone(), ext.clone());
        }
        if let Some(kind) = &self.kind {
            return (format!("{kind} file"), format!("{kind} files"));
        }
        ("file".into(), "files".into())
    }
}

fn norm(path: &str) -> String {
    path.replace('\\', "/")
}

fn ext_of(path: &str) -> String {
    let p = norm(path);
    let base = p.rsplit('/').next().unwrap_or("");
    match base.rfind('.') {
        Some(dot) if base[..dot].chars().any(|c| c != '.') => base[dot + 1..].to_lowercase(),
        _ => String::new(),
    }
}

/// `path` relative to `base` when inside it, `/`-separated.
fn shown(path: &str, base: &Path) -> String {
    let p = Path::new(path);
    if p.is_absolute() {
        return match p.strip_prefix(base) {
            Ok(rel) => norm(&rel.to_string_lossy()),
            Err(_) => norm(path),
        };
    }
    let n = norm(path);
    n.strip_prefix("./").map(str::to_string).unwrap_or(n)
}

fn in_scratch(path: &str, scratch: &str) -> bool {
    let s = norm(scratch);
    let s = s.trim_end_matches('/');
    norm(path)
        .to_lowercase()
        .starts_with(&format!("{}/", s.to_lowercase()))
}

fn some(s: String) -> Option<String> {
    (!s.is_empty()).then_some(s)
}

/// Every file `plan` leaves, in plan order (rules in the module docs). `sets` maps the position
/// of a step that writes many files from one to the files it will write: they are its results,
/// by name, unless they are in the run scratch.
#[allow(clippy::too_many_arguments)]
pub fn result_set(
    plan: &[Value],
    scratch: &str,
    base: &Path,
    expand: &HashMap<String, Vec<String>>,
    readonly: &HashSet<String>,
    joining: &HashSet<String>,
    produces: &HashMap<String, Produces>,
    file_kinds: &HashMap<String, String>,
    sets: &BTreeMap<usize, Vec<String>>,
) -> Vec<ResultFile> {
    let kind_of = |ext: &str| file_kinds.get(ext).cloned();
    let mut results = Vec::new();
    for (position, step) in plan.iter().enumerate() {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let null = Value::Null;
        let args = step.get("args").unwrap_or(&null);
        let output = args
            .get("output")
            .and_then(Value::as_str)
            .filter(|o| !o.is_empty());
        // A readonly tool leaves nothing, unless core gave it an output to save to (a tool's
        // `batch_output:`, e.g. each PDF's text in a batch).
        if CONTROL_TOOLS.contains(&tool) || (readonly.contains(tool) && output.is_none()) {
            continue;
        }
        let mut inputs: Vec<String> = Vec::new();
        for key in INPUT_KEYS {
            let entries: Vec<&str> = match args.get(key) {
                Some(Value::String(s)) => vec![s.as_str()],
                Some(Value::Array(items)) => items.iter().filter_map(Value::as_str).collect(),
                _ => Vec::new(),
            };
            for entry in entries {
                match expand.get(entry) {
                    Some(files) => inputs.extend(files.iter().cloned()),
                    None => inputs.push(entry.to_string()),
                }
            }
        }
        let n = if joining.contains(tool) {
            1
        } else {
            inputs.len()
        };
        let declared = produces.get(tool);
        let declared_ext = match declared {
            Some(Produces::Arg { arg }) => args
                .get(arg)
                .and_then(Value::as_str)
                .map(|v| v.trim().trim_start_matches('.').to_lowercase())
                .filter(|v| !v.is_empty()),
            _ => None,
        };
        if let Some(planned) = sets.get(&position) {
            results.extend(planned.iter().filter(|f| !in_scratch(f, scratch)).map(|f| {
                let ext = ext_of(f);
                ResultFile {
                    tool: tool.into(),
                    name: Some(shown(f, base)),
                    kind: kind_of(&ext),
                    ext: some(ext),
                    note: None,
                }
            }));
            continue;
        }
        if let Some(output) = output {
            if in_scratch(output, scratch) {
                continue;
            }
            let ext = ext_of(output);
            let kind = kind_of(&ext);
            let note = match (declared, &declared_ext) {
                (Some(Produces::Kind(k)), _) if !ext.is_empty() && kind.as_ref() != Some(k) => {
                    Some(format!("named .{ext}, but {tool} writes {k}"))
                }
                (_, Some(d)) if !ext.is_empty() && &ext != d => {
                    Some(format!("named .{ext}, but {tool} writes {d}"))
                }
                _ => None,
            };
            let name = (n == 1 && !output.contains('*')).then(|| shown(output, base));
            let ext = some(ext);
            results.extend((0..n).map(|_| ResultFile {
                tool: tool.into(),
                name: name.clone(),
                ext: ext.clone(),
                kind: kind.clone(),
                note: note.clone(),
            }));
        } else if let Some(d) = &declared_ext {
            results.extend((0..n).map(|_| ResultFile {
                tool: tool.into(),
                name: None,
                ext: Some(d.clone()),
                kind: kind_of(d),
                note: None,
            }));
        } else if let Some(Produces::Kind(k)) = declared {
            results.extend((0..n).map(|_| ResultFile {
                tool: tool.into(),
                name: None,
                ext: None,
                kind: Some(k.clone()),
                note: None,
            }));
        } else {
            let sources: Vec<&String> = if joining.contains(tool) {
                inputs.iter().take(1).collect()
            } else {
                inputs.iter().collect()
            };
            results.extend(sources.into_iter().map(|i| {
                let ext = ext_of(i);
                ResultFile {
                    tool: tool.into(),
                    name: None,
                    kind: kind_of(&ext),
                    ext: some(ext),
                    note: None,
                }
            }));
        }
    }
    results
}

/// `a`, `a and b`, `a, b and c`.
fn join(parts: &[String]) -> String {
    match parts {
        [] => String::new(),
        [one] => one.clone(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

/// `leaves beach_small.mp4` / `leaves 5 mp4 and 5 mp3`; empty when nothing is left.
pub fn leaves_line(results: &[ResultFile]) -> String {
    if results.is_empty() {
        return String::new();
    }
    if results.len() <= 3 && results.iter().all(|r| r.name.is_some()) {
        let named: Vec<String> = results
            .iter()
            .map(|r| {
                let name = r.name.clone().unwrap_or_default();
                match &r.note {
                    Some(note) => format!("{name} ({note})"),
                    None => name,
                }
            })
            .collect();
        return format!("leaves {}", join(&named));
    }
    let mut counts: Vec<((String, String), usize)> = Vec::new();
    for r in results {
        let label = r.label();
        match counts.iter_mut().find(|(l, _)| *l == label) {
            Some((_, n)) => *n += 1,
            None => counts.push((label, 1)),
        }
    }
    let parts: Vec<String> = counts
        .iter()
        .map(|((one, many), n)| format!("{n} {}", if *n == 1 { one } else { many }))
        .collect();
    let notes: String = results
        .iter()
        .filter_map(|r| match (&r.name, &r.note) {
            (Some(name), Some(note)) => Some(format!("; {name} is {note}")),
            _ => None,
        })
        .collect();
    format!("leaves {}{notes}", join(&parts))
}

/// The batch preview after the first file's steps: `the same for 4 more files: …`.
pub fn same_for_line(rest: &[String]) -> String {
    let names: Vec<String> = rest.iter().map(|f| norm(f)).collect();
    let shown: Vec<String> = if names.len() <= 5 {
        names.clone()
    } else {
        names
            .iter()
            .take(3)
            .cloned()
            .chain(std::iter::once("…".to_string()))
            .collect()
    };
    let noun = if names.len() == 1 { "file" } else { "files" };
    format!(
        "the same for {} more {noun}: {}",
        names.len(),
        shown.join(", ")
    )
}
