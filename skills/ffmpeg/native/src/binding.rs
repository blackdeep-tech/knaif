//! Plan-level output naming: rewrite names the filesystem would reject, rebind the readers.
//!
//! Port of the illegal-name half of Python's `_collisions.py`. The collision half (an output
//! that would truncate its own input) is **not** ported yet — `apps/cli` has no plan-level
//! collision pass — so this module covers only the rule it needs to cover, and says so rather
//! than implying parity it does not have.

use serde_json::Value;

/// Tools that carry no filenames; their args are prose, and a `question` that happens to quote a
/// filename must not be rewritten.
const TERMINAL_TOOLS: &[&str] = &["clarify", "reject", "done", "wait_for_confirmation"];

/// Args that name what a step writes. `output_path` is the internal spelling used once intents
/// have expanded; `output` is what the model emits.
const OUTPUT_KEYS: &[&str] = &["output", "output_path"];

/// Rewrite outputs the filesystem would reject, and rebind the steps that read them.
///
/// `ffmpeg_268` named its output after the time range it was given —
/// `clip_trimmed_00:00:00.mp4` — and then referenced that same string as the next step's input.
/// Colons are legal on Linux and illegal on Windows, so ffmpeg refused to open it (*Error opening
/// output files: Invalid argument*) and the chain died at step 1 having written nothing.
///
/// **Rebinding is the whole point, not a detail.** Sanitising the output alone leaves step 1
/// writing `clip_trimmed_00-00-00.mp4` while step 2 still reads the colon spelling, which unlinks
/// the chain silently — a worse failure than the loud one it replaces.
///
/// Only a name a step **declares as its output** is rewritten. An input nobody produces is left
/// exactly as written, so a real file whose name contains one of these characters — which Windows
/// cannot have but Linux can — stays reachable. That also keeps the pass decidable from the plan
/// alone, with no filesystem lookup, so both runtimes agree without consulting a disk.
///
/// Returns one `(requested, used)` pair per rewrite, in plan order.
pub fn bind_legal_output_names(steps: &mut [Value]) -> Vec<(String, String)> {
    let mut renames = Vec::new();
    for idx in 0..steps.len() {
        if is_terminal(&steps[idx]) {
            continue;
        }
        for key in OUTPUT_KEYS {
            let raw = match steps[idx].get("args").and_then(|a| a.get(key)) {
                Some(Value::String(s)) if !s.is_empty() => s.clone(),
                _ => continue,
            };
            let legal = crate::engine::legal_output_path(&raw);
            if legal == raw {
                continue;
            }
            if let Some(slot) = steps[idx]
                .get_mut("args")
                .and_then(|a| a.get_mut(key.to_string()))
            {
                *slot = Value::String(legal.clone());
            }
            // Strictly after the producer, exactly as Python scopes its substitution: the
            // producer keeps reading whatever it reads.
            for later in steps[idx + 1..].iter_mut() {
                if is_terminal(later) {
                    continue;
                }
                if let Some(args) = later.get_mut("args") {
                    replace_exact(args, &raw, &legal);
                }
            }
            renames.push((raw, legal));
        }
    }
    renames
}

fn is_terminal(step: &Value) -> bool {
    step.get("tool")
        .and_then(Value::as_str)
        .is_some_and(|t| TERMINAL_TOOLS.contains(&t))
}

/// Replace every string in `value` that is EXACTLY `from`.
///
/// Exact equality rather than a filename heuristic: the string being matched is one a step already
/// declared as its output, so anything equal to it is a reference to that output. A substring rule
/// would corrupt an unrelated arg that merely contains the name.
fn replace_exact(value: &mut Value, from: &str, to: &str) {
    match value {
        Value::String(s) if s == from => *s = to.to_string(),
        Value::Array(items) => items.iter_mut().for_each(|v| replace_exact(v, from, to)),
        Value::Object(map) => map.values_mut().for_each(|v| replace_exact(v, from, to)),
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn plan_268() -> Vec<Value> {
        vec![
            json!({"tool": "trim_video", "args": {
                "input": "clip.mp4", "start": "00:00:00",
                "output": "clip_trimmed_00:00:00.mp4"}}),
            json!({"tool": "extract_audio", "args": {
                "inputs": ["clip_trimmed_00:00:00.mp4"]}}),
        ]
    }

    #[test]
    fn a_chain_link_survives_an_illegal_output_name() {
        let mut steps = plan_268();
        let renames = bind_legal_output_names(&mut steps);
        assert_eq!(renames.len(), 1);
        let out = steps[0]["args"]["output"].as_str().unwrap();
        assert_eq!(out, "clip_trimmed_00-00-00.mp4");
        // The link is the point: step 2 must read what step 1 actually wrote.
        assert_eq!(steps[1]["args"]["inputs"][0].as_str().unwrap(), out);
    }

    #[test]
    fn a_producer_keeps_reading_its_own_input() {
        // Substitution is scoped to steps AFTER the producer.
        let mut steps = vec![json!({"tool": "convert_video", "args": {
            "inputs": ["a:b.mp4"], "output": "a:b.mp4"}})];
        bind_legal_output_names(&mut steps);
        assert_eq!(steps[0]["args"]["inputs"][0].as_str().unwrap(), "a:b.mp4");
        assert_eq!(steps[0]["args"]["output"].as_str().unwrap(), "a-b.mp4");
    }

    #[test]
    fn an_input_nobody_produces_is_left_alone() {
        // Windows cannot have such a file; Linux can, and it must stay reachable.
        let mut steps = vec![json!({"tool": "extract_audio", "args": {
            "inputs": ["2026-09-16T10:30:00.mp4"]}})];
        assert!(bind_legal_output_names(&mut steps).is_empty());
        assert_eq!(
            steps[0]["args"]["inputs"][0].as_str().unwrap(),
            "2026-09-16T10:30:00.mp4"
        );
    }

    #[test]
    fn a_legal_plan_is_untouched() {
        let mut steps = vec![json!({"tool": "convert_video", "args": {
            "inputs": ["clip.mp4"], "output": "out/clip.mkv"}})];
        let before = steps.clone();
        assert!(bind_legal_output_names(&mut steps).is_empty());
        assert_eq!(steps, before);
    }

    #[test]
    fn a_clarify_question_quoting_a_filename_is_not_rewritten() {
        let mut steps = vec![
            json!({"tool": "trim_video", "args": {"input": "c.mp4", "output": "c:1.mp4"}}),
            json!({"tool": "clarify", "args": {"question": "Did you mean c:1.mp4?"}}),
        ];
        bind_legal_output_names(&mut steps);
        assert_eq!(
            steps[1]["args"]["question"].as_str().unwrap(),
            "Did you mean c:1.mp4?"
        );
    }
}
