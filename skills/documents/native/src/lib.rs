//! knaif-skill-documents — native documents skill (Phase 7).
//!
//! Ports the documents workflow expansion + deterministic handlers, consuming the bundle's shared
//! `profiles/` + `arg_value_sets`. Large/copyleft tools (`gs`, `soffice`, `tesseract`) stay
//! **detected subprocess deps** ([`detect`]); permissive in-process PDF/image ops are reimplemented
//! with a Rust stack (library choice pending — see the plan). Tier 1 (shared data + tool detection)
//! is in place; the operation handlers follow.

pub mod compress;
pub mod convert;
pub mod detect;
pub mod ocr;
pub mod office;
pub mod overlay;
pub mod pdf;
pub mod profile;
pub mod render;
pub mod run;
pub mod text;

use std::collections::BTreeMap;
use std::path::Path;

pub use detect::ExternalTools;
pub use profile::{load_compress_profiles, CompressProfile};

/// The documents skill's shared declarative data, loaded from the bundle root.
#[derive(Debug, Clone)]
pub struct DocumentsData {
    pub compress: BTreeMap<String, CompressProfile>,
}

impl DocumentsData {
    /// Load `profiles/compress/*.yaml` from a bundle dir (e.g. `skills/documents`).
    pub fn load(bundle_dir: &Path) -> anyhow::Result<Self> {
        Ok(Self {
            compress: load_compress_profiles(&bundle_dir.join("profiles").join("compress"))?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_full_bundle_data() {
        let bundle = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/documents");
        let data = DocumentsData::load(&bundle).expect("load documents bundle data");
        assert!(data.compress.contains_key("balanced"));
    }
}
