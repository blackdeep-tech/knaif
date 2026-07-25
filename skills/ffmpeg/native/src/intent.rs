//! Intent-support helpers — the deterministic argument normalization shared by every ffmpeg
//! intent's `expand` (Tier 3 port of the `_engine.py` intent helpers). Pure functions over the
//! shared [`Vocab`]; the plan-step expansion + execution engine that consumes them lands with the
//! `run` verb.

use crate::Vocab;

/// Canonicalize a model-supplied platform name: trim + lowercase, then map through the shared
/// `platform_aliases` (`insta`/`ig`/`reels` → `instagram_reels`, …). Port of `_normalize_platform`.
pub fn normalize_platform(platform: &str, vocab: &Vocab) -> String {
    let key = platform.trim().to_lowercase();
    vocab.platform_aliases.get(&key).cloned().unwrap_or(key)
}

/// Map a raw CRF value to the nearest named quality profile. Port of `_crf_to_profile_name`
/// (mirrors the CRF thresholds in `prompt.yaml`).
pub fn crf_to_profile_name(crf: i64) -> &'static str {
    if crf <= 20 {
        "high_quality"
    } else if crf <= 24 {
        "visually_good"
    } else if crf <= 27 {
        "balanced"
    } else {
        "small_file"
    }
}

/// Parse a raw CRF spec the model may drop into a quality slot (`crf18`, `crf=26`, `"crf 20"`,
/// `crf-23`, `crf_18`) → the CRF integer. Mirrors `_CRF_RE` = `^\s*crf\s*[=:_\- ]?\s*(\d{1,2})\s*$`.
pub(crate) fn parse_crf_spec(s: &str) -> Option<u32> {
    let lowered = s.trim().to_lowercase();
    let rest = lowered.strip_prefix("crf")?;
    let rest = rest.trim_start_matches([' ', '\t']);
    let rest = rest.strip_prefix(['=', ':', '_', '-']).unwrap_or(rest);
    let rest = rest.trim();
    if (1..=2).contains(&rest.len()) && rest.bytes().all(|b| b.is_ascii_digit()) {
        rest.parse().ok()
    } else {
        None
    }
}

/// Resolve a quality value to a named profile: a `crf N` spec maps to the nearest profile, anything
/// else (a profile name like `balanced`) passes through. This is how `load_quality_profile` accepts
/// both a profile name and a leaked raw CRF.
pub fn resolve_quality_name(quality: &str) -> String {
    match parse_crf_spec(quality) {
        Some(crf) => crf_to_profile_name(crf as i64).to_string(),
        None => quality.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn vocab() -> Vocab {
        Vocab::load(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg/vocab.yaml"),
        )
        .unwrap()
    }

    #[test]
    fn normalize_platform_applies_aliases_and_lowercases() {
        let v = vocab();
        assert_eq!(normalize_platform("Instagram", &v), "instagram_reels");
        assert_eq!(normalize_platform("  IG ", &v), "instagram_reels");
        assert_eq!(normalize_platform("reels", &v), "instagram_reels");
        assert_eq!(normalize_platform("YouTube", &v), "youtube"); // no alias → lowercased
    }

    #[test]
    fn crf_to_profile_name_thresholds() {
        assert_eq!(crf_to_profile_name(18), "high_quality");
        assert_eq!(crf_to_profile_name(20), "high_quality");
        assert_eq!(crf_to_profile_name(23), "visually_good");
        assert_eq!(crf_to_profile_name(26), "balanced");
        assert_eq!(crf_to_profile_name(30), "small_file");
    }

    #[test]
    fn resolve_quality_name_handles_crf_specs_and_passthrough() {
        assert_eq!(resolve_quality_name("crf 18"), "high_quality");
        assert_eq!(resolve_quality_name("crf=20"), "high_quality");
        assert_eq!(resolve_quality_name("crf-23"), "visually_good");
        assert_eq!(resolve_quality_name("crf28"), "small_file");
        assert_eq!(resolve_quality_name("balanced"), "balanced"); // profile name passthrough
        assert_eq!(resolve_quality_name("crf 100"), "crf 100"); // 3 digits → not a crf spec
    }
}
