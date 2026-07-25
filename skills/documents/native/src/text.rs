//! Read ops: `extract_text`, `find_in_document`, `inspect_document`. Ports the text-facing
//! `_engine.py` / step logic. PDF text comes from `lopdf`'s extractor and plain-text files are read
//! directly; image OCR and office-format text extraction stay deferred (rasterizing / heavy deps).

use std::path::Path;

use lopdf::Document;
use regex::RegexBuilder;

use crate::pdf;

/// Suffix categories (ports of the `_engine.py` sets), without the leading dot.
pub const TEXT_SUFFIXES: [&str; 2] = ["txt", "md"];
pub const IMAGE_SUFFIXES: [&str; 3] = ["png", "jpg", "jpeg"];
pub const OFFICE_SUFFIXES: [&str; 3] = ["docx", "pptx", "xlsx"];

/// One page's extracted text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageText {
    pub page: i64,
    pub text: String,
}

/// A search hit within a document.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Match {
    pub page: i64,
    pub snippet: String,
    pub span: (usize, usize),
}

/// Result of `inspect_document`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Inspection {
    pub format: String,
    pub size_bytes: u64,
    pub encrypted: bool,
    pub has_text_layer: bool,
    pub pages: usize,
}

fn suffix_of(path: &Path) -> String {
    path.extension()
        .and_then(|e| e.to_str())
        .map(str::to_lowercase)
        .unwrap_or_default()
}

/// Extract per-page text from an already-loaded PDF for the given 1-based `pages`. Errors on an
/// encrypted PDF (mirrors Python: "Unlock it first."). Empty text is returned as-is — the OCR
/// fallback for scanned PDFs is deferred (rasterizing).
pub fn extract_pdf_text(doc: &Document, pages: &[i64]) -> anyhow::Result<Vec<PageText>> {
    if pdf::is_encrypted(doc) {
        anyhow::bail!(
            "Cannot extract text from encrypted PDF without a password. Unlock it first."
        );
    }
    let mut out = Vec::with_capacity(pages.len());
    for &p in pages {
        let text = doc.extract_text(&[p as u32]).unwrap_or_default();
        out.push(PageText { page: p, text });
    }
    Ok(out)
}

/// Extract text records from a document by path. PDF pages honor `pages` (a page spec); plain-text
/// files (`.txt`/`.md`) are read as a single page-1 record. Other formats (image OCR, office) are
/// deferred and error here rather than silently returning nothing.
pub fn extract_text(path: &Path, pages: Option<&str>) -> anyhow::Result<Vec<PageText>> {
    let suffix = suffix_of(path);
    if TEXT_SUFFIXES.contains(&suffix.as_str()) {
        let text = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("could not read {}: {e}", path.display()))?;
        return Ok(vec![PageText { page: 1, text }]);
    }
    if suffix == "pdf" {
        let doc = pdf::load(path)?;
        let total = pdf::page_count(&doc) as i64;
        let selected = pdf::parse_pages(pages, total, true)?;
        return extract_pdf_text(&doc, &selected);
    }
    if OFFICE_SUFFIXES.contains(&suffix.as_str()) {
        return crate::office::extract_office_text(path);
    }
    if IMAGE_SUFFIXES.contains(&suffix.as_str()) {
        // Image text = OCR (Tesseract). Default language `eng` (documents is English-first).
        let text = crate::ocr::TesseractOcr::detect()?.image_file_to_text(path, "eng")?;
        return Ok(vec![PageText { page: 1, text }]);
    }
    anyhow::bail!("Unsupported document format: {}", suffix_of(path));
}

/// Find `query` in the extracted `records`. `regex` toggles regex vs. literal; `ignore_case`
/// defaults on. Reports one match per hit with a trimmed ±40-char snippet and the byte span. Port
/// of `FindInDocumentStep` (byte offsets — documents is English/ASCII-first).
pub fn find(
    records: &[PageText],
    query: &str,
    regex: bool,
    ignore_case: bool,
) -> anyhow::Result<Vec<Match>> {
    let pattern = if regex {
        query.to_string()
    } else {
        regex::escape(query)
    };
    let re = RegexBuilder::new(&pattern)
        .case_insensitive(ignore_case)
        .build()
        .map_err(|e| anyhow::anyhow!("invalid search pattern {query:?}: {e}"))?;
    let mut matches = Vec::new();
    for record in records {
        for m in re.find_iter(&record.text) {
            matches.push(Match {
                page: record.page,
                snippet: snippet(&record.text, m.start(), m.end()),
                span: (m.start(), m.end()),
            });
        }
    }
    Ok(matches)
}

/// A ±`radius`-byte window around `[start, end)`, newlines flattened and trimmed. Byte offsets are
/// snapped to char boundaries so non-ASCII text can't panic. Port of `_snippet` (radius 40).
fn snippet(text: &str, start: usize, end: usize) -> String {
    let radius = 40;
    let left = floor_char_boundary(text, start.saturating_sub(radius));
    let right = ceil_char_boundary(text, (end + radius).min(text.len()));
    text[left..right].replace('\n', " ").trim().to_string()
}

fn floor_char_boundary(s: &str, mut i: usize) -> usize {
    while i > 0 && !s.is_char_boundary(i) {
        i -= 1;
    }
    i
}

fn ceil_char_boundary(s: &str, mut i: usize) -> usize {
    while i < s.len() && !s.is_char_boundary(i) {
        i += 1;
    }
    i
}

/// Inspect a document: format, size, and (for PDFs) page count / encryption / text-layer presence.
/// Port of `InspectDocumentStep` for the permissive formats; office page counts (pptx/xlsx) are
/// approximated as 1 (their per-slide/sheet counting needs office libs — deferred).
pub fn inspect(path: &Path) -> anyhow::Result<Inspection> {
    let suffix = suffix_of(path);
    let size_bytes = std::fs::metadata(path)
        .map_err(|e| anyhow::anyhow!("could not stat {}: {e}", path.display()))?
        .len();
    let format = if suffix.is_empty() {
        "unknown".to_string()
    } else {
        suffix.clone()
    };

    if suffix == "pdf" {
        let doc = pdf::load(path)?;
        let encrypted = pdf::is_encrypted(&doc);
        let pages = if encrypted { 0 } else { pdf::page_count(&doc) };
        let has_text_layer = !encrypted
            && (1..=pages as i64).any(|p| {
                doc.extract_text(&[p as u32])
                    .map(|t| !t.trim().is_empty())
                    .unwrap_or(false)
            });
        return Ok(Inspection {
            format,
            size_bytes,
            encrypted,
            has_text_layer,
            pages,
        });
    }

    let has_text_layer =
        TEXT_SUFFIXES.contains(&suffix.as_str()) || OFFICE_SUFFIXES.contains(&suffix.as_str());
    // pptx/xlsx page count = slides/sheets (docx stays 1). Mirrors Python inspect_document.
    let pages = if matches!(suffix.as_str(), "pptx" | "xlsx") {
        crate::office::extract_office_text(path)
            .map(|records| records.len().max(1))
            .unwrap_or(1)
    } else {
        1
    };
    Ok(Inspection {
        format,
        size_bytes,
        encrypted: false,
        has_text_layer,
        pages,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pdf::test_support::{make_pdf, make_text_pdf};

    fn write_temp(name: &str, bytes: &[u8]) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("knaif_text_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(name);
        std::fs::write(&path, bytes).unwrap();
        path
    }

    #[test]
    fn extract_pdf_text_reads_page_content() {
        let doc = Document::load_mem(&make_text_pdf(&["Alpha", "Beta", "Gamma"])).unwrap();
        let records = extract_pdf_text(&doc, &[1, 2, 3]).unwrap();
        assert_eq!(records.len(), 3);
        assert!(
            records[0].text.contains("Alpha"),
            "got {:?}",
            records[0].text
        );
        assert!(records[1].text.contains("Beta"));
        assert!(records[2].text.contains("Gamma"));
    }

    #[test]
    fn extract_text_from_plain_text_file() {
        let path = write_temp("sample.txt", b"Invoice Alpha\nTotal: 42 EUR\n");
        let records = extract_text(&path, None).unwrap();
        assert_eq!(records.len(), 1);
        assert!(records[0].text.contains("Invoice Alpha"));
    }

    #[test]
    fn extract_text_pdf_honors_page_selection() {
        let path = write_temp("doc.pdf", &make_text_pdf(&["Alpha", "Beta", "Gamma"]));
        let records = extract_text(&path, Some("2")).unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].page, 2);
        assert!(records[0].text.contains("Beta"));
    }

    #[test]
    fn find_literal_and_case_insensitive() {
        let records = vec![
            PageText {
                page: 1,
                text: "Invoice Alpha total".into(),
            },
            PageText {
                page: 2,
                text: "beta and ALPHA again".into(),
            },
        ];
        let hits = find(&records, "alpha", false, true).unwrap();
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].page, 1);
        assert_eq!(hits[1].page, 2);
        assert!(hits[0].snippet.contains("Alpha"));

        // case-sensitive misses the lowercase-only occurrences
        let strict = find(&records, "ALPHA", false, false).unwrap();
        assert_eq!(strict.len(), 1);
        assert_eq!(strict[0].page, 2);
    }

    #[test]
    fn find_regex_mode() {
        let records = vec![PageText {
            page: 1,
            text: "Total: 42 EUR".into(),
        }];
        let hits = find(&records, r"\d+ EUR", true, true).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].snippet, "Total: 42 EUR");
        // a literal search for the same regex text finds nothing
        assert!(find(&records, r"\d+ EUR", false, true).unwrap().is_empty());
    }

    #[test]
    fn inspect_pdf_reports_pages_and_text_layer() {
        let with_text = write_temp("t.pdf", &make_text_pdf(&["Alpha", "Beta"]));
        let insp = inspect(&with_text).unwrap();
        assert_eq!(insp.format, "pdf");
        assert_eq!(insp.pages, 2);
        assert!(!insp.encrypted);
        assert!(insp.has_text_layer);
        assert!(insp.size_bytes > 0);

        // blank pages → no text layer
        let blank = write_temp("b.pdf", &make_pdf(1));
        assert!(!inspect(&blank).unwrap().has_text_layer);
    }

    #[test]
    fn inspect_plain_text_file() {
        let path = write_temp("notes.md", b"# Title\n");
        let insp = inspect(&path).unwrap();
        assert_eq!(insp.format, "md");
        assert!(insp.has_text_layer);
        assert_eq!(insp.pages, 1);
    }
}
