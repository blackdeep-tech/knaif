//! Model-facing prompt construction — port of `prompt.py` `build_prompt` (single-shot; history/
//! chain re-prompting is a later slice) plus the skill `prompt.yaml` loader (`system_header` +
//! rendered `examples`).
//!
//! **This is a byte-for-byte port, and it is graded as one.** Given the same utterance, registry
//! and overrides, [`build_prompt_from`] reproduces Python's `build_prompt` exactly;
//! `contracts/parity/prompt_cases.json` and `retrieval_cases.json` pin it from both sides, and all
//! 847 ffmpeg corpus utterances were verified identical on 2026-08-09.
//!
//! The module header used to record two "intentional, prompt-only divergences" — an alphabetical
//! tool listing, and compact example JSON — deferred to a Phase 10 eval-parity check. Both claims
//! were already stale (the code sorted by `def.order`, and [`to_py_json`] reproduces Python's
//! spaced separators), and the check they were deferred to **was never built**. That is the
//! cautionary note worth keeping: a divergence excused by a measurement nobody ran is simply an
//! unmeasured divergence. See docs/plans/2026-08-08-native-python-planning-parity.md.
//!
//! One divergence genuinely remains, and it is a *layer* difference rather than a rendering one:
//! path normalization runs inside Python's `build_prompt` but in the native CLI
//! (`apps/cli/src/main.rs`), so a non-CLI consumer of this crate gets un-normalized utterances.
//! Pinned by the `path_normalization_cases` section of the prompt contract.

use std::collections::{BTreeSet, HashSet};
use std::path::Path;

use serde::Deserialize;

use crate::registry::Registry;
use crate::retrieval::RetrievedTools;

/// Default system header (port of `_SYSTEM_HEADER`), used when a skill's `prompt.yaml` has none.
pub const DEFAULT_SYSTEM_HEADER: &str = "\
You are a command planner. Output ONLY a JSON object — no explanation.
{ \"plan\": [ { \"tool\": \"<name>\", \"args\": { <params> }, \"output\": \"$var\" }, ... ] }

Rules:
- Emit ONLY the JSON object.
- Use clarify if a required parameter is missing or the request is ambiguous.
- File paths are sandbox-relative; use \".\" for the sandbox root.
- Use move_files for requests to move, copy, transfer, or place files into a destination folder.
- Preserve explicit file filters such as \"text files\" with file_type when the action tool supports it.
- When a later step needs a value produced by an earlier step, add \"output\": \"$varname\" to the earlier step and reference \"$varname\" (or \"$varname.field\") in the later step's args.
- Do NOT include a find/list step if the following action step already accepts the same path/pattern/file_type args — it is redundant.
- Output {\"plan\":[{\"tool\":\"done\",\"args\":{}}]} when the task is fully complete.
";

/// Default examples block (port of `_EXAMPLES`), used when a skill's `prompt.yaml` has none.
pub const DEFAULT_EXAMPLES: &str = "
Examples:
  request: \"list text files in reports\"
  output:  { \"plan\": [ { \"tool\": \"list_files\", \"args\": { \"path\": \"reports\", \"file_type\": \"text\" } } ] }

  request: \"find executable files\"
  output:  { \"plan\": [ { \"tool\": \"find_files\", \"args\": { \"path\": \".\", \"file_type\": \"executable\" } } ] }

  request: \"delete tmp files\"
  output:  { \"plan\": [ { \"tool\": \"clarify\", \"args\": { \"question\": \"Which folder contains the tmp files?\" } } ] }

  request: \"move all files to src folder\"
  output:  { \"plan\": [ { \"tool\": \"move_files\", \"args\": { \"src\": \".\", \"dst\": \"src\" } } ] }
";

/// Tools never listed in the prompt (they're implied control tools). Port of `_SYSTEM_TOOLS`.
fn is_system_tool(name: &str) -> bool {
    matches!(name, "clarify" | "reject" | "done" | "noop")
}

/// Rewrite Windows-style backslash path separators to forward slashes.
///
/// A model echoing a path verbatim (`.\clip.mov`) would otherwise emit an illegal `\c` JSON escape
/// — the dot is lost, the path becomes drive-root-relative and resolves to `C:\clip.mov`, and the
/// file is reported missing. Forward slashes need no escaping and are accepted by ffmpeg and
/// `std::path` on Windows, so this is lossless for the file-path domain these skills operate in.
///
/// **This lives in the core, next to [`build_prompt_from`], deliberately.** It used to be a private
/// helper in `apps/cli`, so the CLI normalized but every other consumer of this crate — an
/// embedder, a GUI, the skill API — silently did not, while every Python caller did. Python's
/// equivalent is in `knaif.prompt`, called inside `build_prompt`; this now matches that layering.
///
/// The *rule* still differs from Python's, which rewrites only whitespace-delimited tokens matching
/// a path-shaped regex. That makes Python miss a quoted path containing a space
/// (`"C:\My Videos\clip.mov"`) — precisely the case this function exists to prevent. No corpus
/// utterance contains a backslash (0 of 847 eval, 0 of 404 train), so neither rule has ever been
/// exercised by a measurement; see the plan's Q5.
pub fn normalize_path_separators(utterance: &str) -> String {
    utterance.replace('\\', "/")
}

/// A skill's `prompt.yaml` overrides: a system header, the rendered examples block, and the
/// **structured** examples the block was rendered from.
///
/// The structured list is kept because example selection is per-utterance: with a retrieved tool
/// subset, [`select_examples`] picks the examples relevant to it rather than emitting all of them.
/// `examples_block` remains the fallback for callers with no retrieved subset — the same
/// arrangement as Python, where `CommandAgent.build_prompt` filters only when both structured
/// examples and a `registry_override` are present.
#[derive(Debug, Clone, Default)]
pub struct PromptOverrides {
    pub system_header: Option<String>,
    pub examples_block: Option<String>,
    pub examples: Vec<Example>,
}

/// One few-shot example from `prompt.yaml`. Port of the dicts Python passes around.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct Example {
    #[serde(default)]
    pub request: String,
    #[serde(default)]
    pub output: Option<serde_json::Value>,
}

#[derive(Deserialize)]
struct RawPrompt {
    #[serde(default)]
    system_header: Option<String>,
    #[serde(default)]
    examples: Vec<Example>,
}

/// Load a skill's `prompt.yaml`. Missing file / non-mapping → empty overrides (the defaults apply),
/// mirroring `_load_prompt`.
pub fn load_prompt_yaml(path: &Path) -> PromptOverrides {
    let Ok(text) = std::fs::read_to_string(path) else {
        return PromptOverrides::default();
    };
    let Ok(raw) = serde_yaml::from_str::<RawPrompt>(&text) else {
        return PromptOverrides::default();
    };
    PromptOverrides {
        system_header: raw.system_header,
        examples_block: render_examples(&raw.examples),
        examples: raw.examples,
    }
}

/// Tools that end a plan rather than doing work. Port of `_TERMINAL_TOOLS`.
const TERMINAL_TOOLS: &[&str] = &["clarify", "reject", "done"];

/// The non-terminal tools an example's plan references. Port of `_example_tool_set`.
fn example_tool_set(example: &Example) -> Vec<&str> {
    let Some(plan) = example
        .output
        .as_ref()
        .and_then(|o| o.get("plan"))
        .and_then(|p| p.as_array())
    else {
        return Vec::new();
    };
    let mut out: Vec<&str> = Vec::new();
    for step in plan {
        if let Some(tool) = step.get("tool").and_then(|t| t.as_str()) {
            if !tool.is_empty() && !TERMINAL_TOOLS.contains(&tool) && !out.contains(&tool) {
                out.push(tool);
            }
        }
    }
    out
}

/// True when the example's plan uses `tool` and nothing else of substance. Port of
/// `_is_clarify_example` / `_is_reject_example`, which both require a non-empty plan mentioning the
/// terminal tool and *no* domain tools.
fn is_terminal_example(example: &Example, tool: &str) -> bool {
    let Some(plan) = example
        .output
        .as_ref()
        .and_then(|o| o.get("plan"))
        .and_then(|p| p.as_array())
    else {
        return false;
    };
    let mentions = plan
        .iter()
        .filter_map(|s| s.get("tool").and_then(|t| t.as_str()))
        .any(|t| t == tool);
    let any_tool = plan
        .iter()
        .any(|s| s.get("tool").and_then(|t| t.as_str()).is_some());
    any_tool && mentions && example_tool_set(example).is_empty()
}

/// Select the examples most relevant to `query` given the retrieved tool set.
///
/// Port of Python `select_examples`. Always includes the first clarify and first reject example
/// when present, fills up to `max_tool_examples` domain examples ranked by (retrieved-tool
/// overlap, query/request token overlap), then re-emits everything in the original `prompt.yaml`
/// order.
///
/// Two details are easy to get wrong and are pinned by `contracts/parity/example_cases.json`:
/// Python's `sorted(..., reverse=True)` is **stable**, so equally scored examples keep their
/// original relative order (Rust's `sort_by` is stable too, so ranking by `b.cmp(a)` matches); and
/// the output is in declaration order, not rank order.
pub fn select_examples<'a>(
    examples: &'a [Example],
    retrieved_tool_names: &[&str],
    query: &str,
    max_tool_examples: usize,
) -> Vec<&'a Example> {
    let clarify = examples
        .iter()
        .position(|e| is_terminal_example(e, "clarify"));
    let reject = examples
        .iter()
        .position(|e| is_terminal_example(e, "reject"));

    let query_tokens: HashSet<String> = query
        .to_lowercase()
        .split_whitespace()
        .map(str::to_string)
        .collect();

    let mut domain: Vec<usize> = (0..examples.len())
        .filter(|i| !example_tool_set(&examples[*i]).is_empty())
        .collect();

    let score = |i: &usize| -> (usize, usize) {
        let tools = example_tool_set(&examples[*i]);
        let overlap = tools
            .iter()
            .filter(|t| retrieved_tool_names.contains(t))
            .count();
        let req_tokens: HashSet<String> = examples[*i]
            .request
            .to_lowercase()
            .split_whitespace()
            .map(str::to_string)
            .collect();
        (overlap, query_tokens.intersection(&req_tokens).count())
    };
    // Descending by score, stable — so equally scored examples keep declaration order, matching
    // Python's `sorted(..., reverse=True)`, which is stable for the same reason.
    domain.sort_by_key(|i| std::cmp::Reverse(score(i)));
    domain.truncate(max_tool_examples);

    let mut keep: Vec<usize> = Vec::new();
    keep.extend(clarify);
    keep.extend(reject);
    keep.extend(domain);

    // Re-emit in declaration order, deduplicated.
    (0..examples.len())
        .filter(|i| keep.contains(i))
        .map(|i| &examples[i])
        .collect()
}

/// A serde_json formatter matching Python `json.dumps(x, separators=(', ', ': '))`: single line,
/// but a space after every `,` and `:`. The fine-tuned model was trained on prompts rendered this
/// way, so the example JSON must reproduce it exactly (serde_json's default is compact, no spaces).
struct PySeparators;

impl serde_json::ser::Formatter for PySeparators {
    fn begin_array_value<W: ?Sized + std::io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> std::io::Result<()> {
        if first {
            Ok(())
        } else {
            w.write_all(b", ")
        }
    }
    fn begin_object_key<W: ?Sized + std::io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> std::io::Result<()> {
        if first {
            Ok(())
        } else {
            w.write_all(b", ")
        }
    }
    fn begin_object_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        w.write_all(b": ")
    }
}

/// Serialize a value like Python's `json.dumps(x, separators=(', ', ': '))` (insertion-order keys
/// via serde_json's `preserve_order` feature + spaced separators).
fn to_py_json(value: &serde_json::Value) -> String {
    use serde::Serialize;
    let mut buf = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, PySeparators);
    if value.serialize(&mut ser).is_err() {
        return serde_json::to_string(value).unwrap_or_default();
    }
    String::from_utf8(buf).unwrap_or_default()
}

/// Render `prompt.yaml` examples into the text block (port of `_render_examples` +
/// `render_examples_block`): `None` when empty, else `Examples:` then `  request: "…"` /
/// `  output:  <json>` per example, the JSON matching Python's `json.dumps(sep=(', ', ': '))`.
fn render_examples(examples: &[Example]) -> Option<String> {
    let refs: Vec<&Example> = examples.iter().collect();
    render_examples_refs(&refs)
}

/// Same rendering, over a borrowed selection — what [`select_examples`] returns.
fn render_examples_refs(examples: &[&Example]) -> Option<String> {
    if examples.is_empty() {
        return None;
    }
    let mut lines = vec!["Examples:".to_string()];
    for ex in examples {
        lines.push(format!("  request: \"{}\"", ex.request));
        if let Some(output) = &ex.output {
            lines.push(format!("  output:  {}", to_py_json(output)));
        }
        lines.push(String::new());
    }
    Some(lines.join("\n"))
}

/// Build `(system_message, user_message)` from the **whole** registry, in `tools.yaml` order.
///
/// The no-retrieval path, and Python's behaviour when a caller passes no `registry_override`.
/// Prefer [`build_prompt_from`] with a retrieved selection on the planning path — see its docs.
pub fn build_prompt(
    utterance: &str,
    registry: &Registry,
    overrides: &PromptOverrides,
) -> (String, String) {
    build_prompt_from(utterance, &RetrievedTools::all(registry), overrides)
}

/// Build `(system_message, user_message)` for a single-shot chat completion. Port of `build_prompt`
/// without history: lists the non-system, non-internal tools with their args, then the header +
/// examples (skill overrides win over the defaults).
///
/// **Tools are listed in the order given.** `RetrievedTools` preserves relevance ranking, and the
/// fine-tuned model was trained on prompts built that way; re-sorting here would put it off
/// distribution. `RetrievedTools::all` supplies `tools.yaml` order for the unfiltered path.
pub fn build_prompt_from(
    utterance: &str,
    tools: &RetrievedTools<'_>,
    overrides: &PromptOverrides,
) -> (String, String) {
    // Normalize here, as Python's `build_prompt` does, so every consumer of this crate gets it —
    // not only the CLI. Idempotent, so a caller that already normalized (to match the utterance
    // against a clarify gate, say) loses nothing by it.
    let utterance = &normalize_path_separators(utterance);
    let mut tool_lines = vec!["Available tools:".to_string()];
    for (name, def) in tools.iter() {
        if is_system_tool(name) || def.internal {
            continue;
        }
        let arg_label = |arg: &str, suffix: &str| {
            let hint = def
                .arg_schemas
                .get(arg)
                .and_then(|s| s.help.as_deref())
                .map(|h| format!(", {h}"))
                .unwrap_or_default();
            format!("{arg} ({suffix}{hint})")
        };
        let req = def
            .required_args
            .iter()
            .map(|a| arg_label(a, "required"))
            .collect::<Vec<_>>()
            .join(", ");
        let opt = if def.optional_args.is_empty() {
            String::new()
        } else {
            let opts = def
                .optional_args
                .iter()
                .map(|a| arg_label(a, "optional"))
                .collect::<Vec<_>>()
                .join(", ");
            format!(", {opts}")
        };
        tool_lines.push(format!("  - {name}: {}", def.description));
        tool_lines.push(format!("    args: {req}{opt}"));
    }

    let header = overrides
        .system_header
        .as_deref()
        .unwrap_or(DEFAULT_SYSTEM_HEADER);

    // Per-utterance example selection, gated exactly as Python gates it: structured examples must
    // exist AND the tools must have come from retrieval. Filtering against the whole registry
    // would rank every example equally, so both runtimes fall back to the full block instead.
    let selected_block = if tools.is_filtered() && !overrides.examples.is_empty() {
        let names: Vec<&str> = tools
            .iter()
            .filter(|(_, d)| !d.internal)
            .map(|(n, _)| n)
            .collect();
        let chosen = select_examples(&overrides.examples, &names, utterance, MAX_TOOL_EXAMPLES);
        render_examples_refs(&chosen)
    } else {
        None
    };
    let examples = selected_block.as_deref().unwrap_or_else(|| {
        overrides
            .examples_block
            .as_deref()
            .unwrap_or(DEFAULT_EXAMPLES)
    });
    let system_msg = format!("{header}{}{examples}", tool_lines.join("\n"));
    (system_msg, utterance.to_string())
}

/// Domain examples kept alongside the fixed clarify/reject pair. Python's `max_tool_examples`
/// default; a different value is a different prompt, so it is not a knob.
const MAX_TOOL_EXAMPLES: usize = 3;

/// Corrective re-prompt that injects the validator error, for the one-shot repair retry. Port of
/// `_validator_feedback_prompt` — becomes the *user* turn of the retry (the system turn is unchanged).
pub fn validator_feedback_prompt(user: &str, previous: &str, error: &str) -> String {
    format!(
        "{user}\n\n\
         Your previous response was rejected because it was invalid:\n\
         {previous}\n\n\
         Validation error: {error}\n\n\
         Return a corrected JSON plan that fixes this error. Respond with ONLY the JSON object."
    )
}

/// The set of tools that appear in a built prompt (test/introspection helper).
pub fn listed_tools(registry: &Registry) -> BTreeSet<String> {
    registry
        .iter()
        .filter(|(name, def)| !is_system_tool(name) && !def.internal)
        .map(|(name, _)| name.clone())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::load_registry_str;

    fn registry() -> Registry {
        // Two tools + a system tool (clarify) + an internal tool (should be excluded).
        load_registry_str(
            "
compress_video:
  description: Compress a video.
  required_args: [inputs]
  optional_args: [quality]
  arg_schemas:
    quality: { type: string, help: small_file|balanced }
clarify:
  description: Ask a question.
  required_args: [question]
run_batch:
  description: internal step.
  internal: true
  required_args: [commands]
",
        )
        .unwrap()
    }

    #[test]
    fn build_prompt_lists_only_planner_tools_with_args() {
        let (system, user) =
            build_prompt("compress a.mp4", &registry(), &PromptOverrides::default());
        assert_eq!(user, "compress a.mp4");
        // the real tool is listed with required + optional (+ help)
        assert!(system.contains("  - compress_video: Compress a video."));
        assert!(system.contains("args: inputs (required), quality (optional, small_file|balanced)"));
        // system + internal tools are excluded
        assert!(!system.contains("- clarify"));
        assert!(!system.contains("- run_batch"));
        // defaults applied
        assert!(system.contains("You are a command planner"));
        assert!(system.contains("Examples:"));
    }

    #[test]
    fn overrides_replace_header_and_examples() {
        let overrides = PromptOverrides {
            system_header: Some("CUSTOM HEADER\n".to_string()),
            examples_block: Some("\nMY EXAMPLES".to_string()),
            ..Default::default()
        };
        let (system, _) = build_prompt("x", &registry(), &overrides);
        assert!(system.starts_with("CUSTOM HEADER\n"));
        assert!(system.ends_with("MY EXAMPLES"));
        assert!(!system.contains("You are a command planner")); // default header gone
    }

    #[test]
    fn render_examples_matches_python_shape() {
        let examples = vec![Example {
            request: "compress a.mp4".into(),
            output: Some(
                serde_json::json!({"plan": [{"tool": "compress_video", "args": {"inputs": ["a.mp4"]}}]}),
            ),
        }];
        let block = render_examples(&examples).unwrap();
        assert!(block.starts_with("Examples:\n  request: \"compress a.mp4\"\n  output:  {"));
        // Must match Python `json.dumps(sep=(', ', ': '))`: spaced separators AND insertion-order
        // keys (tool before args) — the format the model was fine-tuned on.
        assert!(
            block.contains(
                "{\"plan\": [{\"tool\": \"compress_video\", \"args\": {\"inputs\": [\"a.mp4\"]}}]}"
            ),
            "example JSON must match python's spaced, tool-first shape:\n{block}"
        );
    }

    #[test]
    fn loads_real_ffmpeg_prompt_yaml() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg/prompt.yaml");
        let overrides = load_prompt_yaml(&path);
        assert!(overrides.system_header.is_some());
        assert!(overrides
            .examples_block
            .as_deref()
            .unwrap()
            .contains("Examples:"));
    }
}
