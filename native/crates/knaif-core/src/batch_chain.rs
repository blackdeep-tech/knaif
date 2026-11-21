//! Run a chain once per file.
//!
//! Port of Python `knaif/batch_chain.py`. The model writes the steps for one file, with the batch
//! glob as the first step's input, and core repeats them for every file the glob matched (owner,
//! 2026-10-14).
//!
//! * **The head** is the first step, other than a join, with an input the caller expanded to
//!   files.
//! * **The chain** is every later step that reads a chain step's output: by its name (the
//!   per-file form, `clip.mp4`) or by its pattern (`rotated/*.mkv`). Core accepts both links. A
//!   step nothing links to keeps its place, untouched.
//! * **A join** (`concat_video`, `merge_pdfs`) reading a chain output reads every file's, in input
//!   order (fan-in), once, at its own place in the plan.
//! * **Names** (owner, 2026-10-15): the input's name with the extension the step writes, next to
//!   the input (`beach.mov` → `beach.mp4`); the model's label (`clip`) never reaches a name. A
//!   pattern's `*` is the input's name, in the pattern's folder when it has one. On a clash with a
//!   batch input or an earlier output of the lift, the label is added (`dog_trimmed.mp4`), then a
//!   counter. Names are chosen file by file whatever the order, so both orders write the same
//!   files.
//! * **Order**: each file goes through the whole chain before the next. It won the run-order
//!   A/B against step by step (2026-10-20): the same files and failures, the first file
//!   finished 25-72% sooner on 4 of 5 batches.
//!
//! A plan with no head comes back unchanged, and so does one whose head nothing reads when that
//! head takes its files in `inputs`; a lone head on `input` (one path) is repeated per file.
//! Pure: no disk, no registry; the caller expands the globs. Both runtimes read
//! `contracts/parity/batch_chain_cases.json` in their tests.

use std::collections::{HashMap, HashSet};

use serde_json::Value;

/// The args a step reads its files from: `input` (one path) or `inputs` (a list).
pub const INPUT_KEYS: [&str; 2] = ["input", "inputs"];

/// A path as a link compares it: forward slashes, no leading `./`, any case.
fn norm(path: &str) -> String {
    let mut p = path.replace('\\', "/");
    while let Some(rest) = p.strip_prefix("./") {
        p = rest.to_string();
    }
    p.to_lowercase()
}

/// `(key, paths)` for each input arg the step carries, a lone string as a one-item list.
fn entries(args: &Value) -> Vec<(&'static str, Vec<String>)> {
    let mut out = Vec::new();
    for key in INPUT_KEYS {
        match args.get(key) {
            Some(Value::String(s)) => out.push((key, vec![s.clone()])),
            Some(Value::Array(items)) => out.push((
                key,
                items
                    .iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect(),
            )),
            _ => {}
        }
    }
    out
}

/// The files a head step runs over, in input order, each once; empty when it is no head.
fn batch_files(args: &Value, expand: &HashMap<String, Vec<String>>) -> Vec<String> {
    let all: Vec<String> = entries(args).into_iter().flat_map(|(_, p)| p).collect();
    if !all
        .iter()
        .any(|p| expand.get(p).is_some_and(|f| !f.is_empty()))
    {
        return Vec::new();
    }
    let mut files = Vec::new();
    let mut seen = HashSet::new();
    for entry in &all {
        let found: Vec<String> = match expand.get(entry) {
            Some(f) => f.clone(),
            None => vec![entry.clone()],
        };
        for path in found {
            if seen.insert(norm(&path)) {
                files.push(path);
            }
        }
    }
    files
}

/// Python `posixpath.split`: `(folder, name)`.
fn split(path: &str) -> (String, String) {
    match path.rfind('/') {
        Some(i) => {
            let head = path[..i].trim_end_matches('/');
            let head = if head.is_empty() && path.starts_with('/') {
                "/"
            } else {
                head
            };
            (head.to_string(), path[i + 1..].to_string())
        }
        None => (String::new(), path.to_string()),
    }
}

/// Python `posixpath.splitext` on a name: `(stem, ext)`; leading dots are never an extension.
fn splitext(name: &str) -> (String, String) {
    if let Some(dot) = name.rfind('.') {
        if name[..dot].chars().any(|c| c != '.') {
            return (name[..dot].to_string(), name[dot..].to_string());
        }
    }
    (name.to_string(), String::new())
}

/// Python `posixpath.join` of two parts.
fn join(folder: &str, name: &str) -> String {
    if folder.is_empty() || name.starts_with('/') {
        name.to_string()
    } else if folder.ends_with('/') {
        format!("{folder}{name}")
    } else {
        format!("{folder}/{name}")
    }
}

/// The name `model_output` gets for `batch_file` (rules in the module docs).
fn output_name(model_output: &str, batch_file: &str, taken: &mut HashSet<String>) -> String {
    let (mut folder, base) = split(&batch_file.replace('\\', "/"));
    let (mut stem, file_ext) = splitext(&base);
    let (model_folder, model_base) = split(&model_output.replace('\\', "/"));
    let (model_stem, mut ext) = splitext(&model_base);
    let label;
    let mut candidate;
    if model_output.contains('*') {
        let name = model_base.replace('*', &stem);
        if !model_folder.is_empty() {
            folder = model_folder;
        }
        candidate = join(&folder, &name);
        (stem, ext) = splitext(&name);
        label = model_stem
            .replace('*', "")
            .trim_matches(|c| "_-. ".contains(c))
            .to_string();
    } else {
        if ext.is_empty() {
            ext = file_ext;
        }
        candidate = join(&folder, &format!("{stem}{ext}"));
        label = model_stem;
    }
    if taken.contains(&norm(&candidate)) && !label.is_empty() {
        candidate = join(&folder, &format!("{stem}_{label}{ext}"));
    }
    let base_stem = splitext(&split(&candidate).1).0;
    let mut n = 2;
    while taken.contains(&norm(&candidate)) {
        candidate = join(&folder, &format!("{base_stem}_{n}{ext}"));
        n += 1;
    }
    taken.insert(norm(&candidate));
    candidate
}

fn output_of(step: &Value) -> Option<&str> {
    step.get("args")
        .and_then(|a| a.get("output"))
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
}

fn tool_of(step: &Value) -> &str {
    step.get("tool").and_then(Value::as_str).unwrap_or("")
}

/// Whether `step` takes its file through `input` (one path). Such a tool cannot expand a glob, so
/// a lone batch step on it is repeated by core too; a glob in `inputs` (a list) stays the skill's
/// own batch. A list there is a batch of globs ([`widen_kind_globs`]), one file each once
/// lifted.
fn reads_one_file(step: &Value) -> bool {
    step.get("args")
        .and_then(|a| a.get("input"))
        .is_some_and(|v| v.is_string() || v.is_array())
}

/// A lifted plan and, for each of its steps, the batch file it works on. Python
/// `knaif.batch_chain.BatchLift`.
///
/// `file_of[k]` is the index into `files` of step `k`'s file, `None` for a step outside the chain
/// or a join. `join_files[k]` maps each input arg of join step `k` to the file each of its entries
/// came from (`None` for an entry the model named), so a file that fails can be left out of the
/// join. `shown` is each file as the user knows it (its place in their folder), for reports.
#[derive(Debug, Clone, Default)]
pub struct BatchLift {
    pub plan: Vec<Value>,
    pub file_of: Vec<Option<usize>>,
    pub files: Vec<String>,
    pub join_files: std::collections::BTreeMap<usize, HashMap<String, Vec<Option<usize>>>>,
    pub shown: Vec<String>,
}

impl BatchLift {
    /// Whether anything was lifted.
    pub fn lifted(&self) -> bool {
        !self.files.is_empty()
    }
}

/// `plan` with its batch chain repeated for every file. `expand` maps each glob (or folder) the
/// caller resolved to its files, in input order; `joining_tools` read many files into one result;
/// `batch_outputs` maps a tool that writes a file only in a batch (`batch_output:`) to that
/// file's extension, so a lifted step of it with no output of its own writes
/// `<batch file's name>.<ext>` next to the file.
pub fn lift_per_file_chain(
    plan: &[Value],
    expand: &HashMap<String, Vec<String>>,
    joining_tools: &HashSet<String>,
    batch_outputs: &HashMap<String, String>,
) -> Vec<Value> {
    lift_batch(
        plan,
        expand,
        joining_tools,
        &HashMap::new(),
        false,
        batch_outputs,
    )
    .plan
}

/// [`lift_per_file_chain`], keeping which file every lifted step works on. `home` maps a batch
/// file that lives in the run scratch (a piece of a split routed there) to its place in the user's
/// folder: its results are named from that place, and reports show it. `single` lifts a head that
/// nothing else reads too: the pieces of a split exist only as a set core planned, so no skill
/// could expand them. Python `knaif.batch_chain.lift_batch`.
pub fn lift_batch(
    plan: &[Value],
    expand: &HashMap<String, Vec<String>>,
    joining_tools: &HashSet<String>,
    home: &HashMap<String, String>,
    single: bool,
    batch_outputs: &HashMap<String, String>,
) -> BatchLift {
    let steps: Vec<Value> = plan.to_vec();
    let null = Value::Null;
    let args_of = |i: usize| steps[i].get("args").unwrap_or(&null);
    let unlifted = |steps: Vec<Value>| BatchLift {
        plan: steps,
        ..BatchLift::default()
    };

    let mut head = None;
    let mut files = Vec::new();
    for (i, step) in steps.iter().enumerate() {
        if joining_tools.contains(tool_of(step)) {
            continue;
        }
        files = batch_files(args_of(i), expand);
        if !files.is_empty() {
            head = Some(i);
            break;
        }
    }
    let Some(head) = head else {
        return unlifted(steps);
    };

    // Which later steps read a chain step's output, by name or by pattern.
    let mut links: HashMap<String, usize> = HashMap::new();
    let mut chain = vec![head];
    let mut joins = Vec::new();
    if let Some(out) = output_of(&steps[head]) {
        links.insert(norm(out), head);
    }
    // Every later step on the head's own files ("a thumbnail and a short gif of every video":
    // one step per result, each on the batch glob) starts a chain of its own for each file.
    let mut heads: HashSet<usize> = HashSet::from([head]);
    let same_files: Vec<String> = files.iter().map(|f| norm(f)).collect();
    for (i, step) in steps.iter().enumerate().skip(head + 1) {
        if !joining_tools.contains(tool_of(step))
            && batch_files(args_of(i), expand)
                .iter()
                .map(|f| norm(f))
                .eq(same_files.iter().cloned())
        {
            heads.insert(i);
            chain.push(i);
            if let Some(out) = output_of(step) {
                links.insert(norm(out), i);
            }
            continue;
        }
        let reads = entries(args_of(i))
            .iter()
            .any(|(_, paths)| paths.iter().any(|p| links.contains_key(&norm(p))));
        if !reads {
            continue;
        }
        if joining_tools.contains(tool_of(step)) {
            joins.push(i);
        } else {
            chain.push(i);
            if let Some(out) = output_of(step) {
                links.insert(norm(out), i);
            }
        }
    }
    if chain.len() == 1 && joins.is_empty() && !single && !reads_one_file(&steps[head]) {
        return unlifted(steps);
    }

    // Every chain step's output for every file, chosen file by file in either order.
    let shown: Vec<String> = files
        .iter()
        .map(|f| home.get(f).cloned().unwrap_or_else(|| f.clone()))
        .collect();
    let mut taken: HashSet<String> = files.iter().chain(shown.iter()).map(|f| norm(f)).collect();
    let mut outputs: HashMap<(usize, usize), String> = HashMap::new();
    for (f, batch_file) in shown.iter().enumerate() {
        for &i in &chain {
            let out = output_of(&steps[i]).map(str::to_string).or_else(|| {
                batch_outputs
                    .get(tool_of(&steps[i]))
                    .map(|ext| format!("*.{ext}"))
            });
            if let Some(out) = out {
                outputs.insert((i, f), output_name(&out, batch_file, &mut taken));
            }
        }
    }

    let render = |i: usize, f: usize| -> Value {
        let mut step = steps[i].clone();
        let mut args = args_of(i).clone();
        for (key, paths) in entries(&args) {
            let new: Vec<String> = if heads.contains(&i) {
                vec![files[f].clone()]
            } else {
                paths
                    .iter()
                    .map(|p| match links.get(&norm(p)) {
                        Some(&src) => outputs.get(&(src, f)).cloned().unwrap_or_else(|| p.clone()),
                        None => p.clone(),
                    })
                    .collect()
            };
            args[key] = if key == "input" || args[key].is_string() {
                Value::String(new[0].clone())
            } else {
                Value::from(new)
            };
        }
        if let Some(out) = outputs.get(&(i, f)) {
            args["output"] = Value::String(out.clone());
        }
        step["args"] = args;
        step
    };

    let join_step = |i: usize| -> (Value, HashMap<String, Vec<Option<usize>>>) {
        let mut step = steps[i].clone();
        let mut args = args_of(i).clone();
        let mut sources = HashMap::new();
        for (key, paths) in entries(&args) {
            let mut new = Vec::new();
            let mut came_from = Vec::new();
            for p in &paths {
                match links.get(&norm(p)) {
                    Some(&src) => {
                        new.extend((0..files.len()).map(|f| outputs[&(src, f)].clone()));
                        came_from.extend((0..files.len()).map(Some));
                    }
                    None => {
                        new.push(p.clone());
                        came_from.push(None);
                    }
                }
            }
            args[key] = Value::from(new);
            sources.insert(key.to_string(), came_from);
        }
        step["args"] = args;
        (step, sources)
    };

    let mut lift = BatchLift {
        files: files.clone(),
        shown,
        ..BatchLift::default()
    };
    for (i, step) in steps.iter().enumerate() {
        if i == head {
            for f in 0..files.len() {
                for &j in &chain {
                    lift.plan.push(render(j, f));
                    lift.file_of.push(Some(f));
                }
            }
        } else if joins.contains(&i) {
            let (joined, sources) = join_step(i);
            lift.join_files.insert(lift.plan.len(), sources);
            lift.plan.push(joined);
            lift.file_of.push(None);
        } else if !chain.contains(&i) {
            lift.plan.push(step.clone());
            lift.file_of.push(None);
        }
    }
    lift
}

// ── an invented batch folder reads as here ───────────────────────────────────────────────────
//
// Owner, 2026-10-21: the model makes up a folder for "here" (`videos/*.mov`), and
// known-folder resolution would then point it at the user's Videos folder. Unlike the lift, this
// reads the disk: whether the folder exists. contracts/parity/invented_folder_cases.json.

/// The args a plan's paths sit in, for [`drop_invented_folders`].
const PATH_KEYS: [&str; 5] = ["input", "inputs", "output", "base", "append"];

/// Whether `utterance` names `folder`: with a slash, after a place word, next to the word
/// folder, or after "folder called/named".
fn names_folder(utterance: &str, folder: &str) -> bool {
    let low = utterance.to_lowercase().replace('\\', "/");
    let name = folder.to_lowercase();
    if low.contains(&format!("{name}/")) {
        return true;
    }
    // A resolution or size ("to 720p", "1920x1080", "4k") is never a folder name, unless slashed.
    if regex::Regex::new(r"^\d+(?:p|k|x\d+)$").is_ok_and(|re| re.is_match(&name)) {
        return false;
    }
    let n = regex::escape(&name);
    regex::Regex::new(&format!(
        r"\b(?:in|into|to|from|on|onto|inside)\s+(?:my\s+|the\s+)?{n}\b"
    ))
    .is_ok_and(|re| re.is_match(&low))
        || regex::Regex::new(&format!(r"\b{n}\s+folder\b|\bfolder\s+{n}\b"))
            .is_ok_and(|re| re.is_match(&low))
        // "a folder called out"
        || regex::Regex::new(&format!(r#"\bfolder\s+(?:called|named)\s+["']?{n}\b"#))
            .is_ok_and(|re| re.is_match(&low))
}

fn here_if_invented(value: &str, utterance: &str, base: &std::path::Path) -> String {
    let globbed = value.chars().any(|c| matches!(c, '*' | '?' | '['));
    let drive =
        value.len() > 1 && value.as_bytes()[1] == b':' && value.as_bytes()[0].is_ascii_alphabetic();
    if !globbed || value.starts_with(['$', '~', '/', '\\']) || value.contains("://") || drive {
        return value.to_string();
    }
    let v = value.replace('\\', "/");
    let (folder, name) = match v.rsplit_once('/') {
        Some((f, n)) => (f.to_string(), n.to_string()),
        None => return value.to_string(),
    };
    let mut folder = folder.as_str();
    while let Some(rest) = folder.strip_prefix("./") {
        folder = rest;
    }
    if folder.is_empty()
        || folder == "."
        || folder.chars().any(|c| matches!(c, '*' | '?' | '['))
        || base.join(folder).is_dir()
        || names_folder(utterance, folder)
    {
        return value.to_string();
    }
    name
}

/// `plan` with every glob in a folder the model made up read as here: a path with a glob in it
/// whose folder does not exist under `base`, in a request that does not name that folder,
/// becomes its last part. Python `knaif.batch_chain.drop_invented_folders`.
pub fn drop_invented_folders(
    plan: &[Value],
    utterance: &str,
    base: &std::path::Path,
) -> Vec<Value> {
    let mut out = plan.to_vec();
    for step in &mut out {
        let Some(args) = step.get_mut("args").and_then(Value::as_object_mut) else {
            continue;
        };
        for key in PATH_KEYS {
            match args.get_mut(key) {
                Some(Value::String(s)) => *s = here_if_invented(s, utterance, base),
                Some(Value::Array(items)) => {
                    for item in items.iter_mut() {
                        if let Value::String(s) = item {
                            *s = here_if_invented(s, utterance, base);
                        }
                    }
                }
                _ => {}
            }
        }
    }
    out
}

// ── "every video" is every video ─────────────────────────────────────────────────────────────
//
// Owner, 2026-10-21: in a request for many files, a batch glob `*.mp4` whose
// extension the request does not name, of a kind the skill lists under `batch_kinds:`, also
// covers the other extensions of that kind in its folder. contracts/parity/kind_glob_cases.json.

/// Extensions the request names as a plural or the start of a hyphenated word ("the mp4s",
/// "mp4-Dateien", ".mov-Dateien"), which name the files' type as much as a bare `mp4` does.
/// Python `knaif.batch_chain._type_words`.
fn type_words(utterance: &str, file_kinds: &crate::clarify_gate::FileKinds) -> HashSet<String> {
    let u = utterance.to_lowercase();
    let word = |c: char| c.is_alphanumeric() || c == '_';
    let ascii_word = |c: char| c.is_ascii_alphanumeric() || c == '_';
    let mut found = HashSet::new();
    for ext in file_kinds.keys() {
        let e = ext.to_lowercase();
        if e.is_empty() {
            continue;
        }
        for (i, _) in u.match_indices(&e) {
            let mut before = u[..i].chars().rev();
            let prev = before.next();
            let bare = prev.is_none_or(|c| !(ascii_word(c) || c == '.' || c == '-'));
            let dotted = prev == Some('.') && before.next().is_none_or(|c| !word(c));
            let mut after = u[i + e.len()..].chars();
            let suffix = match after.next() {
                Some('s') => after.next().is_none_or(|c| !ascii_word(c)),
                Some('-') => after.next().is_some_and(word),
                _ => false,
            };
            if (bare || dotted) && suffix {
                found.insert(e.clone());
                break;
            }
        }
    }
    found
}

/// `plan` with a batch glob for "every video" covering every video: an input glob `*.<ext>` (here
/// or in a plain folder) is followed by a glob for each other extension of its kind found in its
/// folder under `base` (alphabetical, none the request names, none the list holds already). A
/// one-path `input` becomes such a list, which the lift runs once per file; an input that is an
/// earlier step's output is a link and stays. A format named once, as the value of the step's
/// `produces: {arg}` argument, is what the step converts to, not a name of the files ("convert
/// all videos to mp4" covers the mp4s too). Python `knaif.batch_chain.widen_kind_globs`.
pub fn widen_kind_globs(
    plan: &[Value],
    utterance: &str,
    base: &std::path::Path,
    file_kinds: &crate::clarify_gate::FileKinds,
    batch_kinds: &[String],
    produces: &HashMap<String, crate::registry::Produces>,
) -> Vec<Value> {
    let mut out = plan.to_vec();
    if batch_kinds.is_empty() || !crate::nl_clarify_gate::asks_for_many_files(utterance) {
        return out;
    }
    let Ok(glob_re) = regex::Regex::new(r"^(?:(?P<folder>[^*?\[]*)/)?\*\.(?P<ext>[A-Za-z0-9]+)$")
    else {
        return out;
    };
    let formats = crate::scratch::named_formats(utterance, file_kinds);
    let typed = type_words(utterance, file_kinds);
    let excluded = excluded_formats(utterance, file_kinds);
    // The extensions each step's kind globs take, as written. Steps that share one read the same
    // files and each widens fully; steps that share none split the kinds between them, and the
    // widening never adds to one what another takes or was given. Python mirror.
    let all_outputs: HashSet<String> = out
        .iter()
        .filter_map(|s| {
            s.get("args")
                .and_then(|a| a.get("output"))
                .and_then(Value::as_str)
        })
        .filter(|o| !o.is_empty())
        .map(norm)
        .collect();
    let mut taken: Vec<HashSet<(String, String)>> = out
        .iter()
        .map(|step| {
            input_lists(step)
                .into_iter()
                .flatten()
                .filter(|e| !all_outputs.contains(&norm(e)))
                .filter_map(|e| {
                    let slashed = e.replace('\\', "/");
                    glob_re.captures(&slashed).map(|m| {
                        (
                            m.name("folder").map_or("", |f| f.as_str()).to_lowercase(),
                            m["ext"].to_lowercase(),
                        )
                    })
                })
                .collect()
        })
        .collect();
    let own = taken.clone();
    let mut outputs: HashSet<String> = HashSet::new();
    for (i, step) in out.iter_mut().enumerate() {
        let claimed: HashSet<(String, String)> = (0..own.len())
            .filter(|&j| j != i && !own[j].is_empty() && own[j].is_disjoint(&own[i]))
            .flat_map(|j| taken[j].iter().cloned())
            .collect();
        let mut named: HashSet<String> = formats.union(&typed).cloned().collect();
        named.extend(excluded.iter().cloned());
        if let Some(target) = target_format(step, produces) {
            if formats.contains(&target)
                && !typed.contains(&target)
                && crate::scratch::format_mentions(utterance, &target) == 1
            {
                named.remove(&target);
            }
        }
        let Some(args) = step.get_mut("args").and_then(Value::as_object_mut) else {
            continue;
        };
        for key in INPUT_KEYS {
            let entries: Vec<Value> = match args.get(key) {
                Some(v @ Value::String(_)) => vec![v.clone()],
                Some(Value::Array(items)) => items.clone(),
                _ => continue,
            };
            let mut held: HashSet<String> =
                entries.iter().filter_map(Value::as_str).map(norm).collect();
            let mut widened: Vec<Value> = Vec::new();
            for entry in &entries {
                let Some(text) = entry.as_str() else {
                    widened.push(entry.clone());
                    continue;
                };
                let slashed = text.replace('\\', "/");
                let caps = glob_re.captures(&slashed);
                let is_output = outputs.contains(&norm(text));
                if let Some(m) = &caps {
                    if !is_output && excluded.contains(&m["ext"].to_lowercase()) {
                        continue; // "except the mov files": the glob goes, the widening adds none
                    }
                }
                widened.push(entry.clone());
                let Some(m) = caps else { continue };
                if is_output {
                    continue;
                }
                let ext = m["ext"].to_lowercase();
                let folder = m.name("folder").map_or("", |f| f.as_str()).to_string();
                let Some(kind) = file_kinds.get(&ext) else {
                    continue;
                };
                if named.contains(&ext) || !batch_kinds.iter().any(|k| k == kind) {
                    continue;
                }
                let mut found: Vec<String> = std::fs::read_dir(base.join(&folder))
                    .map(|rd| {
                        rd.filter_map(Result::ok)
                            .filter(|e| e.path().is_file())
                            .filter_map(|e| {
                                e.path()
                                    .extension()
                                    .and_then(|x| x.to_str())
                                    .map(str::to_lowercase)
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                found.sort();
                found.dedup();
                for other in found {
                    if other == ext
                        || named.contains(&other)
                        || file_kinds.get(&other) != Some(kind)
                    {
                        continue;
                    }
                    if claimed.contains(&(folder.to_lowercase(), other.clone())) {
                        continue; // a step that splits the kinds with this one takes it
                    }
                    let glob = if folder.is_empty() {
                        format!("*.{other}")
                    } else {
                        format!("{folder}/*.{other}")
                    };
                    if held.insert(norm(&glob)) {
                        taken[i].insert((folder.to_lowercase(), other.clone()));
                        widened.push(Value::String(glob));
                    }
                }
            }
            if widened != entries {
                args.insert(key.to_string(), Value::Array(widened));
            }
        }
        if let Some(o) = args
            .get("output")
            .and_then(Value::as_str)
            .filter(|o| !o.is_empty())
        {
            outputs.insert(norm(o));
        }
    }
    if excluded.is_empty() {
        out
    } else {
        drop_emptied_steps(plan, out)
    }
}

/// "except the mov files": an exclusion word, then the words after it. Python
/// `knaif.batch_chain._EXCLUSION_RE`.
fn exclusion_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        regex::Regex::new(concat!(
            r"(?i)(?:\b(?:except|excluding|exclude|other\s+than|apart\s+from|but\s+not|außer|ausser|",
            r"ausgenommen|bis\s+auf|excepto|salvo|menos|sauf|hormis|освен|без)|除了)",
            r"([^,;:!?，。；]*)"
        ))
        .unwrap()
    })
}

/// Extensions the request excludes: an extension the skill knows within four words after an
/// exclusion word, read up to the first word in another script ("без звук" excludes nothing).
/// Python `knaif.batch_chain._excluded_formats`.
fn excluded_formats(
    utterance: &str,
    file_kinds: &crate::clarify_gate::FileKinds,
) -> HashSet<String> {
    static WORD: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let word_re = WORD.get_or_init(|| regex::Regex::new(r"[a-z0-9]+|[^\sa-z0-9.]+").unwrap());
    let low = utterance.to_lowercase();
    let mut found = HashSet::new();
    for caps in exclusion_re().captures_iter(&low) {
        for word in word_re.find_iter(&caps[1]).map(|m| m.as_str()).take(4) {
            if !word.is_ascii() {
                break;
            }
            let singular = word.strip_suffix('s').unwrap_or(word);
            for ext in [word, singular] {
                if file_kinds.contains_key(ext) {
                    found.insert(ext.to_string());
                }
            }
        }
    }
    found
}

/// Each input arg of `step` as a list of paths. Python `knaif.batch_chain._input_lists`.
fn input_lists(step: &Value) -> Vec<Vec<String>> {
    let Some(args) = step.get("args") else {
        return Vec::new();
    };
    INPUT_KEYS
        .iter()
        .filter_map(|key| match args.get(*key) {
            Some(Value::String(s)) => Some(vec![s.clone()]),
            Some(Value::Array(items)) => Some(
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect(),
            ),
            _ => None,
        })
        .collect()
}

/// `out` without the steps the exclusion left with no input; if a later step reads what such a
/// step writes (as the plan was written), the plan stays as written. Python
/// `knaif.batch_chain._drop_emptied_steps`.
fn drop_emptied_steps(plan: &[Value], out: Vec<Value>) -> Vec<Value> {
    let emptied: Vec<usize> = (0..out.len())
        .filter(|&i| {
            input_lists(&plan[i]).iter().any(|l| !l.is_empty())
                && input_lists(&out[i]).iter().all(Vec::is_empty)
        })
        .collect();
    for &i in &emptied {
        let written = plan[i]
            .get("args")
            .and_then(|a| a.get("output"))
            .and_then(Value::as_str);
        let later_reads: HashSet<String> = plan[i + 1..]
            .iter()
            .flat_map(input_lists)
            .flatten()
            .map(|e| norm(&e))
            .collect();
        if written.is_some_and(|w| later_reads.contains(&norm(w))) {
            return plan.to_vec();
        }
    }
    out.into_iter()
        .enumerate()
        .filter(|(i, _)| !emptied.contains(i))
        .map(|(_, s)| s)
        .collect()
}

/// The format `step` converts to: the value of its tool's `produces: {arg}` argument. Python
/// `knaif.batch_chain._target_format`.
fn target_format(
    step: &Value,
    produces: &HashMap<String, crate::registry::Produces>,
) -> Option<String> {
    let tool = step.get("tool").and_then(Value::as_str)?;
    let crate::registry::Produces::Arg { arg } = produces.get(tool)? else {
        return None;
    };
    step.get("args")?
        .get(arg)?
        .as_str()
        .map(|v| v.trim().to_lowercase().trim_start_matches('.').to_string())
        .filter(|v| !v.is_empty())
}

// ── natural order and the files a glob or folder names ─────────────────────────────────────────

/// Sort key that counts numbers as numbers: `clip1`, `clip2`, `clip10` (owner, 2026-10-19). Any
/// case; ties (`clip2` / `clip02` / `Clip2`) fall back to the name as written. Python
/// `knaif.batch_chain.natural_key`.
pub fn natural_key(name: &str) -> (String, String) {
    let mut padded = String::with_capacity(name.len() + 20);
    let mut digits = String::new();
    let flush = |digits: &mut String, padded: &mut String| {
        if digits.is_empty() {
            return;
        }
        let trimmed = digits.trim_start_matches('0');
        let trimmed = if trimmed.is_empty() { "0" } else { trimmed };
        padded.push_str(&format!("{trimmed:0>20}"));
        digits.clear();
    };
    for c in name.chars() {
        if c.is_ascii_digit() {
            digits.push(c);
        } else {
            flush(&mut digits, &mut padded);
            padded.push(c);
        }
    }
    flush(&mut digits, &mut padded);
    (padded.to_lowercase(), name.to_string())
}

/// Python `fnmatch.fnmatchcase`: `*` any run (a `/` too), `?` one character, `[seq]` / `[!seq]`
/// one character in (or not in) the set, ranges `a-z` included.
pub fn fnmatch(pattern: &str, name: &str) -> bool {
    let p: Vec<char> = pattern.chars().collect();
    let n: Vec<char> = name.chars().collect();
    // Where each `[` class ends, or None for a `[` that never closes (then it is literal).
    fn class_end(p: &[char], start: usize) -> Option<usize> {
        let mut j = start + 1;
        if j < p.len() && p[j] == '!' {
            j += 1;
        }
        if j < p.len() && p[j] == ']' {
            j += 1;
        }
        while j < p.len() && p[j] != ']' {
            j += 1;
        }
        (j < p.len()).then_some(j)
    }
    fn in_class(class: &[char], c: char) -> bool {
        let (negate, body) = match class.first() {
            Some('!') => (true, &class[1..]),
            _ => (false, class),
        };
        let mut k = 0;
        let mut hit = false;
        while k < body.len() {
            if k + 2 < body.len() && body[k + 1] == '-' {
                hit |= body[k] <= c && c <= body[k + 2];
                k += 3;
            } else {
                hit |= body[k] == c;
                k += 1;
            }
        }
        hit != negate
    }
    fn go(p: &[char], n: &[char]) -> bool {
        let Some(&first) = p.first() else {
            return n.is_empty();
        };
        match first {
            '*' => (0..=n.len()).any(|k| go(&p[1..], &n[k..])),
            '?' => !n.is_empty() && go(&p[1..], &n[1..]),
            '[' => match class_end(p, 0) {
                Some(end) => {
                    !n.is_empty() && in_class(&p[1..end], n[0]) && go(&p[end + 1..], &n[1..])
                }
                None => n.first() == Some(&'[') && go(&p[1..], &n[1..]),
            },
            c => n.first() == Some(&c) && go(&p[1..], &n[1..]),
        }
    }
    go(&p, &n)
}

/// The files in `folder` matching `pattern`: `(path below folder, path)`, natural order. The one
/// folder listing (owner, 2026-10-23): core's per-file batch and the fs toolkit's
/// `resolve_inputs` both list through it. Files only, ordered by their path below `folder`
/// ([`natural_key`]); into subfolders only when `recursive`, the pattern matched against the file
/// name (without case on Windows, as Python's glob). A missing folder lists nothing. Python
/// `knaif.batch_chain.list_files`; `contracts/parity/list_files_cases.json`.
pub fn list_files(
    folder: &std::path::Path,
    pattern: &str,
    recursive: bool,
) -> Vec<(String, std::path::PathBuf)> {
    let fold = |s: &str| {
        if cfg!(windows) {
            s.to_lowercase()
        } else {
            s.to_string()
        }
    };
    let pattern = fold(pattern);
    let mut files = Vec::new();
    let mut pending = vec![folder.to_path_buf()];
    while let Some(dir) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                if recursive {
                    pending.push(path);
                }
                continue;
            }
            let name = entry.file_name().to_string_lossy().into_owned();
            if !path.is_file() || !fnmatch(&pattern, &fold(&name)) {
                continue;
            }
            let rel = path
                .strip_prefix(folder)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            files.push((rel, path));
        }
    }
    files.sort_by_key(|(rel, _)| natural_key(rel));
    files
}

/// The files each glob or folder in `entries` names, for [`lift_batch`]. Python
/// `knaif.batch_chain.expand_inputs`.
///
/// Relative entries resolve against `base`; each file keeps the model's spelling of its folder
/// (`videos/*.mov` → `videos/clip1.mov`). Files only, not recursive, in natural order. When the
/// skill declares `file_kinds`, only files of a declared kind count, so a bare `*` or a folder
/// never pulls `notes.txt` into a video chain. A literal file is not in the result; a glob that
/// matches nothing maps to `[]`. Names compare without case on Windows, as Python's glob does.
pub fn expand_inputs(
    entries: &[String],
    base: &std::path::Path,
    file_kinds: &crate::clarify_gate::FileKinds,
) -> HashMap<String, Vec<String>> {
    let mut found = HashMap::new();
    for entry in entries {
        let as_path = std::path::Path::new(entry);
        let full = if as_path.is_absolute() {
            as_path.to_path_buf()
        } else {
            base.join(entry)
        };
        let (folder, pattern, dir) = if full.is_dir() {
            let folder = entry.trim_end_matches(['/', '\\']).to_string();
            (folder, "*".to_string(), full)
        } else if entry.chars().any(|c| matches!(c, '*' | '?' | '[')) {
            let (folder, pattern) = split(&entry.replace('\\', "/"));
            let dir = full
                .parent()
                .map(std::path::Path::to_path_buf)
                .unwrap_or_default();
            (folder, pattern, dir)
        } else {
            continue;
        };
        let mut names: Vec<String> = list_files(&dir, &pattern, false)
            .into_iter()
            .map(|(rel, _)| rel)
            .collect();
        if !file_kinds.is_empty() {
            names.retain(|n| {
                file_kinds.contains_key(&splitext(n).1.trim_start_matches('.').to_lowercase())
            });
        }
        found.insert(
            entry.clone(),
            names.into_iter().map(|n| join(&folder, &n)).collect(),
        );
    }
    found
}

// ── one file into many ───────────────────────────────────────────────────────────────────────
//
// A step whose tool writes many files from one is told by its skill, before anything runs, the
// set's name and its files (owner, 2026-10-20). Later steps that read the set are pointed at that
// one name, so scratch routing can move the whole set, and the lift then runs them once per file.
// contracts/parity/set_link_cases.json.

/// The files one step will write: the set's `name` (what its `output` becomes, e.g. the sequence
/// pattern `clip_%03d.mp4`) and the `files`, in order. Python `knaif.batch_chain.PlannedSet`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlannedSet {
    pub name: String,
    pub files: Vec<String>,
}

/// `plan` with every step that reads a planned set pointed at the set's name: one of its inputs,
/// after producer `p` (a key of `sets`), is `p`'s own output name, the set's name, or a glob
/// matching every file of the set. The producer's `output` becomes the set's name once a step
/// reads it. Python `knaif.batch_chain.link_set_readers`.
pub fn link_set_readers(
    plan: &[Value],
    sets: &std::collections::BTreeMap<usize, PlannedSet>,
) -> Vec<Value> {
    let mut steps = plan.to_vec();
    for (&p, planned) in sets {
        if p >= steps.len() {
            continue;
        }
        let mut refs: HashSet<String> = HashSet::from([norm(&planned.name)]);
        if let Some(out) = output_of(&steps[p]) {
            refs.insert(norm(out));
        }
        let matches_all = |glob: &str| {
            !planned.files.is_empty()
                && planned.files.iter().all(|f| fnmatch(&norm(glob), &norm(f)))
        };
        let mut linked = false;
        for step in steps.iter_mut().skip(p + 1) {
            let Some(args) = step.get_mut("args") else {
                continue;
            };
            for (key, paths) in entries(args) {
                let new: Vec<String> = paths
                    .iter()
                    .map(|path| {
                        let reads = refs.contains(&norm(path))
                            || (path.chars().any(|c| matches!(c, '*' | '?' | '['))
                                && matches_all(path));
                        linked |= reads;
                        if reads {
                            planned.name.clone()
                        } else {
                            path.clone()
                        }
                    })
                    .collect();
                args[key] = if args[key].is_string() {
                    Value::String(new[0].clone())
                } else {
                    Value::from(new)
                };
            }
        }
        if linked {
            if !steps[p].get("args").is_some_and(Value::is_object) {
                steps[p]["args"] = Value::Object(serde_json::Map::new());
            }
            steps[p]["args"]["output"] = Value::String(planned.name.clone());
        }
    }
    steps
}

// ── the batch report ─────────────────────────────────────────────────────────────────────────
//
// One failed file does not stop the others; a failure is printed when it happens and the run
// ends with counts, time and the size of what it left (owner, 2026-10-13). Both runtimes print
// this text, on stderr; contracts/parity/batch_report_cases.json pins it.

/// The first non-blank line of `text`, trimmed.
pub fn first_line(text: &str) -> &str {
    text.lines()
        .map(str::trim)
        .find(|l| !l.is_empty())
        .unwrap_or("")
}

/// `✗ silent.mov: No audio to extract …`: the file as the plan names it, one line.
pub fn failure_line(file: &str, reason: &str) -> String {
    let why = first_line(reason);
    let why = if why.is_empty() { "failed" } else { why };
    format!("✗ {}: {why}", file.replace('\\', "/"))
}

/// `512 B`, `1.5 KB`, `35.2 MB`, `3.0 GB` (1024 to the step).
pub fn format_size(size: u64) -> String {
    if size < 1024 {
        return format!("{size} B");
    }
    let mut value = size as f64;
    let mut unit = "KB";
    for u in ["KB", "MB", "GB"] {
        value /= 1024.0;
        unit = u;
        if value < 1024.0 {
            break;
        }
    }
    format!("{value:.1} {unit}")
}

/// `8 done, 2 failed in 12.4s, 35.2 MB`; no failed part when nothing failed.
pub fn summary_line(done: usize, failed: usize, seconds: f64, size: u64) -> String {
    let counts = if failed > 0 {
        format!("{done} done, {failed} failed")
    } else {
        format!("{done} done")
    };
    format!("{counts} in {seconds:.1}s, {}", format_size(size))
}

#[cfg(test)]
mod tests {
    use super::fnmatch;

    #[test]
    fn fnmatch_agrees_with_python_fnmatchcase() {
        for (pattern, name, want) in [
            ("clip?.mp4", "clip1.mp4", true),
            ("clip?.mp4", "clip10.mp4", false),
            ("clip[12].mp4", "clip2.mp4", true),
            ("clip[!12].mp4", "clip2.mp4", false),
            ("clip[a-c].mp4", "clipb.mp4", true),
            ("*", "a/b", true),
            ("[x", "[x", true),
            ("*.mp4", ".mp4", true),
        ] {
            assert_eq!(fnmatch(pattern, name), want, "{pattern} vs {name}");
        }
    }
}
