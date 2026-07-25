//! knaif-skill-ffmpeg — native ffmpeg skill (Phase 7).
//!
//! Ports the imperative engine (`_build_one_recipe`, `_geometry_vf`, target-size math) and
//! `Intent.expand`, consuming the bundle's shared `profiles/`, `vocab.yaml`, and
//! `arg_value_sets`. Executes via detected `ffmpeg`/`ffprobe`, never model-emitted shell.
//!
//! Tier 1 (shared data) is in place: [`Vocab`] loads `vocab.yaml`. The imperative engine
//! (Tier 2/3) follows.

pub mod concat;
pub mod engine;
pub mod exec;
pub mod intent;
pub mod profile;
pub mod run;
pub mod vocab;

use std::collections::BTreeMap;
use std::path::Path;

pub use profile::{load_platforms, load_quality, PlatformProfile, QualityProfile};
pub use vocab::Vocab;

/// All of the ffmpeg skill's shared declarative data, loaded from the bundle root — the single
/// entry point the engine uses to consume `vocab.yaml` + `profiles/` without re-hardcoding.
#[derive(Debug, Clone)]
pub struct FfmpegData {
    pub vocab: Vocab,
    pub platforms: BTreeMap<String, PlatformProfile>,
    pub quality: BTreeMap<String, QualityProfile>,
}

impl FfmpegData {
    /// Load `vocab.yaml` + `profiles/{platforms,quality}/` from a bundle dir (e.g. `skills/ffmpeg`).
    pub fn load(bundle_dir: &Path) -> anyhow::Result<Self> {
        Ok(Self {
            vocab: Vocab::load(&bundle_dir.join("vocab.yaml"))?,
            platforms: load_platforms(&bundle_dir.join("profiles").join("platforms"))?,
            quality: load_quality(&bundle_dir.join("profiles").join("quality"))?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_full_bundle_data() {
        let bundle = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/ffmpeg");
        let data = FfmpegData::load(&bundle).expect("load ffmpeg bundle data");
        assert!(data.vocab.video_encoder_map.contains_key("h264"));
        assert!(data.platforms.contains_key("youtube"));
        assert!(data.quality.contains_key("balanced"));
    }
}
