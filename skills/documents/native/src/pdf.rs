//! Structural PDF operations via `lopdf` (pure-Rust, MIT) — the in-process half of the documents
//! skill. Ports the page-selection helpers (`_parse_pages` / `_resolve_endpoint` /
//! `_parse_page_range_specs`) and the page-list manipulations the Python steps do through pikepdf.
//! Rasterizing ops (compress fallback, convert→image, OCR) are out of scope here — they stay
//! subprocess/deferred.

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use lopdf::encryption::crypt_filters::{Aes128CryptFilter, CryptFilter};
use lopdf::{
    dictionary, Document, EncryptionState, EncryptionVersion, Object, ObjectId, Permissions,
};

// ── Page-selection parsing (ports of `_engine.py`) ───────────────────────────────────────────

/// Resolve one range endpoint to a 1-based page number. Accepts an integer, the words
/// `first`/`last`/`end`, or an empty string (open-ended → `default`). Port of `_resolve_endpoint`.
fn resolve_endpoint(token: &str, total_pages: i64, default: i64) -> anyhow::Result<i64> {
    let token = token.trim().to_lowercase();
    match token.as_str() {
        "" => Ok(default),
        "first" => Ok(1),
        "last" | "end" => Ok(total_pages),
        _ => token
            .parse::<i64>()
            .map_err(|_| anyhow::anyhow!("Unrecognized page reference: {token:?}")),
    }
}

/// Parse a page spec (`"1,3-5"`, `"all"`, `"3-end"`, …) into an ordered 1-based page list. `None`/
/// empty → every page. With `strict`, out-of-range pages error; otherwise they are kept for the
/// caller to filter (reorder tolerates padding). Port of `_parse_pages`.
pub fn parse_pages(raw: Option<&str>, total_pages: i64, strict: bool) -> anyhow::Result<Vec<i64>> {
    let raw = match raw {
        None | Some("") => return Ok((1..=total_pages).collect()),
        Some(r) => r,
    };
    let mut pages: Vec<i64> = Vec::new();
    for part in raw.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        if matches!(part.to_lowercase().as_str(), "all" | "*") {
            pages.extend(1..=total_pages);
            continue;
        }
        if let Some((start_s, end_s)) = part.split_once('-') {
            let start = resolve_endpoint(start_s, total_pages, 1)?;
            let end = resolve_endpoint(end_s, total_pages, total_pages)?;
            if start > end {
                anyhow::bail!("Invalid page range: {part}");
            }
            pages.extend(start..=end);
        } else {
            pages.push(resolve_endpoint(part, total_pages, 1)?);
        }
    }
    let invalid: Vec<i64> = pages
        .iter()
        .copied()
        .filter(|p| *p < 1 || *p > total_pages)
        .collect();
    if strict && !invalid.is_empty() {
        anyhow::bail!("Page(s) out of range 1-{total_pages}: {invalid:?}");
    }
    Ok(pages)
}

/// Parse a comma-separated set of range specs into `(label, pages)` pairs (one output file per
/// spec, for `split_pdf`). Port of `_parse_page_range_specs`.
pub fn parse_page_range_specs(
    raw: &str,
    total_pages: i64,
) -> anyhow::Result<Vec<(String, Vec<i64>)>> {
    let mut specs = Vec::new();
    for part in raw.split(',') {
        let label = part.trim();
        if label.is_empty() {
            continue;
        }
        specs.push((
            label.to_string(),
            parse_pages(Some(label), total_pages, true)?,
        ));
    }
    if specs.is_empty() {
        anyhow::bail!("At least one page range is required.");
    }
    Ok(specs)
}

// ── Document ops via lopdf ───────────────────────────────────────────────────────────────────

/// Load a PDF from disk, with a friendly error.
pub fn load(path: &std::path::Path) -> anyhow::Result<Document> {
    Document::load(path).map_err(|e| anyhow::anyhow!("could not open PDF {}: {e}", path.display()))
}

/// Save a document to disk.
pub fn save(doc: &mut Document, path: &std::path::Path) -> anyhow::Result<()> {
    doc.save(path)
        .map(|_| ())
        .map_err(|e| anyhow::anyhow!("could not write PDF {}: {e}", path.display()))
}

/// Number of pages. Port of `_pdf_page_count`.
pub fn page_count(doc: &Document) -> usize {
    doc.get_pages().len()
}

/// Whether the PDF is encrypted (trailer carries an `/Encrypt` entry).
pub fn is_encrypted(doc: &Document) -> bool {
    doc.trailer.get(b"Encrypt").is_ok()
}

/// Rotate `selected` (1-based) pages by `degrees` **relatively** (mirrors pikepdf
/// `page.rotate(degrees, relative=True)`); the result is normalized to `[0, 360)`.
pub fn rotate_pages(doc: &mut Document, selected: &[i64], degrees: i64) -> anyhow::Result<()> {
    let pages = doc.get_pages(); // page number (1-based) → object id
    for &page_number in selected {
        let object_id = pages
            .get(&(page_number as u32))
            .copied()
            .ok_or_else(|| anyhow::anyhow!("page {page_number} not found"))?;
        let dict = doc.get_object_mut(object_id)?.as_dict_mut()?;
        let current = dict
            .get(b"Rotate")
            .ok()
            .and_then(|o| o.as_i64().ok())
            .unwrap_or(0);
        let normalized = (current + degrees).rem_euclid(360);
        dict.set("Rotate", Object::Integer(normalized));
    }
    Ok(())
}

/// Delete `remove` (1-based) pages, keeping the rest in order. Mirrors the pikepdf
/// build-a-new-doc-of-kept-pages behavior via lopdf's `delete_pages`.
pub fn remove_pages(doc: &mut Document, remove: &[i64]) -> anyhow::Result<()> {
    let total = page_count(doc) as i64;
    let to_delete: Vec<u32> = remove.iter().map(|p| *p as u32).collect();
    if to_delete.len() as i64 >= total {
        anyhow::bail!("refusing to remove every page (would produce an empty document)");
    }
    doc.delete_pages(&to_delete);
    Ok(())
}

/// Password-protect a (plain) document with AES-128 (V4), setting both the user and owner password
/// to `password`. Errors if it is already encrypted. Mirrors `protect_pdf` (pikepdf user=owner).
pub fn protect(doc: &mut Document, password: &str) -> anyhow::Result<()> {
    if is_encrypted(doc) {
        anyhow::bail!("document is already password-protected");
    }
    ensure_file_id(doc); // AES/V4 keys off the trailer /ID; synthesize one if the PDF lacks it.
    let mut crypt_filters: BTreeMap<Vec<u8>, Arc<dyn CryptFilter>> = BTreeMap::new();
    crypt_filters.insert(b"StdCF".to_vec(), Arc::new(Aes128CryptFilter));
    // The `V4` builder borrows `doc` immutably; drop that borrow before the `&mut` encrypt call.
    let state = {
        let version = EncryptionVersion::V4 {
            document: doc,
            encrypt_metadata: true,
            crypt_filters,
            stream_filter: b"StdCF".to_vec(),
            string_filter: b"StdCF".to_vec(),
            owner_password: password,
            user_password: password,
            permissions: Permissions::all(),
        };
        EncryptionState::try_from(version)
            .map_err(|e| anyhow::anyhow!("could not set up encryption: {e}"))?
    };
    doc.encrypt(&state)
        .map_err(|e| anyhow::anyhow!("could not encrypt document: {e}"))?;
    Ok(())
}

/// Ensure the trailer carries a `/ID` (a two-element array of 16-byte file identifiers). Required
/// for standard-security encryption; most PDFs already have one, but a freshly built doc may not.
fn ensure_file_id(doc: &mut Document) {
    use std::hash::{Hash, Hasher};
    if doc.trailer.get(b"ID").is_ok() {
        return;
    }
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    std::time::SystemTime::now().hash(&mut hasher);
    doc.max_id.hash(&mut hasher);
    let a = hasher.finish();
    hasher.write_u64(a);
    let b = hasher.finish();
    let bytes: Vec<u8> = a.to_le_bytes().into_iter().chain(b.to_le_bytes()).collect();
    let id = Object::String(bytes, lopdf::StringFormat::Hexadecimal);
    doc.trailer.set("ID", Object::Array(vec![id.clone(), id]));
}

/// Open an encrypted PDF with `password`, returning the decrypted document (the `/Encrypt` entry is
/// removed, so saving it yields a plain PDF). Mirrors `unlock_pdf` (pikepdf open-with-password →
/// save). lopdf 0.43 handles RC4 and AES (incl. AES-256).
pub fn unlock(input: &std::path::Path, password: &str) -> anyhow::Result<Document> {
    Document::load_with_password(input, password).map_err(|e| {
        anyhow::anyhow!(
            "could not unlock {} (wrong password?): {e}",
            input.display()
        )
    })
}

/// The root `Pages` node's object id (via `trailer → Root → /Pages`).
fn root_pages_id(doc: &Document) -> anyhow::Result<ObjectId> {
    let root = doc.trailer.get(b"Root")?.as_reference()?;
    let catalog = doc.get_object(root)?.as_dict()?;
    Ok(catalog.get(b"Pages")?.as_reference()?)
}

/// Reorder (or subset) pages into `order` (1-based). Rebuilds the root `Pages` node's `Kids` in the
/// given sequence and reparents those pages to it; unlisted pages are dropped. Mirrors the pikepdf
/// build-a-doc-from-selected-pages behavior while keeping each page's own attributes intact.
pub fn reorder_pages(doc: &mut Document, order: &[i64]) -> anyhow::Result<()> {
    if order.is_empty() {
        anyhow::bail!("reorder needs at least one page");
    }
    let page_map = doc.get_pages(); // 1-based → ObjectId
    let mut oids = Vec::with_capacity(order.len());
    for &p in order {
        oids.push(
            page_map
                .get(&(p as u32))
                .copied()
                .ok_or_else(|| anyhow::anyhow!("page {p} not found"))?,
        );
    }
    let pages_id = root_pages_id(doc)?;
    // Reparent each selected page to the (flat) root Pages node so Parent stays consistent.
    for &oid in &oids {
        doc.get_object_mut(oid)?
            .as_dict_mut()?
            .set("Parent", pages_id);
    }
    let count = oids.len() as i64;
    let kids: Vec<Object> = oids.iter().map(|id| Object::Reference(*id)).collect();
    let pages_dict = doc.get_object_mut(pages_id)?.as_dict_mut()?;
    pages_dict.set("Kids", kids);
    pages_dict.set("Count", count);
    doc.prune_objects();
    Ok(())
}

/// Split into one document per `(label, pages)` spec (labels are the caller's concern). Each output
/// keeps the spec's pages by deleting the complement on a clone — preserving inherited page
/// attributes. Spec page lists are ascending (ranges), matching pikepdf's per-range extraction.
pub fn split(doc: &Document, specs: &[(String, Vec<i64>)]) -> anyhow::Result<Vec<Document>> {
    let total = page_count(doc) as i64;
    let mut out = Vec::with_capacity(specs.len());
    for (_label, pages) in specs {
        let keep: HashSet<i64> = pages.iter().copied().collect();
        let to_delete: Vec<u32> = (1..=total)
            .filter(|p| !keep.contains(p))
            .map(|p| p as u32)
            .collect();
        let mut clone = doc.clone();
        clone.delete_pages(&to_delete);
        out.push(clone);
    }
    Ok(out)
}

/// Merge whole documents in order into one. Nests each source's root `Pages` node under a new root
/// `Pages` (rather than flattening pages), so every page's inherited attributes (MediaBox,
/// Resources, …) survive. Object ids are renumbered into disjoint ranges before combining.
pub fn merge(docs: Vec<Document>) -> anyhow::Result<Document> {
    if docs.is_empty() {
        anyhow::bail!("merge needs at least one input");
    }
    let mut max_id: u32 = 1;
    let mut all_objects = std::collections::BTreeMap::new();
    let mut source_roots: Vec<ObjectId> = Vec::new();
    let mut total_count: i64 = 0;

    for mut doc in docs {
        doc.renumber_objects_with(max_id);
        max_id = doc.max_id + 1;
        source_roots.push(root_pages_id(&doc)?);
        total_count += doc.get_pages().len() as i64;
        all_objects.extend(doc.objects);
    }

    let new_pages_id: ObjectId = (max_id, 0);
    let new_catalog_id: ObjectId = (max_id + 1, 0);
    // Reparent each source's root Pages node under the new root.
    for &root in &source_roots {
        if let Some(Object::Dictionary(d)) = all_objects.get_mut(&root) {
            d.set("Parent", new_pages_id);
        }
    }
    let kids: Vec<Object> = source_roots
        .iter()
        .map(|id| Object::Reference(*id))
        .collect();

    let mut merged = Document::with_version("1.5");
    merged.objects = all_objects;
    merged.objects.insert(
        new_pages_id,
        Object::Dictionary(dictionary! {
            "Type" => "Pages",
            "Kids" => kids,
            "Count" => total_count,
        }),
    );
    merged.objects.insert(
        new_catalog_id,
        Object::Dictionary(dictionary! {
            "Type" => "Catalog",
            "Pages" => new_pages_id,
        }),
    );
    merged.max_id = max_id + 1;
    merged.trailer.set("Root", new_catalog_id);
    merged.prune_objects();
    Ok(merged)
}

#[cfg(test)]
pub(crate) mod test_support {
    use lopdf::{dictionary, Document, Object};

    /// Build a minimal valid PDF whose pages carry the given `TestIndex` tags (one page per entry),
    /// so tests can assert page identity/order after structural ops. Blank US-Letter pages.
    pub fn make_pdf_indexed(indices: &[i64]) -> Vec<u8> {
        let mut doc = Document::with_version("1.5");
        let pages_id = doc.new_object_id();
        let kids: Vec<Object> = indices
            .iter()
            .map(|&idx| {
                doc.add_object(dictionary! {
                    "Type" => "Page",
                    "Parent" => pages_id,
                    "MediaBox" => vec![0.into(), 0.into(), 612.into(), 792.into()],
                    "TestIndex" => idx,
                })
                .into()
            })
            .collect();
        doc.objects.insert(
            pages_id,
            Object::Dictionary(dictionary! {
                "Type" => "Pages",
                "Kids" => kids,
                "Count" => indices.len() as i64,
            }),
        );
        let catalog_id = doc.add_object(dictionary! {
            "Type" => "Catalog",
            "Pages" => pages_id,
        });
        doc.trailer.set("Root", catalog_id);
        let mut buf = Vec::new();
        doc.save_to(&mut buf).unwrap();
        buf
    }

    /// `pages`-page PDF tagged `TestIndex` 1..=pages.
    pub fn make_pdf(pages: u32) -> Vec<u8> {
        make_pdf_indexed(&(1..=i64::from(pages)).collect::<Vec<_>>())
    }

    /// Build a PDF with one text-bearing page per `lines` entry (a Helvetica `Tj` content stream),
    /// so text-extraction/find/inspect tests have real page text. ASCII lines only (no `(`/`)`).
    pub fn make_text_pdf(lines: &[&str]) -> Vec<u8> {
        use lopdf::Stream;
        let mut doc = Document::with_version("1.5");
        let pages_id = doc.new_object_id();
        let font_id = doc.add_object(dictionary! {
            "Type" => "Font",
            "Subtype" => "Type1",
            "BaseFont" => "Helvetica",
        });
        let kids: Vec<Object> = lines
            .iter()
            .map(|line| {
                let content = format!("BT /F1 24 Tf 72 700 Td ({line}) Tj ET");
                let content_id = doc.add_object(Stream::new(dictionary! {}, content.into_bytes()));
                doc.add_object(dictionary! {
                    "Type" => "Page",
                    "Parent" => pages_id,
                    "MediaBox" => vec![0.into(), 0.into(), 612.into(), 792.into()],
                    "Contents" => content_id,
                    "Resources" => dictionary! { "Font" => dictionary! { "F1" => font_id } },
                })
                .into()
            })
            .collect();
        doc.objects.insert(
            pages_id,
            Object::Dictionary(dictionary! {
                "Type" => "Pages",
                "Kids" => kids,
                "Count" => lines.len() as i64,
            }),
        );
        let catalog_id = doc.add_object(dictionary! {
            "Type" => "Catalog",
            "Pages" => pages_id,
        });
        doc.trailer.set("Root", catalog_id);
        let mut buf = Vec::new();
        doc.save_to(&mut buf).unwrap();
        buf
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::{make_pdf, make_pdf_indexed};
    use super::*;

    /// Each page's `TestIndex` tag, in current page order — reveals identity/order after an op.
    fn page_indices(doc: &Document) -> Vec<i64> {
        doc.get_pages()
            .values()
            .map(|&oid| {
                doc.get_object(oid)
                    .unwrap()
                    .as_dict()
                    .unwrap()
                    .get(b"TestIndex")
                    .unwrap()
                    .as_i64()
                    .unwrap()
            })
            .collect()
    }

    #[test]
    fn parse_pages_basic_and_ranges() {
        assert_eq!(parse_pages(None, 3, true).unwrap(), vec![1, 2, 3]);
        assert_eq!(parse_pages(Some(""), 3, true).unwrap(), vec![1, 2, 3]);
        assert_eq!(parse_pages(Some("all"), 3, true).unwrap(), vec![1, 2, 3]);
        assert_eq!(parse_pages(Some("2"), 3, true).unwrap(), vec![2]);
        assert_eq!(
            parse_pages(Some("1,3-5"), 5, true).unwrap(),
            vec![1, 3, 4, 5]
        );
        assert_eq!(parse_pages(Some("2-end"), 4, true).unwrap(), vec![2, 3, 4]);
        assert_eq!(parse_pages(Some("first-2"), 4, true).unwrap(), vec![1, 2]);
    }

    #[test]
    fn parse_pages_out_of_range_strictness() {
        assert!(parse_pages(Some("3,9"), 3, true).is_err());
        // non-strict keeps the padding for the caller to filter (reorder path)
        assert_eq!(
            parse_pages(Some("3,1,2,9"), 3, false).unwrap(),
            vec![3, 1, 2, 9]
        );
        assert!(parse_pages(Some("5-2"), 9, true).is_err()); // reversed range
        assert!(parse_pages(Some("2-x"), 3, true).is_err()); // bad token
    }

    #[test]
    fn parse_range_specs_one_per_part() {
        let specs = parse_page_range_specs("1-2,3", 3).unwrap();
        assert_eq!(specs.len(), 2);
        assert_eq!(specs[0], ("1-2".to_string(), vec![1, 2]));
        assert_eq!(specs[1], ("3".to_string(), vec![3]));
        assert!(parse_page_range_specs("  ", 3).is_err());
    }

    #[test]
    fn page_count_and_encryption() {
        let doc = Document::load_mem(&make_pdf(3)).unwrap();
        assert_eq!(page_count(&doc), 3);
        assert!(!is_encrypted(&doc)); // freshly built docs are unencrypted
    }

    #[test]
    fn remove_pages_keeps_the_rest() {
        let mut doc = Document::load_mem(&make_pdf(5)).unwrap();
        remove_pages(&mut doc, &[2, 4]).unwrap();
        assert_eq!(page_count(&doc), 3);
        assert_eq!(page_indices(&doc), vec![1, 3, 5]); // 2 and 4 gone, order preserved
    }

    #[test]
    fn reorder_reverse_and_subset() {
        let mut doc = Document::load_mem(&make_pdf(4)).unwrap();
        reorder_pages(&mut doc, &[4, 3, 2, 1]).unwrap();
        assert_eq!(page_indices(&doc), vec![4, 3, 2, 1]);

        let mut doc2 = Document::load_mem(&make_pdf(4)).unwrap();
        reorder_pages(&mut doc2, &[3, 1]).unwrap(); // subset drops 2 and 4
        assert_eq!(page_count(&doc2), 2);
        assert_eq!(page_indices(&doc2), vec![3, 1]);
    }

    #[test]
    fn split_produces_one_doc_per_spec() {
        let doc = Document::load_mem(&make_pdf(6)).unwrap();
        let specs = parse_page_range_specs("1-2,3-4,5-6", 6).unwrap();
        let parts = split(&doc, &specs).unwrap();
        assert_eq!(parts.len(), 3);
        assert_eq!(page_indices(&parts[0]), vec![1, 2]);
        assert_eq!(page_indices(&parts[1]), vec![3, 4]);
        assert_eq!(page_indices(&parts[2]), vec![5, 6]);
    }

    #[test]
    fn merge_concatenates_pages_in_order() {
        let a = Document::load_mem(&make_pdf_indexed(&[10, 11])).unwrap();
        let b = Document::load_mem(&make_pdf_indexed(&[20, 21, 22])).unwrap();
        let merged = merge(vec![a, b]).unwrap();
        assert_eq!(page_count(&merged), 5);
        assert_eq!(page_indices(&merged), vec![10, 11, 20, 21, 22]);
    }

    #[test]
    fn protect_then_unlock_round_trip() {
        let dir = std::env::temp_dir().join(format!("knaif_enc_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let plain = dir.join("plain.pdf");
        let protected = dir.join("protected.pdf");
        let unlocked = dir.join("unlocked.pdf");
        std::fs::write(&plain, make_pdf(3)).unwrap();

        // protect → encrypted on disk
        let mut doc = load(&plain).unwrap();
        protect(&mut doc, "secret").unwrap();
        save(&mut doc, &protected).unwrap();
        assert!(is_encrypted(&load(&protected).unwrap()));

        // wrong password fails, right password unlocks to a plain doc
        assert!(unlock(&protected, "nope").is_err());
        let mut back = unlock(&protected, "secret").unwrap();
        assert_eq!(page_count(&back), 3);
        save(&mut back, &unlocked).unwrap();
        assert!(!is_encrypted(&load(&unlocked).unwrap())); // /Encrypt gone after unlock

        // protecting an already-encrypted doc is refused
        let mut enc = load(&protected).unwrap();
        assert!(protect(&mut enc, "again").is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn merge_survives_disk_round_trip() {
        let a = Document::load_mem(&make_pdf_indexed(&[1, 2])).unwrap();
        let b = Document::load_mem(&make_pdf_indexed(&[3])).unwrap();
        let mut merged = merge(vec![a, b]).unwrap();
        let dir = std::env::temp_dir().join(format!("knaif_merge_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let out = dir.join("merged.pdf");
        save(&mut merged, &out).unwrap();
        let reloaded = load(&out).unwrap();
        assert_eq!(page_indices(&reloaded), vec![1, 2, 3]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn remove_all_pages_refused() {
        let mut doc = Document::load_mem(&make_pdf(2)).unwrap();
        assert!(remove_pages(&mut doc, &[1, 2]).is_err());
    }

    #[test]
    fn load_op_save_round_trip_on_disk() {
        let dir = std::env::temp_dir().join(format!("knaif_pdf_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let src = dir.join("in.pdf");
        let out = dir.join("out.pdf");
        std::fs::write(&src, make_pdf(4)).unwrap();

        let mut doc = load(&src).unwrap();
        assert_eq!(page_count(&doc), 4);
        remove_pages(&mut doc, &[1, 3]).unwrap();
        save(&mut doc, &out).unwrap();

        let reloaded = load(&out).unwrap();
        assert_eq!(page_count(&reloaded), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rotate_is_relative_and_normalized() {
        let mut doc = Document::load_mem(&make_pdf(2)).unwrap();
        rotate_pages(&mut doc, &[1], 90).unwrap();
        rotate_pages(&mut doc, &[1], 300).unwrap(); // 90 + 300 = 390 → 30
        let pages = doc.get_pages();
        let dict = doc.get_object(pages[&1]).unwrap().as_dict().unwrap();
        assert_eq!(dict.get(b"Rotate").unwrap().as_i64().unwrap(), 30);
        // untouched page has no explicit rotation
        let dict2 = doc.get_object(pages[&2]).unwrap().as_dict().unwrap();
        assert!(dict2.get(b"Rotate").is_err());
    }
}
