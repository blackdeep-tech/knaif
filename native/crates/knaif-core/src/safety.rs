//! Safety gates shared by every runtime.
//!
//! The pre-inference `unsafe_phrases` reject: a skill declares phrases in `skill.yaml`
//! (`safety.unsafe_phrases`) that must never reach the model. Port of the Python
//! `CommandAgent.infer` gate — the utterance is lowercased and whitespace-tokenized into a set,
//! and a phrase matches when **all** of its words appear as tokens (order-independent, token-exact,
//! not substring). A match forces a deterministic reject before any planning happens.

use std::collections::HashSet;
use std::path::Path;

use serde::Deserialize;

/// True when `utterance` trips any of the skill's `unsafe_phrases`.
///
/// Mirrors `all(word in u_tokens for word in phrase.split())` over the lowercased token set:
/// `"rm -rf"` matches only when both `rm` and `-rf` appear as whitespace-delimited tokens.
pub fn is_unsafe_request(utterance: &str, phrases: &[String]) -> bool {
    let lower = utterance.to_lowercase();
    let tokens: HashSet<&str> = lower.split_whitespace().collect();
    phrases.iter().any(|phrase| {
        let phrase = phrase.to_lowercase();
        let mut words = phrase.split_whitespace().peekable();
        // An empty phrase would vacuously match everything; treat it as no-op instead.
        words.peek().is_some() && words.all(|w| tokens.contains(w))
    })
}

#[derive(Debug, Deserialize)]
struct RawSafetyDoc {
    #[serde(default)]
    safety: Option<RawSafety>,
}

#[derive(Debug, Deserialize)]
struct RawSafety {
    #[serde(default)]
    unsafe_phrases: Vec<String>,
}

/// Read `safety.unsafe_phrases` from a bundle's `skill.yaml`. Missing file / section / key yields
/// an empty list (no gate) rather than an error — the same permissive default as the Python loader.
pub fn load_unsafe_phrases(skill_yaml: &Path) -> Vec<String> {
    let Ok(text) = std::fs::read_to_string(skill_yaml) else {
        return Vec::new();
    };
    match serde_yaml::from_str::<RawSafetyDoc>(&text) {
        Ok(doc) => doc.safety.map(|s| s.unsafe_phrases).unwrap_or_default(),
        Err(_) => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn phrases() -> Vec<String> {
        ["wipe all videos", "rm -rf", "nuke", "remote server"]
            .into_iter()
            .map(String::from)
            .collect()
    }

    #[test]
    fn matches_all_words_order_independent() {
        let p = phrases();
        assert!(is_unsafe_request("please wipe all videos now", &p));
        assert!(is_unsafe_request("all videos wipe now", &p)); // order-independent (no punctuation)
        assert!(is_unsafe_request("run rm -rf on it", &p));
        assert!(is_unsafe_request("upload to a remote server", &p));
    }

    #[test]
    fn requires_every_word_present() {
        let p = phrases();
        // "wipe" + "videos" present but not "all" → no match.
        assert!(!is_unsafe_request("wipe the videos", &p));
        assert!(!is_unsafe_request("compress this clip for youtube", &p));
    }

    #[test]
    fn token_exact_not_substring() {
        let p = phrases();
        // "nuke" only as a substring of "nukes" is a different token → no match.
        assert!(!is_unsafe_request("show me the nukes documentary", &p));
        assert!(is_unsafe_request("nuke it", &p));
    }

    #[test]
    fn case_insensitive() {
        assert!(is_unsafe_request("WIPE ALL VIDEOS", &phrases()));
    }

    #[test]
    fn empty_phrase_list_never_matches() {
        assert!(!is_unsafe_request("anything at all", &[]));
        assert!(!is_unsafe_request("anything", &[String::new()]));
    }

    #[test]
    fn loads_unsafe_phrases_from_skill_yaml() {
        let bundle =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg/skill.yaml");
        let loaded = load_unsafe_phrases(&bundle);
        assert!(loaded.iter().any(|p| p == "rm -rf"));
        assert!(loaded.iter().any(|p| p == "wipe all videos"));
    }

    #[test]
    fn missing_file_is_empty() {
        assert!(load_unsafe_phrases(Path::new("/no/such/skill.yaml")).is_empty());
    }
}
