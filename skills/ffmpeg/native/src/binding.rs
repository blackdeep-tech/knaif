//! Plan-level output naming: rewrite names the filesystem would reject, rebind the readers,
//! and rename an output that would truncate its own input.
//!
//! Port of Python's `_collisions.py` — both halves. Every rendered ffmpeg command carries `-y`,
//! so an output equal to one of its own inputs destroys the source before ffmpeg reads a frame.
//!
//! **The binding rule.**
//!
//! > A name an earlier step declares it will write binds, for every later step, to what that
//! > step actually wrote. Collision handling SUBSTITUTES the old output name with the resolved
//! > one across steps strictly after the producer; it never re-infers which file was meant. A
//! > name no earlier step writes binds to the file on disk.
//!
//! **Which of the two names moves.** The binding rule says what a later reference means; it does
//! not say which side of a self-overwrite gets renamed, and the two shapes do not want the same
//! answer:
//!
//! > A file the plan itself produced is an intermediate, so the PRODUCER's output moves. A file
//! > that was already on disk cannot be renamed at all, so the CONSUMER's output moves.
//!
//! `ffmpeg_175` ("convert clip.mp4 to mp4, lossless") is the second shape — nothing in the plan
//! wrote `clip.mp4`, so writing the copy to `clip_converted.mp4` is the only move available. The
//! first shape is `trim -> Test1.mov` feeding `convert Test1.mov -> Test1.mov`, where
//! `trim_video` accepts neither `container` nor `quality` and the chain is therefore forced.
//! There the name is the user's and belongs to the branch's last step; renaming the consumer
//! instead leaves `Test1.mov` holding the un-converted trim output — the wrong content under the
//! right name, reported as success.
//!
//! Kept deliberately parallel to `_collisions.py`: same order of passes, same helper names, same
//! reasons in the same places, because the two are read side by side when they disagree.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

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

/// Tools whose handler resolves a relative output against the **sandbox** rather than against
/// the first input's directory. `RunConcatStep` does exactly that, while every per-file mode
/// goes through `build_one_recipe` and resolves against the input's parent. One rule for both is
/// wrong in both directions: a genuine collision survives, and a good destination gets renamed.
const SANDBOX_OUTPUT_TOOLS: &[&str] = &["concat_video", "run_concat"];

/// Suffix for the on-disk shape: the consumer's output moves out of the way.
const COLLISION_SUFFIX: &str = "_converted";

/// Suffix for the other repair: renaming a chained intermediate so the name the user asked for
/// stays on the step that actually produces what they described. Calling that file `_converted`
/// would be a lie — it is the input to the conversion, not its result.
const INTERMEDIATE_SUFFIX: &str = "_intermediate";

/// True if `value` ends with a file extension: `.` then a letter then 1-4 alphanumerics.
///
/// Mirrors Python `_FILENAME_RE = \.[a-z][a-z0-9]{1,4}$` (case-insensitive) and
/// `knaif_core::clarify_gate::looks_like_filename`. Restated here rather than imported because
/// this crate depends on `knaif-skill-api` and deliberately not on `knaif-core` — a skill is a
/// plugin, and reaching into the runtime to borrow a five-line predicate would invert that.
/// Anchored at the end, so `H.264` (digit-led) and bare stems (`clip`) do not match.
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

/// A filename the plan can act on: has an extension, and is not a glob.
fn is_filename(value: &Value) -> bool {
    value
        .as_str()
        .is_some_and(|s| !s.is_empty() && looks_like_filename(s) && !s.contains(['*', '?']))
}

/// Every `(key, index)` in `args` holding a filename the step reads. `index` is `Some` for a
/// member of an array-valued arg (`inputs`), `None` for a scalar one (`input`, `base`).
///
/// Returned as owned locations rather than borrows because the caller mutates the same map while
/// walking it — the Python version can hold a container reference, Rust cannot.
fn input_refs(args: &Value) -> Vec<(String, Option<usize>)> {
    let Some(map) = args.as_object() else {
        return Vec::new();
    };
    let mut refs = Vec::new();
    for (key, value) in map {
        if OUTPUT_KEYS.contains(&key.as_str()) {
            continue;
        }
        match value {
            Value::Array(items) => {
                for (i, item) in items.iter().enumerate() {
                    if is_filename(item) {
                        refs.push((key.clone(), Some(i)));
                    }
                }
            }
            _ if is_filename(value) => refs.push((key.clone(), None)),
            _ => {}
        }
    }
    refs
}

fn ref_str(args: &Value, key: &str, index: Option<usize>) -> Option<String> {
    let at = args.get(key)?;
    match index {
        Some(i) => at.get(i)?.as_str().map(str::to_string),
        None => at.as_str().map(str::to_string),
    }
}

fn set_ref(args: &mut Value, key: &str, index: Option<usize>, value: String) {
    let Some(at) = args.get_mut(key) else { return };
    let slot = match index {
        Some(i) => at.get_mut(i),
        None => Some(at),
    };
    if let Some(slot) = slot {
        *slot = Value::String(value);
    }
}

/// Absolute path for an input: a relative one is relative to the **sandbox**.
fn resolve_input(raw: &str, sandbox: Option<&Path>) -> PathBuf {
    let p = Path::new(raw);
    if p.is_absolute() {
        return p.to_path_buf();
    }
    match sandbox {
        Some(sb) => sb.join(p),
        None => std::env::current_dir().unwrap_or_default().join(p),
    }
}

/// Directory a relative *output* resolves against — which depends on the tool.
///
/// **Inputs and outputs do not share a base.** `build_one_recipe` resolves an input to an
/// absolute probe path and then resolves a relative output against the input's parent, so for
/// `inputs=["a/clip.mp4"], output="a/clip.mp4"` the input is `<sandbox>/a/clip.mp4` and the
/// output is `<sandbox>/a/a/clip.mp4`. Using one base for both moves every comparison into a
/// directory that does not exist.
///
/// **And the base is not the same for every tool.** `concat_video` writes one output for many
/// inputs, so its handler resolves it against the sandbox; "the first input's parent" is
/// meaningless there and gets it wrong both ways.
fn output_base(tool: &str, args: &Value, sandbox: Option<&Path>) -> Option<PathBuf> {
    if SANDBOX_OUTPUT_TOOLS.contains(&tool) {
        return sandbox.map(Path::to_path_buf);
    }
    for (key, index) in input_refs(args) {
        if let Some(raw) = ref_str(args, &key, index) {
            return resolve_input(&raw, sandbox).parent().map(Path::to_path_buf);
        }
    }
    sandbox.map(Path::to_path_buf)
}

/// Absolute path for an output, resolved the way `build_one_recipe` resolves it.
fn resolve_output(raw: &str, out_base: Option<&Path>) -> PathBuf {
    let p = Path::new(raw);
    if p.is_absolute() {
        return p.to_path_buf();
    }
    match out_base {
        Some(base) => base.join(p),
        None => std::env::current_dir().unwrap_or_default().join(p),
    }
}

/// First free `<stem><suffix>[_N]<.ext>`. **Never returns `requested` itself.**
///
/// Always advancing is the contract, not a detail. The caller only reaches here once a
/// self-overwrite is established, and a chained intermediate that does not exist on disk yet
/// collides exactly as hard as one that does — checking only `exists()` hands the colliding path
/// straight back, leaving the `-y` truncation in place while reporting a rename that did not
/// happen, and makes the result depend on whether the plan had been run before.
fn next_free_output(requested: &Path, taken: &HashSet<PathBuf>, suffix: &str) -> PathBuf {
    let stem = requested
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or_default()
        .to_string();
    let ext = requested.extension().and_then(|s| s.to_str()).unwrap_or("");
    let with_name = |name: String| -> PathBuf {
        let mut p = requested.to_path_buf();
        p.set_file_name(if ext.is_empty() {
            name
        } else {
            format!("{name}.{ext}")
        });
        p
    };
    let is_free = |c: &PathBuf| !taken.contains(c) && !c.exists();

    let mut candidate = with_name(format!("{stem}{suffix}"));
    let mut n = 2;
    while !is_free(&candidate) {
        candidate = with_name(format!("{stem}{suffix}_{n}"));
        n += 1;
    }
    candidate
}

/// The step that declares it writes `target`, searched backwards from `before`.
///
/// Backwards because the binding rule is positional: when two earlier steps write the same name,
/// the one a step at `before` reads is the nearer of them. `None` means nothing in the plan
/// writes it — the case that matters, since it distinguishes a chained intermediate from a file
/// that was already on disk.
fn producer_of(
    steps: &[Value],
    target: &Path,
    before: usize,
    sandbox: Option<&Path>,
) -> Option<(usize, String)> {
    for j in (0..before).rev() {
        let step = &steps[j];
        if is_terminal(step) {
            continue;
        }
        let Some(args) = step.get("args") else {
            continue;
        };
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let base = output_base(tool, args, sandbox);
        for key in OUTPUT_KEYS {
            let Some(raw) = args
                .get(key)
                .filter(|v| is_filename(v))
                .and_then(Value::as_str)
            else {
                continue;
            };
            if resolve_output(raw, base.as_deref()) == target {
                return Some((j, (*key).to_string()));
            }
        }
    }
    None
}

/// Write `chosen` back in the shape the reference was written in.
///
/// An absolute reference stays absolute; `a/clip.mp4` keeps its `a/`; a bare name stays bare.
/// Collapsing everything to a basename silently relocates the file — the same defect as matching
/// on basenames, arriving on the way out instead of the way in.
fn respell(original: &str, chosen: &Path) -> String {
    let raw = Path::new(original);
    if raw.is_absolute() {
        return chosen.to_string_lossy().into_owned();
    }
    let name = chosen
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    match raw.parent() {
        Some(parent) if !parent.as_os_str().is_empty() && parent != Path::new(".") => {
            format!("{}/{}", parent.to_string_lossy().replace('\\', "/"), name)
        }
        _ => name,
    }
}

fn file_name_of(p: &Path) -> String {
    p.file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Rewrite every later reference to `from` so it reads `chosen`, over `range` of the plan.
fn rebind_readers(
    steps: &mut [Value],
    range: std::ops::Range<usize>,
    from: &Path,
    chosen: &Path,
    sandbox: Option<&Path>,
) {
    for later in steps[range].iter_mut() {
        if is_terminal(later) {
            continue;
        }
        let Some(largs) = later.get_mut("args") else {
            continue;
        };
        for (key, index) in input_refs(largs) {
            let Some(raw) = ref_str(largs, &key, index) else {
                continue;
            };
            if resolve_input(&raw, sandbox) == from {
                set_ref(largs, &key, index, respell(&raw, chosen));
            }
        }
    }
}

/// Every rename this pass applied, kept apart by cause.
///
/// Python collects both into one `output_substitutions` list and reports them with a single
/// "would have been overwritten" sentence, which is wrong for the illegal-name half. Keeping them
/// separate changes no plan content — only what the user is told — so the two runtimes still
/// produce identical plans, which is what parity is measured on.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct PlanRenames {
    /// Outputs the filesystem would have rejected (`clip_00:00:00.mp4` on Windows).
    pub illegal: Vec<(String, String)>,
    /// Outputs that would have truncated one of their own inputs under `-y`.
    pub collisions: Vec<(String, String)>,
}

/// Rename outputs that would overwrite their own input; rebind later references.
///
/// Returns the renames in plan order so the caller can tell the user what happened. Silently
/// overwriting and hard-refusing are both wrong answers — `ffmpeg_175` expects a *plan*, because
/// the user asked for a copy.
pub fn rebind_colliding_outputs(steps: &mut [Value], sandbox: Option<&Path>) -> PlanRenames {
    // Before anything resolves a path: a name the filesystem would reject is not a collision
    // candidate, it is not a valid path at all. Runs first so the walk below sees the names that
    // will actually be written.
    let mut renames = PlanRenames {
        illegal: bind_legal_output_names(steps),
        collisions: Vec::new(),
    };

    // Every path the plan reads, and every path it declares it will write. Both are spoken for
    // before the walk begins: a fallback chosen at step 0 has to avoid the output step 3 is going
    // to produce, and stepwise accumulation cannot see that far ahead.
    let mut plan_paths: HashSet<PathBuf> = HashSet::new();
    for step in steps.iter() {
        if is_terminal(step) {
            continue;
        }
        let Some(args) = step.get("args") else {
            continue;
        };
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let base = output_base(tool, args, sandbox);
        for (key, index) in input_refs(args) {
            if let Some(raw) = ref_str(args, &key, index) {
                plan_paths.insert(resolve_input(&raw, sandbox));
            }
        }
        for key in OUTPUT_KEYS {
            if let Some(raw) = args
                .get(key)
                .filter(|v| is_filename(v))
                .and_then(Value::as_str)
            {
                plan_paths.insert(resolve_output(raw, base.as_deref()));
            }
        }
    }

    for idx in 0..steps.len() {
        if is_terminal(&steps[idx]) {
            continue;
        }
        let Some(args) = steps[idx].get("args") else {
            continue;
        };
        let tool = steps[idx]
            .get("tool")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let base = output_base(&tool, args, sandbox);

        let Some(out_key) = OUTPUT_KEYS
            .iter()
            .find(|k| args.get(**k).is_some_and(is_filename))
            .map(|k| (*k).to_string())
        else {
            continue;
        };
        let out_raw = match args.get(&out_key).and_then(Value::as_str) {
            Some(s) => s.to_string(),
            None => continue,
        };
        let requested = resolve_output(&out_raw, base.as_deref());

        let own_inputs: HashSet<PathBuf> = input_refs(args)
            .into_iter()
            .filter_map(|(k, i)| ref_str(args, &k, i))
            .map(|raw| resolve_input(&raw, sandbox))
            .collect();
        if !own_inputs.contains(&requested) {
            // Not a self-overwrite. An explicit output landing on some *other* existing file is
            // the user naming a destination, and `-y` overwriting it is what they asked for —
            // renaming there would be the tool second-guessing a clear request.
            plan_paths.insert(requested);
            continue;
        }

        // WHICH name moves depends on where the file being overwritten came from.
        //
        // A file this plan produced is a chained INTERMEDIATE, and the name on it is the one the
        // user asked for — they described the branch's end product ("cut, then convert to mov
        // lossless, name it Test1"), and the model put that name on every step of the branch
        // rather than only its last. Move the intermediate and the user's name stays on the file
        // that holds what they described. Move the destination instead and the name lands on the
        // un-converted trim output: the wrong content under the right name, which is worse than
        // the loud failure it replaces, because nothing reports it.
        if let Some((prod_idx, prod_key)) = producer_of(steps, &requested, idx, sandbox) {
            let chosen = next_free_output(&requested, &plan_paths, INTERMEDIATE_SUFFIX);
            let prod_raw = steps[prod_idx]
                .get("args")
                .and_then(|a| a.get(&prod_key))
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            if let Some(pargs) = steps[prod_idx].get_mut("args") {
                set_ref(pargs, &prod_key, None, respell(&prod_raw, &chosen));
            }
            // Scoped to the producer's consumers — steps after the producer, up to and INCLUDING
            // this one. Past this step the name means what this step writes, which is still
            // `requested`; rewriting those would feed them the intermediate.
            rebind_readers(steps, prod_idx + 1..idx + 1, &requested, &chosen, sandbox);
            plan_paths.insert(chosen.clone());
            renames
                .collisions
                .push((file_name_of(&requested), file_name_of(&chosen)));
            continue;
        }

        // Nothing in the plan wrote it, so it is a file on disk and the output is the only name
        // that can move — `ffmpeg_175`, "create a lossless copy of clip.mp4".
        let chosen = next_free_output(&requested, &plan_paths, COLLISION_SUFFIX);
        if let Some(args) = steps[idx].get_mut("args") {
            set_ref(args, &out_key, None, respell(&out_raw, &chosen));
        }
        plan_paths.insert(chosen.clone());
        renames
            .collisions
            .push((file_name_of(&requested), file_name_of(&chosen)));

        // Strictly after the producer: the producer keeps reading its own original input.
        let end = steps.len();
        rebind_readers(steps, idx + 1..end, &requested, &chosen, sandbox);
    }

    renames
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

    // ── which name moves: the intermediate, or the destination ───────────────
    //
    // Mirrors `skills/ffmpeg/python/tests/test_output_collision_naming.py`. Both runtimes have
    // to pick the SAME side, or one of them writes the user's name onto different content.

    use std::path::PathBuf;

    /// A sandbox that exists but holds nothing, so `next_free_output`'s `exists()` check has a
    /// real directory to consult and the tests do not depend on the repo's cwd.
    fn empty_sandbox() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("knaif-collide-{}", std::process::id() as u64));
        let _ = std::fs::create_dir_all(&dir);
        dir
    }

    fn trim(src: &str, out: &str) -> Value {
        json!({"tool": "trim_video", "args": {"input": src, "output": out}})
    }

    fn convert(src: &str, out: &str) -> Value {
        json!({"tool": "convert_video", "args": {
            "inputs": [src], "output": out, "container": "mov"}})
    }

    #[test]
    fn a_chained_intermediate_moves_and_the_destination_keeps_its_name() {
        // `trim_video` takes neither container nor quality, so trim → convert is forced. The
        // user named the CONVERTED file; the model put that name on both steps.
        let sb = empty_sandbox();
        let mut steps = vec![
            trim("clip.mp4", "Test1.mov"),
            convert("Test1.mov", "Test1.mov"),
        ];
        let renames = rebind_colliding_outputs(&mut steps, Some(&sb));

        assert_eq!(steps[0]["args"]["output"], "Test1_intermediate.mov");
        assert_eq!(steps[1]["args"]["inputs"][0], "Test1_intermediate.mov");
        // The whole point: the name the user asked for stays on the conversion's result.
        assert_eq!(steps[1]["args"]["output"], "Test1.mov");
        assert_eq!(
            renames.collisions,
            vec![(
                "Test1.mov".to_string(),
                "Test1_intermediate.mov".to_string()
            )]
        );
        assert!(renames.illegal.is_empty());
    }

    #[test]
    fn a_step_after_the_destination_still_reads_the_destination() {
        // Substitution is scoped to the producer's consumers — up to and INCLUDING the
        // colliding step. Past it, the name means what that step wrote.
        let sb = empty_sandbox();
        let mut steps = vec![
            trim("clip.mp4", "Test1.mov"),
            convert("Test1.mov", "Test1.mov"),
            json!({"tool": "concat_video", "args": {
                "base": "Test1.mov", "append": ["other.mov"], "output": "Test3.mov"}}),
        ];
        rebind_colliding_outputs(&mut steps, Some(&sb));

        assert_eq!(steps[1]["args"]["output"], "Test1.mov");
        // Not the intermediate: the concat wants the converted file.
        assert_eq!(steps[2]["args"]["base"], "Test1.mov");
    }

    #[test]
    fn an_input_the_plan_never_wrote_moves_the_output_instead() {
        // `ffmpeg_175`, "create a lossless copy of clip.mp4". Nothing in the plan produced
        // clip.mp4, so it is a file on disk and cannot be renamed — the output is the only
        // name that can move. This is the shape the direction rule must NOT break.
        let sb = empty_sandbox();
        let mut steps = vec![convert("clip.mp4", "clip.mp4")];
        let renames = rebind_colliding_outputs(&mut steps, Some(&sb));

        assert_eq!(steps[0]["args"]["inputs"][0], "clip.mp4");
        assert_eq!(steps[0]["args"]["output"], "clip_converted.mp4");
        assert_eq!(
            renames.collisions,
            vec![("clip.mp4".to_string(), "clip_converted.mp4".to_string())]
        );
    }

    #[test]
    fn the_two_branch_chain_that_found_this() {
        // The utterance that started it: strip audio, two trim→convert branches named Test1
        // and Test2, concatenated into Test3.
        let sb = empty_sandbox();
        let mut steps = vec![
            json!({"tool": "strip_audio", "args": {
                "inputs": ["clip.mp4"], "output": "clip_silent.mp4"}}),
            json!({"tool": "trim_video", "args": {
                "input": "clip_silent.mp4", "start": "00:00:01", "end": "00:00:03",
                "output": "Test1.mov"}}),
            convert("Test1.mov", "Test1.mov"),
            json!({"tool": "trim_video", "args": {
                "input": "clip_silent.mp4", "start": "00:00:03", "end": "00:00:06",
                "output": "Test2.mov"}}),
            convert("Test2.mov", "Test2.mov"),
            json!({"tool": "concat_video", "args": {
                "base": "Test1.mov", "append": ["Test2.mov"], "output": "Test3.mov"}}),
        ];
        rebind_colliding_outputs(&mut steps, Some(&sb));

        assert_eq!(steps[1]["args"]["output"], "Test1_intermediate.mov");
        assert_eq!(steps[2]["args"]["inputs"][0], "Test1_intermediate.mov");
        assert_eq!(steps[2]["args"]["output"], "Test1.mov");
        assert_eq!(steps[3]["args"]["output"], "Test2_intermediate.mov");
        assert_eq!(steps[4]["args"]["inputs"][0], "Test2_intermediate.mov");
        assert_eq!(steps[4]["args"]["output"], "Test2.mov");
        // The deliverables the user actually asked for, joined under the name they chose.
        assert_eq!(steps[5]["args"]["base"], "Test1.mov");
        assert_eq!(steps[5]["args"]["append"][0], "Test2.mov");
        assert_eq!(steps[5]["args"]["output"], "Test3.mov");

        // And the invariant behind all of it: no step writes what it reads.
        for step in &steps {
            let args = &step["args"];
            let out = args["output"].as_str().unwrap();
            for (key, index) in input_refs(args) {
                assert_ne!(
                    ref_str(args, &key, index).unwrap(),
                    out,
                    "step {} still truncates its input",
                    step["tool"]
                );
            }
        }
    }

    #[test]
    fn a_plan_with_no_collision_is_left_alone() {
        let sb = empty_sandbox();
        let mut steps = vec![trim("clip.mp4", "cut.mp4"), convert("cut.mp4", "out.mov")];
        let before = steps.clone();
        assert_eq!(
            rebind_colliding_outputs(&mut steps, Some(&sb)),
            PlanRenames::default()
        );
        assert_eq!(steps, before);
    }

    #[test]
    fn a_destination_that_is_not_an_input_is_the_users_choice() {
        // Writing over some OTHER existing file is what `-y` is for — the user named it.
        let sb = empty_sandbox();
        let mut steps = vec![convert("a.mp4", "b.mov")];
        assert_eq!(
            rebind_colliding_outputs(&mut steps, Some(&sb)),
            PlanRenames::default()
        );
        assert_eq!(steps[0]["args"]["output"], "b.mov");
    }

    #[test]
    fn an_output_resolves_against_the_input_parent_not_the_sandbox() {
        // Inputs and outputs do NOT share a base. `build_one_recipe` resolves a relative output
        // against the input's parent, so `inputs=["sub/clip.mp4"], output="clip.mp4"` is a real
        // self-overwrite: both land on `<sandbox>/sub/clip.mp4`. Reading the output against the
        // sandbox instead would miss it and let `-y` truncate the source.
        let sb = empty_sandbox();
        let mut steps = vec![convert("sub/clip.mp4", "clip.mp4")];
        let renames = rebind_colliding_outputs(&mut steps, Some(&sb));
        assert_eq!(
            renames.collisions.len(),
            1,
            "the self-overwrite must be seen"
        );
        // A bare reference stays bare — it is already inside `sub/` by resolution.
        assert_eq!(steps[0]["args"]["output"], "clip_converted.mp4");
    }

    #[test]
    fn the_same_directory_on_both_sides_is_not_a_collision() {
        // The flip side of the rule above, and counter-intuitive enough to pin: with the output
        // resolved against the input's parent, `sub/clip.mp4 -> sub/clip.mp4` writes to
        // `<sandbox>/sub/sub/clip.mp4` and overwrites nothing. Python does the same; asserted
        // here so a future "fix" to one runtime has to explain itself to the other.
        let sb = empty_sandbox();
        let mut steps = vec![convert("sub/clip.mp4", "sub/clip.mp4")];
        assert_eq!(
            rebind_colliding_outputs(&mut steps, Some(&sb)),
            PlanRenames::default()
        );
        assert_eq!(steps[0]["args"]["output"], "sub/clip.mp4");
    }

    #[test]
    fn an_absolute_reference_stays_absolute_through_a_rename() {
        // Collapsing to a basename would silently relocate the file to the sandbox root.
        let sb = empty_sandbox();
        let abs = sb.join("clip.mp4");
        let abs = abs.to_str().unwrap();
        let mut steps = vec![convert(abs, abs)];
        let renames = rebind_colliding_outputs(&mut steps, Some(&sb));
        assert_eq!(renames.collisions.len(), 1);
        let out = steps[0]["args"]["output"].as_str().unwrap();
        assert!(Path::new(out).is_absolute(), "{out} lost its directory");
        assert_eq!(Path::new(out).file_name().unwrap(), "clip_converted.mp4");
        assert_eq!(Path::new(out).parent(), Some(sb.as_path()));
    }

    #[test]
    fn concat_resolves_its_output_against_the_sandbox_not_an_input_parent() {
        // `concat_video` writes one output for many inputs, so its handler resolves the output
        // against the sandbox. Using "the first input's parent" here reports no collision and
        // lets the plan truncate its own input under `-y`.
        let sb = empty_sandbox();
        let mut steps = vec![json!({"tool": "concat_video", "args": {
            "base": "sub/a.mp4", "append": ["sub/b.mp4"], "output": "sub/a.mp4"}})];
        let renames = rebind_colliding_outputs(&mut steps, Some(&sb));
        assert_eq!(
            renames.collisions.len(),
            1,
            "the self-overwrite must be seen"
        );
        assert_eq!(steps[0]["args"]["output"], "sub/a_converted.mp4");
    }
}
