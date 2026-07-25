//! The ffmpeg skill's declarative vocabulary (`skills/ffmpeg/vocab.yaml`).
//!
//! Pure lookup data shared verbatim with the Python runtime — codec/encoder maps, platform
//! aliases, scale presets, container/image sets, and the volume direction words. The engine
//! (Tier 2/3) reads these; nothing here is logic. Loading it in Rust is Tier 1 of the ffmpeg
//! port: the two runtimes consume one source of truth instead of re-hardcoding tables.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;

/// Deserialized `vocab.yaml`. Field names match the YAML keys exactly.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Vocab {
    /// Audio container/extension → ffprobe-style codec name (dry-run probe).
    #[serde(default)]
    pub audio_ext_codec: BTreeMap<String, String>,
    /// Requested audio format → ffmpeg encoder.
    #[serde(default)]
    pub audio_format_encoder: BTreeMap<String, String>,
    /// Model platform synonyms → canonical profile name.
    #[serde(default)]
    pub platform_aliases: BTreeMap<String, String>,
    /// Named resolution presets → ffmpeg scale "W:H".
    #[serde(default)]
    pub scale_presets: BTreeMap<String, String>,
    /// Output filename suffix per workflow mode.
    #[serde(default)]
    pub output_suffix_by_mode: BTreeMap<String, String>,
    /// Video codec token → ffmpeg encoder.
    #[serde(default)]
    pub video_encoder_map: BTreeMap<String, String>,
    /// ffmpeg encoder → short codec token.
    #[serde(default)]
    pub encoder_codec_map: BTreeMap<String, String>,
    /// Recognised video container extensions.
    #[serde(default)]
    pub video_containers: Vec<String>,
    /// Recognised image extensions.
    #[serde(default)]
    pub image_extensions: Vec<String>,
    /// Direction words that mean "louder" in the volume `level` slot.
    #[serde(default)]
    pub volume_louder: Vec<String>,
    /// Direction words that mean "quieter" in the volume `level` slot.
    #[serde(default)]
    pub volume_quieter: Vec<String>,
}

impl Vocab {
    /// Parse vocab from YAML text.
    pub fn from_yaml(text: &str) -> anyhow::Result<Self> {
        Ok(serde_yaml::from_str(text)?)
    }

    /// Load `vocab.yaml` from a path.
    pub fn load(path: &Path) -> anyhow::Result<Self> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("reading vocab {}: {e}", path.display()))?;
        Self::from_yaml(&text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bundle_vocab() -> Vocab {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg/vocab.yaml");
        Vocab::load(&path).expect("load bundle vocab.yaml")
    }

    #[test]
    fn loads_encoder_and_scale_maps() {
        let v = bundle_vocab();
        assert_eq!(
            v.video_encoder_map.get("hevc").map(String::as_str),
            Some("libx265")
        );
        assert_eq!(
            v.video_encoder_map.get("av1").map(String::as_str),
            Some("libsvtav1")
        );
        assert_eq!(
            v.scale_presets.get("1080p").map(String::as_str),
            Some("1920:1080")
        );
        assert_eq!(
            v.encoder_codec_map.get("libx264").map(String::as_str),
            Some("h264")
        );
    }

    #[test]
    fn loads_container_and_image_sets_and_volume_words() {
        let v = bundle_vocab();
        assert!(v.video_containers.iter().any(|c| c == "mp4"));
        assert!(v.image_extensions.iter().any(|c| c == "png"));
        assert!(v.platform_aliases.get("insta").map(String::as_str) == Some("instagram_reels"));
        assert!(v.volume_louder.iter().any(|w| w == "boost"));
        assert!(v.volume_quieter.iter().any(|w| w == "reduce"));
    }
}
