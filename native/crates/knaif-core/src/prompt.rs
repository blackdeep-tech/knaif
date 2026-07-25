//! Model-facing prompt construction — port of `prompt.py` `build_prompt` (single-shot; history/
//! chain re-prompting is a later slice) plus the skill `prompt.yaml` loader (`system_header` +
//! rendered `examples`).
//!
//! Two intentional, prompt-only divergences from Python (not graded byte-for-byte; Phase 10
//! eval-parity measures end quality): the tool listing is **alphabetical** because [`Registry`] is a
//! `BTreeMap` (Python uses tools.yaml insertion order), and rendered example JSON is compact.

use std::collections::BTreeSet;
use std::path::Path;

use serde::Deserialize;

use crate::registry::{Registry, ToolDef};

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

/// A skill's `prompt.yaml` overrides: a system header and a rendered examples block.
#[derive(Debug, Clone, Default)]
pub struct PromptOverrides {
    pub system_header: Option<String>,
    pub examples_block: Option<String>,
}

#[derive(Deserialize)]
struct RawPrompt {
    #[serde(default)]
    system_header: Option<String>,
    #[serde(default)]
    examples: Vec<RawExample>,
}

#[derive(Deserialize)]
struct RawExample {
    #[serde(default)]
    request: String,
    #[serde(default)]
    output: Option<serde_json::Value>,
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

/// Render `prompt.yaml` examples into the text block (port of `_render_examples` +
/// `render_examples_block`): `None` when empty, else `Examples:` then `  request: "…"` /
/// `  output:  <json>` per example, the JSON matching Python's `json.dumps(sep=(', ', ': '))`.
fn render_examples(examples: &[RawExample]) -> Option<String> {
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

/// Build `(system_message, user_message)` for a single-shot chat completion. Port of `build_prompt`
/// without history: lists the non-system, non-internal tools with their args, then the header +
/// examples (skill overrides win over the defaults).
pub fn build_prompt(
    utterance: &str,
    registry: &Registry,
    overrides: &PromptOverrides,
) -> (String, String) {
    let mut tool_lines = vec!["Available tools:".to_string()];
    // Sort by tools.yaml insertion order (not the Registry's alphabetical key order): the
    // fine-tuned model was trained on prompts in that order and is sensitive to it.
    let mut ordered: Vec<&ToolDef> = registry.values().collect();
    ordered.sort_by_key(|d| d.order);
    for def in ordered {
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
        };
        let (system, _) = build_prompt("x", &registry(), &overrides);
        assert!(system.starts_with("CUSTOM HEADER\n"));
        assert!(system.ends_with("MY EXAMPLES"));
        assert!(!system.contains("You are a command planner")); // default header gone
    }

    #[test]
    fn render_examples_matches_python_shape() {
        let examples = vec![RawExample {
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
