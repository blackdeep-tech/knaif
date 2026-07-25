//! Tool retrieval + text normalization.
//!
//! Rust port of Python `retrieve_tools` / `_normalize` / `_query_tokens`: keyword + description
//! scoring with document-frequency down-weighting of shared keywords, plus CJK n-gram
//! tokenization and diacritic-insensitive matching so multilingual queries retrieve the right
//! tool. Same scoring both runtimes use.

use std::collections::{BTreeMap, HashMap, HashSet};

use unicode_normalization::UnicodeNormalization;

use crate::registry::{Registry, ToolDef};

/// Always surfaced to the model regardless of score.
const ALWAYS_INCLUDE: &[&str] = &["clarify", "reject", "done"];
/// Longest CJK character n-gram generated for containment matching.
const CJK_MAX_NGRAM: usize = 4;

/// Unicode combining marks (NFD output for diacritics) — the blocks Python's `category == "Mn"`
/// filter removes in practice.
fn is_combining(c: char) -> bool {
    matches!(c as u32,
        0x0300..=0x036F | 0x1AB0..=0x1AFF | 0x1DC0..=0x1DFF | 0x20D0..=0x20FF | 0xFE20..=0xFE2F)
}

/// Lowercase + strip combining diacritics (é→e) while preserving non-Latin scripts. Mirrors
/// Python `_normalize` (NFD → drop `Mn` → lowercase).
pub fn normalize(text: &str) -> String {
    text.to_lowercase()
        .nfd()
        .filter(|c| !is_combining(*c))
        .collect()
}

/// Runs of non-space-delimited script (CJK ideographs + kana + Hangul) that whitespace
/// tokenization can't split (mirrors `_CJK_RUN`).
fn is_cjk(c: char) -> bool {
    matches!(c as u32,
        0x3040..=0x30FF | 0x3400..=0x4DBF | 0x4E00..=0x9FFF | 0xAC00..=0xD7AF)
}

fn add_ngrams(run: &str, tokens: &mut HashSet<String>) {
    let chars: Vec<char> = run.chars().collect();
    for n in 1..=CJK_MAX_NGRAM {
        if chars.len() < n {
            break;
        }
        for i in 0..=(chars.len() - n) {
            tokens.insert(chars[i..i + n].iter().collect());
        }
    }
}

/// Token set for matching: whitespace/underscore-split words plus CJK character n-grams
/// (1..=`CJK_MAX_NGRAM`). Mirrors Python `_query_tokens`.
fn query_tokens(text: &str) -> HashSet<String> {
    let norm = normalize(text);
    let mut tokens: HashSet<String> = norm
        .replace('_', " ")
        .split_whitespace()
        .map(str::to_string)
        .collect();
    let mut run = String::new();
    for c in norm.chars() {
        if is_cjk(c) {
            run.push(c);
        } else if !run.is_empty() {
            add_ngrams(&run, &mut tokens);
            run.clear();
        }
    }
    if !run.is_empty() {
        add_ngrams(&run, &mut tokens);
    }
    tokens
}

/// Return the top-`top_k` tools for `query` (score ≥ `min_score`) plus the always-included
/// system tools. Keyword match = 3/df points; description/name/arg word match = 1 point.
/// Internal tools are never surfaced. Mirrors Python `retrieve_tools`.
pub fn retrieve_tools<'a>(
    query: &str,
    registry: &'a Registry,
    top_k: usize,
    min_score: f64,
) -> BTreeMap<String, &'a ToolDef> {
    let tokens = query_tokens(query);

    // Document frequency: how many (non-internal) tools claim each normalized keyword.
    let mut df: HashMap<String, usize> = HashMap::new();
    for tool in registry.values() {
        if tool.internal {
            continue;
        }
        for kw in &tool.keywords {
            *df.entry(normalize(kw)).or_insert(0) += 1;
        }
    }

    let always: HashSet<&str> = ALWAYS_INCLUDE.iter().copied().collect();
    let mut scores: Vec<(f64, String)> = Vec::new();
    for (name, tool) in registry {
        if always.contains(name.as_str()) || tool.internal {
            continue;
        }
        let tool_kw: HashSet<String> = tool.keywords.iter().map(|k| normalize(k)).collect();
        let kw_score: f64 = 3.0
            * tokens
                .iter()
                .filter(|t| tool_kw.contains(*t))
                .map(|k| 1.0 / df[k] as f64)
                .sum::<f64>();

        let args = tool
            .required_args
            .iter()
            .chain(tool.optional_args.iter())
            .cloned()
            .collect::<Vec<_>>()
            .join(" ");
        let text = format!("{} {} {}", name.replace('_', " "), tool.description, args);
        let text_tokens: HashSet<String> = normalize(&text)
            .replace('_', " ")
            .split_whitespace()
            .map(str::to_string)
            .collect();
        let desc_score = tokens.iter().filter(|t| text_tokens.contains(*t)).count() as f64;

        scores.push((kw_score + desc_score, name.clone()));
    }

    // Highest score first; name as a deterministic tiebreak (Python sorts (score, name) desc).
    scores.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));

    let mut selected: BTreeMap<String, &ToolDef> = BTreeMap::new();
    for (score, name) in scores.into_iter().take(top_k) {
        if score >= min_score {
            if let Some(t) = registry.get(&name) {
                selected.insert(name, t);
            }
        }
    }
    for name in ALWAYS_INCLUDE {
        if let Some(t) = registry.get(*name) {
            selected.insert((*name).to_string(), t);
        }
    }
    selected
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::load_registry;
    use std::path::Path;

    #[test]
    fn normalize_strips_diacritics_and_lowercases() {
        assert_eq!(normalize("comprimír"), "comprimir");
        assert_eq!(normalize("kürzEN"), "kurzen");
        assert_eq!(normalize("réduire"), "reduire");
        assert_eq!(normalize("COMPRESS"), "compress");
    }

    fn base() -> &'static Path {
        Path::new(env!("CARGO_MANIFEST_DIR"))
    }

    fn ffmpeg() -> Registry {
        load_registry(base().join("../../../skills/ffmpeg/tools.yaml").as_path()).unwrap()
    }

    /// ffmpeg tools ∪ the shared core control tools (clarify/reject/done/…).
    fn ffmpeg_with_core() -> Registry {
        let mut r = ffmpeg();
        r.extend(
            load_registry(
                base()
                    .join("../../../contracts/runtime/core_tools.yaml")
                    .as_path(),
            )
            .unwrap(),
        );
        r
    }

    #[test]
    fn multilingual_retrieval_parity() {
        let r = ffmpeg();
        let cases = [
            ("comprimir video", "compress_video"),
            ("convertir a mp4", "convert_video"),
            ("extraer audio", "extract_audio"),
            ("video komprimieren", "compress_video"),
            ("video schneiden", "trim_video"),
            ("couper la video", "trim_video"),
            ("сжать видео", "compress_video"),
            ("обрезать видео", "trim_video"),
            ("comprimír video", "compress_video"), // accented → still matches
        ];
        for (query, expected) in cases {
            let result = retrieve_tools(query, &r, 5, 0.0);
            assert!(
                result.contains_key(expected),
                "expected {expected:?} in top-5 for {query:?}, got {:?}",
                result.keys().collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn always_includes_system_tools_and_respects_top_k() {
        let r = ffmpeg_with_core();
        let result = retrieve_tools("xyzzy no match at all", &r, 5, 0.0);
        for sys in ["clarify", "reject", "done"] {
            assert!(result.contains_key(sys), "missing system tool {sys}");
        }
        // top_k bounds the non-system selection
        let result = retrieve_tools("video", &r, 2, 0.0);
        let non_system = result
            .keys()
            .filter(|k| !["clarify", "reject", "done"].contains(&k.as_str()))
            .count();
        assert!(
            non_system <= 2,
            "non-system count {non_system} exceeds top_k"
        );
    }
}
