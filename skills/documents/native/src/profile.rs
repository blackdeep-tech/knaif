//! Compress-quality profiles (`skills/documents/profiles/compress/{small,balanced,high}.yaml`).
//!
//! Declarative targets shared with the Python runtime: the Ghostscript `-dPDFSETTINGS` preset, the
//! lossless object-stream mode, and a rasterize fallback (DPI + JPEG quality) used when Ghostscript
//! is absent. Tier 1 loads them as typed data; the compress handler (later slice) turns them into a
//! `gs` invocation or a rasterize pass.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;

/// One compress-quality target (`small` / `balanced` / `high`).
#[derive(Debug, Clone, Deserialize)]
pub struct CompressProfile {
    /// Ghostscript `-dPDFSETTINGS` preset, e.g. `/screen`, `/ebook`, `/printer`.
    pub ghostscript_pdfsettings: String,
    #[serde(default)]
    pub lossless: Lossless,
    pub rasterize_fallback: RasterizeFallback,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Lossless {
    /// pikepdf object-stream mode (`generate` / `preserve` / `disable`).
    #[serde(default)]
    pub object_stream_mode: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RasterizeFallback {
    pub dpi: u32,
    pub jpeg_quality: u32,
}

/// Load every `profiles/compress/*.yaml`, keyed by file stem (`small` / `balanced` / `high`).
pub fn load_compress_profiles(dir: &Path) -> anyhow::Result<BTreeMap<String, CompressProfile>> {
    let mut out = BTreeMap::new();
    let entries = std::fs::read_dir(dir)
        .map_err(|e| anyhow::anyhow!("reading compress profiles dir {}: {e}", dir.display()))?;
    for entry in entries {
        let path = entry?.path();
        if path.extension().and_then(|x| x.to_str()) != Some("yaml") {
            continue;
        }
        let stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .ok_or_else(|| anyhow::anyhow!("bad profile filename {}", path.display()))?
            .to_string();
        let text = std::fs::read_to_string(&path)?;
        let profile: CompressProfile = serde_yaml::from_str(&text)
            .map_err(|e| anyhow::anyhow!("parsing compress profile {}: {e}", path.display()))?;
        out.insert(stem, profile);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn compress_dir() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/documents/profiles/compress")
    }

    #[test]
    fn loads_all_three_quality_profiles() {
        let profiles = load_compress_profiles(&compress_dir()).unwrap();
        assert_eq!(profiles.len(), 3);
        assert_eq!(profiles["small"].ghostscript_pdfsettings, "/screen");
        assert_eq!(profiles["balanced"].ghostscript_pdfsettings, "/ebook");
        assert_eq!(profiles["high"].ghostscript_pdfsettings, "/printer");
        assert_eq!(profiles["high"].rasterize_fallback.dpi, 200);
        assert_eq!(profiles["small"].rasterize_fallback.jpeg_quality, 60);
        assert_eq!(
            profiles["balanced"].lossless.object_stream_mode.as_deref(),
            Some("generate")
        );
    }
}
