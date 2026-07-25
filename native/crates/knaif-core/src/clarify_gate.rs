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
pub fn output_capable_tools(registry: &Registry) -> HashSet<String> {
    registry
        .iter()
        .filter(|(_, d)| {
            d.optional_args.iter().any(|a| a == "output")
                || d.required_args.iter().any(|a| a == "output")
                || d.arg_schemas.contains_key("output")
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
