//! URL inputs: the value class, the refusals made before any request, and download naming. Port
//! of Python `knaif.urls`; both runtimes are pinned by `contracts/parity/url_inputs_cases.json`.
//!
//! A plan argument is a URL when it starts with a scheme followed by `://`. A URL is never a
//! path. Only `http` and `https` can be downloaded, and a URL that carries a user name or
//! password is refused, since knaif sends no credentials.

use anyhow::{bail, Result};
use serde_yaml::Value as Yaml;

const OCTET_STREAM: &str = "application/octet-stream";
const WEB_PAGE_TYPES: [&str; 2] = ["text/html", "application/xhtml+xml"];

/// The extension a download gets when its URL names none, by content type. Same table as Python's
/// `knaif.urls.EXTENSIONS`.
const EXTENSIONS: [(&str, &str); 28] = [
    ("video/mp4", "mp4"),
    ("video/quicktime", "mov"),
    ("video/webm", "webm"),
    ("video/x-matroska", "mkv"),
    ("video/x-msvideo", "avi"),
    ("video/mpeg", "mpg"),
    ("audio/mpeg", "mp3"),
    ("audio/mp4", "m4a"),
    ("audio/aac", "aac"),
    ("audio/wav", "wav"),
    ("audio/x-wav", "wav"),
    ("audio/flac", "flac"),
    ("audio/ogg", "ogg"),
    ("image/png", "png"),
    ("image/jpeg", "jpg"),
    ("image/gif", "gif"),
    ("image/webp", "webp"),
    ("image/tiff", "tif"),
    ("application/pdf", "pdf"),
    ("text/plain", "txt"),
    ("text/markdown", "md"),
    ("text/csv", "csv"),
    ("application/rtf", "rtf"),
    ("application/msword", "doc"),
    (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
    (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
    ("application/vnd.oasis.opendocument.text", "odt"),
];

/// A skill's `url_inputs:`: the content types it can work on and the size cap.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UrlInputs {
    pub accepted: Vec<String>,
    pub max_bytes: u64,
}

/// The parts of a URL these rules need: `scheme://netloc/path?query#fragment`.
struct Split<'a> {
    netloc: &'a str,
    path: &'a str,
}

fn split(url: &str) -> Split<'_> {
    let rest = url.split_once("://").map_or("", |(_, r)| r);
    let end_netloc = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let (netloc, tail) = rest.split_at(end_netloc);
    let path_end = tail.find(['?', '#']).unwrap_or(tail.len());
    Split {
        netloc,
        path: &tail[..path_end],
    }
}

/// Whether `value` is a URL (a scheme followed by `://`), whatever the scheme.
pub fn is_url(value: &str) -> bool {
    let Some((scheme, _)) = value.split_once("://") else {
        return false;
    };
    let mut chars = scheme.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphabetic())
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '.' | '-'))
}

/// Why `value` cannot be downloaded, or `None` when it can. Call only on a URL.
pub fn check_url(value: &str) -> Option<String> {
    let scheme = value
        .split_once("://")
        .map_or("", |(s, _)| s)
        .to_lowercase();
    if scheme != "http" && scheme != "https" {
        return Some(format!(
            "only http and https URLs can be downloaded, not {scheme}:// ({value})"
        ));
    }
    let parts = split(value);
    if parts.netloc.contains('@') {
        return Some(format!(
            "a URL with a user name or password is refused; knaif sends no credentials ({value})"
        ));
    }
    let host = parts
        .netloc
        .rsplit_once(':')
        .map_or(parts.netloc, |(h, _)| h);
    if host.is_empty() {
        return Some(format!("the URL has no host ({value})"));
    }
    None
}

/// The bare, lower-case media type of a Content-Type header; a missing one is octet-stream.
pub fn media_type(content_type: Option<&str>) -> String {
    let bare = content_type
        .unwrap_or("")
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    if bare.is_empty() {
        OCTET_STREAM.to_string()
    } else {
        bare
    }
}

/// Percent-decoding as Python's `unquote`: escapes decode to bytes, read as UTF-8 (lossy).
fn unquote(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).ok();
            if let Some(b) = hex.and_then(|h| u8::from_str_radix(h, 16).ok()) {
                out.push(b);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// A safe local file name for what `url` returns: the last segment of the decoded path (a `/` or
/// `\` anywhere ends a segment), leading dots and the characters Windows refuses removed, and an
/// extension from `content_type` when the name has none.
pub fn url_file_name(url: &str, content_type: Option<&str>) -> String {
    let decoded = unquote(split(url).path);
    let last = decoded
        .split(['/', '\\'])
        .rfind(|s| !s.is_empty())
        .unwrap_or("");
    let cleaned: String = last
        .chars()
        .map(|c| {
            if matches!(c, '<' | '>' | ':' | '"' | '|' | '?' | '*') || (c as u32) < 0x20 {
                '_'
            } else {
                c
            }
        })
        .collect();
    let mut name = cleaned
        .trim_start_matches('.')
        .trim_end_matches(['.', ' '])
        .to_string();
    if name.is_empty() || name == ".." {
        name = "download".to_string();
    }
    if !name.contains('.') {
        if let Some(ct) = content_type {
            let media = media_type(Some(ct));
            if let Some((_, ext)) = EXTENSIONS.iter().find(|(t, _)| *t == media) {
                name = format!("{name}.{ext}");
            }
        }
    }
    name
}

/// Why a response of `content_type` is refused for a skill accepting `accepted`, or `None`.
/// Patterns are exact types, or end in `*` to match a prefix; a missing type counts as
/// octet-stream; a web page is named as one.
pub fn check_content_type(content_type: Option<&str>, accepted: &[String]) -> Option<String> {
    let media = media_type(content_type);
    for pattern in accepted {
        let p = pattern.trim().to_lowercase();
        if p == media || (p.ends_with('*') && media.starts_with(&p[..p.len() - 1])) {
            return None;
        }
    }
    if WEB_PAGE_TYPES.contains(&media.as_str()) {
        return Some(format!(
            "the link is a web page ({media}), not a file this skill can work on"
        ));
    }
    Some(format!(
        "the download is {media}, not a type this skill accepts ({})",
        accepted.join(", ")
    ))
}

/// `url_inputs: {accepted: [...], max_mb: N}` from `skill.yaml`; `None` when absent. Same rules
/// and messages as Python's `parse_url_inputs`.
pub fn parse_url_inputs(raw: Option<&Yaml>, toolkits: &[String]) -> Result<Option<UrlInputs>> {
    let Some(raw) = raw else { return Ok(None) };
    let Some(map) = raw.as_mapping() else {
        bail!("url_inputs must be a mapping with `accepted` and `max_mb`");
    };
    let accepted: Vec<String> = map
        .get("accepted")
        .and_then(Yaml::as_sequence)
        .map(|seq| {
            seq.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    let n_items = map
        .get("accepted")
        .and_then(Yaml::as_sequence)
        .map_or(0, Vec::len);
    if accepted.is_empty() || accepted.len() != n_items {
        bail!("url_inputs.accepted must be a non-empty list of content types");
    }
    let max_mb = map.get("max_mb").and_then(Yaml::as_f64).unwrap_or(0.0);
    if max_mb <= 0.0 || map.get("max_mb").is_some_and(Yaml::is_bool) {
        bail!("url_inputs.max_mb must be a positive number");
    }
    if !toolkits.iter().any(|t| t == "net") {
        bail!("url_inputs needs the net toolkit: add `net` to `toolkits:`");
    }
    Ok(Some(UrlInputs {
        accepted,
        max_bytes: (max_mb * 1_000_000.0) as u64,
    }))
}

/// Arg keys that name files a step reads: the only places a URL may stand. An arg whose schema
/// says `path_role: input` counts too.
pub const URL_INPUT_KEYS: [&str; 5] = ["inputs", "input", "files", "base", "append"];
/// Core control tools: exempt from the URL rule (a clarify may quote a URL).
pub const CONTROL_TOOLS: [&str; 4] = ["clarify", "reject", "done", "wait_for_confirmation"];

/// A bundle's `url_inputs:` from its `skill.yaml` (checked against its `toolkits:`); `None` when
/// the skill takes local files only.
pub fn load_url_inputs(skill_yaml: &std::path::Path) -> Result<Option<UrlInputs>> {
    let text = std::fs::read_to_string(skill_yaml)?;
    let doc: Yaml = serde_yaml::from_str(&text)?;
    let toolkits: Vec<String> = doc
        .get("toolkits")
        .and_then(Yaml::as_sequence)
        .map(|s| {
            s.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    parse_url_inputs(doc.get("url_inputs"), &toolkits)
}
