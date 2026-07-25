//! Pull a JSON plan object out of raw model output.
//!
//! Rust port of the Python agent's `_extract_json` / `_clean_json` (agent.py): strip a
//! `<think>…</think>` reasoning block (or a dangling `</think>`), remove ```` ```json ```` /
//! ```` ``` ```` fences, then return the first brace-balanced `{…}`. Brace counting is naive
//! (it does not track string context) — deliberately matching the Python implementation so the
//! two runtimes agree; malformed leftovers are handed to `parse_plan`, which raises a clear
//! error. This is what makes real (fenced/thinking-wrapped) LLM output parseable.

/// A JSON candidate extracted from model text, plus any captured reasoning.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExtractedJson {
    /// The `{…}` substring to hand to `parse_plan` (the whole cleaned text if no `{` was found).
    pub json: String,
    /// `<think>…</think>` (or pre-`</think>`) reasoning that was stripped before extraction.
    pub thinking: String,
}

/// Extract the first JSON object from raw model output, capturing any thinking block.
pub fn extract_json(text: &str) -> ExtractedJson {
    // Capture the first full <think>…</think> block's content, then strip all such blocks.
    let mut thinking = first_think(text).unwrap_or_default();
    let mut body = strip_think_blocks(text).trim().to_string();

    // Some builds emit only the closing </think>; treat everything before it as reasoning.
    if let Some(idx) = body.find("</think>") {
        if thinking.is_empty() {
            thinking = body[..idx].trim().to_string();
        }
        body = body[idx + "</think>".len()..].trim().to_string();
    }

    ExtractedJson {
        json: clean_json(&body),
        thinking,
    }
}

/// Inner text of the first `<think>…</think>` block, if any.
fn first_think(text: &str) -> Option<String> {
    let open = text.find("<think>")? + "<think>".len();
    let close = text[open..].find("</think>")?;
    Some(text[open..open + close].trim().to_string())
}

/// Remove every complete `<think>…</think>` block (an unclosed `<think>` is left intact).
fn strip_think_blocks(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(open) = rest.find("<think>") {
        let after = open + "<think>".len();
        match rest[after..].find("</think>") {
            Some(close) => {
                out.push_str(&rest[..open]);
                rest = &rest[after + close + "</think>".len()..];
            }
            None => break,
        }
    }
    out.push_str(rest);
    out
}

/// Strip ```` ```json ````/```` ``` ```` fences and return the first brace-balanced `{…}`.
fn clean_json(text: &str) -> String {
    let stripped = text.replace("```json", "").replace("```", "");
    let stripped = stripped.trim();
    let Some(start) = stripped.find('{') else {
        return stripped.to_string();
    };
    let mut depth = 0i32;
    for (i, ch) in stripped[start..].char_indices() {
        match ch {
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    return stripped[start..start + i + ch.len_utf8()].to_string();
                }
            }
            _ => {}
        }
    }
    stripped[start..].to_string() // incomplete; parse_plan will surface the error
}

#[cfg(test)]
mod tests {
    use super::*;

    fn json_of(text: &str) -> String {
        extract_json(text).json
    }

    #[test]
    fn strips_markdown_fences() {
        assert_eq!(json_of("```json\n{\"plan\": []}\n```"), "{\"plan\": []}");
        assert_eq!(json_of("```\n{\"plan\": []}\n```"), "{\"plan\": []}");
    }

    #[test]
    fn extracts_object_from_surrounding_prose() {
        assert_eq!(json_of("Sure! {\"plan\": []} done"), "{\"plan\": []}");
    }

    #[test]
    fn matches_nested_braces() {
        let s = "{\"plan\": [{\"tool\": \"x\", \"args\": {}}]}";
        assert_eq!(json_of(s), s);
    }

    #[test]
    fn captures_and_strips_think_block() {
        let e = extract_json("<think>reason here</think>{\"plan\": []}");
        assert_eq!(e.thinking, "reason here");
        assert_eq!(e.json, "{\"plan\": []}");
    }

    #[test]
    fn handles_dangling_close_think() {
        // some builds emit only the closing tag; everything before it is reasoning
        let e = extract_json("thinking out loud</think>{\"plan\": []}");
        assert_eq!(e.thinking, "thinking out loud");
        assert_eq!(e.json, "{\"plan\": []}");
    }

    #[test]
    fn no_brace_returns_cleaned_text_for_parse_plan_to_reject() {
        assert_eq!(json_of("no json here"), "no json here");
    }

    #[test]
    fn incomplete_object_returned_from_first_brace() {
        // unbalanced — return from the first '{' so parse_plan surfaces the error
        assert_eq!(json_of("prefix {\"plan\": ["), "{\"plan\": [");
    }
}
