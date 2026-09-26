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
    office_to_pdf_with(&soffice, input, output)
}

/// The conversion itself, with the `soffice` to run passed in (so a test can stand one in).
///
/// soffice names its result `<stem>.pdf` and writes it over any file of that name in `--outdir`.
/// Pointing `--outdir` at the output's folder therefore destroyed the user's own `sample.pdf` on
/// `sample.docx -> conv.pdf` (found 2026-09-26, both runtimes). It converts into a private
/// directory instead, and only the requested output is written where the user's files are.
fn office_to_pdf_with(soffice: &Path, input: &Path, output: &Path) -> anyhow::Result<()> {
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let private = private_dir()?;
    let result = std::process::Command::new(soffice)
        .args(["--headless", "--convert-to", "pdf", "--outdir"])
        .arg(&private)
        .arg(input)
        .output()
        .map_err(|e| anyhow::anyhow!("could not launch soffice ({}): {e}", soffice.display()));
    let moved = result.and_then(|result| {
        if !result.status.success() {
            anyhow::bail!(
                "soffice conversion failed: {}",
                String::from_utf8_lossy(&result.stderr).trim()
            );
        }
        let stem = input
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("output");
        let produced = private.join(format!("{stem}.pdf"));
        // `rename` fails across filesystems (a temp dir on another volume); copy then.
        std::fs::rename(&produced, output)
            .or_else(|_| std::fs::copy(&produced, output).map(|_| ()))
            .map_err(|e| {
                anyhow::anyhow!(
                    "soffice produced {} but it could not be moved to {}: {e}",
                    produced.display(),
                    output.display()
                )
            })
    });
    let _ = std::fs::remove_dir_all(&private);
    moved
}

/// A fresh directory under the system temp dir, unique to this process and call.
fn private_dir() -> anyhow::Result<std::path::PathBuf> {
    use std::sync::atomic::{AtomicU32, Ordering};
    static N: AtomicU32 = AtomicU32::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let dir = std::env::temp_dir().join(format!(
        "knaif-soffice-{}-{nanos}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&dir)
        .map_err(|e| anyhow::anyhow!("could not create {}: {e}", dir.display()))?;
    Ok(dir)
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

    /// A stand-in for `soffice --headless --convert-to pdf --outdir DIR INPUT` that behaves as the
    /// real one does where it matters: it writes `DIR/<stem>.pdf`, over any file of that name.
    fn fake_soffice(dir: &Path) -> std::path::PathBuf {
        if cfg!(windows) {
            let path = dir.join("fake_soffice.cmd");
            std::fs::write(
                &path,
                "@echo off\r\nfor %%F in (%6) do echo converted> \"%~5\\%%~nF.pdf\"\r\n",
            )
            .unwrap();
            path
        } else {
            let path = dir.join("fake_soffice.sh");
            std::fs::write(
                &path,
                "#!/bin/sh\nstem=$(basename \"$6\"); stem=${stem%.*}\necho converted > \"$5/$stem.pdf\"\n",
            )
            .unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
            }
            path
        }
    }

    /// `sample.docx -> conv.pdf` destroyed the user's own `sample.pdf` (found 2026-09-26): soffice
    /// writes `<stem>.pdf` into `--outdir`, which was the output's folder. Now a private one.
    #[test]
    fn office_conversion_never_replaces_a_same_stem_pdf() {
        let dir = tmpdir();
        let soffice = fake_soffice(&dir);
        std::fs::write(dir.join("sample.docx"), "docx").unwrap();
        let users_pdf = dir.join("sample.pdf");
        std::fs::write(&users_pdf, "the user's own file").unwrap();

        let out = dir.join("conv.pdf");
        office_to_pdf_with(&soffice, &dir.join("sample.docx"), &out).unwrap();

        assert_eq!(
            std::fs::read_to_string(&users_pdf).unwrap(),
            "the user's own file"
        );
        assert!(std::fs::read_to_string(&out).unwrap().contains("converted"));
        let _ = std::fs::remove_dir_all(&dir);
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
