//! The net toolkit, native: one http(s) URL into the run scratch, under the owner's policy. Port
//! of the Python tool `download_file` (`toolkits/net/python/download.py`); the URL rules come
//! from `knaif_core::urls`, pinned for both runtimes by `contracts/parity/url_inputs_cases.json`.
//!
//! - only http and https, no credentials, on the first request and on every redirect (at most 5);
//! - no cookies, a user agent that names knaif, timeouts on connecting, on each read and overall;
//! - the response must be a content type the skill accepts, and a web page is named as one;
//! - a body over the cap is refused by its Content-Length, or cut off while streaming;
//! - a refusal leaves nothing behind: bytes go to `<name>.part`, renamed when complete.
//!
//! Asking the user is the caller's job. `KNAIF_NET_TEST_HOST=<host>=<base>` sends requests for
//! that one host to `<base>` (the eval harness's local server answers `files.knaif.test` this
//! way); the product never sets it.

use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use knaif_core::urls::{check_content_type, check_url, is_url, media_type, url_file_name};

pub const MAX_REDIRECTS: usize = 5;

/// What a skill allows a download to be: its `url_inputs:` plus the timeouts.
#[derive(Debug, Clone)]
pub struct Policy {
    pub accepted: Vec<String>,
    pub max_bytes: u64,
    pub connect_timeout: Duration,
    pub read_timeout: Duration,
    pub total_timeout: Duration,
}

impl Policy {
    /// The default timeouts (10 s connect, 60 s per read, 10 min overall) for these limits.
    pub fn new(accepted: Vec<String>, max_bytes: u64) -> Self {
        Self {
            accepted,
            max_bytes,
            connect_timeout: Duration::from_secs(10),
            read_timeout: Duration::from_secs(60),
            total_timeout: Duration::from_secs(600),
        }
    }
}

/// A finished download.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Download {
    pub path: PathBuf,
    pub bytes: u64,
    pub content_type: String,
    pub url: String,
    pub final_url: String,
}

fn user_agent() -> String {
    format!("knaif/{}", env!("CARGO_PKG_VERSION"))
}

/// `url` with the test host (`KNAIF_NET_TEST_HOST`) sent to its local base, if set.
fn mapped(url: &str) -> String {
    let Ok(spec) = std::env::var("KNAIF_NET_TEST_HOST") else {
        return url.to_string();
    };
    let Some((host, base)) = spec.split_once('=') else {
        return url.to_string();
    };
    let Some((_, rest)) = url.split_once("://") else {
        return url.to_string();
    };
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let netloc = &rest[..end];
    let hostname = netloc.rsplit_once(':').map_or(netloc, |(h, _)| h);
    if host.is_empty() || !hostname.eq_ignore_ascii_case(host) {
        return url.to_string();
    }
    format!("{}{}", base.trim_end_matches('/'), &rest[end..])
}

/// A redirect's Location resolved against the URL that answered with it.
fn join(base: &str, location: &str) -> String {
    if is_url(location) {
        return location.to_string();
    }
    let (scheme, rest) = base.split_once("://").unwrap_or(("http", base));
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let origin = format!("{scheme}://{}", &rest[..end]);
    if let Some(net) = location.strip_prefix("//") {
        return format!("{scheme}://{net}");
    }
    if location.starts_with('/') {
        return format!("{origin}{location}");
    }
    let path = &rest[end..];
    let path = path.split(['?', '#']).next().unwrap_or("");
    let dir = path.rsplit_once('/').map_or("", |(d, _)| d);
    format!("{origin}{dir}/{location}")
}

/// A transport error caused by a timeout. By the I/O error kind, not the text: Windows reports a
/// read timeout as os error 10060 ("failed to respond"), whose message never says "timed out".
fn is_timeout(t: &ureq::Transport) -> bool {
    let mut source = std::error::Error::source(t);
    while let Some(err) = source {
        if let Some(io) = err.downcast_ref::<std::io::Error>() {
            if matches!(
                io.kind(),
                std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
            ) {
                return true;
            }
        }
        source = err.source();
    }
    t.to_string().to_lowercase().contains("timed out")
}

fn human(n: u64) -> String {
    if n >= 1_000_000 {
        let mb = n as f64 / 1_000_000.0;
        let s = format!("{mb:.3}");
        format!("{} MB", s.trim_end_matches('0').trim_end_matches('.'))
    } else {
        format!("{n} bytes")
    }
}

fn free(path: PathBuf) -> Result<PathBuf, String> {
    if !path.exists() {
        return Ok(path);
    }
    let stem = path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let ext = path
        .extension()
        .map(|e| format!(".{}", e.to_string_lossy()))
        .unwrap_or_default();
    for i in 1..10_000 {
        let candidate = path.with_file_name(format!("{stem}-{i}{ext}"));
        if !candidate.exists() {
            return Ok(candidate);
        }
    }
    Err(format!(
        "no free name for {} in {}",
        path.display(),
        path.display()
    ))
}

/// The path a dry run reports for `url`: where the download would land, with no request made.
pub fn planned_path(url: &str, dest_dir: &Path) -> PathBuf {
    dest_dir.join(url_file_name(url, None))
}

/// Download `url` into `dest_dir` under `policy`. The error is a message for the user naming the
/// URL and the reason; on any error nothing is left in `dest_dir`.
pub fn download_file(url: &str, dest_dir: &Path, policy: &Policy) -> Result<Download, String> {
    let agent = ureq::AgentBuilder::new()
        .redirects(0)
        .timeout_connect(policy.connect_timeout)
        .timeout_read(policy.read_timeout)
        .user_agent(&user_agent())
        .try_proxy_from_env(true)
        .build();
    let deadline = Instant::now() + policy.total_timeout;
    let mut current = url.to_string();
    for _hop in 0..=MAX_REDIRECTS {
        if let Some(error) = check_url(&current) {
            return Err(if current == url {
                error
            } else {
                format!("{url} redirected: {error}")
            });
        }
        let response = match agent.get(&mapped(&current)).call() {
            Ok(r) => r,
            Err(ureq::Error::Status(code, _)) => {
                return Err(format!("the server answered {code} for {url}"))
            }
            Err(ureq::Error::Transport(t)) => {
                let text = t.to_string();
                return Err(if is_timeout(&t) {
                    format!("{url} timed out ({text})")
                } else {
                    format!("{url} could not be fetched: {text}")
                });
            }
        };
        let status = response.status();
        if (300..400).contains(&status) {
            if let Some(location) = response.header("Location") {
                current = join(&current, location);
                continue;
            }
        }
        if status >= 400 {
            return Err(format!("the server answered {status} for {url}"));
        }
        let ctype = response.header("Content-Type").map(str::to_string);
        if let Some(error) = check_content_type(ctype.as_deref(), &policy.accepted) {
            return Err(format!("{url}: {error}"));
        }
        if let Some(declared) = response
            .header("Content-Length")
            .and_then(|v| v.parse::<u64>().ok())
        {
            if declared > policy.max_bytes {
                return Err(format!(
                    "{url} is larger than the {} limit",
                    human(policy.max_bytes)
                ));
            }
        }
        fs::create_dir_all(dest_dir)
            .map_err(|e| format!("cannot make {}: {e}", dest_dir.display()))?;
        let fin = free(dest_dir.join(url_file_name(&current, ctype.as_deref())))?;
        let part = fin.with_file_name(format!(
            "{}.part",
            fin.file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default()
        ));
        let written = match stream(response.into_reader(), &part, url, policy, deadline) {
            Ok(n) => n,
            Err(e) => {
                let _ = fs::remove_file(&part);
                return Err(e);
            }
        };
        fs::rename(&part, &fin).map_err(|e| {
            let _ = fs::remove_file(&part);
            format!("cannot finish {}: {e}", fin.display())
        })?;
        return Ok(Download {
            path: fin,
            bytes: written,
            content_type: media_type(ctype.as_deref()),
            url: url.to_string(),
            final_url: current,
        });
    }
    Err(format!(
        "{url}: too many redirects (more than {MAX_REDIRECTS})"
    ))
}

fn stream(
    mut reader: impl Read,
    part: &Path,
    url: &str,
    policy: &Policy,
    deadline: Instant,
) -> Result<u64, String> {
    let mut file =
        fs::File::create(part).map_err(|e| format!("cannot write {}: {e}", part.display()))?;
    let mut buf = vec![0u8; 64 * 1024];
    let mut written: u64 = 0;
    loop {
        let n = match reader.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => n,
            Err(e) => return Err(format!("{url} timed out or broke off: {e}")),
        };
        written += n as u64;
        if written > policy.max_bytes {
            return Err(format!(
                "{url} is larger than the {} limit",
                human(policy.max_bytes)
            ));
        }
        if Instant::now() > deadline {
            return Err(format!("{url} timed out (took too long overall)"));
        }
        file.write_all(&buf[..n])
            .map_err(|e| format!("cannot write {}: {e}", part.display()))?;
    }
    Ok(written)
}
