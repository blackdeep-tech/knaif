//! Office text extraction (docx / pptx / xlsx) — port of `_extract_office_text`. docx/pptx read the
//! OOXML zip parts and pull the `<w:t>` / `<a:t>` runs grouped by paragraph (`quick-xml`); xlsx reads
//! cells via `calamine`. Permissive deps (zip / quick-xml / calamine, all MIT). No rendering.

use std::io::Read;
use std::path::Path;

use crate::text::PageText;

/// Extract per-"page" text records: docx → one record; pptx → one per slide; xlsx → one per sheet.
pub fn extract_office_text(path: &Path) -> anyhow::Result<Vec<PageText>> {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_lowercase)
        .as_deref()
    {
        Some("docx") => docx(path),
        Some("pptx") => pptx(path),
        Some("xlsx") => xlsx(path),
        other => anyhow::bail!("Unsupported office format: {}", other.unwrap_or("")),
    }
}

fn open_zip(path: &Path) -> anyhow::Result<zip::ZipArchive<std::fs::File>> {
    let file = std::fs::File::open(path)
        .map_err(|e| anyhow::anyhow!("could not open {}: {e}", path.display()))?;
    zip::ZipArchive::new(file)
        .map_err(|e| anyhow::anyhow!("not a valid office file {}: {e}", path.display()))
}

fn read_entry(archive: &mut zip::ZipArchive<std::fs::File>, name: &str) -> anyhow::Result<String> {
    let mut entry = archive
        .by_name(name)
        .map_err(|e| anyhow::anyhow!("missing {name}: {e}"))?;
    let mut s = String::new();
    entry.read_to_string(&mut s)?;
    Ok(s)
}

fn docx(path: &Path) -> anyhow::Result<Vec<PageText>> {
    let mut zip = open_zip(path)?;
    let xml = read_entry(&mut zip, "word/document.xml")?;
    Ok(vec![PageText {
        page: 1,
        text: paragraphs(&xml).join("\n"),
    }])
}

fn pptx(path: &Path) -> anyhow::Result<Vec<PageText>> {
    let mut zip = open_zip(path)?;
    // Slide parts `ppt/slides/slideN.xml`, ordered by N (filename numbering is the common case).
    let mut slides: Vec<String> = zip
        .file_names()
        .filter(|n| n.starts_with("ppt/slides/slide") && n.ends_with(".xml"))
        .map(String::from)
        .collect();
    slides.sort_by_key(|n| slide_index(n));
    let mut out = Vec::with_capacity(slides.len());
    for (i, name) in slides.iter().enumerate() {
        let xml = read_entry(&mut zip, name)?;
        out.push(PageText {
            page: (i + 1) as i64,
            text: paragraphs(&xml).join("\n"),
        });
    }
    Ok(out)
}

fn slide_index(name: &str) -> u32 {
    name.trim_start_matches("ppt/slides/slide")
        .trim_end_matches(".xml")
        .parse()
        .unwrap_or(0)
}

fn xlsx(path: &Path) -> anyhow::Result<Vec<PageText>> {
    use calamine::{open_workbook, Reader, Xlsx};
    let mut workbook: Xlsx<_> = open_workbook(path)
        .map_err(|e| anyhow::anyhow!("could not open {}: {e}", path.display()))?;
    let names = workbook.sheet_names();
    let mut out = Vec::with_capacity(names.len());
    for (i, name) in names.iter().enumerate() {
        let range = workbook
            .worksheet_range(name)
            .map_err(|e| anyhow::anyhow!("sheet {name}: {e}"))?;
        let mut rows = Vec::new();
        for row in range.rows() {
            let cells: Vec<String> = row.iter().map(ToString::to_string).collect();
            if cells.iter().any(|c| !c.is_empty()) {
                rows.push(cells.join("\t").trim_end().to_string());
            }
        }
        out.push(PageText {
            page: (i + 1) as i64,
            text: rows.join("\n"),
        });
    }
    Ok(out)
}

/// Group `<*:t>` text runs by their enclosing `<*:p>` paragraph → one string per paragraph. Works
/// for both docx (`w:p`/`w:t`) and pptx (`a:p`/`a:t`) — the local names match after prefix strip.
fn paragraphs(xml: &str) -> Vec<String> {
    use quick_xml::events::Event;
    use quick_xml::reader::Reader;

    let mut reader = Reader::from_str(xml);
    let mut out = Vec::new();
    let mut current = String::new();
    let mut in_text = false;
    loop {
        match reader.read_event() {
            Ok(Event::Start(e)) => {
                if e.local_name().as_ref() == b"t" {
                    in_text = true;
                }
            }
            Ok(Event::End(e)) => match e.local_name().as_ref() {
                b"t" => in_text = false,
                b"p" => out.push(std::mem::take(&mut current)),
                _ => {}
            },
            Ok(Event::Text(t)) if in_text => {
                current.push_str(&t.unescape().unwrap_or_default());
            }
            Ok(Event::Eof) | Err(_) => break,
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paragraphs_group_runs_by_paragraph() {
        // docx-shaped: two paragraphs, the first with two runs.
        let xml = r#"<w:document><w:body>
            <w:p><w:r><w:t>Hello </w:t></w:r><w:r><w:t>World</w:t></w:r></w:p>
            <w:p><w:r><w:t>Second</w:t></w:r></w:p>
        </w:body></w:document>"#;
        assert_eq!(paragraphs(xml), vec!["Hello World", "Second"]);
    }

    #[test]
    fn paragraphs_work_for_pptx_local_names() {
        // pptx uses the a: namespace but the same local names.
        let xml = r#"<a:txBody><a:p><a:r><a:t>Slide text</a:t></a:r></a:p></a:txBody>"#;
        assert_eq!(paragraphs(xml), vec!["Slide text"]);
    }

    #[test]
    fn unsupported_extension_errs() {
        assert!(extract_office_text(Path::new("x.rtf")).is_err());
    }
}
