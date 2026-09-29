//! Model-facing prompt construction — port of `prompt.py` `build_prompt` (single-shot; history/
//! chain re-prompting is a later slice) plus the skill `prompt.yaml` loader (`system_header` +
//! `examples`), including the per-utterance example selection ([`select_examples`]).
//!
//! **There are no intentional divergences from the reference left here.** The prompt this module
//! builds for a retrieved tool subset is byte-identical to Python's for the same inputs, and the
//! L1a/L1b/L1e contracts under `contracts/parity/` hold it there. That is a change of position:
//! the two divergences this note used to record — an alphabetical tool listing and compact example
//! JSON — were both real, both wrong, and both fixed. Alphabetical order came from [`Registry`]
//! being a `BTreeMap`; [`build_prompt`] now sorts by `ToolDef::order` and [`build_prompt_ordered`]
//! preserves retrieval's ranking (V1). Compact JSON came from serde_json's default; [`to_py_json`]
//! now reproduces Python's spaced separators.
//!
//! The reason none of it was "prompt-only" cosmetics: the fine-tune was trained on prompts built
//! by this exact pipeline in Python — `retrieve_tools` → `build_prompt` with a retrieved subset
//! and a selected examples block (`python/training/build_dataset.py`). A prompt shaped differently
//! is out of distribution for the model that has to answer it.

use std::collections::{BTreeSet, HashSet};
use std::path::Path;

use serde::Deserialize;

use crate::registry::{Registry, ToolDef};

/// Tools that end a plan rather than doing work. An example whose plan contains only these is a
/// control example, not a domain one. Port of Python `_TERMINAL_TOOLS` — note it does **not**
/// include `noop`, unlike [`is_system_tool`]'s prompt-listing filter.
const TERMINAL_TOOLS: &[&str] = &["clarify", "reject", "done"];

/// How many domain examples the per-utterance selection keeps, on top of the fixed clarify and
/// reject slots. Mirrors Python `select_examples`'s `max_tool_examples` default.
pub const MAX_TOOL_EXAMPLES: usize = 3;

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

/// A skill's `prompt.yaml` overrides: a system header, the whole rendered examples block, and the
/// examples still structured.
///
/// `examples_block` is the unfiltered rendering — the fallback, and what a caller with no
/// retrieved subset sends. `examples` is kept alongside it because selection is *per utterance*
/// (see [`select_examples`]): rendering at load time and throwing the structure away is exactly
/// what made native send all 28 of ffmpeg's examples where the reference sends 5.
#[derive(Debug, Clone, Default)]
pub struct PromptOverrides {
    pub system_header: Option<String>,
    pub examples_block: Option<String>,
    pub examples: Vec<PromptExample>,
}

#[derive(Deserialize)]
struct RawPrompt {
    #[serde(default)]
    system_header: Option<String>,
    #[serde(default)]
    examples: Vec<PromptExample>,
}

/// One `prompt.yaml` example: the request and the plan the reference answers it with.
#[derive(Debug, Clone, Deserialize)]
pub struct PromptExample {
    #[serde(default)]
    pub request: String,
    #[serde(default)]
    pub output: Option<serde_json::Value>,
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

/// Render examples into the text block (port of `render_examples_block`): `Examples:` then
/// `  request: "…"` / `  output:  <json>` per example, the JSON matching Python's
/// `json.dumps(sep=(', ', ': '))`.
pub fn render_examples_block(examples: &[&PromptExample]) -> String {
    let mut lines = vec!["Examples:".to_string()];
    for ex in examples {
        lines.push(format!("  request: \"{}\"", ex.request));
        if let Some(output) = &ex.output {
            lines.push(format!("  output:  {}", to_py_json(output)));
        }
        lines.push(String::new());
    }
    lines.join("\n")
}

/// The whole, unfiltered block (port of `_render_examples`): `None` when there are no examples.
fn render_examples(examples: &[PromptExample]) -> Option<String> {
    if examples.is_empty() {
        return None;
    }
    let refs: Vec<&PromptExample> = examples.iter().collect();
    Some(render_examples_block(&refs))
}

/// The non-terminal tools an example's plan uses. Empty for a pure clarify/reject/done example.
/// Port of Python `_example_tool_set`.
fn example_tool_set(example: &PromptExample) -> HashSet<&str> {
    plan_tools(example)
        .into_iter()
        .filter(|t| !TERMINAL_TOOLS.contains(t))
        .collect()
}

/// Every tool named in the example's plan, terminal ones included.
fn plan_tools(example: &PromptExample) -> Vec<&str> {
    example
        .output
        .as_ref()
        .and_then(|o| o.get("plan"))
        .and_then(serde_json::Value::as_array)
        .map(|steps| {
            steps
                .iter()
                .filter_map(|s| s.get("tool").and_then(serde_json::Value::as_str))
                .filter(|t| !t.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

/// An example whose plan is *only* a `control` step (`clarify` or `reject`) — the shapes that
/// hold the two fixed slots. Port of `_is_clarify_example` / `_is_reject_example`.
fn is_control_example(example: &PromptExample, control: &str) -> bool {
    let tools = plan_tools(example);
    !tools.is_empty() && tools.contains(&control) && example_tool_set(example).is_empty()
}

/// Whitespace-split lowercase tokens, matching Python's `set(text.lower().split())`.
fn word_set(text: &str) -> HashSet<String> {
    text.to_lowercase()
        .split_whitespace()
        .map(str::to_string)
        .collect()
}

/// Select the examples most relevant to `query` given the retrieved tool set. Port of Python
/// `select_examples`.
///
/// Always includes the first clarify example and the first reject example, when the corpus has
/// them, then fills up to `max_tool_examples` domain examples ranked by how many retrieved tools
/// the example's plan uses (primary) and how many query words its request shares (tiebreaker),
/// and re-emits the whole selection in corpus order.
///
/// Two things a re-derivation gets wrong, both pinned by `contracts/parity/example_cases.json`:
/// the cap is a **cap, not a relevance threshold** — three domain examples are kept even when
/// every one of them scores zero — and the ranking sort must be **stable**, because Python's
/// `sorted(..., reverse=True)` leaves equal-scoring examples in corpus order. Sorting ascending
/// and reversing would keep the *last* three of a tied group instead of the first three.
pub fn select_examples<'a>(
    examples: &'a [PromptExample],
    retrieved_tool_names: &HashSet<String>,
    query: &str,
    max_tool_examples: usize,
) -> Vec<&'a PromptExample> {
    let query_tokens = word_set(query);

    let mut keep: Vec<bool> = vec![false; examples.len()];
    for control in ["clarify", "reject"] {
        if let Some(i) = examples.iter().position(|e| is_control_example(e, control)) {
            keep[i] = true;
        }
    }

    let mut domain: Vec<(usize, (usize, usize))> = examples
        .iter()
        .enumerate()
        .filter(|(_, e)| !example_tool_set(e).is_empty())
        .map(|(i, e)| {
            let tool_overlap = example_tool_set(e)
                .iter()
                .filter(|t| retrieved_tool_names.contains(**t))
                .count();
            let word_overlap = word_set(&e.request)
                .iter()
                .filter(|w| query_tokens.contains(*w))
                .count();
            (i, (tool_overlap, word_overlap))
        })
        .collect();
    // Stable, descending by score: ties keep corpus order, as Python's stable reverse sort does.
    // `sort_by_key` + `Reverse` rather than `sort_by(|a, b| b.cmp(a))` only because clippy asks;
    // both are stable, which is the property that matters — `sort` then `reverse` is not.
    domain.sort_by_key(|(_, score)| std::cmp::Reverse(*score));
    for (i, _) in domain.into_iter().take(max_tool_examples) {
        keep[i] = true;
    }

    examples
        .iter()
        .zip(keep)
        .filter_map(|(e, k)| k.then_some(e))
        .collect()
}

/// Build `(system_message, user_message)` for a single-shot chat completion. Port of `build_prompt`
/// without history: lists the non-system, non-internal tools with their args, then the header +
/// examples (skill overrides win over the defaults).
pub fn build_prompt(
    utterance: &str,
    registry: &Registry,
    overrides: &PromptOverrides,
) -> (String, String) {
    // Whole-registry prompt: tools.yaml insertion order (not the `Registry`'s alphabetical key
    // order), which is what Python's dict iteration yields for a full registry.
    let mut ordered: Vec<&ToolDef> = registry.values().collect();
    ordered.sort_by_key(|d| d.order);
    build_prompt_ordered(utterance, &ordered, overrides)
}

/// `build_prompt` over an explicit tool sequence, rendered **in the order given**.
///
/// This is the entry point for a retrieved subset, where the order carries meaning: retrieval
/// ranks by relevance and the fine-tune's prompts were relevance-ordered, so re-sorting the
/// listing by `tools.yaml` position would discard the ranking on the way to the model. The
/// whole-registry path above still sorts by `order`, which is the same thing the reference
/// produces for a full registry.
pub fn build_prompt_ordered(
    utterance: &str,
    tools: &[&ToolDef],
    overrides: &PromptOverrides,
) -> (String, String) {
    let mut tool_lines = vec!["Available tools:".to_string()];
    for def in tools.iter().copied() {
        let name = def.name.as_str();
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
    let examples = overrides
        .examples_block
        .as_deref()
        .unwrap_or(DEFAULT_EXAMPLES);
    let system_msg = format!("{header}{}{examples}", tool_lines.join("\n"));
    (system_msg, utterance.to_string())
}

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
        let examples = vec![PromptExample {
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
