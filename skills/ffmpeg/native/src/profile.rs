//! Platform and quality profiles (`skills/ffmpeg/profiles/{platforms,quality}/*.yaml`).
//!
//! Declarative targets shared with the Python runtime: a platform profile pins container /
//! codecs / resolution caps for a destination (whatsapp, youtube, …); a quality profile pins
//! CRF / preset / audio bitrate. Tier 1 loads them as typed data; the engine (Tier 2/3)
//! turns them into ffmpeg flags.

use std::collections::BTreeMap;
use std::path::Path;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Deserializer};

/// A destination platform target (e.g. `whatsapp`, `youtube`, `archive`).
#[derive(Debug, Clone, Deserialize)]
pub struct PlatformProfile {
    pub platform: String,
    pub container: String,
    pub video_codec: String,
    pub video_encoder: String,
    pub audio_codec: String,
    #[serde(default)]
    pub pixel_format: Option<String>,
    #[serde(default)]
    pub max_width: Option<u32>,
    #[serde(default)]
    pub max_height: Option<u32>,
    #[serde(default)]
    pub aspect_ratio: Option<String>,
    /// `"0"` (or absent) means "no cap"; otherwise an ffmpeg bitrate like `"128k"`. YAML allows
    /// the value as a bare number or a string, so it is coerced to `String`.
    #[serde(default, deserialize_with = "de_opt_str_or_num")]
    pub max_audio_bitrate: Option<String>,
    #[serde(default)]
    pub faststart: bool,
    #[serde(default)]
    pub default_target_size_mb: Option<u32>,
    #[serde(default)]
    pub notes: Option<String>,
}

/// A quality target (CRF / preset / audio bitrate).
#[derive(Debug, Clone, Deserialize)]
pub struct QualityProfile {
    pub quality: String,
    pub video_crf: i32,
    pub encoder_preset: String,
    #[serde(default, deserialize_with = "de_opt_str_or_num")]
    pub audio_bitrate: Option<String>,
    #[serde(default)]
    pub notes: Option<String>,
}

/// Accept a YAML scalar that may be a string or a number, yielding `Option<String>`.
fn de_opt_str_or_num<'de, D>(d: D) -> Result<Option<String>, D::Error>
where
    D: Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum StrOrNum {
        S(String),
        N(i64),
    }
    Ok(Option::<StrOrNum>::deserialize(d)?.map(|v| match v {
        StrOrNum::S(s) => s,
        StrOrNum::N(n) => n.to_string(),
    }))
}

/// Load every `*.yaml` in `dir`, keyed by each record's own name (`key_fn`).
fn load_dir<T, F>(dir: &Path, key_fn: F) -> anyhow::Result<BTreeMap<String, T>>
where
    T: DeserializeOwned,
    F: Fn(&T) -> String,
{
    let mut out = BTreeMap::new();
    let entries = std::fs::read_dir(dir)
        .map_err(|e| anyhow::anyhow!("reading profiles dir {}: {e}", dir.display()))?;
    for entry in entries {
        let path = entry?.path();
        if path.extension().and_then(|x| x.to_str()) != Some("yaml") {
            continue;
        }
        let text = std::fs::read_to_string(&path)?;
        let value: T = serde_yaml::from_str(&text)
            .map_err(|e| anyhow::anyhow!("parsing profile {}: {e}", path.display()))?;
        out.insert(key_fn(&value), value);
    }
    Ok(out)
}

/// Load `profiles/platforms/*.yaml` keyed by `platform`.
pub fn load_platforms(dir: &Path) -> anyhow::Result<BTreeMap<String, PlatformProfile>> {
    load_dir(dir, |p: &PlatformProfile| p.platform.clone())
}

/// Load `profiles/quality/*.yaml` keyed by `quality`.
pub fn load_quality(dir: &Path) -> anyhow::Result<BTreeMap<String, QualityProfile>> {
    load_dir(dir, |q: &QualityProfile| q.quality.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profiles_root() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg/profiles")
    }

    #[test]
    fn loads_platform_profiles_with_caps() {
        let platforms = load_platforms(&profiles_root().join("platforms")).unwrap();
        let wa = platforms.get("whatsapp").expect("whatsapp profile");
        assert_eq!(wa.container, "mp4");
        assert_eq!(wa.max_width, Some(1280));
        assert_eq!(wa.max_height, Some(720));
        assert!(wa.faststart);
        assert_eq!(wa.max_audio_bitrate.as_deref(), Some("128k"));

        // archive has a bare-number 0 bitrate (no cap) and no resolution caps
        let ar = platforms.get("archive").expect("archive profile");
        assert_eq!(ar.max_audio_bitrate.as_deref(), Some("0"));
        assert_eq!(ar.max_width, None);
    }

    #[test]
    fn loads_quality_profiles() {
        let quality = load_quality(&profiles_root().join("quality")).unwrap();
        assert_eq!(quality.get("lossless").unwrap().video_crf, 0);
        assert_eq!(quality.get("balanced").unwrap().encoder_preset, "medium");
        assert_eq!(
            quality
                .get("visually_good")
                .unwrap()
                .audio_bitrate
                .as_deref(),
            Some("128k")
        );
    }
}
