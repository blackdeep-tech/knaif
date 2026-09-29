//! Hallucinated-input-filename gate — native port of Python `CommandAgent._hallucinated_filename`.
//!
//! Runs at plan-build time (right after inference + validation), on the model's plan envelope.
//! The model sometimes invents an *input* filename the user never named (e.g. "speed up the
//! video" → `inputs: ["video.mp4"]`). This guard downgrades such a plan to a single `clarify`
//! step instead of silently operating on a made-up file — the behavior Python already has and
//! the native `run`/`plan` paths were missing.
//!
//! Rule (mirrors Python exactly): for each non-terminal step, every string arg value under a key
//! other than `output` that (a) looks like a filename, (b) is not a glob, and (c) is not produced
//! by an earlier step's `output` must appear — case-insensitively — as a substring of the
//! utterance. The first value that doesn't is the hallucinated filename. Output names are the
//! model's to invent, so they're exempt.
//!
//! One exemption: a value whose **stem** the user named, when that value is a file the sandbox
//! actually holds (`known_files`). People say "downscale clip_4k to 1920x1080", and the model
//! answering `clip_4k.mp4` — the real file — was being overridden with a clarify by the plain
//! substring test. Both halves are required: `clip_4k.mov` for the same utterance stays flagged
//! because no such file exists, and a stem must carry a structural marker (`_`, `-`, a digit) or
//! "make the video smaller" would pass `video.mp4`. That marker rule is the same one the stem
//! resolver uses, so the guard cannot admit a name the resolver would then refuse.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use serde_json::{json, Value};

use crate::registry::Registry;

/// Did the user name this file by its stem, and does the stem's file really exist?
///
/// Both halves are load-bearing — see the module header. `known_files` is the sandbox
/// listing, lowercased by the caller; it is passed in rather than read here so the gate stays
/// pure and the L2 contract can state it (`sandbox_files` in
/// `contracts/parity/clarify_gate_cases.json`).
fn named_by_stem(value: &str, u_lower: &str, known_files: &HashSet<String>) -> bool {
    let name = value.rsplit(['/', '\\']).next().unwrap_or(value);
    let stem = match name.rfind('.') {
        Some(i) => &name[..i],
        None => name,
    };
    if stem.is_empty() || !is_stem_candidate(stem) {
        return false;
    }
    u_lower.contains(&stem.to_lowercase()) && known_files.contains(&name.to_lowercase())
}

/// A stem is an extension-less identifier carrying a structural marker (`_`, `-` or a digit).
/// Mirrors Python `planner._is_stem_candidate`: bare words like "video" or "clip" are English,
/// not filenames, and treating them as stems turns a hallucination into a silent plan.
fn is_stem_candidate(stem: &str) -> bool {
    let mut chars = stem.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !first.is_ascii_alphanumeric() {
        return false;
    }
    if !stem
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return false;
    }
    stem.chars()
        .any(|c| c == '_' || c == '-' || c.is_ascii_digit())
}

/// Terminal control tools carry no file inputs (mirrors Python `_TERMINAL_TOOLS`).
const TERMINAL_TOOLS: &[&str] = &["done", "clarify", "reject"];

/// Tools whose schema accepts an `output` arg — eligible chain-intermediate producers.
/// Tools that can be handed an `output` filename.
///
/// **Declared args only — an `arg_schemas` entry does not make a tool output-capable.**
/// This used to also accept `arg_schemas.contains_key("output")`, which put this function
/// at odds with our own validator: `validate_plan` rejects an arg that is not in
/// `required_args`/`optional_args` ("unsupported args"), so binding a chain intermediate to
/// such a tool produced a plan the next stage refused. Python derives the set the same way,
/// and the L2 clarify-gate contract carries a case for exactly this shape
/// (`output_capable_only_via_arg_schemas`), which is how the disagreement surfaced.
pub fn output_capable_tools(registry: &Registry) -> HashSet<String> {
    registry
        .iter()
        .filter(|(_, d)| {
            d.optional_args.iter().any(|a| a == "output")
                || d.required_args.iter().any(|a| a == "output")
        })
        .map(|(n, _)| n.clone())
        .collect()
}

/// Terminal tools the arg-shape gates skip. Wider than [`TERMINAL_TOOLS`] by
/// `wait_for_confirmation`, matching Python's `nl_clarify_gate._TERMINAL_TOOLS`.
const ARG_GATE_TERMINAL: &[&str] = &["clarify", "reject", "done", "wait_for_confirmation"];

fn clarify_payload(question: String) -> Value {
    json!({ "plan": [{ "tool": "clarify", "args": { "question": question } }] })
}

fn non_terminal_steps(payload: &Value) -> impl Iterator<Item = &Value> {
    payload
        .get("plan")
        .and_then(Value::as_array)
        .map(|v| v.as_slice())
        .unwrap_or(&[])
        .iter()
        .filter(|s| {
            let t = s.get("tool").and_then(Value::as_str).unwrap_or("");
            !ARG_GATE_TERMINAL.contains(&t)
        })
}

/// Clarify instead of hard-erroring when a step omits an arg the user must supply.
///
/// Port of Python `nl_clarify_gate.required_args_clarify`. Fires on a missing required arg,
/// on one present but empty, and on a tool declaring `any_of_args` with none of them given.
/// Known tools only — an unknown tool stays a validation error.
pub fn required_args_clarify(payload: &Value, registry: &Registry) -> Option<Value> {
    let empty = serde_json::Map::new();
    for step in non_terminal_steps(payload) {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let Some(def) = registry.get(tool) else {
            continue; // unknown tool → leave for validation to reject
        };
        let args = step
            .get("args")
            .and_then(Value::as_object)
            .unwrap_or(&empty);
        for arg in &def.required_args {
            let missing = match args.get(arg.as_str()) {
                None | Some(Value::Null) => true,
                Some(Value::String(v)) => v.trim().is_empty(),
                Some(_) => false,
            };
            if missing {
                return Some(clarify_payload(format!("What {arg} should I use?")));
            }
        }
        if !def.any_of_args.is_empty()
            && !def
                .any_of_args
                .iter()
                .any(|a| args.contains_key(a.as_str()))
        {
            return Some(clarify_payload(format!(
                "What {} should I use?",
                def.any_of_args.join(" or ")
            )));
        }
    }
    None
}

/// Clarify instead of hard-erroring when a step puts an arg on a *known* tool that the tool
/// does not declare.
///
/// Port of Python `nl_clarify_gate.unsupported_args_clarify`. The inventory-gap case of the
/// reject/clarify taxonomy: the user asked for something real that no tool can express, and
/// the model wrote it down as the closest arg it had. Deliberately narrow, so it does not
/// become somewhere bugs hide behind a polite question — known tools only, and the caller
/// runs it after `normalize_plan` (so a merely-misnamed key is aliased, not clarified) and
/// only on a model-proposed plan, never on an expanded one.
pub fn unsupported_args_clarify(payload: &Value, registry: &Registry) -> Option<Value> {
    for step in non_terminal_steps(payload) {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let Some(def) = registry.get(tool) else {
            continue;
        };
        let Some(args) = step.get("args").and_then(Value::as_object) else {
            continue;
        };
        let allowed: HashSet<&str> = def
            .required_args
            .iter()
            .chain(def.optional_args.iter())
            .map(String::as_str)
            .collect();
        let extra: Vec<&str> = args
            .keys()
            .map(String::as_str)
            .filter(|k| !allowed.contains(k))
            .collect();
        if !extra.is_empty() {
            let joined = match extra.split_last() {
                Some((last, [])) => (*last).to_string(),
                Some((last, rest)) => format!("{} and {last}", rest.join(", ")),
                None => unreachable!("extra is non-empty"),
            };
            return Some(clarify_payload(format!(
                "I can't {} with {joined} — that isn't supported. Could you rephrase?",
                tool.replace('_', " ")
            )));
        }
    }
    None
}

/// Link chain intermediates, then return the plan unchanged or downgraded to a clarify.
///
/// Ordering mirrors Python `infer`: `_link_chain_intermediates` runs first (so a downstream
/// intermediate the producer didn't declare becomes that producer's `output` and is exempt, and
/// a reused source is threaded onto its producer's output), then the hallucinated-input-filename
/// guard. `file_kinds` is the skill's [`load_file_kinds`]. A plan with no `plan` array is
/// returned as-is.
pub fn apply_clarify_gate(
    mut payload: Value,
    utterance: &str,
    output_capable: &HashSet<String>,
    known_files: &HashSet<String>,
    file_kinds: &FileKinds,
) -> Value {
    if let Some(steps) = payload.get_mut("plan").and_then(Value::as_array_mut) {
        link_chain_intermediates(steps, utterance, output_capable, file_kinds);
    }
    let Some(steps) = payload.get("plan").and_then(Value::as_array) else {
        return payload;
    };
    if let Some(name) = hallucinated_filename(steps, utterance, known_files) {
        let q =
            format!("You didn't mention '{name}' in your request — which file should I work on?");
        payload["plan"] = json!([{ "tool": "clarify", "args": { "question": q } }]);
    }
    payload
}

/// Bind undeclared chain intermediates to the producing step's `output` (native port of Python
/// `_link_chain_intermediates`). When a non-first step consumes a filename-like value that is
/// absent from the utterance and not yet produced, assign it as the `output` of the nearest
/// preceding non-terminal, output-capable step that has none and does not fan out (a single
/// producer). Mutates `plan` in place; single-step plans are left untouched.
pub fn link_chain_intermediates(
    plan: &mut [Value],
    utterance: &str,
    output_capable: &HashSet<String>,
    file_kinds: &FileKinds,
) {
    let u_lower = utterance.to_lowercase();
    let mut produced: HashSet<String> = HashSet::new();
    for idx in 0..plan.len() {
        let tool = plan[idx].get("tool").and_then(Value::as_str).unwrap_or("");
        if TERMINAL_TOOLS.contains(&tool) {
            continue;
        }
        // Undeclared intermediates this step consumes (owned, so no borrow is held while mutating).
        let candidates: Vec<String> = intermediate_candidates(&plan[idx], &produced, &u_lower);
        for value in candidates {
            for prev in (0..idx).rev() {
                let ptool = plan[prev].get("tool").and_then(Value::as_str).unwrap_or("");
                if TERMINAL_TOOLS.contains(&ptool) || !output_capable.contains(ptool) {
                    continue;
                }
                let (fans_out, has_output) = producer_shape(&plan[prev]);
                if fans_out || has_output {
                    continue;
                }
                // Nearest eligible producer: make the intermediate its declared output.
                let args = plan[prev]
                    .get_mut("args")
                    .filter(|a| a.is_object())
                    .map(|a| a.as_object_mut().unwrap());
                match args {
                    Some(map) => {
                        map.insert("output".into(), Value::String(value.clone()));
                    }
                    None => {
                        plan[prev]["args"] = json!({ "output": value });
                    }
                }
                produced.insert(value.to_lowercase());
                break;
            }
        }
        if let Some(out) = plan[idx]
            .get("args")
            .and_then(|a| a.get("output"))
            .and_then(Value::as_str)
        {
            produced.insert(out.to_lowercase());
        }
    }

    // Second pass, as Python does at the end of `_link_chain_intermediates`. The first pass only
    // claims filenames nothing has produced yet; this one repoints a later step that reuses an
    // earlier step's *source* rather than its result.
    forward_thread_reused_sources(plan, utterance, output_capable, file_kinds);
}

/// A skill's `file_kinds:` as an extension → kind map (`mp4` → `video`).
pub type FileKinds = HashMap<String, String>;

/// Build a [`FileKinds`] map from `kind → extensions` groups, as `skill.yaml` writes them.
///
/// Port of Python `knaif.skill.parse_file_kinds`: extensions are lower-cased with any leading
/// dot dropped, and one listed under two kinds is an error — the comparison would otherwise
/// depend on which kind was read last.
pub fn file_kinds_from_groups<I, E>(groups: I) -> Result<FileKinds, String>
where
    I: IntoIterator<Item = (String, E)>,
    E: IntoIterator<Item = String>,
{
    let mut kinds = FileKinds::new();
    for (kind, exts) in groups {
        for ext in exts {
            let key = ext.to_lowercase().trim_start_matches('.').to_string();
            if let Some(prev) = kinds.get(&key) {
                if *prev != kind {
                    return Err(format!(
                        "file_kinds lists '{key}' under both '{prev}' and '{kind}'"
                    ));
                }
            }
            kinds.insert(key, kind.clone());
        }
    }
    Ok(kinds)
}

/// Read `file_kinds:` from a bundle's `skill.yaml`. A missing file or section means no kinds;
/// a section that is not `kind → [extensions]`, or lists an extension twice, is an error, as
/// it is when Python loads the skill.
pub fn load_file_kinds(skill_yaml: &Path) -> Result<FileKinds, String> {
    let Ok(text) = std::fs::read_to_string(skill_yaml) else {
        return Ok(FileKinds::new());
    };
    let doc: serde_yaml::Value =
        serde_yaml::from_str(&text).map_err(|e| format!("{}: {e}", skill_yaml.display()))?;
    let Some(raw) = doc.get("file_kinds").filter(|v| !v.is_null()) else {
        return Ok(FileKinds::new());
    };
    let map = raw
        .as_mapping()
        .ok_or("file_kinds must map a kind to a list of extensions")?;
    let mut groups = Vec::new();
    for (kind, exts) in map {
        let kind = kind.as_str().ok_or("file_kinds: a kind must be a string")?;
        let exts = exts
            .as_sequence()
            .ok_or_else(|| format!("file_kinds.{kind} must be a list of extensions"))?;
        let exts: Vec<String> = exts
            .iter()
            .map(|e| match e {
                serde_yaml::Value::String(s) => s.clone(),
                other => serde_yaml::to_string(other)
                    .unwrap_or_default()
                    .trim()
                    .to_string(),
            })
            .collect();
        groups.push((kind.to_string(), exts));
    }
    file_kinds_from_groups(groups)
}

/// Basename of a path-ish string, lower-cased for comparison.
fn basename_lower(value: &str) -> String {
    value
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or(value)
        .to_lowercase()
}

/// Lower-cased extension of `value`'s basename, without the dot (`""` when none). Port of
/// Python `_extension`.
fn extension(value: &str) -> String {
    let name = basename_lower(value);
    match name.rfind('.') {
        Some(dot) if dot > 0 => name[dot + 1..].to_string(),
        _ => String::new(),
    }
}

/// A character that can extend a filename token: ASCII letters and digits, plus `_`. Not
/// Unicode `\w` — unspaced CJK ("为clip.mp4生成") would otherwise hide every mention.
fn is_word(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// How many times the filename `name` appears in `utterance`, case-insensitively.
///
/// Port of Python `_mention_count`, whose pattern is
/// `(?<![A-Za-z0-9_.-])NAME(?![A-Za-z0-9_-]|\.[A-Za-z0-9_])`: a match must stand alone as a
/// filename, so `clip.mp4`
/// inside `myclip.mp4` or `clip.mp4.bak` is not a mention, while `./clip.mp4` or `clip.mp4,`
/// is. Scanned like a regex: on a match the search resumes after it, otherwise one character
/// on, so the count agrees with `re.findall` for any name.
fn mention_count(utterance: &str, name: &str) -> usize {
    let u = utterance.to_lowercase();
    let n = name.to_lowercase();
    if n.is_empty() {
        return 0;
    }
    let mut count = 0;
    let mut i = 0;
    while i < u.len() {
        if u[i..].starts_with(&n) {
            let before_ok = u[..i]
                .chars()
                .next_back()
                .is_none_or(|c| !(is_word(c) || c == '.' || c == '-'));
            let mut after = u[i + n.len()..].chars();
            let after_ok = match after.next() {
                Some(c) if is_word(c) || c == '-' => false,
                Some('.') => !after.next().is_some_and(is_word),
                _ => true,
            };
            if before_ok && after_ok {
                count += 1;
                i += n.len();
                continue;
            }
        }
        i += u[i..].chars().next().map_or(1, char::len_utf8);
    }
    count
}

/// Derive a chain-intermediate filename from `src`, avoiding names already `taken`.
///
/// Preserves the directory prefix and extension and inserts a `-chained` marker
/// (`report.pdf` → `report-chained.pdf`). Port of Python `_intermediate_name`, including the
/// numbered fallback so repeated transforms of one source do not collide.
fn intermediate_name(src: &str, taken: &HashSet<String>) -> String {
    let name = src.rsplit(['/', '\\']).next().unwrap_or(src);
    let prefix = &src[..src.len() - name.len()];
    let (stem, ext) = match name.rfind('.') {
        Some(dot) if dot > 0 => (&name[..dot], &name[dot..]),
        _ => (name, ""),
    };
    let mut n = 1usize;
    loop {
        let marker = if n == 1 {
            "-chained".to_string()
        } else {
            format!("-chained{n}")
        };
        let candidate = format!("{prefix}{stem}{marker}{ext}");
        if !taken.contains(&basename_lower(&candidate)) {
            return candidate;
        }
        n += 1;
    }
}

/// Thread a **reused source filename** onto the transforming step's output.
///
/// Port of Python `_forward_thread_reused_sources`, the second pass of chain linking. The model
/// sometimes emits a correct chain but points a later step at the ORIGINAL source rather than
/// the file an earlier step produced from it (`unlock_pdf s.pdf` then `find_in_document s.pdf`
/// searches the still-locked original). This pass repoints the later reference at the
/// producer's `output`, minting one (`s-chained.pdf`) when the producer declares none.
///
/// It leaves the reference alone when:
/// - the producer is read-only (not output-capable), or consumes several files or a glob;
/// - **the user named the source more than once** — a repeated name is their choice, and a
///   fan-out (several steps reading one file) must run as written;
/// - **the producer's declared output is a different kind of file** from its source, per the
///   skill's `file_kinds` (a thumbnail of a video). An unlisted extension is unrestricted.
///
/// See docs/plans/2026-09-23-chain-source-threading.md.
fn forward_thread_reused_sources(
    plan: &mut [Value],
    utterance: &str,
    output_capable: &HashSet<String>,
    file_kinds: &FileKinds,
) {
    let mut produced: HashSet<String> = plan
        .iter()
        .filter_map(|s| s.get("args")?.get("output")?.as_str())
        .map(basename_lower)
        .collect();

    for idx in 0..plan.len() {
        let tool = plan[idx].get("tool").and_then(Value::as_str).unwrap_or("");
        if TERMINAL_TOOLS.contains(&tool) || !output_capable.contains(tool) {
            continue;
        }
        // Exactly one source file, or a single `output` cannot stand in for the batch.
        let sources: Vec<String> = plan[idx]
            .get("args")
            .and_then(Value::as_object)
            .map(|map| {
                map.iter()
                    .filter(|(k, _)| *k != "output")
                    .flat_map(|(_, v)| string_values(v))
                    .filter(|v| looks_like_filename(v) && !v.contains('*') && !v.contains('?'))
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        if sources.len() != 1 {
            continue;
        }
        let src_base = basename_lower(&sources[0]);
        if mention_count(utterance, &src_base) > 1 {
            continue; // the user named it again: later references are theirs
        }

        // Later references to that same source, as (step index, arg key, list index).
        let mut targets: Vec<(usize, String, Option<usize>)> = Vec::new();
        for (offset, later) in plan.iter().enumerate().skip(idx + 1) {
            let ltool = later.get("tool").and_then(Value::as_str).unwrap_or("");
            if TERMINAL_TOOLS.contains(&ltool) {
                continue;
            }
            let Some(map) = later.get("args").and_then(Value::as_object) else {
                continue;
            };
            for (key, value) in map {
                if key == "output" {
                    continue;
                }
                match value {
                    Value::String(s) if looks_like_filename(s) && basename_lower(s) == src_base => {
                        targets.push((offset, key.clone(), None));
                    }
                    Value::Array(items) => {
                        for (li, item) in items.iter().enumerate() {
                            if item.as_str().is_some_and(|s| {
                                looks_like_filename(s) && basename_lower(s) == src_base
                            }) {
                                targets.push((offset, key.clone(), Some(li)));
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        if targets.is_empty() {
            continue;
        }

        // Reuse the producer's declared output — unless it is another kind of file — or mint an
        // intermediate, which keeps the source's extension and so its kind.
        let existing = plan[idx]
            .get("args")
            .and_then(|a| a.get("output"))
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        let out = match existing {
            Some(o) => {
                let src_kind = file_kinds.get(&extension(&sources[0]));
                let out_kind = file_kinds.get(&extension(&o));
                if matches!((src_kind, out_kind), (Some(a), Some(b)) if a != b) {
                    continue; // a different kind of file: the later step meant the source
                }
                o
            }
            None => {
                let name = intermediate_name(&sources[0], &produced);
                produced.insert(basename_lower(&name));
                match plan[idx].get_mut("args").and_then(Value::as_object_mut) {
                    Some(a) => {
                        a.insert("output".into(), Value::String(name.clone()));
                    }
                    None => plan[idx]["args"] = json!({ "output": name.clone() }),
                }
                name
            }
        };
        for (step_idx, key, list_idx) in targets {
            let Some(args) = plan[step_idx].get_mut("args") else {
                continue;
            };
            let slot = match list_idx {
                Some(li) => args.get_mut(&key).and_then(|v| v.get_mut(li)),
                None => args.get_mut(&key),
            };
            if let Some(slot) = slot {
                *slot = Value::String(out.clone());
            }
        }
    }
}

/// Filename-like input values (non-`output`) that are neither in the utterance nor already
/// produced — the undeclared intermediates a producer should claim.
fn intermediate_candidates(step: &Value, produced: &HashSet<String>, u_lower: &str) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(map) = step.get("args").and_then(Value::as_object) {
        for (key, value) in map {
            if key == "output" {
                continue;
            }
            for v in string_values(value) {
                if v.chars().any(|c| "*?/\\".contains(c)) || !looks_like_filename(v) {
                    continue;
                }
                let vl = v.to_lowercase();
                if produced.contains(&vl) || u_lower.contains(&vl) {
                    continue;
                }
                out.push(v.to_string());
            }
        }
    }
    out
}

/// `(fans_out, has_output)` for a candidate producer step. A producer that emits more than one
/// deliverable (multiple input files, or a glob) can't safely name a single `output`.
fn producer_shape(step: &Value) -> (bool, bool) {
    let Some(map) = step.get("args").and_then(Value::as_object) else {
        return (false, false);
    };
    let has_output = map
        .get("output")
        .and_then(Value::as_str)
        .is_some_and(|s| !s.is_empty());
    let mut producers = 0usize;
    let mut glob = false;
    for (key, value) in map {
        if key == "output" {
            continue;
        }
        for v in string_values(value) {
            let is_glob = v.contains('*') || v.contains('?');
            if looks_like_filename(v) || is_glob {
                producers += 1;
            }
            glob |= is_glob;
        }
    }
    (producers > 1 || glob, has_output)
}

/// The first invented input filename in `plan`, or `None`. See module docs for the rule.
pub fn hallucinated_filename(
    plan: &[Value],
    utterance: &str,
    known_files: &HashSet<String>,
) -> Option<String> {
    let u_lower = utterance.to_lowercase();

    // Filenames the plan itself produces (an earlier step's `output`); consuming one downstream
    // is legitimate, not a hallucination.
    let mut produced: HashSet<String> = HashSet::new();
    for step in plan {
        if let Some(out) = step
            .get("args")
            .and_then(|a| a.get("output"))
            .and_then(Value::as_str)
        {
            produced.insert(out.to_lowercase());
        }
    }

    for step in plan {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        if TERMINAL_TOOLS.contains(&tool) {
            continue;
        }
        let Some(args) = step.get("args").and_then(Value::as_object) else {
            continue;
        };
        for (key, value) in args {
            if key == "output" {
                continue; // invented output names must not be flagged
            }
            for v in string_values(value) {
                if v.contains('*') || v.contains('?') {
                    continue; // glob pattern, not a concrete filename
                }
                if !looks_like_filename(v) {
                    continue; // not a filename-like token
                }
                let vl = v.to_lowercase();
                if produced.contains(&vl) {
                    continue; // produced by an earlier step
                }
                if u_lower.contains(&vl) {
                    continue;
                }
                if named_by_stem(v, &u_lower, known_files) {
                    continue; // the user named the stem and this file is really there
                }
                return Some(v.to_string());
            }
        }
    }
    None
}

/// String values reachable from an arg value: the string itself, or the string elements of a
/// list (mirrors Python `_iter_string_values` — not recursive into nested maps).
fn string_values(value: &Value) -> Vec<&str> {
    match value {
        Value::String(s) => vec![s.as_str()],
        Value::Array(a) => a.iter().filter_map(Value::as_str).collect(),
        _ => Vec::new(),
    }
}

/// True if `value` ends with a file extension: `.` then a letter then 1–4 alphanumerics
/// (mirrors Python `_FILENAME_RE = \.[a-z][a-z0-9]{1,4}$`, case-insensitive). Anchored at the
/// end, so `H.264` (digit-led) and bare stems (`clip`) don't match.
fn looks_like_filename(value: &str) -> bool {
    let Some(dot) = value.rfind('.') else {
        return false;
    };
    let ext = &value[dot + 1..];
    let len = ext.chars().count();
    if !(2..=5).contains(&len) {
        return false; // one leading letter + 1..=4 more = 2..=5 chars
    }
    let mut chars = ext.chars();
    let first = chars.next().unwrap();
    first.is_ascii_alphabetic() && chars.all(|c| c.is_ascii_alphanumeric())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan(steps: Value) -> Value {
        json!({ "plan": steps })
    }

    fn gate(payload: Value, utterance: &str) -> Value {
        apply_clarify_gate(
            payload,
            utterance,
            &HashSet::new(),
            &HashSet::new(),
            &FileKinds::new(),
        )
    }

    fn files(names: &[&str]) -> HashSet<String> {
        names.iter().map(|n| n.to_lowercase()).collect()
    }

    #[test]
    fn flags_invented_input_filename() {
        let p = plan(json!([{"tool": "resize_video", "args": {"inputs": ["video.mp4"]}}]));
        let out = gate(p, "speed up the video 4 times");
        assert_eq!(out["plan"][0]["tool"], "clarify");
        assert!(out["plan"][0]["args"]["question"]
            .as_str()
            .unwrap()
            .contains("video.mp4"));
    }

    #[test]
    fn allows_filename_written_in_utterance() {
        let p = plan(
            json!([{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "container": "mkv"}}]),
        );
        let out = gate(p.clone(), "convert clip.mp4 to mkv");
        assert_eq!(out, p); // unchanged
    }

    #[test]
    fn case_insensitive_substring_match() {
        let p = plan(json!([{"tool": "convert_video", "args": {"inputs": ["Clip.MP4"]}}]));
        let out = gate(p.clone(), "convert clip.mp4 please");
        assert_eq!(out, p); // Clip.MP4 vs clip.mp4 — case-insensitive, unchanged
    }

    #[test]
    fn exempts_output_and_chained_intermediates() {
        // convert produces clip.mp4 (output), strip consumes it — not a hallucination.
        let p = plan(json!([
            {"tool": "convert_video", "args": {"inputs": ["clip.mov"], "output": "clip.mp4"}},
            {"tool": "strip_audio", "args": {"inputs": ["clip.mp4"]}},
        ]));
        let out = gate(p.clone(), "convert clip.mov to mp4 and strip audio");
        assert_eq!(out, p);
    }

    #[test]
    fn links_undeclared_chain_intermediate_then_exempts_it() {
        // rotate omits `output`; compress consumes clip_rotated.mp4. The linker assigns it as
        // rotate's output (rotate is output-capable) → no clarify, and the plan is made explicit.
        let capable: HashSet<String> = ["rotate_video".to_string()].into_iter().collect();
        let p = plan(json!([
            {"tool": "rotate_video", "args": {"inputs": ["clip.mp4"], "angle": 90}},
            {"tool": "compress_video", "args": {"inputs": ["clip_rotated.mp4"]}},
        ]));
        let out = apply_clarify_gate(
            p,
            "rotate clip.mp4 90 degrees then compress it",
            &capable,
            &HashSet::new(),
            &FileKinds::new(),
        );
        assert_eq!(out["plan"][0]["tool"], "rotate_video");
        assert_eq!(out["plan"][0]["args"]["output"], "clip_rotated.mp4");
        assert_eq!(out["plan"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn still_flags_intermediate_when_no_capable_producer() {
        // Same shape, but the producer is NOT output-capable → intermediate stays hallucinated.
        let p = plan(json!([
            {"tool": "rotate_video", "args": {"inputs": ["clip.mp4"], "angle": 90}},
            {"tool": "compress_video", "args": {"inputs": ["clip_rotated.mp4"]}},
        ]));
        let out = apply_clarify_gate(
            p,
            "rotate clip.mp4 90 degrees then compress it",
            &HashSet::new(),
            &HashSet::new(),
            &FileKinds::new(),
        );
        assert_eq!(out["plan"][0]["tool"], "clarify");
    }

    #[test]
    fn exempts_globs_and_non_filenames() {
        let p = plan(
            json!([{"tool": "convert_video", "args": {"inputs": ["*.mp4"], "container": "mkv"}}]),
        );
        let out = gate(p.clone(), "convert everything to mkv");
        assert_eq!(out, p); // glob exempt
                            // A codec token like h264 / H.264 is not a filename.
        let p2 = plan(
            json!([{"tool": "convert_video", "args": {"inputs": ["clip.mp4"], "video_codec": "H.264"}}]),
        );
        let out2 = gate(p2.clone(), "convert clip.mp4 to h.264");
        assert_eq!(out2, p2);
    }

    #[test]
    fn ignores_terminal_and_planless() {
        let p = plan(json!([{"tool": "clarify", "args": {"question": "which file?"}}]));
        assert_eq!(gate(p.clone(), "do a thing"), p);
        let np = json!({"not_a_plan": true});
        assert_eq!(gate(np.clone(), "x"), np);
    }

    /// The named-once rule's counter, against what Python's `re.findall` gives for the same
    /// pattern. The L2 contract covers these through whole plans; this pins the boundaries.
    #[test]
    fn mention_count_matches_python_boundaries() {
        assert_eq!(mention_count("trim clip.mp4 and clip.mp4", "clip.mp4"), 2);
        assert_eq!(mention_count("unlock ./s.pdf then s.pdf", "s.pdf"), 2);
        assert_eq!(mention_count("Clip.MP4 then clip.mp4.", "clip.mp4"), 2);
        assert_eq!(mention_count("clip.mp4, please", "clip.mp4"), 1);
        assert_eq!(mention_count("myclip.mp4 and clip.mp4.bak", "clip.mp4"), 0);
        assert_eq!(mention_count("clip.mp4-old and my-clip.mp4", "clip.mp4"), 0);
        assert_eq!(mention_count("видео.mp4 и видео.mp4", "видео.mp4"), 2);
        assert_eq!(mention_count("nothing here", "clip.mp4"), 0);
        assert_eq!(
            mention_count("为clip.mp4生成缩略图，并压缩clip.mp4", "clip.mp4"),
            2
        );
        assert_eq!(mention_count("将clip.mp4剪切到5秒", "clip.mp4"), 1);
    }

    #[test]
    fn intermediate_names_do_not_collide() {
        let mut taken: HashSet<String> = HashSet::new();
        let first = intermediate_name("dir/clip.mp4", &taken);
        assert_eq!(first, "dir/clip-chained.mp4");
        taken.insert(basename_lower(&first));
        assert_eq!(
            intermediate_name("dir/clip.mp4", &taken),
            "dir/clip-chained2.mp4"
        );
    }

    #[test]
    fn file_kinds_normalise_and_refuse_a_duplicate() {
        let kinds = file_kinds_from_groups(vec![
            (
                "video".to_string(),
                vec!["MP4".to_string(), ".mkv".to_string()],
            ),
            ("image".to_string(), vec!["jpg".to_string()]),
        ])
        .unwrap();
        assert_eq!(kinds["mp4"], "video");
        assert_eq!(kinds["mkv"], "video");
        let err = file_kinds_from_groups(vec![
            ("video".to_string(), vec!["gif".to_string()]),
            ("image".to_string(), vec!["gif".to_string()]),
        ])
        .unwrap_err();
        assert!(err.contains("gif"), "{err}");
    }

    #[test]
    fn active_skills_declare_file_kinds() {
        let skills = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills");
        let ffmpeg = load_file_kinds(&skills.join("ffmpeg/skill.yaml")).unwrap();
        assert_eq!(ffmpeg["gif"], "video", "convert_video makes animated gifs");
        assert_ne!(ffmpeg["jpg"], ffmpeg["mp4"]);
        let documents = load_file_kinds(&skills.join("documents/skill.yaml")).unwrap();
        assert_eq!(documents["docx"], documents["pdf"]);
        assert!(load_file_kinds(Path::new("/no/such/skill.yaml"))
            .unwrap()
            .is_empty());
    }

    #[test]
    fn looks_like_filename_matches_python_regex() {
        assert!(looks_like_filename("clip.mp4"));
        assert!(looks_like_filename("a.mov"));
        assert!(looks_like_filename("x.jpeg"));
        assert!(!looks_like_filename("clip")); // no ext
        assert!(!looks_like_filename("H.264")); // digit-led ext
        assert!(!looks_like_filename("e.g")); // 1-char ext
        assert!(!looks_like_filename("file.")); // empty ext
    }

    // ── the stem exemption (mirrors Python's four tests in test_agent.py) ────────────────

    #[test]
    fn named_stem_resolving_to_a_real_file_is_not_hallucinated() {
        let p = plan(
            json!([{"tool": "resize_video", "args": {"inputs": ["clip_4k.mp4"], "height": 1080}}]),
        );
        let out = apply_clarify_gate(
            p,
            "downscale clip_4k to 1920x1080",
            &HashSet::new(),
            &files(&["clip.mp4", "clip_4k.mp4"]),
            &FileKinds::new(),
        );
        assert_eq!(out["plan"][0]["tool"], "resize_video");
    }

    #[test]
    fn named_stem_with_an_invented_extension_still_clarifies() {
        // clip_4k.mp4 is the file; the model took the extension from the other input.
        let p = plan(
            json!([{"tool": "concat_video", "args": {"inputs": ["clip.mov", "clip_4k.mov"]}}]),
        );
        let out = apply_clarify_gate(
            p,
            "join clip.mov and clip_4k together",
            &HashSet::new(),
            &files(&["clip.mov", "clip_4k.mp4"]),
            &FileKinds::new(),
        );
        assert_eq!(out["plan"][0]["tool"], "clarify");
    }

    #[test]
    fn a_bare_word_is_not_a_stem_even_when_the_file_exists() {
        // "the video" is English. Without the structural-marker rule this would plan against
        // a file the user never named.
        let p = plan(json!([{"tool": "compress_video", "args": {"inputs": ["video.mp4"]}}]));
        let out = apply_clarify_gate(
            p,
            "make the video smaller",
            &HashSet::new(),
            &files(&["video.mp4"]),
            &FileKinds::new(),
        );
        assert_eq!(out["plan"][0]["tool"], "clarify");
    }

    #[test]
    fn without_a_listing_the_strict_rule_holds() {
        let p = plan(
            json!([{"tool": "resize_video", "args": {"inputs": ["clip_4k.mp4"], "height": 1080}}]),
        );
        let out = gate(p, "downscale clip_4k to 1920x1080");
        assert_eq!(out["plan"][0]["tool"], "clarify");
    }

    // ── arg-shape gates ──────────────────────────────────────────────────────

    fn arg_reg(yaml: &str) -> crate::Registry {
        crate::registry::load_registry_str(yaml).expect("registry")
    }

    const VOL: &str = "adjust_volume:
  description: d
  required_args: [inputs]
  optional_args: [normalize, level]
clarify:
  description: d
  required_args: [question]
";

    #[test]
    fn unsupported_arg_on_a_known_tool_clarifies() {
        let r = arg_reg(VOL);
        let p = plan(json!([{"tool": "adjust_volume",
            "args": {"inputs": ["a.wav"], "target_sample_rate": 22050}}]));
        let out = unsupported_args_clarify(&p, &r).expect("should clarify");
        assert_eq!(out["plan"][0]["tool"], "clarify");
        let q = out["plan"][0]["args"]["question"].as_str().unwrap();
        assert!(q.contains("target_sample_rate"), "question was {q:?}");
        assert!(q.contains("support"), "question was {q:?}");
    }

    #[test]
    fn supported_args_pass_through() {
        let r = arg_reg(VOL);
        let p = plan(json!([{"tool": "adjust_volume",
            "args": {"inputs": ["a.wav"], "normalize": true}}]));
        assert!(unsupported_args_clarify(&p, &r).is_none());
    }

    #[test]
    fn an_unknown_tool_stays_a_hard_validation_error() {
        let r = arg_reg(VOL);
        let p = plan(json!([{"tool": "teleport", "args": {"whatever": 1}}]));
        assert!(unsupported_args_clarify(&p, &r).is_none());
    }

    #[test]
    fn terminal_tools_are_skipped_by_the_arg_gates() {
        let r = arg_reg(VOL);
        let p = plan(json!([{"tool": "clarify", "args": {"question": "q", "extra": 1}}]));
        assert!(unsupported_args_clarify(&p, &r).is_none());
    }

    #[test]
    fn every_offending_key_is_named() {
        let r = arg_reg(VOL);
        let p = plan(json!([{"tool": "adjust_volume",
            "args": {"inputs": ["a.wav"], "bitrate": "128k", "target_sr": 1}}]));
        let out = unsupported_args_clarify(&p, &r).unwrap();
        let q = out["plan"][0]["args"]["question"].as_str().unwrap();
        assert!(
            q.contains("bitrate") && q.contains("target_sr"),
            "question was {q:?}"
        );
    }

    #[test]
    fn a_missing_required_arg_clarifies() {
        let r = arg_reg(VOL);
        let p = plan(json!([{"tool": "adjust_volume", "args": {"normalize": true}}]));
        let out = required_args_clarify(&p, &r).expect("should clarify");
        assert_eq!(out["plan"][0]["tool"], "clarify");
        let q = out["plan"][0]["args"]["question"].as_str().unwrap();
        assert!(q.contains("inputs"), "question was {q:?}");
    }

    #[test]
    fn a_blank_required_arg_counts_as_missing() {
        let r = arg_reg(VOL);
        let p = plan(json!([{"tool": "adjust_volume", "args": {"inputs": "   "}}]));
        assert!(required_args_clarify(&p, &r).is_some());
    }

    #[test]
    fn any_of_args_with_none_present_clarifies() {
        let r = arg_reg(
            "rotate:
  description: d
  required_args: [input]
  optional_args: [angle, flip]
  any_of_args: [angle, flip]
",
        );
        let p = plan(json!([{"tool": "rotate", "args": {"input": "a.mp4"}}]));
        let out = required_args_clarify(&p, &r).expect("should clarify");
        let q = out["plan"][0]["args"]["question"].as_str().unwrap();
        assert!(
            q.contains("angle") && q.contains("flip"),
            "question was {q:?}"
        );
    }
}
