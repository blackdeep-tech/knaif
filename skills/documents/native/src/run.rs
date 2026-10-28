//! Dispatch a validated documents plan step to a native op — the `run documents` engine.
//!
//! Unlike ffmpeg (which renders an argv for a subprocess), documents ops run in-process. So the
//! dispatch itself does the work: [`preview`] performs the **safe read** tools immediately and, for
//! **destructive write** tools, derives the output path(s) without writing; [`commit`] performs the
//! write. The CLI confirms between the two. The ported structural + read + encryption tools are
//! wired; the overlay tools (watermark/add_page_numbers) and rasterizing tools (compress/convert/
//! ocr) are not yet here.

use std::path::{Path, PathBuf};

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
            | "split_pdf_every"
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
    preview_in(tool, args, base, sandbox, bundle, None)
}

/// [`preview`] in a run with a scratch folder (`work_dir`): a routed chain intermediate is read
/// from there, and a default output derived from it is named in the user's folder.
pub fn preview_in(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    bundle: &Path,
    work_dir: Option<&Path>,
) -> anyhow::Result<Preview> {
    let p = Paths {
        base,
        sandbox,
        work: work_dir,
    };
    match tool {
        "inspect_document" => {
            let input = input_path(args, &p)?;
            Ok(Preview::Read(ReadResult::Inspection(text::inspect(
                &input,
            )?)))
        }
        "extract_text" => {
            let input = input_path(args, &p)?;
            // The text is saved as <name>.txt beside its source, one file or many (owner,
            // 2026-10-23): `output` from core in a batch (`batch_output: txt`), else the
            // free default name. Python `ExtractTextStep`.
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, ".txt");
            p.check(&out)?;
            // A PDF that cannot be read stops here, as Python's dry run stops, so the rehearsal
            // before a real run refuses it first (`dry_run_depth_cases.json`).
            if input.exists() && is_pdf(&input) && pdf::is_encrypted(&pdf::load(&input)?) {
                anyhow::bail!(
                    "Cannot extract text from encrypted PDF without a password. Unlock it first."
                );
            }
            Ok(Preview::Write {
                outputs: vec![out],
                summary: "save the text".into(),
            })
        }
        "find_in_document" => {
            let input = input_path(args, &p)?;
            let query = str_arg(args, "query")
                .ok_or_else(|| anyhow::anyhow!("find_in_document requires 'query'"))?;
            let records = text::extract_text_with(
                &input,
                str_arg(args, "pages").as_deref(),
                bool_arg(args, "ocr").unwrap_or(false),
            )?;
            let matches = text::find(
                &records,
                &query,
                bool_arg(args, "regex").unwrap_or(false),
                bool_arg(args, "ignore_case").unwrap_or(true),
            )?;
            Ok(Preview::Read(ReadResult::Matches(matches)))
        }
        _ => {
            let (outputs, summary) = write_outputs(tool, args, &p, bundle)?;
            Ok(Preview::Write { outputs, summary })
        }
    }
}

fn is_pdf(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("pdf"))
}

/// A read tool's input when it is not there yet, which a dry run must not read: a URL input is
/// downloaded, and a chain intermediate made, only in a real run (Python's read steps return a
/// stub then). `None` for a write tool or an input that exists.
pub fn read_input_absent(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    work_dir: Option<&Path>,
) -> anyhow::Result<Option<PathBuf>> {
    // extract_text writes its text: its dry run only names the file, so it reads
    // nothing and is not here.
    if !matches!(tool, "inspect_document" | "find_in_document") {
        return Ok(None);
    }
    let p = Paths {
        base,
        sandbox,
        work: work_dir,
    };
    let input = input_path(args, &p)?;
    Ok((!input.exists()).then_some(input))
}

/// What a compress did to its file's size, for the result line (`30% smaller (…)`, or the
/// original kept). Python's `RunCompressStep` result carries the same three numbers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SizeReport {
    pub before: u64,
    /// The size the compressed result came to, even when the original was kept instead.
    pub after: u64,
    pub kept: bool,
}

static SIZE_REPORT: std::sync::Mutex<Option<SizeReport>> = std::sync::Mutex::new(None);

/// Take what the last `compress_pdf` commit recorded (see [`SizeReport`]).
pub fn take_size_report() -> Option<SizeReport> {
    SIZE_REPORT.lock().ok().and_then(|mut r| r.take())
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
    commit_in(tool, args, base, sandbox, bundle, None)
}

/// [`commit`] in a run with a scratch folder (`work_dir`). Each output is written to a staged path
/// in the scratch and moved into place only once it is good (`knaif_toolkit_fs`), so a failure
/// leaves the user's folder as it was; a routed intermediate is written in the scratch itself.
pub fn commit_in(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    bundle: &Path,
    work_dir: Option<&Path>,
) -> anyhow::Result<Vec<PathBuf>> {
    let p = Paths {
        base,
        sandbox,
        work: work_dir,
    };
    match tool {
        "extract_text" => {
            let input = input_path(args, &p)?;
            let output = derive_output(&p, &input, out_arg(args, &p, "output")?, ".txt");
            let records = text::extract_text_with(
                &input,
                str_arg(args, "pages").as_deref(),
                bool_arg(args, "ocr").unwrap_or(false),
            )?;
            let text = text::document_text(&records);
            write_with(&p, &output, |target| Ok(std::fs::write(target, &text)?))?;
            Ok(vec![output])
        }
        "merge_pdfs" => {
            let output = out_arg(args, &p, "output")?
                .ok_or_else(|| anyhow::anyhow!("merge_pdfs requires 'output'"))?;
            let inputs = merge_inputs(args, &p, &output)?;
            let docs = inputs
                .iter()
                .map(|p| pdf::load_unlocked(p))
                .collect::<Result<_, _>>()?;
            let mut merged = pdf::merge(docs)?;
            write(&p, &mut merged, &output)?;
            Ok(vec![output])
        }
        "rotate_pages" => {
            let input = input_path(args, &p)?;
            let mut doc = pdf::load_unlocked(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let selected = pdf::parse_pages(str_arg(args, "pages").as_deref(), total, true)?;
            let degrees = int_arg(args, "degrees")
                .ok_or_else(|| anyhow::anyhow!("rotate_pages requires integer 'degrees'"))?;
            pdf::rotate_pages(&mut doc, &selected, degrees)?;
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-rotated.pdf");
            write(&p, &mut doc, &out)?;
            Ok(vec![out])
        }
        "remove_pages" => {
            let input = input_path(args, &p)?;
            let mut doc = pdf::load_unlocked(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let remove = pdf::parse_pages(Some(&require_str(args, "pages")?), total, true)?;
            pdf::remove_pages(&mut doc, &remove)?;
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-removed.pdf");
            write(&p, &mut doc, &out)?;
            Ok(vec![out])
        }
        "reorder_pages" => {
            let input = input_path(args, &p)?;
            let mut doc = pdf::load_unlocked(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let order = reorder_sequence(&require_str(args, "order")?, total)?;
            pdf::reorder_pages(&mut doc, &order)?;
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-reordered.pdf");
            write(&p, &mut doc, &out)?;
            Ok(vec![out])
        }
        "split_pdf" | "split_pdf_every" => {
            let input = input_path(args, &p)?;
            let doc = pdf::load_unlocked(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let specs = split_specs(args, total)?;
            let outputs = split_outputs(&p, &input, args, &specs)?;
            let mut parts = pdf::split(&doc, &specs)?;
            for (part, out) in parts.iter_mut().zip(&outputs) {
                write(&p, part, out)?;
            }
            Ok(outputs)
        }
        "protect_pdf" => {
            let input = input_path(args, &p)?;
            let mut doc = pdf::load_unlocked(&input)?;
            pdf::protect(&mut doc, &require_str(args, "password")?)?;
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-protected.pdf");
            write(&p, &mut doc, &out)?;
            Ok(vec![out])
        }
        "unlock_pdf" => {
            let input = input_path(args, &p)?;
            let mut doc = pdf::unlock(&input, &require_str(args, "password")?)?;
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-unlocked.pdf");
            write(&p, &mut doc, &out)?;
            Ok(vec![out])
        }
        "watermark" => {
            let input = input_path(args, &p)?;
            let mut doc = pdf::load_unlocked(&input)?;
            let position = str_arg(args, "position").unwrap_or_else(|| "center".into());
            let opacity = float_arg(args, "opacity").unwrap_or(0.35);
            if let Some(text) = str_arg(args, "text") {
                overlay::watermark_text(&mut doc, &text, &position, opacity, 42.0)?;
            } else if let Some(image) = str_arg(args, "image") {
                let image_path = p.resolve(&image)?;
                overlay::add_image_overlay(&mut doc, &image_path, &position, opacity)?;
            } else {
                anyhow::bail!("watermark requires 'text' or 'image'");
            }
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-watermarked.pdf");
            write(&p, &mut doc, &out)?;
            Ok(vec![out])
        }
        "add_page_numbers" => {
            let input = input_path(args, &p)?;
            let mut doc = pdf::load_unlocked(&input)?;
            let position = str_arg(args, "position").unwrap_or_else(|| "bottom-center".into());
            let start_at = int_arg(args, "start_at").unwrap_or(1);
            overlay::add_page_numbers(&mut doc, start_at, &position, 12.0)?;
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-numbered.pdf");
            write(&p, &mut doc, &out)?;
            Ok(vec![out])
        }
        "convert_document" => {
            let input = input_path(args, &p)?;
            let to_format = require_str(args, "to_format")?;
            let out = derive_output(
                &p,
                &input,
                out_arg(args, &p, "output")?,
                &convert::output_suffix(&to_format),
            );
            p.check(&out)?;
            write_with(&p, &out, |target| {
                convert::convert(&input, &to_format, target)
            })?;
            Ok(vec![out])
        }
        "compress_pdf" => {
            let input = input_path(args, &p)?;
            let quality = str_arg(args, "compress_quality").unwrap_or_else(|| "balanced".into());
            let (profile, gs) = compress_setup(bundle, &quality)?;
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-compressed.pdf");
            p.check(&out)?;
            // Never a larger file: see `compress_no_larger`. What it did is the result line.
            write_with(&p, &out, |target| {
                let done = crate::compress::compress_no_larger(
                    &input,
                    target,
                    &quality,
                    &profile,
                    gs.as_deref(),
                )?;
                if let Ok(mut report) = SIZE_REPORT.lock() {
                    *report = Some(SizeReport {
                        before: done.before,
                        after: done.attempt,
                        kept: done.kept,
                    });
                }
                Ok(())
            })?;
            Ok(vec![out])
        }
        "ocr_document" => {
            let input = input_path(args, &p)?;
            let language = str_arg(args, "language").unwrap_or_else(|| "eng".into());
            let out = derive_output(&p, &input, out_arg(args, &p, "output")?, "-ocr.pdf");
            p.check(&out)?;
            write_with(&p, &out, |target| {
                crate::ocr::ocr_document(&input, target, &language).map(|_| ())
            })?;
            Ok(vec![out])
        }
        other => anyhow::bail!("documents tool {other:?} is not implemented natively yet"),
    }
}

/// Derive (and sandbox-check) the output path(s) a write tool would produce, without writing.
fn write_outputs(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    p: &Paths,
    bundle: &Path,
) -> anyhow::Result<(Vec<PathBuf>, String)> {
    match tool {
        "merge_pdfs" => {
            let output = out_arg(args, p, "output")?
                .ok_or_else(|| anyhow::anyhow!("merge_pdfs requires 'output'"))?;
            let inputs = merge_inputs(args, p, &output)?;
            Ok((vec![output], format!("merge {} file(s)", inputs.len())))
        }
        "rotate_pages" => {
            let input = input_path(args, p)?;
            // Opened as Python's dry run opens it (`dry_run_depth_cases.json`); a chain's input
            // not written yet is checked when run, as remove_pages does.
            if input.exists() {
                pdf::load_unlocked(&input)?;
            }
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-rotated.pdf");
            p.check(&out)?;
            Ok((vec![out], "rotate pages".into()))
        }
        "remove_pages" => {
            let input = input_path(args, p)?;
            // Check the pages as `run` and Python's dry run do: a preview must not promise an
            // output the real run refuses (out of range, or every page). A chain's input that
            // an earlier step has yet to write has no pages to count, so it is checked when run.
            let pages = require_str(args, "pages")?;
            if input.exists() {
                let doc = pdf::load_unlocked(&input)?;
                let total = pdf::page_count(&doc) as i64;
                pdf::check_removal(total, &pdf::parse_pages(Some(&pages), total, true)?)?;
            }
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-removed.pdf");
            p.check(&out)?;
            Ok((vec![out], "remove pages".into()))
        }
        "reorder_pages" => {
            let input = input_path(args, p)?;
            // Validate the order against the page count, as `run` and Python's dry run do: a
            // preview must not promise an output the real run refuses (documents_105). A chain's
            // input that an earlier step has yet to write has no pages to count, so it is checked
            // when run, as remove_pages does.
            let order = require_str(args, "order")?;
            if input.exists() {
                let doc = pdf::load_unlocked(&input)?;
                reorder_sequence(&order, pdf::page_count(&doc) as i64)?;
            }
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-reordered.pdf");
            p.check(&out)?;
            Ok((vec![out], "reorder pages".into()))
        }
        "split_pdf" | "split_pdf_every" => {
            let input = input_path(args, p)?;
            // A chain's input that an earlier step has yet to write has no pages to count, so
            // the split is checked when run, as remove_pages and reorder_pages are.
            if !input.exists() {
                return Ok((Vec::new(), "split".into()));
            }
            let doc = pdf::load_unlocked(&input)?;
            let total = pdf::page_count(&doc) as i64;
            let specs = split_specs(args, total)?;
            let outputs = split_outputs(p, &input, args, &specs)?;
            Ok((outputs, format!("split into {} file(s)", specs.len())))
        }
        "protect_pdf" => {
            let input = input_path(args, p)?;
            require_str(args, "password")?;
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-protected.pdf");
            p.check(&out)?;
            Ok((vec![out], "password-protect".into()))
        }
        "unlock_pdf" => {
            let input = input_path(args, p)?;
            require_str(args, "password")?;
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-unlocked.pdf");
            p.check(&out)?;
            Ok((vec![out], "unlock".into()))
        }
        "watermark" => {
            let input = input_path(args, p)?;
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-watermarked.pdf");
            p.check(&out)?;
            Ok((vec![out], "watermark".into()))
        }
        "add_page_numbers" => {
            let input = input_path(args, p)?;
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-numbered.pdf");
            p.check(&out)?;
            Ok((vec![out], "add page numbers".into()))
        }
        "convert_document" => {
            let input = input_path(args, p)?;
            let to_format = require_str(args, "to_format")?;
            let out = derive_output(
                p,
                &input,
                out_arg(args, p, "output")?,
                &convert::output_suffix(&to_format),
            );
            p.check(&out)?;
            Ok((vec![out], format!("convert to {to_format}")))
        }
        "compress_pdf" => {
            let input = input_path(args, p)?;
            // A broken PDF stops the dry run, as Python's inspection does; a protected one does
            // not (`dry_run_depth_cases.json`).
            if input.exists() {
                pdf::load(&input)?;
            }
            let quality = str_arg(args, "compress_quality").unwrap_or_else(|| "balanced".into());
            let (_profile, gs) = compress_setup(bundle, &quality)?;
            let method = crate::compress::compress_method_label(&quality, gs.is_some());
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-compressed.pdf");
            p.check(&out)?;
            Ok((vec![out], format!("compress ({method})")))
        }
        "ocr_document" => {
            let input = input_path(args, p)?;
            // As compress_pdf: a broken PDF stops the dry run, a protected one does not.
            if input.exists() && is_pdf(&input) {
                pdf::load(&input)?;
            }
            let out = derive_output(p, &input, out_arg(args, p, "output")?, "-ocr.pdf");
            p.check(&out)?;
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
/// The page files a split will write, when it writes more than one, before it runs: the set's
/// name and its files, relative to where paths resolve (`base`). Their names are the step's own
/// (`<stem>-pages-<range>.pdf` next to the input, a free name when one is taken, or the `%s` set
/// name core gave it). An input that cannot be read is no set: the step itself reports it. Port
/// of Python `DocumentsSkill.planned_outputs`.
pub fn planned_set(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
) -> Option<(String, Vec<String>)> {
    if !matches!(tool, "split_pdf" | "split_pdf_every") {
        return None;
    }
    let p = Paths {
        base,
        sandbox,
        work: None,
    };
    let input = input_path(args, &p).ok()?;
    let doc = pdf::load_unlocked(&input).ok()?;
    let specs = split_specs(args, pdf::page_count(&doc) as i64).ok()?;
    if specs.len() < 2 {
        return None;
    }
    let source = str_arg(args, "input")
        .unwrap_or_default()
        .replace('\\', "/");
    let folder = source
        .rsplit_once('/')
        .map(|(f, _)| format!("{f}/"))
        .unwrap_or_default();
    let label = |l: &str| l.replace('-', "_");
    if let Some(pattern) = str_arg(args, "output").filter(|o| o.contains("%s")) {
        let files = specs
            .iter()
            .map(|(l, _)| pattern.replacen("%s", &label(l), 1))
            .collect();
        return Some((pattern, files));
    }
    let stem = input.file_stem()?.to_string_lossy().into_owned();
    let name = format!("{folder}{stem}-pages-%s.pdf");
    let root = std::fs::canonicalize(base).unwrap_or_else(|_| base.to_path_buf());
    let parent = input.parent().unwrap_or(Path::new("."));
    let files = specs
        .iter()
        .map(|(l, _)| {
            let free = free_name(&parent.join(format!("{stem}-pages-{}.pdf", label(l))));
            let real = std::fs::canonicalize(free.parent().unwrap_or(Path::new(".")))
                .map(|d| d.join(free.file_name().unwrap_or_default()))
                .unwrap_or_else(|_| free.clone());
            match real.strip_prefix(&root) {
                Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
                Err(_) => free.to_string_lossy().into_owned(),
            }
        })
        .collect();
    Some((name, files))
}

/// The page ranges a split writes: `ranges` as given, or one every `every` pages
/// (`split_pdf_every`). Port of Python `split_specs`.
fn split_specs(
    args: &serde_json::Map<String, Value>,
    total: i64,
) -> anyhow::Result<Vec<(String, Vec<i64>)>> {
    if let Some(n) = int_arg(args, "every").filter(|n| *n != 0) {
        if n < 1 {
            anyhow::bail!("`every` must be at least 1 page.");
        }
        let raw: Vec<String> = (1..=total)
            .step_by(n as usize)
            .map(|a| {
                let b = (a + n - 1).min(total);
                if b > a {
                    format!("{a}-{b}")
                } else {
                    format!("{a}")
                }
            })
            .collect();
        return pdf::parse_page_range_specs(&raw.join(","), total);
    }
    pdf::parse_page_range_specs(&require_str(args, "ranges")?, total)
}

fn split_outputs(
    p: &Paths,
    input: &Path,
    args: &serde_json::Map<String, Value>,
    specs: &[(String, Vec<i64>)],
) -> anyhow::Result<Vec<PathBuf>> {
    // An output with `%s` names every piece (the set name core gives a split a later step reads):
    // `part_%s.pdf` -> `part_1.pdf`, `part_2_3.pdf`.
    if let Some(pattern) = str_arg(args, "output").filter(|o| o.contains("%s")) {
        let outputs = specs
            .iter()
            .map(|(label, _)| p.resolve(&pattern.replacen("%s", &label.replace('-', "_"), 1)))
            .collect::<anyhow::Result<Vec<_>>>()?;
        return Ok(outputs);
    }
    let outputs = if specs.len() == 1 {
        if let Some(out) = out_arg(args, p, "output")? {
            vec![out]
        } else {
            split_dir_outputs(p, input, args, specs)
        }
    } else {
        split_dir_outputs(p, input, args, specs)
    };
    for out in &outputs {
        p.check(out)?;
    }
    Ok(outputs)
}

fn split_dir_outputs(
    p: &Paths,
    input: &Path,
    args: &serde_json::Map<String, Value>,
    specs: &[(String, Vec<i64>)],
) -> Vec<PathBuf> {
    // Default: beside the input in the user's folder (a routed input counts as where it would
    // have been, so no split part lands in the scratch).
    let out_dir = str_arg(args, "output_dir")
        .map(|d| resolve_path(&d, p.base))
        .unwrap_or_else(|| {
            p.user_side(input)
                .parent()
                .unwrap_or(Path::new("."))
                .to_path_buf()
        });
    let stem = input
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("output");
    specs
        .iter()
        .map(|(label, _)| {
            free_name(&out_dir.join(format!("{stem}-pages-{}.pdf", label.replace('-', "_"))))
        })
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

/// Where a step's paths resolve and what they may touch: relative paths against `base`, inside
/// `sandbox` or this run's scratch (`work`, a routed chain intermediate).
#[derive(Clone, Copy)]
struct Paths<'a> {
    base: &'a Path,
    sandbox: Option<&'a Path>,
    work: Option<&'a Path>,
}

impl Paths<'_> {
    /// Raise if `path` is outside the sandbox, unless it is in this run's scratch.
    fn check(&self, path: &Path) -> anyhow::Result<()> {
        if knaif_toolkit_fs::within(path, self.work) {
            return Ok(());
        }
        assert_in_sandbox(path, self.sandbox)
    }

    fn resolve(&self, raw: &str) -> anyhow::Result<PathBuf> {
        let path = resolve_path(raw, self.base);
        self.check(&path)?;
        Ok(path)
    }

    /// `path`, or where it would sit in the user's folder if it is a routed intermediate.
    fn user_side(&self, path: &Path) -> PathBuf {
        knaif_toolkit_fs::user_side_path(path, self.work, Some(self.base))
    }
}

/// Resolve + sandbox-check the `input` path arg.
fn input_path(args: &serde_json::Map<String, Value>, p: &Paths) -> anyhow::Result<PathBuf> {
    p.resolve(&require_str(args, "input")?)
}

/// The `input` a step reads, resolved and sandbox-checked as its run would: for a caller that
/// reads it itself (`run --stdout`'s extract_text in apps/cli).
pub fn input_path_in(
    args: &serde_json::Map<String, Value>,
    base: &Path,
    sandbox: Option<&Path>,
    work_dir: Option<&Path>,
) -> anyhow::Result<PathBuf> {
    input_path(
        args,
        &Paths {
            base,
            sandbox,
            work: work_dir,
        },
    )
}

/// Resolve + sandbox-check the `inputs` list arg.
fn input_paths(args: &serde_json::Map<String, Value>, p: &Paths) -> anyhow::Result<Vec<PathBuf>> {
    let items = args
        .get("inputs")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("'inputs' must be a list"))?;
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let raw = item
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("each of 'inputs' must be a string"))?;
        out.push(p.resolve(raw)?);
    }
    Ok(out)
}

/// The PDFs a merge reads, in order: a named file as given, a glob or a folder expanded by the fs
/// toolkit's `resolve_inputs` (natural order, PDFs only), like `concat_video`. A pattern never
/// matches the output itself: "merge all the PDFs here" run twice would read its own earlier
/// result. Port of Python `_merge_inputs`.
fn merge_inputs(
    args: &serde_json::Map<String, Value>,
    p: &Paths,
    output: &Path,
) -> anyhow::Result<Vec<PathBuf>> {
    let raws: Vec<String> = args
        .get("inputs")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let mut out = Vec::new();
    for (raw, path) in raws.iter().zip(input_paths(args, p)?) {
        if path.is_dir() || raw.chars().any(|c| matches!(c, '*' | '?' | '[')) {
            out.extend(knaif_toolkit_fs::resolve_inputs(
                &[path.to_string_lossy().into_owned()],
                p.base,
                p.sandbox,
                p.work,
                &["pdf".to_string()],
                false,
                &[output.to_string_lossy().into_owned()],
            )?);
        } else {
            out.push(path);
        }
    }
    Ok(out)
}

/// Resolve + sandbox-check an explicit output-path arg (e.g. `output`), if present.
fn out_arg(
    args: &serde_json::Map<String, Value>,
    p: &Paths,
    key: &str,
) -> anyhow::Result<Option<PathBuf>> {
    str_arg(args, key).map(|raw| p.resolve(&raw)).transpose()
}

/// `<parent>/<stem><suffix>` unless an explicit output is given. Port of `_output_path` for the
/// suffix-based outputs (`-rotated.pdf`, …). An explicit output is a request and is honoured even
/// over an existing file; a derived default that already exists moves to [`free_name`]. The
/// default sits beside the input in the user's folder, a routed input counting as where it would
/// have been, so nothing derived lands in the scratch.
fn derive_output(p: &Paths, input: &Path, output: Option<PathBuf>, suffix: &str) -> PathBuf {
    if let Some(out) = output {
        return out;
    }
    let anchor = p.user_side(input);
    let stem = anchor
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("output");
    let name = format!("{stem}{suffix}");
    free_name(&match anchor.parent() {
        Some(parent) => parent.join(name),
        None => PathBuf::from(name),
    })
}

/// `path` if nothing is there, else `<stem>-1<ext>`, `-2`, … — the first free one (the fs
/// toolkit's rule, as Python's `_free_name`). For a name the plan did not choose: a derived default
/// that already exists is someone's file (`convert notes.txt to markdown` wrote over an existing
/// `notes.md`, 2026-09-24 L4). Preview and commit both derive through here, so they agree.
fn free_name(path: &Path) -> PathBuf {
    let rule = knaif_toolkit_fs::FreeName {
        separator: "-",
        first_number: 1,
        keep_requested: true,
        ..Default::default()
    };
    knaif_toolkit_fs::free_output_path(path, &[], &rule)
}

fn resolve_path(raw: &str, base: &Path) -> PathBuf {
    let p = Path::new(raw);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        base.join(p)
    }
}

fn write(p: &Paths, doc: &mut lopdf::Document, path: &Path) -> anyhow::Result<()> {
    write_with(p, path, |target| pdf::save(doc, target))
}

/// Write `out` through `produce`, so only a good file reaches the user's folder: with a scratch,
/// `produce` writes a staged path and `commit_output` checks it (it exists and is not empty) and
/// moves it into place. An output already in the scratch (a routed intermediate), or any output
/// in a run without one, is written where it is, its folder made first.
fn write_with(
    p: &Paths,
    out: &Path,
    produce: impl FnOnce(&Path) -> anyhow::Result<()>,
) -> anyhow::Result<()> {
    match p.work {
        Some(work) if !knaif_toolkit_fs::within(out, Some(work)) => {
            let staged = knaif_toolkit_fs::stage_output(work, out)?;
            produce(&staged)?;
            knaif_toolkit_fs::commit_output(&staged, out, p.sandbox, work)?;
            Ok(())
        }
        _ => {
            knaif_toolkit_fs::ensure_parent(out, p.sandbox, p.work)?;
            produce(out)
        }
    }
}

/// Raise if `p` is outside `sandbox`. No-op when `sandbox` is `None`. Delegates to the
/// shared `knaif-skill-api` primitive (filesystem-real: existing ancestors are
/// canonicalized, following symlinks/junctions, not just lexically normalized) so this
/// applies the identical containment rule as ffmpeg and core. A purely lexical check here
/// previously let a Windows junction placed inside the sandbox read a file outside it — see
/// docs/audits/2026-09-07-core-principles-and-rtx5080.md, F4.
fn assert_in_sandbox(p: &Path, sandbox: Option<&Path>) -> anyhow::Result<()> {
    let Some(sandbox) = sandbox else {
        return Ok(());
    };
    knaif_skill_api::sandbox::assert_in_sandbox(p, sandbox)
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

    // A preview must not promise an output the run refuses (as documents_105 found for
    // reorder_pages): remove_pages checks its page list in the preview, as Python does.
    #[test]
    fn remove_pages_preview_refuses_what_the_run_refuses() {
        let dir = tmpdir();
        std::fs::write(dir.join("a.pdf"), make_pdf(3)).unwrap();
        for (pages, why) in [("7", "out of range"), ("1-3", "every page")] {
            let a = args(serde_json::json!({"input": "a.pdf", "pages": pages}));
            let err = preview("remove_pages", &a, &dir, Some(&dir), &docs_bundle())
                .err()
                .unwrap_or_else(|| panic!("preview accepted pages {pages:?}"));
            assert!(err.to_string().contains(why), "{pages:?}: {err}");
            assert!(commit("remove_pages", &a, &dir, Some(&dir), &docs_bundle()).is_err());
        }
        assert!(!dir.join("a-removed.pdf").exists());
        // A repeated page is one page: "1,1,2" leaves page 3, in both.
        let a = args(serde_json::json!({"input": "a.pdf", "pages": "1,1,2"}));
        assert!(preview("remove_pages", &a, &dir, Some(&dir), &docs_bundle()).is_ok());
        let written = commit("remove_pages", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        let doc = crate::pdf::load_unlocked(&written[0]).unwrap();
        assert_eq!(crate::pdf::page_count(&doc), 1);
        // A chain's later step previews against a file an earlier step will create: nothing to
        // count yet, so the preview still names its output, as it did before the check.
        let a = args(serde_json::json!({"input": "merged.pdf", "pages": "2"}));
        assert!(preview("remove_pages", &a, &dir, Some(&dir), &docs_bundle()).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // documents_105 made reorder_pages check its order in the preview; like remove_pages, a
    // chain's input that an earlier step has yet to write is checked when the step runs.
    #[test]
    fn reorder_pages_preview_waits_for_a_file_an_earlier_step_creates() {
        let dir = tmpdir();
        std::fs::write(dir.join("a.pdf"), make_pdf(3)).unwrap();
        let a = args(serde_json::json!({"input": "a.pdf", "order": "original"}));
        assert!(preview("reorder_pages", &a, &dir, Some(&dir), &docs_bundle()).is_err());
        let a = args(serde_json::json!({"input": "merged.pdf", "order": "reverse"}));
        match preview("reorder_pages", &a, &dir, Some(&dir), &docs_bundle()).unwrap() {
            Preview::Write { outputs, .. } => {
                assert_eq!(outputs, vec![dir.join("merged-reordered.pdf")]);
            }
            Preview::Read(_) => panic!("reorder is a write op"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Like remove_pages and reorder_pages: a split of a file an earlier step has yet to write
    // ("merge a.pdf and b.pdf, then keep pages 1-2", documents_hard2_005) has no pages to count
    // in the preview, so it is checked when it runs. Python: test_dry_run_chain.py.
    #[test]
    fn split_preview_waits_for_a_file_an_earlier_step_creates() {
        let dir = tmpdir();
        for (tool, extra) in [
            ("split_pdf", serde_json::json!({"ranges": "1-2"})),
            ("split_pdf_every", serde_json::json!({"every": 1})),
        ] {
            let mut a = args(serde_json::json!({"input": "merged.pdf"}));
            a.extend(extra.as_object().unwrap().clone());
            match preview(tool, &a, &dir, Some(&dir), &docs_bundle()).unwrap() {
                Preview::Write { outputs, .. } => assert!(outputs.is_empty(), "{tool}"),
                Preview::Read(_) => panic!("{tool} is a write op"),
            }
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Port of skills/documents/python/tests/test_output_never_overwrites.py. The 2026-09-24 L4 run
    // found both runtimes writing a default output name over a file that already existed.
    #[test]
    fn a_default_output_that_exists_moves_to_the_next_free_name() {
        let dir = tmpdir();
        std::fs::write(
            dir.join("notes.txt"),
            "Invoice Alpha
",
        )
        .unwrap();
        std::fs::write(
            dir.join("notes.md"),
            "the user's own notes
",
        )
        .unwrap();
        std::fs::write(
            dir.join("notes-1.md"),
            "also taken
",
        )
        .unwrap();
        let a = args(serde_json::json!({"input": "notes.txt", "to_format": "md"}));

        match preview("convert_document", &a, &dir, Some(&dir), &docs_bundle()).unwrap() {
            Preview::Write { outputs, .. } => assert_eq!(outputs, vec![dir.join("notes-2.md")]),
            Preview::Read(_) => panic!("convert is a write op"),
        }
        let written = commit("convert_document", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        assert_eq!(written, vec![dir.join("notes-2.md")]);
        assert_eq!(
            std::fs::read_to_string(dir.join("notes.md")).unwrap(),
            "the user's own notes
"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_suffixed_default_moves_and_a_free_one_stays() {
        let dir = tmpdir();
        std::fs::write(dir.join("a.pdf"), make_pdf(2)).unwrap();
        std::fs::write(dir.join("a-rotated.pdf"), b"someone else's file").unwrap();
        let a = args(serde_json::json!({"input": "a.pdf", "degrees": 90}));
        let written = commit("rotate_pages", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        assert_eq!(written, vec![dir.join("a-rotated-1.pdf")]);
        assert_eq!(
            std::fs::read(dir.join("a-rotated.pdf")).unwrap(),
            b"someone else's file"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_explicit_output_is_honoured_even_when_it_exists() {
        let dir = tmpdir();
        std::fs::write(
            dir.join("notes.txt"),
            "fresh
",
        )
        .unwrap();
        std::fs::write(
            dir.join("out.md"),
            "old
",
        )
        .unwrap();
        let a =
            args(serde_json::json!({"input": "notes.txt", "to_format": "md", "output": "out.md"}));
        let written = commit("convert_document", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        assert_eq!(written, vec![dir.join("out.md")]);
        assert!(std::fs::read_to_string(dir.join("out.md"))
            .unwrap()
            .contains("fresh"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn split_defaults_move_too() {
        let dir = tmpdir();
        std::fs::write(dir.join("doc.pdf"), make_pdf(4)).unwrap();
        std::fs::write(dir.join("doc-pages-1_2.pdf"), b"taken").unwrap();
        let a = args(serde_json::json!({"input": "doc.pdf", "ranges": "1-2,3-4"}));
        let outputs = commit("split_pdf", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        assert_eq!(
            outputs,
            vec![
                dir.join("doc-pages-1_2-1.pdf"),
                dir.join("doc-pages-3_4.pdf")
            ]
        );
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
    fn a_merge_expands_a_pattern_or_folder_in_natural_order() {
        // "merge all the PDFs here into one", a second time: PDFs only, part1/part2/part10, and
        // the first run's all.pdf is not read back (Python `_merge_inputs`).
        let dir = tmpdir();
        std::fs::create_dir_all(dir.join("pdfs")).unwrap();
        for name in [
            "part10.pdf",
            "part2.pdf",
            "part1.pdf",
            "all.pdf",
            "pdfs/b.pdf",
            "pdfs/a.pdf",
        ] {
            std::fs::write(dir.join(name), make_pdf(1)).unwrap();
        }
        std::fs::write(dir.join("notes.txt"), b"x").unwrap();
        let p = Paths {
            base: &dir,
            sandbox: Some(&dir),
            work: None,
        };
        let names = |a: serde_json::Value| -> Vec<String> {
            let a = args(a);
            let output = out_arg(&a, &p, "output").unwrap().unwrap();
            merge_inputs(&a, &p, &output)
                .unwrap()
                .iter()
                .map(|f| f.file_name().unwrap().to_string_lossy().into_owned())
                .collect()
        };
        assert_eq!(
            names(serde_json::json!({"inputs": ["*"], "output": "all.pdf"})),
            ["part1.pdf", "part2.pdf", "part10.pdf"]
        );
        assert_eq!(
            names(serde_json::json!({"inputs": ["cover.pdf", "pdfs/"], "output": "all.pdf"})),
            ["cover.pdf", "a.pdf", "b.pdf"],
            "a named file stays as given, a folder expands"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn extract_text_with_an_output_saves_the_text() {
        // A batch saves each file's text (`batch_output: txt`): core gives the lifted step its
        // `output`, the step writes the pages' text one blank line apart, as Python does.
        let dir = tmpdir();
        std::fs::write(
            dir.join("a.pdf"),
            crate::pdf::test_support::make_text_pdf(&["Alpha", "Beta"]),
        )
        .unwrap();
        let a = args(serde_json::json!({"input": "a.pdf", "output": "a.txt"}));
        match preview("extract_text", &a, &dir, Some(&dir), &docs_bundle()).unwrap() {
            Preview::Write { outputs, .. } => assert_eq!(outputs, vec![dir.join("a.txt")]),
            _ => panic!("extract_text with an output is a write"),
        }
        let written = commit("extract_text", &a, &dir, Some(&dir), &docs_bundle()).unwrap();
        assert_eq!(written, vec![dir.join("a.txt")]);
        assert_eq!(
            std::fs::read_to_string(dir.join("a.txt")).unwrap(),
            "Alpha\n\nBeta\n"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_read_input_not_there_yet_is_named() {
        // A dry run skips it (Python's read steps return a stub): a URL input is downloaded,
        // and a chain intermediate made, only in a real run.
        let dir = tmpdir();
        std::fs::write(dir.join("a.pdf"), make_pdf(1)).unwrap();
        let here = args(serde_json::json!({"input": "a.pdf"}));
        let gone = args(serde_json::json!({"input": "b.pdf", "query": "x"}));
        let absent = |tool: &str, a| read_input_absent(tool, a, &dir, Some(&dir), None).unwrap();
        assert_eq!(absent("inspect_document", &here), None);
        for tool in ["inspect_document", "find_in_document"] {
            assert_eq!(absent(tool, &gone), Some(dir.join("b.pdf")), "{tool}");
        }
        // extract_text writes its text: its dry run names the file, reads nothing.
        assert_eq!(absent("extract_text", &gone), None);
        assert_eq!(absent("rotate_pages", &gone), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn sandbox_escape_rejected() {
        let dir = tmpdir();
        let a = args(serde_json::json!({"input": "../escape.pdf", "degrees": 90}));
        assert!(preview("rotate_pages", &a, &dir, Some(&dir), &docs_bundle()).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn sandbox_junction_escape_rejected() {
        // The audit's literal F4 repro: `run documents "Inspect junction/example.pdf"
        // --sandbox <sandbox> --dry-run` read a file outside the sandbox through a junction
        // placed inside it, because the old `assert_in_sandbox` was lexical-only. It must
        // now be rejected the same way `sandbox_escape_rejected` above already is.
        let dir = tmpdir();
        let sandbox = dir.join("sandbox");
        let outside = dir.join("outside");
        std::fs::create_dir_all(&sandbox).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(outside.join("example.pdf"), make_pdf(1)).unwrap();

        let link = sandbox.join("junction");
        let status = std::process::Command::new("cmd")
            .args([
                "/C",
                "mklink",
                "/J",
                &link.display().to_string(),
                &outside.display().to_string(),
            ])
            .status()
            .expect("mklink must run on Windows");
        assert!(
            status.success(),
            "junction creation must succeed (no admin needed)"
        );

        let a = args(serde_json::json!({"input": "junction/example.pdf"}));
        let result = preview(
            "inspect_document",
            &a,
            &sandbox,
            Some(&sandbox),
            &docs_bundle(),
        );
        let err = match result {
            Ok(_) => panic!("junction escape must be rejected"),
            Err(e) => e,
        };
        assert!(err.to_string().contains("outside the sandbox"), "{err}");

        let _ = std::fs::remove_dir_all(&dir);
    }

    // ── run scratch ─────────────────────────────────────────────────────────────────────────
    // Mirrors skills/documents/python/tests/test_scratch.py: a routed intermediate is read and
    // written inside this run's scratch, a default output derived from it lands in the user's
    // folder, and every output is staged and moved into place only once it is good.

    fn files(dir: &Path) -> Vec<String> {
        let mut out: Vec<String> = std::fs::read_dir(dir)
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.path().is_file())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        out.sort();
        out
    }

    #[test]
    fn a_routed_chain_leaves_only_its_deliverable() {
        let dir = tmpdir();
        let work = tmpdir();
        std::fs::write(dir.join("sample.pdf"), make_pdf(3)).unwrap();
        let trimmed = work.join("sample_trimmed.pdf");
        let step1 = args(serde_json::json!({
            "input": "sample.pdf", "pages": "-1", "output": trimmed.to_string_lossy()
        }));
        let w1 = commit_in(
            "remove_pages",
            &step1,
            &dir,
            Some(&dir),
            &docs_bundle(),
            Some(&work),
        )
        .unwrap();
        assert_eq!(w1, vec![trimmed.clone()]);
        let step2 = args(serde_json::json!({"input": trimmed.to_string_lossy()}));
        let w2 = commit_in(
            "add_page_numbers",
            &step2,
            &dir,
            Some(&dir),
            &docs_bundle(),
            Some(&work),
        )
        .unwrap();
        assert_eq!(w2, vec![dir.join("sample_trimmed-numbered.pdf")]);
        assert_eq!(
            files(&dir),
            vec!["sample.pdf", "sample_trimmed-numbered.pdf"]
        );
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&work);
    }

    #[test]
    fn a_preview_of_a_routed_input_names_the_users_folder() {
        let dir = tmpdir();
        let work = tmpdir();
        std::fs::write(work.join("sample_rotated.pdf"), make_pdf(2)).unwrap();
        let a = args(serde_json::json!({
            "input": work.join("sample_rotated.pdf").to_string_lossy()
        }));
        match preview_in(
            "compress_pdf",
            &a,
            &dir,
            Some(&dir),
            &docs_bundle(),
            Some(&work),
        )
        .unwrap()
        {
            Preview::Write { outputs, .. } => {
                assert_eq!(outputs, vec![dir.join("sample_rotated-compressed.pdf")])
            }
            Preview::Read(_) => panic!("compress is a write"),
        }
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&work);
    }

    #[test]
    fn an_output_is_staged_then_moved_into_place() {
        let dir = tmpdir();
        let work = tmpdir();
        std::fs::write(dir.join("a.pdf"), make_pdf(2)).unwrap();
        let a = args(serde_json::json!({"input": "a.pdf", "degrees": 90}));
        let written = commit_in(
            "rotate_pages",
            &a,
            &dir,
            Some(&dir),
            &docs_bundle(),
            Some(&work),
        )
        .unwrap();
        assert_eq!(written, vec![dir.join("a-rotated.pdf")]);
        assert!(dir.join("a-rotated.pdf").is_file());
        // The staged copy was moved, not left behind in the scratch.
        assert!(!work.join("stage").join("0").join("a-rotated.pdf").exists());
        assert!(
            work.join("stage").join("0").is_dir(),
            "the write went through the stage"
        );
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&work);
    }

    #[test]
    fn a_scratch_path_is_refused_when_the_run_has_no_scratch() {
        let dir = tmpdir();
        let elsewhere = tmpdir();
        std::fs::write(elsewhere.join("x.pdf"), make_pdf(1)).unwrap();
        let a = args(serde_json::json!({"input": elsewhere.join("x.pdf").to_string_lossy()}));
        let err = commit_in(
            "add_page_numbers",
            &a,
            &dir,
            Some(&dir),
            &docs_bundle(),
            None,
        )
        .expect_err("outside the sandbox and no scratch");
        assert!(err.to_string().contains("outside the sandbox"), "{err}");
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&elsewhere);
    }
}
