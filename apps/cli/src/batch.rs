//! A batch chain in the native run: the plan's chain runs once per file, and one
//! file's failure stops only that file. Port of Python `CommandAgent._lift_batch_chain` and the
//! isolation in `CommandAgent._execute_plan` (`failed`, `_join_without_failed`, `BatchReport`).
//!
//! The lift itself is `knaif_core::batch_chain::lift_batch` (contracts/parity/
//! batch_chain_cases.json); this module expands the globs on disk, runs the lifted plan with
//! per-file isolation, and prints what Python prints: each failure as it happens and the end
//! summary, both on stderr (contracts/parity/batch_report_cases.json).

use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::time::Instant;

use knaif_core::batch_chain::{expand_inputs, failure_line, first_line, lift_batch, BatchLift};
use serde_json::Value;

use crate::StepOutcome;

/// Why the last failed command failed, in a sentence (ffmpeg's own cause, `moov atom not
/// found`). A step's error says only "1 of 1 command(s) failed"; the batch reason is the cause,
/// as Python's is the step's own words.
static FAILURE_CAUSE: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

/// Record why a command failed, for the batch's failure line.
pub fn note_failure_cause(cause: &str) {
    if let Ok(mut c) = FAILURE_CAUSE.lock() {
        *c = Some(cause.to_string());
    }
}

fn take_failure_cause() -> Option<String> {
    FAILURE_CAUSE.lock().ok().and_then(|mut c| c.take())
}

/// `steps` with their batch chain run once per file, the globs and folders expanded under `base`
/// (natural order, the skill's file kinds). `None` when nothing was lifted: the plan stays.
pub fn lift(
    steps: &[Value],
    registry: &knaif_core::Registry,
    base: &Path,
    file_kinds: &knaif_core::FileKinds,
) -> Option<BatchLift> {
    let mut entries: Vec<String> = Vec::new();
    for step in steps {
        let Some(args) = step.get("args") else {
            continue;
        };
        for key in knaif_core::batch_chain::INPUT_KEYS {
            match args.get(key) {
                Some(Value::String(s)) => entries.push(s.clone()),
                Some(Value::Array(items)) => {
                    entries.extend(items.iter().filter_map(Value::as_str).map(str::to_string))
                }
                _ => {}
            }
        }
    }
    let expand = expand_inputs(&entries, base, file_kinds);
    let joining = knaif_core::scratch::joining_tools(registry);
    let batch_outputs: HashMap<String, String> = registry
        .iter()
        .filter_map(|(name, td)| td.batch_output.clone().map(|ext| (name.clone(), ext)))
        .collect();
    let lift = lift_batch(
        steps,
        &expand,
        &joining,
        &HashMap::new(),
        false,
        &batch_outputs,
    );
    lift.lifted().then_some(lift)
}

/// How each file of a step over many files ended, run-wide: Python's `_FileTally`. A step's
/// reports are held until it has reported a second file (one file is no batch, and its failure
/// takes the usual error path); from then on each failure line is printed as it comes.
#[derive(Default)]
struct Tally {
    entries: Vec<(String, Option<String>)>,
    step: Vec<(String, Option<String>)>,
    started: Option<Instant>,
}

static TALLY: std::sync::Mutex<Option<Tally>> = std::sync::Mutex::new(None);

/// A new step begins: its files are counted on their own.
pub fn tally_start_step() {
    if let Ok(mut t) = TALLY.lock() {
        let t = t.get_or_insert_with(Tally::default);
        t.step.clear();
        t.started.get_or_insert_with(Instant::now);
    }
}

/// One file of the current step ended: done (`reason` is `None`) or failed, said why. Its name is
/// shown relative to `base` when inside it, else as the bare file name; the reason never shows
/// the run's folders.
pub fn tally_record(file: &Path, reason: Option<&str>, base: &Path, work: Option<&Path>) {
    let relative = |f: &Path, b: &Path| {
        f.strip_prefix(b)
            .ok()
            .map(|r| r.to_string_lossy().replace('\\', "/"))
    };
    let name = relative(file, base)
        .or_else(
            || match (std::fs::canonicalize(file), std::fs::canonicalize(base)) {
                (Ok(f), Ok(b)) => relative(&f, &b),
                _ => None,
            },
        )
        .unwrap_or_else(|| {
            file.file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default()
        });
    let reason = reason.map(|r| first_line(&without_folders(r, base, work)).to_string());
    let Ok(mut t) = TALLY.lock() else {
        return;
    };
    let t = t.get_or_insert_with(Tally::default);
    t.step.push((name, reason));
    let keep: Vec<(String, Option<String>)> = match t.step.len() {
        2 => t.step.clone(),
        n if n > 2 => vec![t.step[n - 1].clone()],
        _ => Vec::new(),
    };
    for (name, reason) in keep {
        if let Some(why) = &reason {
            eprintln!("{}", failure_line(&name, why));
        }
        t.entries.push((name, reason));
    }
}

/// The current step is a batch in which at least one file is done: a failed command among them
/// does not stop the run.
pub fn tally_partial() -> bool {
    TALLY.lock().ok().and_then(|t| {
        t.as_ref()
            .map(|t| t.step.len() >= 2 && t.step.iter().any(|(_, r)| r.is_none()))
    }) == Some(true)
}

/// The run's report of its steps over many files, when there was one.
pub fn take_tally_report() -> Option<Report> {
    let t = TALLY.lock().ok()?.take()?;
    if t.entries.is_empty() {
        return None;
    }
    Some(Report {
        files: t.entries.iter().map(|(n, _)| n.clone()).collect(),
        failures: t
            .entries
            .into_iter()
            .filter_map(|(n, r)| r.map(|r| (n, r)))
            .collect(),
        started: t.started,
    })
}

/// The skill's answer for one step: the set's name and files it will write, or `None`.
pub type PlannedOne<'a> = dyn Fn(&str, &serde_json::Map<String, Value>) -> anyhow::Result<Option<(String, Vec<String>)>>
    + 'a;

/// Each step whose tool writes many files from one, with what it will write (the skill's
/// planned outputs). Port of Python `CommandAgent._planned_sets`.
pub fn planned_sets(
    steps: &[Value],
    plan_one: &PlannedOne,
) -> anyhow::Result<BTreeMap<usize, knaif_core::batch_chain::PlannedSet>> {
    let mut sets = BTreeMap::new();
    for (i, step) in steps.iter().enumerate() {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        if tool.is_empty() || crate::is_control_step(step) {
            continue;
        }
        let empty = serde_json::Map::new();
        let args = step
            .get("args")
            .and_then(Value::as_object)
            .unwrap_or(&empty);
        if let Some((name, files)) = plan_one(tool, args)?.filter(|(_, f)| !f.is_empty()) {
            sets.insert(i, knaif_core::batch_chain::PlannedSet { name, files });
        }
    }
    Ok(sets)
}

/// Run the steps reading a planned set once per file of it (Python
/// `CommandAgent._lift_sets`). A file in the run scratch is shown, and names its results, by its
/// place in the user's folder. A join reading the set with no step before it reads every file.
/// Returns the plan, the lift when one was made, and each producer's files (for what it leaves).
pub fn lift_sets(
    steps: &[Value],
    sets: &BTreeMap<usize, knaif_core::batch_chain::PlannedSet>,
    registry: &knaif_core::Registry,
    work: &Path,
) -> (Vec<Value>, Option<BatchLift>, BTreeMap<usize, Vec<String>>) {
    let mut planned: HashMap<String, Vec<String>> = HashMap::new();
    let mut by_producer = BTreeMap::new();
    for (&i, set) in sets {
        let output = steps[i]
            .get("args")
            .and_then(|a| a.get("output"))
            .and_then(Value::as_str)
            .filter(|o| !o.is_empty())
            .unwrap_or(&set.name)
            .to_string();
        planned.insert(output, set.files.clone());
        by_producer.insert(i, set.files.clone());
    }
    let home: HashMap<String, String> = planned
        .values()
        .flatten()
        .filter_map(|f| {
            Path::new(f)
                .strip_prefix(work)
                .ok()
                .map(|rel| (f.clone(), rel.to_string_lossy().replace('\\', "/")))
        })
        .collect();
    let joining = knaif_core::scratch::joining_tools(registry);
    let batch_outputs: HashMap<String, String> = registry
        .iter()
        .filter_map(|(name, td)| td.batch_output.clone().map(|ext| (name.clone(), ext)))
        .collect();
    let lift = lift_batch(steps, &planned, &joining, &home, true, &batch_outputs);
    if lift.lifted() {
        return (lift.plan.clone(), Some(lift), by_producer);
    }
    let joined = steps
        .iter()
        .map(|step| {
            let mut step = step.clone();
            let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
            if joining.contains(tool) {
                for key in ["inputs", "input"] {
                    let new = match step.get("args").and_then(|a| a.get(key)) {
                        Some(Value::Array(items)) => Some(
                            items
                                .iter()
                                .flat_map(|v| match v.as_str().and_then(|s| planned.get(s)) {
                                    Some(files) => files.iter().cloned().map(Value::from).collect(),
                                    None => vec![v.clone()],
                                })
                                .collect::<Vec<Value>>(),
                        ),
                        Some(Value::String(s)) => planned
                            .get(s)
                            .map(|files| files.iter().cloned().map(Value::from).collect()),
                        _ => None,
                    };
                    if let Some(new) = new {
                        step["args"][key] = Value::Array(new);
                    }
                }
            }
            step
        })
        .collect();
    (joined, None, by_producer)
}

/// What `steps` will leave (`knaif_core::result_set`): `leaves 3 mp4`, empty when nothing. Globs
/// and folders are expanded under `base` as the run will expand them.
pub fn leaves(
    steps: &[Value],
    registry: &knaif_core::Registry,
    base: &Path,
    scratch: &Path,
    file_kinds: &knaif_core::FileKinds,
    sets: &BTreeMap<usize, Vec<String>>,
) -> String {
    let entries: Vec<String> = steps
        .iter()
        .filter_map(|s| s.get("args"))
        .flat_map(|a| {
            knaif_core::batch_chain::INPUT_KEYS
                .iter()
                .filter_map(move |k| a.get(*k))
        })
        .flat_map(|v| match v {
            Value::String(s) => vec![s.clone()],
            Value::Array(items) => items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect(),
            _ => Vec::new(),
        })
        .collect();
    let readonly = registry
        .iter()
        .filter(|(_, td)| td.readonly)
        .map(|(n, _)| n.clone())
        .collect();
    let results = knaif_core::result_set::result_set(
        steps,
        &scratch.to_string_lossy(),
        base,
        &expand_inputs(&entries, base, file_kinds),
        &readonly,
        &knaif_core::scratch::joining_tools(registry),
        &knaif_core::scratch::produced_kinds(registry),
        file_kinds,
        sets,
    );
    knaif_core::result_set::leaves_line(&results)
}

/// The steps a preview lists and the lines after them: a batch shows its first file's steps,
/// then `the same for N more files: …`; every preview ends with what the plan leaves.
pub fn preview(
    steps: &[Value],
    lift: Option<&BatchLift>,
    leaves: &str,
) -> (Vec<Value>, Vec<String>) {
    let mut notes = Vec::new();
    let shown: Vec<Value> = match lift {
        Some(lift) => {
            if lift.shown.len() > 1 {
                notes.push(knaif_core::result_set::same_for_line(&lift.shown[1..]));
            }
            steps
                .iter()
                .zip(&lift.file_of)
                .filter(|(_, f)| f.is_none_or(|f| f == 0))
                .map(|(s, _)| s.clone())
                .collect()
        }
        None => steps.to_vec(),
    };
    if !leaves.is_empty() {
        notes.push(leaves.to_string());
    }
    (shown, notes)
}

/// A join with the failed files' results left out; `None` when no file it was to join is left.
fn join_without_failed(
    step: &Value,
    sources: &HashMap<String, Vec<Option<usize>>>,
    failed: &BTreeMap<usize, String>,
) -> Option<Value> {
    let mut step = step.clone();
    let mut kept_any = false;
    for (key, came_from) in sources {
        let Some(Value::Array(values)) = step.get("args").and_then(|a| a.get(key)).cloned() else {
            continue;
        };
        if values.len() != came_from.len() {
            continue;
        }
        let kept: Vec<Value> = values
            .into_iter()
            .zip(came_from)
            .filter(|(_, f)| f.is_none_or(|f| !failed.contains_key(&f)))
            .map(|(v, _)| v)
            .collect();
        kept_any |= came_from
            .iter()
            .any(|f| f.is_some_and(|f| !failed.contains_key(&f)));
        step["args"][key] = Value::Array(kept);
    }
    kept_any.then_some(step)
}

/// `text` with the folders the run works in left out of every path in it: a reason names
/// `clip.mov`, never the user's folders, and the run scratch shows as `<work>`. Python
/// `agent._without_folder` (after `display`).
pub fn without_folders(text: &str, base: &Path, work: Option<&Path>) -> String {
    let mut text = text.to_string();
    if let Some(work) = work {
        for folder in spellings(work) {
            text = text.replace(&folder, "<work>");
        }
    }
    for folder in spellings(base) {
        text = text
            .replace(&format!("{folder}\\"), "")
            .replace(&format!("{folder}/"), "")
            .replace(&folder, ".");
    }
    text
}

fn spellings(folder: &Path) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let canonical = std::fs::canonicalize(folder).ok();
    for f in std::iter::once(folder.to_path_buf()).chain(canonical) {
        let s = f.to_string_lossy().trim_start_matches(r"\\?\").to_string();
        for spelling in [s.clone(), s.replace('\\', "/")] {
            if !spelling.is_empty() && !out.contains(&spelling) {
                out.push(spelling);
            }
        }
    }
    // Longest first, so a folder is never cut inside a longer spelling of itself.
    out.sort_by_key(|s| std::cmp::Reverse(s.len()));
    out
}

/// How a batch went: the files as the user knows them, the ones that failed and why.
#[derive(Debug, Default)]
pub struct Report {
    pub files: Vec<String>,
    pub failures: Vec<(String, String)>,
    pub started: Option<Instant>,
}

impl Report {
    pub fn failed(&self) -> usize {
        self.failures.len()
    }

    /// `8 done, 2 failed in 12.4s, 35.2 MB`: the size of what the run's steps wrote and is still
    /// there outside the scratch.
    pub fn summary(&self, written: &[PathBuf], work: Option<&Path>) -> String {
        let seconds = self.started.map_or(0.0, |s| s.elapsed().as_secs_f64());
        knaif_core::batch_chain::summary_line(
            self.files.len() - self.failed(),
            self.failed(),
            seconds,
            size_left(written, work),
        )
    }

    /// What the terminal view's closing line adds for a batch: ` · 2 failed · 35.2 MB` (no failed
    /// part when nothing failed). Python `BatchReport.close_extra`.
    pub fn close_extra(&self, written: &[PathBuf], work: Option<&Path>) -> String {
        let failed = match self.failed() {
            0 => String::new(),
            n => format!(" · {n} failed"),
        };
        let size = knaif_core::batch_chain::format_size(size_left(written, work));
        format!("{failed} · {size}")
    }
}

/// The size of what the run's steps wrote and is still there outside the scratch.
fn size_left(written: &[PathBuf], work: Option<&Path>) -> u64 {
    let mut seen: Vec<PathBuf> = Vec::new();
    let mut size = 0u64;
    for path in written {
        if work.is_some_and(|w| path.starts_with(w)) || seen.contains(path) {
            continue;
        }
        if let Ok(meta) = std::fs::metadata(path) {
            if meta.is_file() {
                size += meta.len();
            }
        }
        seen.push(path.clone());
    }
    size
}

/// Run the lifted plan: a step of one file that fails puts that file out (its later steps are
/// skipped, its failure line goes to `failed_line` at once) and the other files go on; a join
/// leaves out the failed files' results and, with none left, does not run. A declined step or a
/// control step ends the run as it does without a batch, and so does a failure outside the chain.
///
/// `prefailed` are the files the rehearsal ([`rehearse`]) found would fail: each is out before
/// anything runs, its failure line first.
pub fn execute(
    steps: &[Value],
    lift: &BatchLift,
    total: usize,
    prefailed: BTreeMap<usize, String>,
    clean: impl Fn(&str) -> String,
    mut failed_line: impl FnMut(&str),
    mut run: impl FnMut(&Value) -> anyhow::Result<StepOutcome>,
) -> anyhow::Result<Report> {
    for (f, reason) in &prefailed {
        failed_line(&failure_line(&lift.shown[*f], reason));
    }
    let mut failed = prefailed;
    let mut report = Report {
        files: lift.shown.clone(),
        started: Some(Instant::now()),
        ..Report::default()
    };
    for (idx, step) in steps.iter().enumerate() {
        let file = lift.file_of.get(idx).copied().flatten();
        if file.is_some_and(|f| failed.contains_key(&f)) {
            continue;
        }
        let mut step = step.clone();
        if let Some(sources) = lift.join_files.get(&idx) {
            if !failed.is_empty() {
                match join_without_failed(&step, sources, &failed) {
                    Some(joined) => step = joined,
                    None => continue,
                }
            }
        }
        crate::announce_step(&step, idx + 1, total);
        take_failure_cause();
        let outcome = run(&step);
        if !crate::is_control_step(&step) && !matches!(outcome, Ok(StepOutcome::Declined)) {
            crate::run_facts::work_started();
        }
        match (outcome, file) {
            (Err(e), Some(f)) => {
                let why = take_failure_cause().unwrap_or_else(|| e.to_string());
                let reason = first_line(&clean(&why)).to_string();
                failed_line(&failure_line(&lift.shown[f], &reason));
                failed.insert(f, reason);
            }
            (Err(e), None) => return Err(e.context(crate::chain_failure_context(idx, total))),
            (Ok(StepOutcome::Continue), _) => {}
            (Ok(StepOutcome::ShortCircuit), _) => break,
            (Ok(StepOutcome::Declined), _) => {
                crate::declined_step(idx, total);
                break;
            }
        }
    }
    report.failures = failed
        .into_iter()
        .map(|(f, why)| (lift.shown[f].clone(), why))
        .collect();
    Ok(report)
}

/// Rehearse the lifted plan before it really runs (owner, 2026-10-24): `run` builds each
/// step as a dry run, printing nothing. Returns the files that would fail, each with its reason,
/// for [`execute`] to leave out; errs when a step outside the chain would fail, or every file
/// would. Python `CommandAgent._rehearse`.
pub fn rehearse(
    steps: &[Value],
    lift: &BatchLift,
    total: usize,
    clean: impl Fn(&str) -> String,
    mut run: impl FnMut(&Value) -> anyhow::Result<StepOutcome>,
) -> anyhow::Result<BTreeMap<usize, String>> {
    let mut failed: BTreeMap<usize, String> = BTreeMap::new();
    for (idx, step) in steps.iter().enumerate() {
        let file = lift.file_of.get(idx).copied().flatten();
        if file.is_some_and(|f| failed.contains_key(&f)) {
            continue;
        }
        match (run(step), file) {
            (Err(e), Some(f)) => {
                failed.insert(f, first_line(&clean(&e.to_string())).to_string());
            }
            (Err(e), None) => {
                return Err(e.context(crate::rehearsal_failure_context(idx, total)));
            }
            (Ok(StepOutcome::ShortCircuit | StepOutcome::Declined), _) => break,
            (Ok(StepOutcome::Continue), _) => {}
        }
    }
    if !failed.is_empty() && failed.len() == lift.shown.len() {
        let cause = failed.values().next().cloned().unwrap_or_default();
        return Err(anyhow::anyhow!(cause).context(crate::EVERY_FILE_WOULD_FAIL));
    }
    Ok(failed)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn two_file_chain() -> (Vec<Value>, BatchLift) {
        let plan = vec![
            json!({"tool": "convert_video", "args": {"inputs": ["*.mov"], "container": "mp4", "output": "clip.mp4"}}),
            json!({"tool": "extract_audio", "args": {"inputs": ["clip.mp4"]}}),
            json!({"tool": "concat_video", "args": {"inputs": ["clip.mp4"], "output": "all.mp4"}}),
        ];
        let expand = HashMap::from([(
            "*.mov".to_string(),
            vec!["beach.mov".to_string(), "silent.mov".to_string()],
        )]);
        let joining = std::collections::HashSet::from(["concat_video".to_string()]);
        let lift = lift_batch(
            &plan,
            &expand,
            &joining,
            &HashMap::new(),
            false,
            &HashMap::new(),
        );
        (lift.plan.clone(), lift)
    }

    #[test]
    fn a_failed_file_stops_only_its_own_chain_and_leaves_the_join() {
        let (steps, lift) = two_file_chain();
        let mut ran: Vec<Value> = Vec::new();
        let mut lines: Vec<String> = Vec::new();
        let report = execute(
            &steps,
            &lift,
            steps.len(),
            BTreeMap::new(),
            |t| t.to_string(),
            |l| lines.push(l.to_string()),
            |step| {
                ran.push(step.clone());
                let reads_silent =
                    step.to_string().contains("silent") && step["tool"] == "extract_audio";
                if reads_silent {
                    anyhow::bail!("No audio to extract: silent.mp4 has no audio track.\nmore")
                }
                Ok(StepOutcome::Continue)
            },
        )
        .unwrap();
        let tools: Vec<&str> = ran.iter().map(|s| s["tool"].as_str().unwrap()).collect();
        assert_eq!(
            tools,
            [
                "convert_video",
                "extract_audio",
                "convert_video",
                "extract_audio",
                "concat_video"
            ]
        );
        assert_eq!(
            lines,
            ["✗ silent.mov: No audio to extract: silent.mp4 has no audio track."]
        );
        assert_eq!(
            ran[4]["args"]["inputs"],
            json!(["beach.mp4"]),
            "the join leaves silent out"
        );
        assert_eq!(report.failed(), 1);
        assert_eq!(report.files, ["beach.mov", "silent.mov"]);
    }

    /// The step that fails on `silent`, as the failure test above.
    fn fails_on_silent(step: &Value) -> anyhow::Result<StepOutcome> {
        if step.to_string().contains("silent") && step["tool"] == "extract_audio" {
            anyhow::bail!("No audio to extract: silent.mp4 has no audio track.\nmore")
        }
        Ok(StepOutcome::Continue)
    }

    #[test]
    fn the_rehearsal_finds_the_file_that_would_fail() {
        // The file is out before anything runs, the others go on (owner, 2026-10-24).
        let (steps, lift) = two_file_chain();
        let failed = rehearse(
            &steps,
            &lift,
            steps.len(),
            |t| t.to_string(),
            fails_on_silent,
        )
        .unwrap();
        assert_eq!(
            failed,
            BTreeMap::from([(
                1,
                "No audio to extract: silent.mp4 has no audio track.".to_string()
            )])
        );
        let events = std::cell::RefCell::new(Vec::<String>::new());
        execute(
            &steps,
            &lift,
            steps.len(),
            failed,
            |t| t.to_string(),
            |l| events.borrow_mut().push(l.to_string()),
            |step| {
                events
                    .borrow_mut()
                    .push(step["tool"].as_str().unwrap().to_string());
                Ok(StepOutcome::Continue)
            },
        )
        .unwrap();
        assert_eq!(
            events.into_inner(),
            [
                "✗ silent.mov: No audio to extract: silent.mp4 has no audio track.",
                "convert_video",
                "extract_audio",
                "concat_video"
            ],
            "reported before anything ran, the other file runs"
        );
    }

    #[test]
    fn the_rehearsal_refuses_when_every_file_would_fail() {
        let (steps, lift) = two_file_chain();
        let err = rehearse(
            &steps,
            &lift,
            steps.len(),
            |t| t.to_string(),
            |step| {
                if step["tool"] == "extract_audio" {
                    anyhow::bail!("No audio to extract.")
                }
                Ok(StepOutcome::Continue)
            },
        )
        .unwrap_err();
        assert_eq!(err.to_string(), crate::EVERY_FILE_WOULD_FAIL);
        assert_eq!(err.root_cause().to_string(), "No audio to extract.");
    }

    #[test]
    fn a_batch_preview_shows_the_first_file_then_the_rest_then_what_it_leaves() {
        let (steps, lift) = two_file_chain();
        let (shown, notes) = preview(&steps, Some(&lift), "leaves 2 mp4 and 2 audio files");
        let tools: Vec<&str> = shown.iter().map(|s| s["tool"].as_str().unwrap()).collect();
        assert_eq!(tools, ["convert_video", "extract_audio", "concat_video"]);
        assert_eq!(
            notes,
            [
                "the same for 1 more file: silent.mov",
                "leaves 2 mp4 and 2 audio files"
            ]
        );
        let (all, only_leaves) = preview(&steps, None, "");
        assert_eq!(all.len(), steps.len());
        assert!(only_leaves.is_empty(), "nothing left, no line");
    }

    #[test]
    fn leaves_names_what_a_one_file_plan_writes() {
        let registry = knaif_core::registry::load_registry_str(
            "compress_video:\n  description: Compress\n  effects: []\n  required_args: [inputs]\n  optional_args: [output]\n",
        )
        .unwrap();
        let base = std::env::temp_dir();
        let steps = vec![
            json!({"tool": "compress_video", "args": {"inputs": ["beach.mov"], "output": "beach_small.mp4"}}),
        ];
        let line = leaves(
            &steps,
            &registry,
            &base,
            &base.join("work"),
            &Default::default(),
            &BTreeMap::new(),
        );
        assert_eq!(line, "leaves beach_small.mp4");
    }

    #[test]
    fn a_join_with_nothing_left_does_not_run() {
        let (steps, lift) = two_file_chain();
        let mut ran: Vec<String> = Vec::new();
        let report = execute(
            &steps,
            &lift,
            steps.len(),
            BTreeMap::new(),
            |t| t.to_string(),
            |_| {},
            |step| {
                ran.push(step["tool"].as_str().unwrap().to_string());
                if step["tool"] == "convert_video" {
                    anyhow::bail!("moov atom not found")
                }
                Ok(StepOutcome::Continue)
            },
        )
        .unwrap();
        assert_eq!(ran, ["convert_video", "convert_video"]);
        assert_eq!(report.failed(), 2);
    }

    #[test]
    fn a_failure_outside_the_chain_stops_the_run() {
        let plan = vec![
            json!({"tool": "compress_video", "args": {"input": "*.mov"}}),
            json!({"tool": "create_thumbnail", "args": {"input": "poster.mp4"}}),
        ];
        let expand = HashMap::from([(
            "*.mov".to_string(),
            vec!["a.mov".to_string(), "b.mov".to_string()],
        )]);
        let lift = lift_batch(
            &plan,
            &expand,
            &Default::default(),
            &HashMap::new(),
            false,
            &HashMap::new(),
        );
        let steps = lift.plan.clone();
        let got = execute(
            &steps,
            &lift,
            steps.len(),
            BTreeMap::new(),
            |t| t.to_string(),
            |_| {},
            |step| {
                if step["tool"] == "create_thumbnail" {
                    anyhow::bail!("poster.mp4 not found")
                }
                Ok(StepOutcome::Continue)
            },
        );
        assert!(got.is_err());
    }

    #[test]
    fn a_reason_never_names_the_run_folder() {
        let base = std::env::temp_dir().join(format!("knaif-batch-reason-{}", std::process::id()));
        std::fs::create_dir_all(&base).unwrap();
        let text = format!("cannot read {}", base.join("clip.mov").display());
        assert_eq!(without_folders(&text, &base, None), "cannot read clip.mov");
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn the_summary_counts_what_is_left_outside_the_scratch() {
        let base = std::env::temp_dir().join(format!("knaif-batch-size-{}", std::process::id()));
        let work = base.join(".work");
        std::fs::create_dir_all(&work).unwrap();
        std::fs::write(base.join("a.mp4"), vec![0u8; 2048]).unwrap();
        std::fs::write(work.join("piece.mp4"), vec![0u8; 4096]).unwrap();
        let report = Report {
            files: vec!["a.mov".into(), "b.mov".into()],
            failures: vec![("b.mov".into(), "broken".into())],
            started: None,
        };
        let line = report.summary(
            &[
                base.join("a.mp4"),
                base.join("a.mp4"),
                work.join("piece.mp4"),
            ],
            Some(&work),
        );
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(line, "1 done, 1 failed in 0.0s, 2.0 KB");
    }
}
