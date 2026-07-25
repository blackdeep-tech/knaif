//! `compress_pdf` — three backends chosen the way `RunCompressStep` does:
//! - **ghostscript** (`gs` present, quality small/balanced): best quality, keeps text.
//! - **rasterize** (quality small, no `gs`): render pages → JPEG PDF via the PDFium [`Rasterizer`]
//!   (feature `pdfium`); loses the text layer (last-resort fallback).
//! - **lossless** (everything else): lopdf stream compression + object streams; keeps text.

use std::path::Path;

use crate::pdf;
use crate::profile::CompressProfile;

/// The compression backend used.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    Ghostscript,
    Rasterize,
    Lossless,
}

impl Method {
    pub fn label(self) -> &'static str {
        match self {
            Method::Ghostscript => "ghostscript",
            Method::Rasterize => "rasterize",
            Method::Lossless => "lossless-optimize",
        }
    }
    /// Whether the text layer survives (rasterization flattens to images).
    pub fn text_preserved(self) -> bool {
        !matches!(self, Method::Rasterize)
    }
}

/// Pick the backend: gs for small/balanced when present; raster only for small without gs; else
/// lossless. Port of `RunCompressStep`'s `use_gs` / `use_raster` logic.
pub fn choose_method(quality: &str, gs_present: bool) -> Method {
    if gs_present && matches!(quality, "small" | "balanced") {
        Method::Ghostscript
    } else if quality == "small" && !gs_present {
        Method::Rasterize
    } else {
        Method::Lossless
    }
}

/// A human label for the method that will run, with the raster text-loss warning appended. For the
/// preview/summary line (so `--dry-run` reveals which backend + whether text survives).
pub fn compress_method_label(quality: &str, gs_present: bool) -> String {
    let method = choose_method(quality, gs_present);
    if method.text_preserved() {
        method.label().to_string()
    } else {
        format!("{} — removes selectable text", method.label())
    }
}

/// Compress `input` → `output` using the chosen backend; returns which one ran.
pub fn compress(
    input: &Path,
    output: &Path,
    quality: &str,
    profile: &CompressProfile,
    gs: Option<&Path>,
) -> anyhow::Result<Method> {
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let method = choose_method(quality, gs.is_some());
    match method {
        Method::Ghostscript => ghostscript(
            input,
            output,
            gs.expect("gs present"),
            &profile.ghostscript_pdfsettings,
        )?,
        Method::Rasterize => rasterize(
            input,
            output,
            profile.rasterize_fallback.dpi,
            profile.rasterize_fallback.jpeg_quality,
        )?,
        Method::Lossless => lossless(input, output)?,
    }
    Ok(method)
}

/// lopdf stream compression + object-stream save (keeps text/vectors). Port of `_lossless_compress`.
fn lossless(input: &Path, output: &Path) -> anyhow::Result<()> {
    let mut doc = pdf::load(input)?;
    doc.compress();
    let mut file = std::fs::File::create(output)
        .map_err(|e| anyhow::anyhow!("could not create {}: {e}", output.display()))?;
    doc.save_modern(&mut file)
        .map_err(|e| anyhow::anyhow!("could not write {}: {e}", output.display()))
}

/// Ghostscript `pdfwrite` with the profile's `-dPDFSETTINGS` preset. Port of `_ghostscript_compress`.
fn ghostscript(input: &Path, output: &Path, gs: &Path, pdfsettings: &str) -> anyhow::Result<()> {
    let result = std::process::Command::new(gs)
        .arg("-sDEVICE=pdfwrite")
        .arg("-dCompatibilityLevel=1.4")
        .arg(format!("-dPDFSETTINGS={pdfsettings}"))
        .args(["-dNOPAUSE", "-dQUIET", "-dBATCH"])
        .arg(format!("-sOutputFile={}", output.display()))
        .arg(input)
        .output()
        .map_err(|e| anyhow::anyhow!("could not launch Ghostscript ({}): {e}", gs.display()))?;
    if !result.status.success() {
        anyhow::bail!(
            "Ghostscript failed: {}",
            String::from_utf8_lossy(&result.stderr).trim()
        );
    }
    Ok(())
}

/// Render every page to a JPEG and assemble a new image-only PDF (loses text). Port of
/// `_rasterize_compress`. Needs the PDFium rasterizer (feature `pdfium`).
#[cfg(feature = "pdfium")]
fn rasterize(input: &Path, output: &Path, dpi: u32, jpeg_quality: u32) -> anyhow::Result<()> {
    use crate::render::{PdfiumRasterizer, Rasterizer};
    use lopdf::{dictionary, Document, Object};

    let rasterizer = PdfiumRasterizer::new()?;
    let pages = rasterizer.page_count(input)?;
    if pages == 0 {
        anyhow::bail!("Cannot rasterize an empty PDF.");
    }
    let mut doc = Document::with_version("1.5");
    let pages_id = doc.new_object_id();
    let mut kids: Vec<Object> = Vec::with_capacity(pages as usize);
    for page in 1..=pages {
        let raster = rasterizer.render_page(input, page, dpi as f32)?;
        let rgb = rgba_to_rgb(&raster);
        let jpeg = crate::convert::encode_jpeg(&rgb, jpeg_quality.min(100) as u8)?;
        let page_id =
            crate::convert::add_jpeg_page(&mut doc, pages_id, jpeg, raster.width, raster.height);
        kids.push(page_id.into());
    }
    let count = kids.len() as i64;
    doc.objects.insert(
        pages_id,
        Object::Dictionary(dictionary! {
            "Type" => "Pages",
            "Kids" => kids,
            "Count" => count,
        }),
    );
    let catalog_id = doc.add_object(dictionary! {
        "Type" => "Catalog",
        "Pages" => pages_id,
    });
    doc.trailer.set("Root", catalog_id);
    doc.save(output)
        .map(|_| ())
        .map_err(|e| anyhow::anyhow!("could not write {}: {e}", output.display()))
}

#[cfg(not(feature = "pdfium"))]
fn rasterize(_input: &Path, _output: &Path, _dpi: u32, _jpeg_quality: u32) -> anyhow::Result<()> {
    anyhow::bail!(
        "raster PDF compression needs the PDFium rasterizer (build with --features pdfium), or \
         install Ghostscript for lossy compression"
    )
}

#[cfg(feature = "pdfium")]
fn rgba_to_rgb(raster: &crate::render::RasterImage) -> image::RgbImage {
    let mut rgb = image::RgbImage::new(raster.width, raster.height);
    for (i, px) in rgb.pixels_mut().enumerate() {
        let o = i * 4;
        *px = image::Rgb([raster.rgba[o], raster.rgba[o + 1], raster.rgba[o + 2]]);
    }
    rgb
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn method_selection_matches_python() {
        assert_eq!(choose_method("small", true), Method::Ghostscript);
        assert_eq!(choose_method("balanced", true), Method::Ghostscript);
        assert_eq!(choose_method("high", true), Method::Lossless); // gs not used for high
        assert_eq!(choose_method("small", false), Method::Rasterize);
        assert_eq!(choose_method("balanced", false), Method::Lossless);
        assert_eq!(choose_method("high", false), Method::Lossless);
        assert!(!Method::Rasterize.text_preserved());
        assert!(Method::Lossless.text_preserved());
    }

    #[test]
    fn lossless_produces_a_valid_pdf() {
        let dir = std::env::temp_dir().join(format!("knaif_cmp_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let input = dir.join("in.pdf");
        let output = dir.join("out.pdf");
        std::fs::write(&input, pdf::test_support::make_pdf(3)).unwrap();

        let profile = CompressProfile {
            ghostscript_pdfsettings: "/ebook".into(),
            lossless: Default::default(),
            rasterize_fallback: crate::profile::RasterizeFallback {
                dpi: 150,
                jpeg_quality: 75,
            },
        };
        // no gs, quality high → lossless
        let method = compress(&input, &output, "high", &profile, None).unwrap();
        assert_eq!(method, Method::Lossless);
        assert_eq!(pdf::page_count(&pdf::load(&output).unwrap()), 3);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Raster fallback (small quality, no gs) via the PDFium rasterizer — gated on the runtime lib
    /// (`$KNAIF_PDFIUM_PATH` or system). No-op otherwise, like the render backend's gated test.
    #[cfg(feature = "pdfium")]
    #[test]
    fn rasterize_fallback_when_lib_present() {
        if std::env::var("KNAIF_PDFIUM_PATH").is_err() {
            return;
        }
        let dir = std::env::temp_dir().join(format!("knaif_ras_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let input = dir.join("in.pdf");
        let output = dir.join("out.pdf");
        std::fs::write(&input, pdf::test_support::make_text_pdf(&["Alpha", "Beta"])).unwrap();
        let profile = CompressProfile {
            ghostscript_pdfsettings: "/screen".into(),
            lossless: Default::default(),
            rasterize_fallback: crate::profile::RasterizeFallback {
                dpi: 100,
                jpeg_quality: 60,
            },
        };
        let method = compress(&input, &output, "small", &profile, None).unwrap();
        assert_eq!(method, Method::Rasterize);
        // a valid 2-page image PDF (text is flattened away, so page count is the check)
        assert_eq!(pdf::page_count(&pdf::load(&output).unwrap()), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
