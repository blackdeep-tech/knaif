//! Dispatch a validated documents plan step to a native op — the `run documents` engine.
//!
//! Unlike ffmpeg (which renders an argv for a subprocess), documents ops run in-process. So the
//! dispatch itself does the work: [`preview`] performs the **safe read** tools immediately and, for
//! **destructive write** tools, derives the output path(s) without writing; [`commit`] performs the
//! write. The CLI confirms between the two. The ported structural + read + encryption tools are
//! wired; the overlay tools (watermark/add_page_numbers) and rasterizing tools (compress/convert/
//! ocr) are not yet here.

use std::path::{Component, Path, PathBuf};

use serde_json::Value;

use crate::{convert, overlay, pdf, text};

/// The result of previewing a documents step.
pub enum Preview {
    /// A safe read tool — already executed, carrying its result to print.
    Read(ReadResult),
    /// A destructive write tool — the output path(s) it *would* produce (nothing written yet).
    Write {
        outputs: Vec<PathBuf>,
        summary: String,
    },
}

/// Output of a read tool.
pub enum ReadResult {
    Inspection(text::Inspection),
    Text(Vec<text::PageText>),
    Matches(Vec<text::Match>),
}

/// Whether `tool` is one this native runtime implements.
pub fn is_supported(tool: &str) -> bool {
    matches!(
        tool,
        "inspect_document"
            | "extract_text"
            | "find_in_document"
            | "merge_pdfs"
            | "split_pdf"
            | "rotate_pages"
            | "remove_pages"
            | "reorder_pages"
            | "protect_pdf"
            | "unlock_pdf"
            | "watermark"
            | "add_page_numbers"
            | "convert_document"
            | "compress_pdf"
            | "ocr_document"
    )
}

/// Load the compress profile (falling back to `balanced`) + detect Ghostscript. Used by both the
/// compress preview (to name the method) and commit.
fn compress_setup(
    bundle: &Path,
    quality: &str,
) -> anyhow::Result<(crate::profile::CompressProfile, Option<PathBuf>)> {
    let data = crate::DocumentsData::load(bundle)?;
    let profile = data
        .compress
        .get(quality)
        .or_else(|| data.compress.get("balanced"))
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("no compress profile for {quality:?}"))?;
    Ok((profile, crate::detect::ExternalTools::detect().ghostscript))
}

/// Preview a step: run read tools now; for write tools, derive + sandbox-check the output path(s).
/// `bundle` is the skill bundle dir (only `compress_pdf` reads it — for the compress profiles).
pub fn preview(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    bundle: &Path,
) -> anyhow::Result<Preview> {
    match tool {
        "inspect_document" => {
            let input = input_path(args, base, sandbox)?;
            Ok(Preview::Read(ReadResult::Inspection(text::inspect(
                &input,
            )?)))
        }
        "extract_text" => {
            let input = input_path(args, base, sandbox)?;
            let records = text::extract_text(&input, str_arg(args, "pages").as_deref())?;
            Ok(Preview::Read(ReadResult::Text(records)))
        }
        "find_in_document" => {
            let input = input_path(args, base, sandbox)?;
            let query = str_arg(args, "query")
                .ok_or_else(|| anyhow::anyhow!("find_in_document requires 'query'"))?;
            let records = text::extract_text(&input, str_arg(args, "pages").as_deref())?;
            let matches = text::find(
                &records,
                &query,
                bool_arg(args, "regex").unwrap_or(false),
                bool_arg(args, "ignore_case").unwrap_or(true),
            )?;
            Ok(Preview::Read(ReadResult::Matches(matches)))
        }
        _ => {
            let (outputs, summary) = write_outputs(tool, args, base, sandbox, bundle)?;
            Ok(Preview::Write { outputs, summary })
        }
    }
}

/// Commit a destructive write step: perform the op and write the file(s). Returns the paths written.
/// `bundle` is the skill bundle dir (only `compress_pdf` reads it).
pub fn commit(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    bundle: &Path,
) -> anyhow::Result<Vec<PathBuf>> {
    match tool {
        "merge_pdfs" => {
            let inputs = input_paths(args, base, sandbox)?;
            let output = out_arg(args, base, sandbox, "output")?
                .ok_or_else(|| anyhow::anyhow!("merge_pdfs requires 'output'"))?;
            let docs = inputs
                .iter()
                .map(|p| pdf::load(p))
                .collect::<Result<_, _>>()?;
            let mut merged = pdf::merge(docs)?;
            write(&mut merged, &output)?;
            Ok(vec![output])
        }
        "rotate_pages" => {
            let input = input_path(args, base, sandbox)?;
            let mut doc = pdf::load(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let selected = pdf::parse_pages(str_arg(args, "pages").as_deref(), total, true)?;
            let degrees = int_arg(args, "degrees")
                .ok_or_else(|| anyhow::anyhow!("rotate_pages requires integer 'degrees'"))?;
            pdf::rotate_pages(&mut doc, &selected, degrees)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-rotated.pdf",
            );
            write(&mut doc, &out)?;
            Ok(vec![out])
        }
        "remove_pages" => {
            let input = input_path(args, base, sandbox)?;
            let mut doc = pdf::load(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let remove = pdf::parse_pages(Some(&require_str(args, "pages")?), total, true)?;
            pdf::remove_pages(&mut doc, &remove)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-removed.pdf",
            );
            write(&mut doc, &out)?;
            Ok(vec![out])
        }
        "reorder_pages" => {
            let input = input_path(args, base, sandbox)?;
            let mut doc = pdf::load(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let order = reorder_sequence(&require_str(args, "order")?, total)?;
            pdf::reorder_pages(&mut doc, &order)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-reordered.pdf",
            );
            write(&mut doc, &out)?;
            Ok(vec![out])
        }
        "split_pdf" => {
            let input = input_path(args, base, sandbox)?;
            let doc = pdf::load(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let specs = pdf::parse_page_range_specs(&require_str(args, "ranges")?, total)?;
            let outputs = split_outputs(&input, args, base, sandbox, &specs)?;
            let mut parts = pdf::split(&doc, &specs)?;
            for (part, out) in parts.iter_mut().zip(&outputs) {
                write(part, out)?;
            }
            Ok(outputs)
        }
        "protect_pdf" => {
            let input = input_path(args, base, sandbox)?;
            let mut doc = pdf::load(&input)?;
            pdf::protect(&mut doc, &require_str(args, "password")?)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-protected.pdf",
            );
            write(&mut doc, &out)?;
            Ok(vec![out])
        }
        "unlock_pdf" => {
            let input = input_path(args, base, sandbox)?;
            let mut doc = pdf::unlock(&input, &require_str(args, "password")?)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-unlocked.pdf",
            );
            write(&mut doc, &out)?;
            Ok(vec![out])
        }
        "watermark" => {
            let input = input_path(args, base, sandbox)?;
            let mut doc = pdf::load(&input)?;
            let position = str_arg(args, "position").unwrap_or_else(|| "center".into());
            let opacity = float_arg(args, "opacity").unwrap_or(0.35);
            if let Some(text) = str_arg(args, "text") {
                overlay::watermark_text(&mut doc, &text, &position, opacity, 42.0)?;
            } else if let Some(image) = str_arg(args, "image") {
                let image_path = resolve_path(&image, base);
                assert_in_sandbox(&image_path, sandbox)?;
                overlay::add_image_overlay(&mut doc, &image_path, &position, opacity)?;
            } else {
                anyhow::bail!("watermark requires 'text' or 'image'");
            }
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-watermarked.pdf",
            );
            write(&mut doc, &out)?;
            Ok(vec![out])
        }
        "add_page_numbers" => {
            let input = input_path(args, base, sandbox)?;
            let mut doc = pdf::load(&input)?;
            let position = str_arg(args, "position").unwrap_or_else(|| "bottom-center".into());
            let start_at = int_arg(args, "start_at").unwrap_or(1);
            overlay::add_page_numbers(&mut doc, start_at, &position, 12.0)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-numbered.pdf",
            );
            write(&mut doc, &out)?;
            Ok(vec![out])
        }
        "convert_document" => {
            let input = input_path(args, base, sandbox)?;
            let to_format = require_str(args, "to_format")?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                &convert::output_suffix(&to_format),
            );
            assert_in_sandbox(&out, sandbox)?;
            convert::convert(&input, &to_format, &out)?;
            Ok(vec![out])
        }
        "compress_pdf" => {
            let input = input_path(args, base, sandbox)?;
            let quality = str_arg(args, "compress_quality").unwrap_or_else(|| "balanced".into());
            let (profile, gs) = compress_setup(bundle, &quality)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-compressed.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            crate::compress::compress(&input, &out, &quality, &profile, gs.as_deref())?;
            Ok(vec![out])
        }
        "ocr_document" => {
            let input = input_path(args, base, sandbox)?;
            let language = str_arg(args, "language").unwrap_or_else(|| "eng".into());
            let out = derive_output(&input, out_arg(args, base, sandbox, "output")?, "-ocr.pdf");
            assert_in_sandbox(&out, sandbox)?;
            crate::ocr::ocr_document(&input, &out, &language)?;
            Ok(vec![out])
        }
        other => anyhow::bail!("documents tool {other:?} is not implemented natively yet"),
    }
}

/// Derive (and sandbox-check) the output path(s) a write tool would produce, without writing.
fn write_outputs(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    bundle: &Path,
) -> anyhow::Result<(Vec<PathBuf>, String)> {
    match tool {
        "merge_pdfs" => {
            let inputs = input_paths(args, base, sandbox)?;
            let output = out_arg(args, base, sandbox, "output")?
                .ok_or_else(|| anyhow::anyhow!("merge_pdfs requires 'output'"))?;
            Ok((vec![output], format!("merge {} file(s)", inputs.len())))
        }
        "rotate_pages" => {
            let input = input_path(args, base, sandbox)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-rotated.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], "rotate pages".into()))
        }
        "remove_pages" => {
            let input = input_path(args, base, sandbox)?;
            require_str(args, "pages")?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-removed.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], "remove pages".into()))
        }
        "reorder_pages" => {
            let input = input_path(args, base, sandbox)?;
            require_str(args, "order")?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-reordered.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], "reorder pages".into()))
        }
        "split_pdf" => {
            let input = input_path(args, base, sandbox)?;
            let doc = pdf::load(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let specs = pdf::parse_page_range_specs(&require_str(args, "ranges")?, total)?;
            let outputs = split_outputs(&input, args, base, sandbox, &specs)?;
            Ok((outputs, format!("split into {} file(s)", specs.len())))
        }
        "protect_pdf" => {
            let input = input_path(args, base, sandbox)?;
            require_str(args, "password")?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-protected.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], "password-protect".into()))
        }
        "unlock_pdf" => {
            let input = input_path(args, base, sandbox)?;
            require_str(args, "password")?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-unlocked.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], "unlock".into()))
        }
        "watermark" => {
            let input = input_path(args, base, sandbox)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-watermarked.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], "watermark".into()))
        }
        "add_page_numbers" => {
            let input = input_path(args, base, sandbox)?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-numbered.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], "add page numbers".into()))
        }
        "convert_document" => {
            let input = input_path(args, base, sandbox)?;
            let to_format = require_str(args, "to_format")?;
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                &convert::output_suffix(&to_format),
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], format!("convert to {to_format}")))
        }
        "compress_pdf" => {
            let input = input_path(args, base, sandbox)?;
            let quality = str_arg(args, "compress_quality").unwrap_or_else(|| "balanced".into());
            let (_profile, gs) = compress_setup(bundle, &quality)?;
            let method = crate::compress::compress_method_label(&quality, gs.is_some());
            let out = derive_output(
                &input,
                out_arg(args, base, sandbox, "output")?,
                "-compressed.pdf",
            );
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], format!("compress ({method})")))
        }
        "ocr_document" => {
            let input = input_path(args, base, sandbox)?;
            let out = derive_output(&input, out_arg(args, base, sandbox, "output")?, "-ocr.pdf");
            assert_in_sandbox(&out, sandbox)?;
            Ok((vec![out], "OCR → searchable PDF".into()))
        }
        other => anyhow::bail!("documents tool {other:?} is not implemented natively yet"),
    }
}

/// Reorder sequence: `reverse`/`reversed` → all pages descending; else parse (padding-tolerant),
/// filter to valid pages, require ≥1. Port of `ReorderPagesStep`.
fn reorder_sequence(raw: &str, total: i64) -> anyhow::Result<Vec<i64>> {
    if matches!(raw.trim().to_lowercase().as_str(), "reverse" | "reversed") {
        return Ok((1..=total).rev().collect());
    }
    let order: Vec<i64> = pdf::parse_pages(Some(raw), total, false)?
        .into_iter()
        .filter(|p| (1..=total).contains(p))
        .collect();
    if order.is_empty() {
        anyhow::bail!("Reorder sequence {raw:?} has no valid pages (1-{total})");
    }
    Ok(order)
}

/// Compute split output paths (port of `SplitPdfStep`): a single explicit `output` only when one
/// spec; else `<stem>-pages-<label>.pdf` in `output_dir` (default: the input's directory).
fn split_outputs(
    input: &Path,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    specs: &[(String, Vec<i64>)],
) -> anyhow::Result<Vec<PathBuf>> {
    let outputs = if specs.len() == 1 {
        if let Some(out) = out_arg(args, base, sandbox, "output")? {
            vec![out]
        } else {
            split_dir_outputs(input, args, base, specs)
        }
    } else {
        split_dir_outputs(input, args, base, specs)
    };
    for out in &outputs {
        assert_in_sandbox(out, sandbox)?;
    }
    Ok(outputs)
}

fn split_dir_outputs(
    input: &Path,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    specs: &[(String, Vec<i64>)],
) -> Vec<PathBuf> {
    let out_dir = str_arg(args, "output_dir")
        .map(|d| resolve_path(&d, base))
        .unwrap_or_else(|| input.parent().unwrap_or(Path::new(".")).to_path_buf());
    let stem = input
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("output");
    specs
        .iter()
        .map(|(label, _)| out_dir.join(format!("{stem}-pages-{}.pdf", label.replace('-', "_"))))
        .collect()
}

// ── arg + path helpers ───────────────────────────────────────────────────────────────────────

fn str_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<String> {
    args.get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|s| !s.is_empty())
}

fn bool_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<bool> {
    args.get(key).and_then(Value::as_bool)
}

fn int_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<i64> {
    match args.get(key) {
        Some(Value::Number(n)) => n.as_i64(),
        Some(Value::String(s)) => s.trim().parse().ok(),
        _ => None,
    }
}

fn float_arg(args: &serde_json::Map<String, Value>, key: &str) -> Option<f64> {
    match args.get(key) {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => s.trim().parse().ok(),
        _ => None,
    }
}

fn require_str(args: &serde_json::Map<String, Value>, key: &str) -> anyhow::Result<String> {
    str_arg(args, key).ok_or_else(|| anyhow::anyhow!("missing required arg {key:?}"))
}

/// Resolve + sandbox-check the `input` path arg.
fn input_path(
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
) -> anyhow::Result<PathBuf> {
    let raw = require_str(args, "input")?;
    let p = resolve_path(&raw, base);
    assert_in_sandbox(&p, sandbox)?;
    Ok(p)
}

/// Resolve + sandbox-check the `inputs` list arg.
fn input_paths(
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
) -> anyhow::Result<Vec<PathBuf>> {
    let items = args
        .get("inputs")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("'inputs' must be a list"))?;
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let raw = item
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("each of 'inputs' must be a string"))?;
        let p = resolve_path(raw, base);
        assert_in_sandbox(&p, sandbox)?;
        out.push(p);
    }
    Ok(out)
}

/// Resolve + sandbox-check an explicit output-path arg (e.g. `output`), if present.
fn out_arg(
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    key: &str,
) -> anyhow::Result<Option<PathBuf>> {
    match str_arg(args, key) {
        Some(raw) => {
            let p = resolve_path(&raw, base);
            assert_in_sandbox(&p, sandbox)?;
            Ok(Some(p))
        }
        None => Ok(None),
    }
}

/// `<parent>/<stem><suffix>` unless an explicit output is given. Port of `_output_path` for the
/// suffix-based outputs (`-rotated.pdf`, …).
fn derive_output(input: &Path, output: Option<PathBuf>, suffix: &str) -> PathBuf {
    if let Some(p) = output {
        return p;
    }
    let stem = input
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("output");
    let name = format!("{stem}{suffix}");
    match input.parent() {
        Some(p) => p.join(name),
        None => PathBuf::from(name),
    }
}

fn resolve_path(raw: &str, base: &Path) -> PathBuf {
    let p = Path::new(raw);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        base.join(p)
    }
}

fn write(doc: &mut lopdf::Document, path: &Path) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    pdf::save(doc, path)
}

/// Lexically absolutize + collapse `.`/`..` (no filesystem access — works on not-yet-created files).
fn lexical_abs(p: &Path) -> PathBuf {
    let base = if p.is_absolute() {
        p.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_default().join(p)
    };
    let mut out = PathBuf::new();
    for comp in base.components() {
        match comp {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            c => out.push(c.as_os_str()),
        }
    }
    out
}

/// Raise if `p` is outside `sandbox` (both lexically resolved). No-op when `sandbox` is `None`.
fn assert_in_sandbox(p: &Path, sandbox: Option<&Path>) -> anyhow::Result<()> {
    let Some(sandbox) = sandbox else {
        return Ok(());
    };
    if !lexical_abs(p).starts_with(lexical_abs(sandbox)) {
        anyhow::bail!("Path {:?} is outside the sandbox", p.display().to_string());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pdf::test_support::make_pdf;

    fn args(json: Value) -> serde_json::Map<String, Value> {
        json.as_object().unwrap().clone()
    }

    fn tmpdir() -> PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static COUNTER: AtomicU32 = AtomicU32::new(0);
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!("knaif_docrun_{}_{n}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// The real documents bundle (only `compress_pdf` reads it; other tools ignore it).
    fn docs_bundle() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/documents")
    }

    #[test]
    fn rotate_writes_derived_output() {
        let dir = tmpdir();
        std::fs::write(dir.join("a.pdf"), make_pdf(2)).unwrap();
        let a = args(serde_json::json!({"input": "a.pdf", "degrees": 90}));

        match preview("rotate_pages", &a, &dir, Some(&dir), &docs_bundle()).unwrap() {
            Preview::Write { outputs, .. } => {
                assert_eq!(outputs, vec![dir.join("a-rotated.pdf")]);
            }
            Preview::Read(_) => panic!("rotate is a write op"),
        }
        let written = commit("rotate_pages", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        assert_eq!(written, vec![dir.join("a-rotated.pdf")]);
        assert!(dir.join("a-rotated.pdf").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn merge_reads_inputs_and_writes_output() {
        let dir = tmpdir();
        std::fs::write(dir.join("a.pdf"), make_pdf(1)).unwrap();
        std::fs::write(dir.join("b.pdf"), make_pdf(2)).unwrap();
        let a = args(serde_json::json!({"inputs": ["a.pdf", "b.pdf"], "output": "out.pdf"}));
        commit("merge_pdfs", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        let merged = pdf::load(&dir.join("out.pdf")).unwrap();
        assert_eq!(pdf::page_count(&merged), 3);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn split_multi_range_names_files_by_label() {
        let dir = tmpdir();
        std::fs::write(dir.join("doc.pdf"), make_pdf(4)).unwrap();
        let a = args(serde_json::json!({"input": "doc.pdf", "ranges": "1-2,3-4"}));
        let outputs = commit("split_pdf", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        assert_eq!(
            outputs,
            vec![dir.join("doc-pages-1_2.pdf"), dir.join("doc-pages-3_4.pdf")]
        );
        assert!(outputs.iter().all(|p| p.exists()));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn reorder_reverse_keyword() {
        assert_eq!(reorder_sequence("reverse", 3).unwrap(), vec![3, 2, 1]);
        assert_eq!(reorder_sequence("3,1", 3).unwrap(), vec![3, 1]);
        assert!(reorder_sequence("9,9", 3).is_err()); // all out of range
    }

    #[test]
    fn inspect_is_a_read() {
        let dir = tmpdir();
        std::fs::write(dir.join("a.pdf"), make_pdf(3)).unwrap();
        let a = args(serde_json::json!({"input": "a.pdf"}));
        match preview("inspect_document", &a, &dir, Some(&dir), &docs_bundle()).unwrap() {
            Preview::Read(ReadResult::Inspection(i)) => assert_eq!(i.pages, 3),
            _ => panic!("inspect should be a read"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn sandbox_escape_rejected() {
        let dir = tmpdir();
        let a = args(serde_json::json!({"input": "../escape.pdf", "degrees": 90}));
        assert!(preview("rotate_pages", &a, &dir, Some(&dir), &docs_bundle()).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
