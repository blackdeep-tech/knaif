//! `convert_document` — the format-conversion tool. Port of `ConvertDocumentStep`:
//! - →`txt`/`md`: extract text and write it (in-process).
//! - image (`png`/`jpg`/`jpeg`) → `pdf`: embed the image as a single full-page PDF (via the `image`
//!   crate + lopdf; no rasterizer needed).
//! - office (`docx`/`pptx`/`xlsx`) → `pdf`: LibreOffice `soffice` subprocess (installer-managed).
//! - anything else (incl. pdf→image): not implemented (matches Python's `NotImplementedError`).

use std::io::Cursor;
use std::path::Path;

use lopdf::{dictionary, Document, Object, Stream};

use crate::detect::ExternalTools;
use crate::text::{self, IMAGE_SUFFIXES, OFFICE_SUFFIXES};

fn suffix_of(path: &Path) -> String {
    path.extension()
        .and_then(|e| e.to_str())
        .map(str::to_lowercase)
        .unwrap_or_default()
}

/// Convert `input` to `to_format`, writing `output`. Supported combos mirror the Python slice.
pub fn convert(input: &Path, to_format: &str, output: &Path) -> anyhow::Result<()> {
    let to = to_format.to_lowercase();
    let from = suffix_of(input);

    if to == "txt" || to == "md" {
        let joined = text::extract_text(input, None)?
            .iter()
            .map(|r| r.text.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        std::fs::write(output, joined)
            .map_err(|e| anyhow::anyhow!("could not write {}: {e}", output.display()))?;
        return Ok(());
    }
    if IMAGE_SUFFIXES.contains(&from.as_str()) && to == "pdf" {
        return image_to_pdf(input, output);
    }
    if OFFICE_SUFFIXES.contains(&from.as_str()) && to == "pdf" {
        return office_to_pdf(input, output);
    }
    anyhow::bail!("Conversion to {to_format:?} is not implemented natively yet.");
}

/// Embed a raster image as a single-page PDF (image drawn to fill the page). The image is JPEG-
/// re-encoded and embedded as a `DCTDecode` XObject; the page is sized to the pixel dimensions.
fn image_to_pdf(input: &Path, output: &Path) -> anyhow::Result<()> {
    let img = image::open(input)
        .map_err(|e| anyhow::anyhow!("could not open image {}: {e}", input.display()))?
        .to_rgb8();
    let (w, h) = img.dimensions();
    let jpeg = encode_jpeg(&img, 90)?;

    let mut doc = Document::with_version("1.5");
    let pages_id = doc.new_object_id();
    let page_id = add_jpeg_page(&mut doc, pages_id, jpeg, w, h);
    doc.objects.insert(
        pages_id,
        Object::Dictionary(dictionary! {
            "Type" => "Pages",
            "Kids" => vec![page_id.into()],
            "Count" => 1,
        }),
    );
    let catalog_id = doc.add_object(dictionary! {
        "Type" => "Catalog",
        "Pages" => pages_id,
    });
    doc.trailer.set("Root", catalog_id);
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    doc.save(output)
        .map(|_| ())
        .map_err(|e| anyhow::anyhow!("could not write {}: {e}", output.display()))
}

/// JPEG-encode an RGB image at `quality` (1–100).
pub(crate) fn encode_jpeg(img: &image::RgbImage, quality: u8) -> anyhow::Result<Vec<u8>> {
    let mut jpeg = Vec::new();
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut Cursor::new(&mut jpeg), quality)
        .encode_image(img)
        .map_err(|e| anyhow::anyhow!("could not encode JPEG: {e}"))?;
    Ok(jpeg)
}

/// Add one page to `doc` that draws `jpeg` (a `w`×`h` DCTDecode image) filling a page of the same
/// pixel dimensions; returns the new page's object id. Shared by image→pdf and raster compression.
pub(crate) fn add_jpeg_page(
    doc: &mut Document,
    pages_id: lopdf::ObjectId,
    jpeg: Vec<u8>,
    w: u32,
    h: u32,
) -> lopdf::ObjectId {
    let image_id = doc.add_object(Stream::new(
        dictionary! {
            "Type" => "XObject",
            "Subtype" => "Image",
            "Width" => w as i64,
            "Height" => h as i64,
            "ColorSpace" => "DeviceRGB",
            "BitsPerComponent" => 8,
            "Filter" => "DCTDecode",
        },
        jpeg,
    ));
    // Scale the unit image XObject to the full page via the `cm` matrix.
    let content = format!("q\n{w} 0 0 {h} 0 0 cm\n/Im0 Do\nQ\n");
    let content_id = doc.add_object(Stream::new(dictionary! {}, content.into_bytes()));
    doc.add_object(dictionary! {
        "Type" => "Page",
        "Parent" => pages_id,
        "MediaBox" => vec![0.into(), 0.into(), (w as i64).into(), (h as i64).into()],
        "Contents" => content_id,
        "Resources" => dictionary! {
            "XObject" => dictionary! { "Im0" => image_id },
        },
    })
}

/// Convert an office document to PDF via a detected `soffice`/`libreoffice`. Port of the Python
/// subprocess call (`--headless --convert-to pdf --outdir <dir>`), then rename to `output`.
fn office_to_pdf(input: &Path, output: &Path) -> anyhow::Result<()> {
    let soffice = ExternalTools::detect().libreoffice.ok_or_else(|| {
        anyhow::anyhow!("LibreOffice (soffice) not found. Install it for Office→PDF conversion.")
    })?;
    let outdir = output.parent().unwrap_or_else(|| Path::new("."));
    std::fs::create_dir_all(outdir).ok();
    let result = std::process::Command::new(&soffice)
        .args(["--headless", "--convert-to", "pdf", "--outdir"])
        .arg(outdir)
        .arg(input)
        .output()
        .map_err(|e| anyhow::anyhow!("could not launch soffice ({}): {e}", soffice.display()))?;
    if !result.status.success() {
        anyhow::bail!(
            "soffice conversion failed: {}",
            String::from_utf8_lossy(&result.stderr).trim()
        );
    }
    // soffice writes `<stem>.pdf` into outdir; move it to the requested output if different.
    let stem = input
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("output");
    let produced = outdir.join(format!("{stem}.pdf"));
    if produced != output {
        std::fs::rename(&produced, output).map_err(|e| {
            anyhow::anyhow!(
                "soffice produced {} but could not move it to {}: {e}",
                produced.display(),
                output.display()
            )
        })?;
    }
    Ok(())
}

/// The extension suffix a converted output gets when no explicit output is given
/// (mirrors `_output_path(..., f".{to_format}")` → replace the extension).
pub fn output_suffix(to_format: &str) -> String {
    format!(".{}", to_format.to_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pdf;

    fn tmpdir() -> std::path::PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static N: AtomicU32 = AtomicU32::new(0);
        let dir = std::env::temp_dir().join(format!(
            "knaif_convert_{}_{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn text_file_to_md_writes_content() {
        let dir = tmpdir();
        std::fs::write(dir.join("a.txt"), "Hello\nWorld\n").unwrap();
        let out = dir.join("a.md");
        convert(&dir.join("a.txt"), "md", &out).unwrap();
        let written = std::fs::read_to_string(&out).unwrap();
        assert!(written.contains("Hello") && written.contains("World"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn png_to_pdf_is_a_valid_one_page_pdf() {
        let dir = tmpdir();
        // a tiny 4x3 red PNG
        let img = image::RgbImage::from_pixel(4, 3, image::Rgb([200, 30, 30]));
        let png = dir.join("pic.png");
        img.save(&png).unwrap();

        let out = dir.join("pic.pdf");
        convert(&png, "pdf", &out).unwrap();

        let doc = pdf::load(&out).unwrap();
        assert_eq!(pdf::page_count(&doc), 1);
        // the page carries our image XObject
        let pages = doc.get_pages();
        let page = doc.get_object(pages[&1]).unwrap().as_dict().unwrap();
        let res = page.get(b"Resources").unwrap().as_dict().unwrap();
        assert!(res.get(b"XObject").unwrap().as_dict().unwrap().has(b"Im0"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn unsupported_conversion_errs() {
        let dir = tmpdir();
        std::fs::write(dir.join("a.pdf"), pdf::test_support::make_pdf(1)).unwrap();
        // pdf→png is deliberately not implemented (matches Python)
        assert!(convert(&dir.join("a.pdf"), "png", &dir.join("a.png")).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn output_suffix_replaces_extension() {
        assert_eq!(output_suffix("TXT"), ".txt");
        assert_eq!(output_suffix("pdf"), ".pdf");
    }
}
