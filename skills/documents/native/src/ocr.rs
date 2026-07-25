//! `ocr_document` → a searchable PDF. Tesseract (subprocess, installer-managed) does the
//! recognition; for PDF inputs the PDFium [`Rasterizer`](crate::render::Rasterizer) renders each
//! page first. Port of `RunOcrStep` / `_write_ocr_pdf` / `_write_ocr_image_pdf`.

use std::path::{Path, PathBuf};

use crate::detect::ExternalTools;
use crate::render::{OcrEngine, RasterImage};
use crate::text::{self, IMAGE_SUFFIXES};

/// How an OCR request was fulfilled.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OcrMethod {
    /// The PDF already had a text layer, so it was copied unchanged.
    CopyExistingText,
    /// Recognized with Tesseract.
    Tesseract,
}

impl OcrMethod {
    pub fn label(self) -> &'static str {
        match self {
            OcrMethod::CopyExistingText => "copy-existing-text-layer",
            OcrMethod::Tesseract => "tesseract",
        }
    }
}

/// Tesseract OCR backend (subprocess). `$KNAIF_TESSERACT_BIN` overrides detection.
pub struct TesseractOcr {
    bin: PathBuf,
}

impl TesseractOcr {
    /// Locate Tesseract on `PATH` (or via the env override).
    pub fn detect() -> anyhow::Result<Self> {
        ExternalTools::detect()
            .tesseract
            .map(|bin| Self { bin })
            .ok_or_else(|| {
                anyhow::anyhow!(
                    "Tesseract not found. Install it (or set KNAIF_TESSERACT_BIN) for OCR."
                )
            })
    }

    /// `tesseract <image> <out-base> -l <lang> pdf` → a searchable PDF written to `out_pdf`.
    pub fn image_to_pdf(&self, image: &Path, out_pdf: &Path, language: &str) -> anyhow::Result<()> {
        // Tesseract appends `.pdf` to the output base itself.
        let base = out_pdf.with_extension("");
        let result = std::process::Command::new(&self.bin)
            .arg(image)
            .arg(&base)
            .args(["-l", language, "pdf"])
            .output()
            .map_err(|e| {
                anyhow::anyhow!("could not launch tesseract ({}): {e}", self.bin.display())
            })?;
        if !result.status.success() {
            anyhow::bail!(
                "tesseract failed: {}",
                String::from_utf8_lossy(&result.stderr).trim()
            );
        }
        let produced = base.with_extension("pdf");
        if produced != out_pdf && produced.exists() {
            std::fs::rename(&produced, out_pdf).map_err(|e| {
                anyhow::anyhow!(
                    "could not move {} to {}: {e}",
                    produced.display(),
                    out_pdf.display()
                )
            })?;
        }
        Ok(())
    }

    /// Recognize plain text from an image file (`tesseract <image> <base> -l <lang>` → `<base>.txt`).
    /// Port of `_ocr_image_text_records` (used by `extract_text` / `find` on image inputs).
    pub fn image_file_to_text(&self, image: &Path, language: &str) -> anyhow::Result<String> {
        let dir = temp_dir("knaif_ocr_imgtxt");
        let run = || -> anyhow::Result<String> {
            let base = dir.join("out");
            let result = std::process::Command::new(&self.bin)
                .arg(image)
                .arg(&base)
                .args(["-l", language])
                .output()
                .map_err(|e| anyhow::anyhow!("could not launch tesseract: {e}"))?;
            if !result.status.success() {
                anyhow::bail!(
                    "tesseract failed: {}",
                    String::from_utf8_lossy(&result.stderr).trim()
                );
            }
            Ok(std::fs::read_to_string(base.with_extension("txt")).unwrap_or_default())
        };
        let out = run();
        let _ = std::fs::remove_dir_all(&dir);
        out
    }
}

impl OcrEngine for TesseractOcr {
    fn recognize(&self, image: &RasterImage, language: &str) -> anyhow::Result<String> {
        let dir = temp_dir("knaif_ocr_rec");
        let run = || -> anyhow::Result<String> {
            let png = dir.join("in.png");
            save_rgba_png(image, &png)?;
            let base = dir.join("out");
            let result = std::process::Command::new(&self.bin)
                .arg(&png)
                .arg(&base)
                .args(["-l", language])
                .output()
                .map_err(|e| anyhow::anyhow!("could not launch tesseract: {e}"))?;
            if !result.status.success() {
                anyhow::bail!(
                    "tesseract failed: {}",
                    String::from_utf8_lossy(&result.stderr).trim()
                );
            }
            Ok(std::fs::read_to_string(base.with_extension("txt")).unwrap_or_default())
        };
        let out = run();
        let _ = std::fs::remove_dir_all(&dir);
        out
    }
}

/// Produce a searchable PDF from `input` at `output`. A PDF that already has a text layer is copied
/// unchanged; an image is OCR'd directly; a scanned PDF is rendered page-by-page then OCR'd + merged.
pub fn ocr_document(input: &Path, output: &Path, language: &str) -> anyhow::Result<OcrMethod> {
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let suffix = suffix_of(input);
    if suffix == "pdf" {
        if text::inspect(input)?.has_text_layer {
            std::fs::copy(input, output)
                .map_err(|e| anyhow::anyhow!("could not copy to {}: {e}", output.display()))?;
            return Ok(OcrMethod::CopyExistingText);
        }
        ocr_pdf(input, output, language)?;
        return Ok(OcrMethod::Tesseract);
    }
    if IMAGE_SUFFIXES.contains(&suffix.as_str()) {
        TesseractOcr::detect()?.image_to_pdf(input, output, language)?;
        return Ok(OcrMethod::Tesseract);
    }
    anyhow::bail!("OCR supports PDF and image inputs, not .{suffix}");
}

/// Render each page (PDFium), OCR it to a one-page searchable PDF, and merge. Port of
/// `_write_ocr_pdf`. Needs the PDFium rasterizer (feature `pdfium`).
#[cfg(feature = "pdfium")]
fn ocr_pdf(input: &Path, output: &Path, language: &str) -> anyhow::Result<()> {
    use crate::render::{PdfiumRasterizer, Rasterizer};

    let tess = TesseractOcr::detect()?;
    let rasterizer = PdfiumRasterizer::new()?;
    let pages = rasterizer.page_count(input)?;
    if pages == 0 {
        anyhow::bail!("Cannot OCR an empty PDF.");
    }
    let dir = temp_dir("knaif_ocr_pdf");
    let run = || -> anyhow::Result<()> {
        let mut page_pdfs = Vec::with_capacity(pages as usize);
        for page in 1..=pages {
            // scale 2.0 ≈ 144 dpi, matching `_render_pdf_page_image`.
            let raster = rasterizer.render_page(input, page, 144.0)?;
            let png = dir.join(format!("p{page}.png"));
            save_rgba_png(&raster, &png)?;
            let page_pdf = dir.join(format!("p{page}.pdf"));
            tess.image_to_pdf(&png, &page_pdf, language)?;
            page_pdfs.push(page_pdf);
        }
        let docs = page_pdfs
            .iter()
            .map(|p| crate::pdf::load(p))
            .collect::<Result<Vec<_>, _>>()?;
        let mut merged = crate::pdf::merge(docs)?;
        crate::pdf::save(&mut merged, output)
    };
    let out = run();
    let _ = std::fs::remove_dir_all(&dir);
    out
}

#[cfg(not(feature = "pdfium"))]
fn ocr_pdf(_input: &Path, _output: &Path, _language: &str) -> anyhow::Result<()> {
    anyhow::bail!(
        "OCR of PDF inputs needs the PDFium rasterizer (build with --features pdfium); image \
         inputs OCR without it"
    )
}

fn suffix_of(path: &Path) -> String {
    path.extension()
        .and_then(|e| e.to_str())
        .map(str::to_lowercase)
        .unwrap_or_default()
}

fn save_rgba_png(image: &RasterImage, path: &Path) -> anyhow::Result<()> {
    image::RgbaImage::from_raw(image.width, image.height, image.rgba.clone())
        .ok_or_else(|| anyhow::anyhow!("raster buffer does not match its dimensions"))?
        .save(path)
        .map_err(|e| anyhow::anyhow!("could not write {}: {e}", path.display()))
}

fn temp_dir(prefix: &str) -> PathBuf {
    use std::sync::atomic::{AtomicU32, Ordering};
    static N: AtomicU32 = AtomicU32::new(0);
    let dir = std::env::temp_dir().join(format!(
        "{prefix}_{}_{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&dir).ok();
    dir
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Image → searchable PDF, gated on Tesseract being installed (no-op otherwise).
    #[test]
    fn image_ocr_when_tesseract_present() {
        let Ok(tess) = TesseractOcr::detect() else {
            return;
        };
        let dir = temp_dir("knaif_ocr_test");
        // a white image with black "HELLO" text drawn as blocks is overkill; a blank image still
        // produces a valid (empty-text) searchable PDF, which is what we assert.
        let png = dir.join("in.png");
        image::RgbImage::from_pixel(200, 60, image::Rgb([255, 255, 255]))
            .save(&png)
            .unwrap();
        let out = dir.join("out.pdf");
        tess.image_to_pdf(&png, &out, "eng").unwrap();
        assert_eq!(crate::pdf::page_count(&crate::pdf::load(&out).unwrap()), 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Full scanned-PDF path: render (PDFium) → OCR (Tesseract) → merge. Gated on both libs.
    #[cfg(feature = "pdfium")]
    #[test]
    fn scanned_pdf_ocr_end_to_end() {
        if std::env::var("KNAIF_PDFIUM_PATH").is_err() || TesseractOcr::detect().is_err() {
            return;
        }
        let dir = temp_dir("knaif_ocr_scan");
        // An image-only PDF (no text layer), i.e. a "scan".
        let png = dir.join("scan.png");
        image::RgbImage::from_pixel(300, 120, image::Rgb([255, 255, 255]))
            .save(&png)
            .unwrap();
        let scanned = dir.join("scan.pdf");
        crate::convert::convert(&png, "pdf", &scanned).unwrap();

        let out = dir.join("scan-ocr.pdf");
        let method = ocr_document(&scanned, &out, "eng").unwrap();
        assert_eq!(method, OcrMethod::Tesseract);
        assert_eq!(crate::pdf::page_count(&crate::pdf::load(&out).unwrap()), 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A PDF that already has a text layer is copied, not re-OCR'd.
    #[test]
    fn pdf_with_text_layer_is_copied() {
        let dir = temp_dir("knaif_ocr_copy");
        let input = dir.join("in.pdf");
        std::fs::write(&input, crate::pdf::test_support::make_text_pdf(&["Alpha"])).unwrap();
        let out = dir.join("out.pdf");
        assert_eq!(
            ocr_document(&input, &out, "eng").unwrap(),
            OcrMethod::CopyExistingText
        );
        assert!(out.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn unsupported_input_errors() {
        let dir = temp_dir("knaif_ocr_unsupported");
        std::fs::write(dir.join("a.txt"), "hi").unwrap();
        assert!(ocr_document(&dir.join("a.txt"), &dir.join("a-ocr.pdf"), "eng").is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
