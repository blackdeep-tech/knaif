//! One file into many: which pieces a split, a frame grab or several thumbnails will write.
//! Port of `skills/ffmpeg/python/_pieces.py` (owner, 2026-10-20).
//!
//! One planner for both sides of the promise: what core is told before anything runs (the
//! planned set) and the recipes the step renders read the same pieces, so the preview names what
//! is written. Each piece is an ordinary recipe: a trim (re-encoded, so it starts and ends exactly
//! where asked) or a thumbnail.
//!
//! Names: the set is a sequence pattern (`clip_%03d.mp4`), the files number from 1. A pattern the
//! model wrote is kept; a plain output name gets the number before its extension
//! (`part.mp4` -> `part_001.mp4`); without one, next to the input: `clip_001.mp4` for pieces,
//! `clip_frame_001.jpg` for frames, `clip_thumb_001.jpg` for thumbnails at several times.

use serde_json::Value;

use crate::engine::{format_seconds, timestamp_seconds};

/// A remainder shorter than this is not a piece of its own (`every: 10` over 20.04 s is two).
const SLIVER_S: f64 = 0.1;

/// What one piece is: a span of the input, or a still at a time.
#[derive(Debug, Clone, PartialEq)]
pub enum Piece {
    Trim {
        start: String,
        duration: String,
    },
    Still {
        at_time: String,
        image_format: String,
        scale: Option<String>,
    },
}

/// The set's name and, per piece, its output and what it is.
#[derive(Debug, Clone, PartialEq)]
pub struct Pieces {
    pub name: String,
    pub items: Vec<(String, Piece)>,
}

/// Whether `tool` writes many files from one for these args: a split, a frame grab, or
/// `create_thumbnail` at two or more times.
pub fn writes_many(tool: &str, args: &serde_json::Map<String, Value>) -> bool {
    match tool {
        "split_video" | "extract_frames" => true,
        "create_thumbnail" => times(args.get("at_time")).len() >= 2,
        _ => false,
    }
}

fn text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn source(args: &serde_json::Map<String, Value>) -> String {
    let value = args
        .get("input")
        .filter(|v| !v.is_null() && v.as_str() != Some(""))
        .or_else(|| args.get("inputs"));
    let value = match value {
        Some(Value::Array(items)) => items.first().map(text).unwrap_or_default(),
        Some(Value::Null) | None => String::new(),
        Some(v) => text(v),
    };
    value.replace('\\', "/")
}

/// Several times, as a list or one comma-separated string; a single time is one.
fn times(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(items)) => items
            .iter()
            .map(|v| text(v).trim().to_string())
            .filter(|t| !t.is_empty())
            .collect(),
        Some(Value::String(s)) => s
            .split(',')
            .map(|t| t.trim().to_string())
            .filter(|t| !t.is_empty())
            .collect(),
        _ => Vec::new(),
    }
}

fn seconds(value: Option<&Value>) -> Option<f64> {
    let raw = text(value.filter(|v| !v.is_null())?).trim().to_string();
    if raw.is_empty() {
        return None;
    }
    timestamp_seconds(Some(&raw)).filter(|s| *s > 0.0)
}

fn integer(value: Option<&Value>) -> anyhow::Result<Option<i64>> {
    let Some(v) = value.filter(|v| !v.is_null()) else {
        return Ok(None);
    };
    let n = match v {
        Value::Number(n) => n.as_f64().map(|f| f as i64),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        Value::Bool(b) => Some(i64::from(*b)),
        _ => None,
    };
    n.map(Some)
        .ok_or_else(|| anyhow::anyhow!("not a whole number: {v}"))
}

/// Python `posixpath.split`.
fn split(path: &str) -> (String, String) {
    match path.rfind('/') {
        Some(i) => {
            let head = path[..i].trim_end_matches('/');
            let head = if head.is_empty() && path.starts_with('/') {
                "/"
            } else {
                head
            };
            (head.to_string(), path[i + 1..].to_string())
        }
        None => (String::new(), path.to_string()),
    }
}

/// Python `posixpath.splitext` on a path: `(root, ext)`, a leading dot never an extension.
fn splitext(path: &str) -> (String, String) {
    let (folder, base) = split(path);
    if let Some(dot) = base.rfind('.') {
        if base[..dot].chars().any(|c| c != '.') {
            let stem = &path[..path.len() - (base.len() - dot)];
            let _ = folder;
            return (stem.to_string(), base[dot..].to_string());
        }
    }
    (path.to_string(), String::new())
}

fn join(folder: &str, name: &str) -> String {
    if folder.is_empty() || name.starts_with('/') {
        name.to_string()
    } else if folder.ends_with('/') {
        format!("{folder}{name}")
    } else {
        format!("{folder}/{name}")
    }
}

/// The sequence pattern of the set, relative to where paths resolve. A relative output is
/// relative to the input's folder, as for every ffmpeg tool, so the input's folder leads it.
fn set_name(output: Option<&str>, source: &str, suffix: &str, ext: &str) -> String {
    let (folder, base) = split(source);
    if let Some(output) = output.filter(|o| !o.is_empty()) {
        let absolute = output.starts_with(['/', '\\']) || output.chars().take(3).any(|c| c == ':');
        let output = if absolute {
            output.to_string()
        } else {
            join(&folder, &output.replace('\\', "/"))
        };
        if output.contains('%') {
            return output;
        }
        let (stem, out_ext) = splitext(&output);
        let out_ext = if out_ext.is_empty() {
            format!(".{ext}")
        } else {
            out_ext
        };
        return format!("{stem}_%03d{out_ext}");
    }
    let stem = splitext(&base).0;
    join(&folder, &format!("{stem}{suffix}_%03d.{ext}"))
}

/// Python's `name % n` for the one `%d`-style field a sequence pattern holds (`%03d`, `%2d`,
/// `%d`); `%%` is a percent sign.
pub fn number(pattern: &str, n: usize) -> String {
    let mut out = String::with_capacity(pattern.len() + 4);
    let mut chars = pattern.chars().peekable();
    let mut done = false;
    while let Some(c) = chars.next() {
        if c != '%' {
            out.push(c);
            continue;
        }
        if chars.peek() == Some(&'%') {
            chars.next();
            out.push('%');
            continue;
        }
        let mut spec = String::new();
        while let Some(&d) = chars.peek() {
            if d.is_ascii_digit() {
                spec.push(d);
                chars.next();
            } else {
                break;
            }
        }
        if chars.peek() == Some(&'d') && !done {
            chars.next();
            done = true;
            let zero = spec.starts_with('0');
            let width: usize = spec.trim_start_matches('0').parse().unwrap_or(0);
            if zero {
                out.push_str(&format!("{n:0width$}"));
            } else {
                out.push_str(&format!("{n:width$}"));
            }
        } else {
            out.push('%');
            out.push_str(&spec);
        }
    }
    out
}

/// The image format a still is written in: the output's extension when it is an image and the
/// caller named none, else the one asked (`jpg` by default). Port of `_image_format_from_output`.
fn image_format(output: Option<&str>, asked: &str, image_exts: &[String]) -> String {
    if let (Some(output), "jpg") = (output, asked) {
        let ext = splitext(&output.replace('\\', "/"))
            .1
            .trim_start_matches('.')
            .to_lowercase();
        if image_exts.iter().any(|e| e.eq_ignore_ascii_case(&ext)) {
            return ext;
        }
    }
    asked.to_string()
}

/// The pieces `tool` will write from one input of `duration` seconds, else `None` (a tool that
/// writes one file, including `create_thumbnail` at one time). `image_exts` are the extensions
/// a still may take from its output's name.
pub fn plan_pieces(
    tool: &str,
    args: &serde_json::Map<String, Value>,
    duration: f64,
    fps: Option<f64>,
    image_exts: &[String],
) -> anyhow::Result<Option<Pieces>> {
    let source = source(args);
    let output = args.get("output").and_then(Value::as_str);
    match tool {
        "split_video" => {
            let every = seconds(args.get("every"));
            let parts = integer(args.get("parts"))?.filter(|n| *n != 0);
            let mut spans: Vec<(f64, f64)> = Vec::new();
            if let Some(every) = every {
                let mut start = 0.0;
                while duration - start >= SLIVER_S {
                    spans.push((start, every.min(duration - start)));
                    start += every;
                }
            } else if let Some(n) = parts {
                let length = duration / n as f64;
                spans = (0..n)
                    .map(|i| {
                        let start = i as f64 * length;
                        let len = if i < n - 1 { length } else { duration - start };
                        (start, len)
                    })
                    .collect();
            } else {
                anyhow::bail!(
                    "To split a video, say how long each piece is (every) or how many pieces (parts)."
                );
            }
            let ext = splitext(&source).1.trim_start_matches('.').to_string();
            let ext = if ext.is_empty() {
                "mp4".to_string()
            } else {
                ext
            };
            let name = set_name(output, &source, "", &ext);
            let items = spans
                .iter()
                .enumerate()
                .map(|(i, (start, length))| {
                    (
                        number(&name, i + 1),
                        Piece::Trim {
                            start: format_seconds(*start),
                            duration: format_seconds(*length),
                        },
                    )
                })
                .collect();
            Ok(Some(Pieces { name, items }))
        }
        "extract_frames" => {
            let mut every = seconds(args.get("every"));
            let count = integer(args.get("count"))?.filter(|n| *n != 0);
            let nth = integer(args.get("every_nth"))?.filter(|n| *n != 0);
            if every.is_none() {
                if let (Some(nth), Some(fps)) = (nth, fps.filter(|f| *f != 0.0)) {
                    every = Some(nth as f64 / fps);
                }
            }
            let at: Vec<f64> = if let Some(every) = every {
                let mut times = Vec::new();
                let mut k = 0u64;
                while (k as f64) * every < duration - 1e-9 {
                    times.push(k as f64 * every);
                    k += 1;
                }
                times
            } else if let Some(c) = count {
                (0..c)
                    .map(|i| duration * (i as f64 + 0.5) / c as f64)
                    .collect()
            } else {
                anyhow::bail!(
                    "To take frames, say how often (every), every which frame (every_nth) or how \
                     many frames (count)."
                );
            };
            let at: Vec<String> = at.into_iter().map(format_seconds).collect();
            Ok(Some(stills(
                args, &source, output, "_frame", at, image_exts,
            )))
        }
        "create_thumbnail" => {
            let at = times(args.get("at_time"));
            if at.len() < 2 {
                return Ok(None);
            }
            Ok(Some(stills(
                args, &source, output, "_thumb", at, image_exts,
            )))
        }
        _ => Ok(None),
    }
}

fn stills(
    args: &serde_json::Map<String, Value>,
    source: &str,
    output: Option<&str>,
    suffix: &str,
    at: Vec<String>,
    image_exts: &[String],
) -> Pieces {
    let asked = args
        .get("image_format")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or("jpg");
    let format = image_format(output, asked, image_exts);
    let name = set_name(output, source, suffix, &format);
    let scale = args.get("scale").filter(|v| !v.is_null()).map(text);
    let items = at
        .into_iter()
        .enumerate()
        .map(|(i, at_time)| {
            (
                number(&name, i + 1),
                Piece::Still {
                    at_time,
                    image_format: format.clone(),
                    scale: scale.clone(),
                },
            )
        })
        .collect();
    Pieces { name, items }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_fill_the_sequence_field() {
        assert_eq!(number("clip_%03d.mp4", 7), "clip_007.mp4");
        assert_eq!(number("piece_%02d.mov", 12), "piece_12.mov");
        assert_eq!(number("a_%d.jpg", 3), "a_3.jpg");
        assert_eq!(number("100%%_%03d.jpg", 1), "100%_001.jpg");
    }

    #[test]
    fn a_split_names_its_set_next_to_the_input() {
        let args = serde_json::json!({"input": "videos/clip.mp4", "every": "4s"});
        let p = plan_pieces("split_video", args.as_object().unwrap(), 10.0, None, &[])
            .unwrap()
            .unwrap();
        assert_eq!(p.name, "videos/clip_%03d.mp4");
        let names: Vec<&str> = p.items.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(
            names,
            [
                "videos/clip_001.mp4",
                "videos/clip_002.mp4",
                "videos/clip_003.mp4"
            ]
        );
        assert_eq!(
            p.items[2].1,
            Piece::Trim {
                start: "8".into(),
                duration: "2".into()
            }
        );
    }

    #[test]
    fn a_split_without_a_size_says_what_is_missing() {
        let args = serde_json::json!({"input": "clip.mp4"});
        let err = plan_pieces("split_video", args.as_object().unwrap(), 10.0, None, &[])
            .unwrap_err()
            .to_string();
        assert!(err.contains("every") && err.contains("parts"), "{err}");
    }

    #[test]
    fn one_thumbnail_is_no_set() {
        let args = serde_json::json!({"input": "clip.mp4", "at_time": "00:00:05"});
        assert!(!writes_many("create_thumbnail", args.as_object().unwrap()));
        let several = serde_json::json!({"input": "clip.mp4", "at_time": ["1", "2"]});
        assert!(writes_many(
            "create_thumbnail",
            several.as_object().unwrap()
        ));
    }
}
