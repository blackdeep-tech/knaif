//! NL clarify gate — native port of Python `knaif.nl_clarify_gate.nl_clarify_gate`, as both CLIs
//! run it: with no host-supplied file listing (`injected_files=None`).
//!
//! Runs after stem resolution and asks instead of acting when the plan uses something the user
//! never actually said:
//!
//! 1. **Grounded args** (a tool's `grounded_args`, e.g. a PDF password) must appear in the
//!    utterance, case-insensitively; otherwise "What {arg} should I use?". Checked even for a
//!    batch request.
//! 2. Unless the utterance is a batch request ("all", "every", "folder", ...), every **input
//!    token** (keys `inputs input files src dst path base append`, in that order) must be named
//!    concretely: written as a filename in the utterance, or its stem written as an identifier
//!    carrying `_`, `-` or a digit. Globs, `$chain` references, paths with `/` or `\` and the
//!    outputs of earlier steps are exempt. Otherwise "Which {token} did you mean?".
//!
//! Native had no port until 2026-09-28: the R5c L3 run found Python asking "Which mov did you
//! mean?" where native ran and failed with "input not found: mov". The contract both runtimes
//! are held to is `contracts/parity/nl_clarify_gate_cases.json`.

use std::collections::HashSet;
use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Value};

use crate::registry::Registry;

/// Python `_INPUT_ARG_ORDER` (the planner's `_PATH_ARG_KEYS`, in a fixed order).
const INPUT_KEYS: [&str; 8] = [
    "inputs", "input", "files", "src", "dst", "path", "base", "append",
];
const OUTPUT_KEYS: [&str; 2] = ["output", "outputs"];
/// Python `_BATCH_SIGNALS`.
const BATCH_SIGNALS: [&str; 13] = [
    "all",
    "every",
    "each",
    "batch",
    "bulk",
    "folder",
    "directory",
    "alle",
    "todos",
    "toutes",
    "lote",
    "stapel",
    "lot",
];
/// Python `input_refs.GLOB_CHARS`.
const GLOB_CHARS: [char; 3] = ['*', '?', '['];

/// Python `input_refs.FILENAME_RE` (`re.ASCII`): a stem, a dot, a 2–4 char tail.
fn filename_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?-u)\b[\w\-]+\.[A-Za-z0-9]{2,4}\b").unwrap())
}

/// Python `_has_matching_stem_in_utterance`'s word pattern (Unicode word boundaries).
fn stem_word_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b[a-zA-Z0-9][a-zA-Z0-9_\-]*\b").unwrap())
}

/// The gate. Returns *payload* unchanged, or with its plan replaced by one clarify step.
pub fn nl_clarify_gate(mut payload: Value, utterance: &str, registry: &Registry) -> Value {
    let Some(steps) = payload.get("plan").and_then(Value::as_array) else {
        return payload;
    };
    if let Some(question) = question_for(steps, utterance, registry) {
        payload["plan"] = json!([{ "tool": "clarify", "args": { "question": question } }]);
    }
    payload
}

fn question_for(steps: &[Value], utterance: &str, registry: &Registry) -> Option<String> {
    // 1. Grounded args, batch or not: a batch request still must not carry an invented password.
    for step in steps {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let Some(def) = registry.get(tool) else {
            continue;
        };
        let args = step.get("args").and_then(Value::as_object);
        for arg in &def.grounded_args {
            let Some(value) = args.and_then(|a| a.get(arg)) else {
                continue;
            };
            if !is_value_grounded(value, utterance) {
                return Some(format!("What {arg} should I use?"));
            }
        }
    }

    // 2. Input tokens, unless the user asked for a batch.
    if is_batch_utterance(utterance) {
        return None;
    }
    let inline = inline_filenames(utterance);
    let mut plan_outputs: HashSet<String> = HashSet::new();
    for step in steps {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let args = step.get("args").and_then(Value::as_object);
        for key in INPUT_KEYS {
            let Some(value) = args.and_then(|a| a.get(key)) else {
                continue;
            };
            for token in strings(value) {
                if token.contains(GLOB_CHARS) {
                    continue; // a glob is exempt on its own
                }
                if token.contains('/') || token.contains('\\') {
                    continue; // a path, not a media-file reference
                }
                if plan_outputs.contains(token) {
                    continue; // produced by an earlier step
                }
                if !is_concretely_specified(token, utterance, &inline) {
                    return Some(clarify_question(token, tool));
                }
            }
        }
        for key in OUTPUT_KEYS {
            if let Some(value) = args.and_then(|a| a.get(key)) {
                plan_outputs.extend(strings(value).into_iter().map(str::to_string));
            }
        }
    }
    None
}

/// A string, or the string items of a list (Python `_input_tokens` / `_output_tokens`).
fn strings(value: &Value) -> Vec<&str> {
    match value {
        Value::String(s) => vec![s.as_str()],
        Value::Array(items) => items.iter().filter_map(Value::as_str).collect(),
        _ => Vec::new(),
    }
}

/// Python `_is_value_grounded`: a non-empty string that appears in the utterance.
fn is_value_grounded(value: &Value, utterance: &str) -> bool {
    let Some(s) = value.as_str() else {
        return false;
    };
    let s = s.trim();
    !s.is_empty() && utterance.to_lowercase().contains(&s.to_lowercase())
}

/// Python `_is_batch_utterance`: any `[a-z]+` word of the lowercased utterance is a signal.
fn is_batch_utterance(utterance: &str) -> bool {
    utterance
        .to_lowercase()
        .split(|c: char| !c.is_ascii_lowercase())
        .any(|w| BATCH_SIGNALS.contains(&w))
}

/// Python `input_refs.parse_inline_filenames`.
fn inline_filenames(utterance: &str) -> HashSet<String> {
    filename_re()
        .find_iter(utterance)
        .map(|m| m.as_str())
        .filter(|name| {
            let ext = name.rsplit('.').next().unwrap_or("");
            (2..=4).contains(&ext.chars().count()) && ext.chars().any(char::is_alphabetic)
        })
        .map(str::to_string)
        .collect()
}

/// Python `_is_concretely_specified` with no injected listing.
fn is_concretely_specified(token: &str, utterance: &str, inline: &HashSet<String>) -> bool {
    if token.starts_with('$') {
        return true; // a chain reference
    }
    if inline.contains(token) {
        return true; // the user wrote it out
    }
    has_matching_stem_in_utterance(token, utterance)
}

/// Python `_has_matching_stem_in_utterance`: an identifier in the utterance that carries a
/// structural marker and equals the token's `Path(...).stem`, ignoring case.
fn has_matching_stem_in_utterance(token: &str, utterance: &str) -> bool {
    let stem = path_stem(token).to_lowercase();
    stem_word_re().find_iter(utterance).any(|m| {
        let word = m.as_str();
        word.chars()
            .any(|c| c == '_' || c == '-' || c.is_ascii_digit())
            && word.to_lowercase() == stem
    })
}

/// Python `pathlib.Path(token).stem` for a token with no separator: `.` is empty, `..` stays
/// whole, otherwise everything before the last dot unless that dot leads.
fn path_stem(token: &str) -> &str {
    match token {
        "." => "",
        ".." => "..",
        _ => match token.rfind('.') {
            Some(i) if i > 0 => &token[..i],
            _ => token,
        },
    }
}

/// Python `_clarify_question`: the token without a leading article, or the tool as a fallback.
fn clarify_question(token: &str, tool: &str) -> String {
    let body = strip_article(token).trim();
    if body.is_empty() {
        format!("Which file would you like to {}?", tool.replace('_', " "))
    } else {
        format!("Which {body} did you mean?")
    }
}

/// Python `_ARTICLE_RE = ^\s*(the|a|an)\s+` (case-insensitive), removed once.
fn strip_article(token: &str) -> &str {
    let rest = token.trim_start();
    for article in ["the", "a", "an"] {
        let Some(head) = rest.get(..article.len()) else {
            continue;
        };
        if !head.eq_ignore_ascii_case(article) {
            continue;
        }
        let after = &rest[article.len()..];
        if after.starts_with(char::is_whitespace) {
            return after.trim_start();
        }
    }
    token
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_stem_matches_python() {
        for (token, stem) in [
            ("clip.", "clip"),
            (".mp4", ".mp4"),
            ("a..b", "a."),
            ("a.b.c", "a.b"),
            ("silent clip", "silent clip"),
            ("", ""),
            ("4K_clip.mp4", "4K_clip"),
            ("x .mp4", "x "),
            ("..", ".."),
            (".", ""),
        ] {
            assert_eq!(path_stem(token), stem, "{token:?}");
        }
    }

    #[test]
    fn article_is_removed_once_and_only_before_whitespace() {
        assert_eq!(strip_article("the silent clip"), "silent clip");
        assert_eq!(strip_article("  An intro"), "intro");
        assert_eq!(strip_article("another clip"), "another clip");
        assert_eq!(strip_article("the the clip"), "the clip");
        assert_eq!(strip_article("thereafter"), "thereafter");
    }

    #[test]
    fn batch_words_are_whole_words() {
        assert!(is_batch_utterance("compress ALL the videos"));
        assert!(!is_batch_utterance("compress the tallest video"));
        assert!(!is_batch_utterance("call me later"));
    }

    #[test]
    fn inline_filenames_need_a_lettered_tail() {
        let found = inline_filenames("convert clip.mp4 and H.264 then v1.2 and song.MP3");
        assert!(found.contains("clip.mp4") && found.contains("song.MP3"));
        assert!(!found.iter().any(|f| f == "H.264"));
    }
}
