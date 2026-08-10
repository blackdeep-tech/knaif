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

use std::collections::HashSet;

use serde_json::{json, Value};

use crate::registry::Registry;

/// Terminal control tools carry no file inputs (mirrors Python `_TERMINAL_TOOLS`).
const TERMINAL_TOOLS: &[&str] = &["done", "clarify", "reject"];

/// Tools whose schema accepts an `output` arg — eligible chain-intermediate producers.
///
/// The test is `required_args | optional_args`, matching Python `_output_capable` and, more to the
/// point, the validator's own `allowed` set: an `arg_schemas` entry describes an arg's type but
/// does not make it accepted. Counting one as a producer writes `output` onto a tool that
/// `validate_plan` then rejects with "unsupported args", and repoints the downstream step at a file
/// nothing will produce.
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

/// Link chain intermediates, then return the plan unchanged or downgraded to a clarify.
///
/// Ordering mirrors Python `infer`: `_link_chain_intermediates` runs first (so a downstream
/// intermediate the producer didn't declare becomes that producer's `output` and is exempt),
/// then the hallucinated-input-filename guard. A plan with no `plan` array is returned as-is.
pub fn apply_clarify_gate(
    mut payload: Value,
    utterance: &str,
    output_capable: &HashSet<String>,
) -> Value {
    if let Some(steps) = payload.get_mut("plan").and_then(Value::as_array_mut) {
        link_chain_intermediates(steps, utterance, output_capable);
    }
    let Some(steps) = payload.get("plan").and_then(Value::as_array) else {
        return payload;
    };
    if let Some(name) = hallucinated_filename(steps, utterance) {
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
    forward_thread_reused_sources(plan, output_capable);
}

/// Basename of a path-ish string, lower-cased for comparison.
fn basename_lower(value: &str) -> String {
    value
        .replace('\\', "/")
        .rsplit('/')
        .next()
        .unwrap_or(value)
        .to_lowercase()
}

/// Derive a chain-intermediate filename from `src`, avoiding names already `taken`.
///
/// Preserves the directory prefix and extension and inserts a `-chained` marker
/// (`report.pdf` → `report-chained.pdf`). Port of Python `_intermediate_name`, including the
/// numbered fallback so repeated transforms of one source do not collide.
fn intermediate_name(src: &str, taken: &HashSet<String>) -> String {
    let norm = src.replace('\\', "/");
    let name = norm.rsplit('/').next().unwrap_or(&norm);
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
/// Port of Python `_forward_thread_reused_sources`, the second pass of chain linking — and the one
/// native was missing. The model often emits a correct-looking chain but points a later step at the
/// ORIGINAL source rather than the file an earlier step produced from it. For
/// "trim clip.mp4, compress it, and remove the audio" it emitted:
///
/// ```text
/// trim_video     clip.mp4        -> clip_trimmed.mp4
/// compress_video clip_trimmed.mp4                       (no output declared)
/// strip_audio    clip_trimmed.mp4                       <- step 1's output, not step 2's
/// ```
///
/// so the silent video was made from the *uncompressed* trim and the compression was discarded.
/// The first pass cannot fix it: `clip_trimmed.mp4` is already `produced`, so it is not an
/// undeclared intermediate. This pass gives the middle step an explicit intermediate `output` and
/// repoints the later reference at it.
///
/// Only output-capable producers are eligible — a read-only tool does not transform the file, so a
/// later reuse of its input is legitimate. A producer consuming several files (or a glob) is
/// skipped: one `output` cannot name many deliverables.
fn forward_thread_reused_sources(plan: &mut [Value], output_capable: &HashSet<String>) {
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

        // Reuse the producer's declared output, or mint an intermediate for it.
        let existing = plan[idx]
            .get("args")
            .and_then(|a| a.get("output"))
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        let out = match existing {
            Some(o) => o,
            None => {
                let name = intermediate_name(&sources[0], &produced);
                produced.insert(basename_lower(&name));
                match plan[idx].get_mut("args").filter(|a| a.is_object()) {
                    Some(a) => {
                        a.as_object_mut()
                            .unwrap()
                            .insert("output".into(), Value::String(name.clone()));
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
            match list_idx {
                Some(li) => {
                    if let Some(item) = args.get_mut(&key).and_then(|v| v.get_mut(li)) {
                        *item = Value::String(out.clone());
                    }
                }
                None => {
                    if let Some(slot) = args.get_mut(&key) {
                        *slot = Value::String(out.clone());
                    }
                }
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
pub fn hallucinated_filename(plan: &[Value], utterance: &str) -> Option<String> {
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
                if !u_lower.contains(&vl) {
                    return Some(v.to_string());
                }
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
        apply_clarify_gate(payload, utterance, &HashSet::new())
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
        let out = apply_clarify_gate(p, "rotate clip.mp4 90 degrees then compress it", &capable);
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

    /// The regression that revealed the missing second pass.
    ///
    /// "trim clip.mp4 to the first 4 seconds, compress it, and remove the audio" produced a plan
    /// whose third step consumed step *one's* output, so the silent video was built from the
    /// uncompressed trim and the compression was thrown away. Python repairs this via
    /// `_forward_thread_reused_sources`; native had no equivalent. Found by
    /// `parity_check.py --mode command --strict`, which plan-envelope parity cannot detect.
    ///
    /// The expected values are Python's actual output for this input, including the `-chained`
    /// intermediate name.
    #[test]
    fn reused_source_is_forward_threaded_onto_the_producer_output() {
        let mut plan = vec![
            json!({"tool": "trim_video", "args": {"input": "clip.mp4", "output": "clip_trimmed.mp4"}}),
            json!({"tool": "compress_video", "args": {"inputs": ["clip_trimmed.mp4"]}}),
            json!({"tool": "strip_audio", "args": {"inputs": ["clip_trimmed.mp4"]}}),
        ];
        let capable: HashSet<String> = ["trim_video", "compress_video", "strip_audio"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        link_chain_intermediates(
            &mut plan,
            "trim clip.mp4 to the first 4 seconds, compress it, and remove the audio",
            &capable,
        );

        assert_eq!(
            plan[1]["args"]["output"], "clip_trimmed-chained.mp4",
            "the middle step must declare an intermediate output"
        );
        assert_eq!(
            plan[2]["args"]["inputs"][0], "clip_trimmed-chained.mp4",
            "the last step must consume the COMPRESSED file, not the raw trim"
        );
    }

    #[test]
    fn intermediate_names_do_not_collide() {
        let mut taken: HashSet<String> = HashSet::new();
        let first = intermediate_name("clip.mp4", &taken);
        assert_eq!(first, "clip-chained.mp4");
        taken.insert(first.to_lowercase());
        assert_eq!(intermediate_name("clip.mp4", &taken), "clip-chained2.mp4");
    }

    #[test]
    fn a_read_only_reuse_is_left_alone() {
        // `inspect_media` is not output-capable: it does not transform the file, so a later step
        // reusing its input is legitimate and must not be repointed.
        let mut plan = vec![
            json!({"tool": "inspect_media", "args": {"inputs": ["clip.mp4"]}}),
            json!({"tool": "strip_audio", "args": {"inputs": ["clip.mp4"]}}),
        ];
        let capable: HashSet<String> = ["strip_audio"].iter().map(|s| s.to_string()).collect();
        link_chain_intermediates(&mut plan, "inspect clip.mp4 and remove its audio", &capable);
        assert_eq!(plan[1]["args"]["inputs"][0], "clip.mp4");
        assert!(plan[0]["args"].get("output").is_none());
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
}
