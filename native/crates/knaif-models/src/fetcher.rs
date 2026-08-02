//! Real HTTP fetcher for model downloads.
//!
//! Unauthenticated public GET — models are hosted publicly (e.g. `blackdeep/knaif` on
//! Hugging Face), so end-users and third-party devs need no token. (The admin upload path
//! uses a token, but that lives in the publish tooling, never here.) Follows redirects
//! because HF `resolve` URLs 302 to a CDN.
//!
//! ## Why parallel + resumable
//!
//! A single stream to HF's CDN is throttled per connection (a few hundred KiB/s), so a 2.5 GB
//! GGUF crawls. We fix that the way `hf_transfer` does: probe the file with a ranged GET, then —
//! when the server honors `Range` — pull it as N concurrent chunks, each seeking to its offset in
//! the `.part` file. Completed chunks are recorded in a tiny sidecar so an interrupted pull
//! **resumes** the missing chunks instead of restarting. A server that ignores `Range` (200 to the
//! probe) transparently falls back to the original single-stream download.
//!
//! Every request — the probe and each chunk — targets the **original** URL and follows redirects
//! itself. Reusing the post-redirect CDN link instead is tempting (it keeps the per-chunk fan-out
//! off `huggingface.co`, the rate-limited surface for tokenless clients) and it is **unsound**: a
//! signed CDN URL may carry arbitrary conditions, and Hugging Face's Xet storage signs the
//! requested *byte range* into the policy. A link obtained by probing `bytes=0-0` therefore answers
//! **403** to every other range *and* to a full GET, so pinning it breaks every download of a
//! Xet-backed repo. Nothing in the redirect distinguishes a range-bound link from a plain one, so
//! re-resolving is the only correct default; the 429 retry path above absorbs the rate-limit risk
//! it buys back.
//!
//! Because chunks land out of order (and possibly across runs), the file is hashed by the caller
//! ([`crate::store::ModelStore::pull_with_progress`]) in one pass after assembly, not on the write
//! path.

use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::store::{Fetcher, ProgressFn};

/// Chunk size for the parallel path: each `Range` request pulls this many bytes (except the last).
/// 16 MiB keeps the request count modest for multi-GB GGUFs (~156 chunks for 2.5 GB) while staying
/// small enough that a re-fetched chunk after an error costs little. The chunk grid is fixed, so a
/// resume computes the same chunk boundaries regardless of the connection count.
const CHUNK_SIZE: u64 = 16 * 1024 * 1024;

/// How many chunks to fetch at once by default. The win over a single stream comes from beating the
/// CDN's per-connection throttle; 8 is a safe multiplier that won't look like an abusive fan-out.
const DEFAULT_PARALLELISM: usize = 8;

/// Read buffer for streaming a chunk body to disk (matches the old single-stream buffer).
const READ_BUF: usize = 64 * 1024;

/// Magic prefix of the resume sidecar, so a stale/foreign file is never mistaken for resume state.
const SIDECAR_MAGIC: &[u8; 4] = b"KDL1";

/// Downloads model bytes over HTTP(S) with `ureq`, in parallel byte-range chunks when the server
/// supports it, retrying transient rate-limit / unavailable responses with backoff. HF's CDN can
/// emit 429 under shared-IP load, so a bare fetch is fragile at scale; retrying here (rather than
/// asking users for a token) is the plan's chosen fix.
#[derive(Debug, Clone)]
pub struct HttpFetcher {
    /// Retries **after** the first attempt (so total attempts = `max_retries + 1`).
    max_retries: u32,
    /// Base backoff, doubled each attempt unless the server sends `Retry-After`.
    base_delay: Duration,
    /// Concurrent chunk requests on the parallel path.
    parallelism: usize,
    /// Bytes per ranged chunk (overridable so tests can exercise the grid without multi-MB files).
    chunk_size: u64,
}

impl Default for HttpFetcher {
    fn default() -> Self {
        Self {
            max_retries: 4,
            base_delay: Duration::from_secs(1),
            parallelism: DEFAULT_PARALLELISM,
            chunk_size: CHUNK_SIZE,
        }
    }
}

impl HttpFetcher {
    pub fn new() -> Self {
        Self::default()
    }

    /// Tune the retry budget and base backoff. Tests use a tiny delay to stay fast; a caller can
    /// pass `max_retries = 0` to opt out of retrying entirely.
    pub fn with_backoff(mut self, max_retries: u32, base_delay: Duration) -> Self {
        self.max_retries = max_retries;
        self.base_delay = base_delay;
        self
    }

    /// Set how many chunks download concurrently on the parallel path (min 1).
    pub fn with_parallelism(mut self, parallelism: usize) -> Self {
        self.parallelism = parallelism.max(1);
        self
    }

    /// Override the ranged-chunk size (tests use a tiny grid; production keeps [`CHUNK_SIZE`]).
    pub fn with_chunk_size(mut self, chunk_size: u64) -> Self {
        self.chunk_size = chunk_size.max(1);
        self
    }

    /// GET `url` (optionally a byte `range`), retrying a 429/503 up to `max_retries` times with
    /// backoff (honoring the server's `Retry-After` when present). ureq treats a non-2xx status as
    /// `Err`, so retryable statuses surface as `Error::Status` here; any other error (or an
    /// exhausted budget) is returned.
    fn get(&self, url: &str, range: Option<(u64, u64)>) -> anyhow::Result<ureq::Response> {
        let mut attempt = 0u32;
        loop {
            let mut req = ureq::get(url);
            if let Some((start, end)) = range {
                req = req.set("Range", &format!("bytes={start}-{end}"));
            }
            match req.call() {
                Ok(resp) => return Ok(resp),
                Err(ureq::Error::Status(code, resp))
                    if is_retryable_status(code) && attempt < self.max_retries =>
                {
                    let retry_after = resp
                        .header("Retry-After")
                        .and_then(|v| v.trim().parse::<u64>().ok());
                    std::thread::sleep(backoff_delay(attempt, retry_after, self.base_delay));
                    attempt += 1;
                }
                Err(e) => return Err(anyhow::anyhow!("fetch {url:?}: {e}")),
            }
        }
    }

    /// Stream a whole 200 response straight to `dest` (server ignored `Range`, or the file is
    /// small): the original single-connection path, kept as the always-correct fallback.
    fn stream_whole(
        &self,
        resp: ureq::Response,
        dest: &Path,
        total: Option<u64>,
        progress: &mut ProgressFn<'_>,
    ) -> anyhow::Result<()> {
        let mut reader = resp.into_reader();
        let mut file = std::fs::File::create(dest)?;
        let mut buf = [0u8; READ_BUF];
        let mut downloaded = 0u64;
        loop {
            let n = reader
                .read(&mut buf)
                .map_err(|e| anyhow::anyhow!("read body from {dest:?}: {e}"))?;
            if n == 0 {
                break;
            }
            file.write_all(&buf[..n])
                .map_err(|e| anyhow::anyhow!("write model bytes: {e}"))?;
            downloaded += n as u64;
            progress(downloaded, total);
        }
        Ok(())
    }

    /// Download `[0, total)` as concurrent ranged chunks into `dest`, resuming any chunks a prior
    /// run already completed (tracked in the sidecar). On success the sidecar is removed; on error
    /// both `dest` and the sidecar are left in place so the next call continues.
    fn download_parallel(
        &self,
        url: &str,
        dest: &Path,
        total: u64,
        progress: &mut ProgressFn<'_>,
    ) -> anyhow::Result<()> {
        let chunk_size = self.chunk_size;
        let nchunks = total.div_ceil(chunk_size) as usize;
        let sidecar = resume_sidecar_path(dest);

        // Only trust the sidecar if `dest` is a full-length pre-allocated file from a prior run.
        // (If the file was removed but the sidecar lingered, its "done" bits would be lies.)
        let dest_len = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
        let resume = if dest_len >= total {
            Resume::load(&sidecar, total, chunk_size, nchunks)
        } else {
            Resume::fresh(&sidecar, total, chunk_size, nchunks)
        };

        // Pre-size the file so every chunk can seek to its offset and write in place. Never
        // truncate: on resume the existing bytes are exactly what we want to keep.
        {
            let file = OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(false)
                .open(dest)?;
            file.set_len(total)?;
        }

        let base = resume.completed_bytes();
        progress(base, Some(total));

        let missing: Vec<u64> = (0..nchunks as u64)
            .filter(|&i| !resume.done[i as usize])
            .collect();
        if missing.is_empty() {
            let _ = std::fs::remove_file(&sidecar);
            progress(total, Some(total));
            return Ok(());
        }

        let nthreads = self.parallelism.max(1).min(missing.len());
        let missing = Arc::new(missing);
        let claim = Arc::new(AtomicUsize::new(0));
        let downloaded = Arc::new(AtomicU64::new(base));
        let abort = Arc::new(AtomicBool::new(false));
        let err_slot: Arc<Mutex<Option<anyhow::Error>>> = Arc::new(Mutex::new(None));
        let resume = Arc::new(Mutex::new(resume));
        let active = Arc::new(AtomicUsize::new(nthreads));

        let mut handles = Vec::with_capacity(nthreads);
        for _ in 0..nthreads {
            let fetcher = self.clone();
            let url = url.to_string();
            let dest = dest.to_path_buf();
            let missing = Arc::clone(&missing);
            let claim = Arc::clone(&claim);
            let downloaded = Arc::clone(&downloaded);
            let abort = Arc::clone(&abort);
            let err_slot = Arc::clone(&err_slot);
            let resume = Arc::clone(&resume);
            let active = Arc::clone(&active);
            handles.push(std::thread::spawn(move || {
                // Decrement the active count even if the worker returns early or panics, so the
                // reporter loop below can never hang.
                let _guard = ActiveGuard(&active);
                fetcher.run_worker(
                    &url,
                    &dest,
                    total,
                    chunk_size,
                    &missing,
                    &claim,
                    &downloaded,
                    &abort,
                    &err_slot,
                    &resume,
                );
            }));
        }

        // The `progress` callback is `&mut` and lives on this thread, so workers publish byte counts
        // to an atomic and this loop renders them until every worker has exited.
        while active.load(Ordering::SeqCst) > 0 {
            progress(downloaded.load(Ordering::Relaxed), Some(total));
            std::thread::sleep(Duration::from_millis(100));
        }
        for h in handles {
            let _ = h.join();
        }

        if let Some(e) = err_slot.lock().unwrap().take() {
            // Keep `dest` + sidecar for a resumed retry.
            return Err(e);
        }
        let _ = std::fs::remove_file(&sidecar);
        progress(total, Some(total));
        Ok(())
    }

    /// One worker: claim missing chunk indices until the queue drains or a sibling aborts, fetching
    /// each into `dest` at its offset and recording completion in the shared sidecar.
    #[allow(clippy::too_many_arguments)]
    fn run_worker(
        &self,
        url: &str,
        dest: &Path,
        total: u64,
        chunk_size: u64,
        missing: &[u64],
        claim: &AtomicUsize,
        downloaded: &AtomicU64,
        abort: &AtomicBool,
        err_slot: &Mutex<Option<anyhow::Error>>,
        resume: &Mutex<Resume>,
    ) {
        let mut file = match OpenOptions::new().write(true).open(dest) {
            Ok(f) => f,
            Err(e) => {
                record_first_error(abort, err_slot, anyhow::anyhow!("open {dest:?}: {e}"));
                return;
            }
        };
        loop {
            if abort.load(Ordering::SeqCst) {
                return;
            }
            let k = claim.fetch_add(1, Ordering::SeqCst);
            if k >= missing.len() {
                return;
            }
            let idx = missing[k];
            let start = idx * chunk_size;
            let end = ((idx + 1) * chunk_size).min(total) - 1; // inclusive
            match self.fetch_chunk(url, start, end, &mut file, downloaded, abort) {
                Ok(()) => {
                    let mut r = resume.lock().unwrap();
                    r.mark(idx as usize);
                    // A failed sidecar write only costs resume efficiency, never correctness, so it
                    // must not abort the download.
                    let _ = r.save();
                }
                Err(e) => {
                    record_first_error(abort, err_slot, e);
                    return;
                }
            }
        }
    }

    /// Fetch bytes `[start, end]` (inclusive) via a ranged GET and write them at `start` in `file`.
    /// Requires the server to answer 206 — the caller only takes this path after a probe confirmed
    /// range support. Bumps `downloaded` as bytes land and rolls it back on a short/aborted chunk.
    fn fetch_chunk(
        &self,
        url: &str,
        start: u64,
        end: u64,
        file: &mut std::fs::File,
        downloaded: &AtomicU64,
        abort: &AtomicBool,
    ) -> anyhow::Result<()> {
        let resp = self.get(url, Some((start, end)))?;
        if resp.status() != 206 {
            anyhow::bail!(
                "range request for bytes {start}-{end} of {url:?} not honored (status {})",
                resp.status()
            );
        }
        let expected = end - start + 1;
        file.seek(SeekFrom::Start(start))?;
        let mut reader = resp.into_reader();
        let mut buf = [0u8; READ_BUF];
        let mut got = 0u64;
        loop {
            if abort.load(Ordering::SeqCst) {
                break; // a sibling failed — stop and let the length check below unwind us
            }
            let n = reader
                .read(&mut buf)
                .map_err(|e| anyhow::anyhow!("read range {start}-{end} of {url:?}: {e}"))?;
            if n == 0 {
                break;
            }
            file.write_all(&buf[..n])?;
            got += n as u64;
            downloaded.fetch_add(n as u64, Ordering::Relaxed);
        }
        if got != expected {
            downloaded.fetch_sub(got, Ordering::Relaxed); // don't count a chunk we won't keep
            if abort.load(Ordering::SeqCst) {
                anyhow::bail!("download aborted");
            }
            anyhow::bail!("short read for bytes {start}-{end} of {url:?}: got {got} of {expected}");
        }
        Ok(())
    }
}

/// Record `err` as the download's cause only if we are the first worker to fail; later failures
/// (including the "aborted" errors that our own abort provokes in siblings) don't clobber it.
fn record_first_error(
    abort: &AtomicBool,
    err_slot: &Mutex<Option<anyhow::Error>>,
    err: anyhow::Error,
) {
    if !abort.swap(true, Ordering::SeqCst) {
        *err_slot.lock().unwrap() = Some(err);
    }
}

/// Decrements the shared active-worker count when a worker's scope ends (normally or by panic).
struct ActiveGuard<'a>(&'a AtomicUsize);
impl Drop for ActiveGuard<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

/// Path of the resume sidecar for a `.part` file (its name plus `.resume`). Public to the crate so
/// the store can drop it alongside a corrupt `.part`.
pub(crate) fn resume_sidecar_path(dest: &Path) -> PathBuf {
    let mut s = dest.as_os_str().to_owned();
    s.push(".resume");
    PathBuf::from(s)
}

/// Which chunks of a parallel download are already on disk, persisted next to the `.part` file so an
/// interrupted pull resumes the rest. Serialized as `magic | total | chunk_size | bitset`; a
/// mismatch on `total`/`chunk_size` (e.g. the URL now points at a different file) is treated as no
/// progress rather than trusted blindly.
struct Resume {
    path: PathBuf,
    total: u64,
    chunk_size: u64,
    done: Vec<bool>,
}

impl Resume {
    fn fresh(path: &Path, total: u64, chunk_size: u64, nchunks: usize) -> Self {
        Self {
            path: path.to_path_buf(),
            total,
            chunk_size,
            done: vec![false; nchunks],
        }
    }

    fn load(path: &Path, total: u64, chunk_size: u64, nchunks: usize) -> Self {
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(_) => return Self::fresh(path, total, chunk_size, nchunks),
        };
        if bytes.len() < 20 || &bytes[0..4] != SIDECAR_MAGIC {
            return Self::fresh(path, total, chunk_size, nchunks);
        }
        let saved_total = u64::from_le_bytes(bytes[4..12].try_into().unwrap());
        let saved_chunk = u64::from_le_bytes(bytes[12..20].try_into().unwrap());
        if saved_total != total || saved_chunk != chunk_size {
            return Self::fresh(path, total, chunk_size, nchunks);
        }
        let bitset = &bytes[20..];
        let mut done = vec![false; nchunks];
        for (i, slot) in done.iter_mut().enumerate() {
            let (byte, bit) = (i / 8, i % 8);
            if byte < bitset.len() && (bitset[byte] >> bit) & 1 == 1 {
                *slot = true;
            }
        }
        Self {
            path: path.to_path_buf(),
            total,
            chunk_size,
            done,
        }
    }

    fn mark(&mut self, idx: usize) {
        if let Some(slot) = self.done.get_mut(idx) {
            *slot = true;
        }
    }

    /// Bytes covered by the chunks marked done (the last chunk may be short).
    fn completed_bytes(&self) -> u64 {
        let mut sum = 0u64;
        for (i, &d) in self.done.iter().enumerate() {
            if d {
                let start = i as u64 * self.chunk_size;
                let end = ((i as u64 + 1) * self.chunk_size).min(self.total);
                sum += end - start;
            }
        }
        sum
    }

    /// Persist the bitset atomically (write-then-rename) so a crash mid-write can't leave a torn
    /// sidecar that a later run would misread.
    fn save(&self) -> std::io::Result<()> {
        let nchunks = self.done.len();
        let mut buf = Vec::with_capacity(20 + nchunks.div_ceil(8));
        buf.extend_from_slice(SIDECAR_MAGIC);
        buf.extend_from_slice(&self.total.to_le_bytes());
        buf.extend_from_slice(&self.chunk_size.to_le_bytes());
        let mut bitset = vec![0u8; nchunks.div_ceil(8)];
        for (i, &d) in self.done.iter().enumerate() {
            if d {
                bitset[i / 8] |= 1 << (i % 8);
            }
        }
        buf.extend_from_slice(&bitset);
        let mut tmp = self.path.as_os_str().to_owned();
        tmp.push(".tmp");
        let tmp = PathBuf::from(tmp);
        std::fs::write(&tmp, &buf)?;
        std::fs::rename(&tmp, &self.path)
    }
}

/// Total size from a 206's `Content-Range` (`bytes 0-0/12345` → `12345`); `None` if the header is
/// absent or the length is unknown (`*`).
fn content_range_total(resp: &ureq::Response) -> Option<u64> {
    resp.header("Content-Range")?
        .rsplit('/')
        .next()?
        .trim()
        .parse()
        .ok()
}

/// `Content-Length` as a number, when present.
fn content_length(resp: &ureq::Response) -> Option<u64> {
    resp.header("Content-Length")
        .and_then(|v| v.trim().parse().ok())
}

/// Statuses worth retrying: 429 (rate limited) and 503 (temporarily unavailable) — both are
/// "back off and try again" per HTTP semantics and are what HF / a CDN emit under load.
fn is_retryable_status(code: u16) -> bool {
    matches!(code, 429 | 503)
}

/// Cap so a hostile/huge `Retry-After` or a deep exponential step can't stall a download.
const MAX_BACKOFF: Duration = Duration::from_secs(30);

/// Delay before the next attempt: honor the server's `Retry-After` (seconds) when present, else
/// exponential `base · 2^attempt`. Capped at [`MAX_BACKOFF`]. `attempt` is 0-based (0 = first retry).
fn backoff_delay(attempt: u32, retry_after_secs: Option<u64>, base: Duration) -> Duration {
    if let Some(secs) = retry_after_secs {
        return Duration::from_secs(secs).min(MAX_BACKOFF);
    }
    let factor = 2u32.saturating_pow(attempt);
    base.saturating_mul(factor).min(MAX_BACKOFF)
}

impl Fetcher for HttpFetcher {
    fn fetch_to_file(
        &self,
        url: &str,
        dest: &Path,
        progress: &mut ProgressFn<'_>,
    ) -> anyhow::Result<()> {
        std::fs::create_dir_all(dest.parent().unwrap_or(Path::new(".")))?;
        // A single ranged probe answers three things at once: does the server honor `Range` (206),
        // what is the total size (`Content-Range`), and — after redirects — what is the final URL?
        // A 200 means it ignored the range, and the body is already the whole file, so stream it
        // rather than waste it.
        let probe = self.get(url, Some((0, 0)))?;
        // Deliberately NOT pinned to `probe.get_url()`. See the module docs: a Xet-backed HF repo
        // signs the probed byte range into the CDN link's policy, so reusing it 403s on every
        // chunk. Each request below re-resolves `url` and follows the redirect itself.
        match probe.status() {
            206 => {
                let total = content_range_total(&probe).ok_or_else(|| {
                    anyhow::anyhow!("206 without a usable Content-Range for {url:?}")
                })?;
                drop(probe); // discard the 1-byte probe body
                if total == 0 {
                    std::fs::File::create(dest)?;
                    progress(0, Some(0));
                    return Ok(());
                }
                if total <= self.chunk_size {
                    // One chunk's worth: the parallel machinery buys nothing, so stream it.
                    let resp = self.get(url, None)?;
                    let total = content_length(&resp).or(Some(total));
                    return self.stream_whole(resp, dest, total, progress);
                }
                self.download_parallel(url, dest, total, progress)
            }
            200 => {
                let total = content_length(&probe);
                self.stream_whole(probe, dest, total, progress)
            }
            code => anyhow::bail!("unexpected status {code} fetching {url:?}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::{Arc, Mutex};

    /// Every `(start, end)` byte range the test server was asked for, shared with the caller.
    type RecordedRanges = Arc<Mutex<Vec<(u64, u64)>>>;

    fn tmp_path(tag: &str) -> PathBuf {
        let mut p = std::env::temp_dir();
        p.push(format!(
            "knaif_fetch_{}_{}_{}",
            tag,
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_file(&p);
        let _ = std::fs::remove_file(resume_sidecar_path(&p));
        p
    }
    static NEXT: AtomicU64 = AtomicU64::new(0);

    /// Read one HTTP request off the stream, returning the raw header bytes (up to the blank line).
    fn read_request(stream: &mut std::net::TcpStream) -> Vec<u8> {
        let mut req = Vec::new();
        let mut buf = [0u8; 512];
        loop {
            match stream.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    req.extend_from_slice(&buf[..n]);
                    if req.windows(4).any(|w| w == b"\r\n\r\n") {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
        req
    }

    /// Minimal one-shot HTTP/1.1 server: accepts one connection, replies with `status_line` +
    /// `body`, then closes. Returns the URL to GET. No external network, fully deterministic.
    fn serve_once(status_line: &'static str, body: Vec<u8>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let _ = read_request(&mut stream);
                let header = format!(
                    "HTTP/1.1 {status_line}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(header.as_bytes());
                let _ = stream.write_all(&body);
            }
        });
        format!("http://127.0.0.1:{port}/model.gguf")
    }

    /// Like [`serve_once`] but answers `responses.len()` sequential connections in order — so a
    /// client that retries (a fresh connection each time, `Connection: close`) sees the scripted
    /// sequence (e.g. 429 then 200).
    fn serve_sequence(responses: Vec<(&'static str, Vec<u8>)>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            for (status_line, body) in responses {
                let Ok((mut stream, _)) = listener.accept() else {
                    break;
                };
                let _ = read_request(&mut stream);
                let header = format!(
                    "HTTP/1.1 {status_line}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(header.as_bytes());
                let _ = stream.write_all(&body);
            }
        });
        format!("http://127.0.0.1:{port}/model.gguf")
    }

    /// A range-aware server backed by `body`: answers `Range: bytes=s-e` with a 206 slice (and a
    /// `Content-Range` carrying the total), or a full 200 when no range is asked. Serves many
    /// concurrent connections (one handler thread each) so the parallel fetcher's fan-out is met.
    /// Records every requested `(start, end)` for assertions.
    fn serve_ranges(body: Vec<u8>) -> (String, RecordedRanges) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let body = Arc::new(body);
        let ranges: RecordedRanges = Arc::new(Mutex::new(Vec::new()));
        let ranges_srv = Arc::clone(&ranges);
        std::thread::spawn(move || {
            for conn in listener.incoming() {
                let Ok(mut stream) = conn else { break };
                let body = Arc::clone(&body);
                let ranges = Arc::clone(&ranges_srv);
                std::thread::spawn(move || {
                    let req = read_request(&mut stream);
                    let text = String::from_utf8_lossy(&req);
                    let range = text.lines().find_map(parse_range_header);
                    let total = body.len() as u64;
                    match range {
                        Some((s, e)) => {
                            ranges.lock().unwrap().push((s, e));
                            let slice = &body[s as usize..=e as usize];
                            let header = format!(
                                "HTTP/1.1 206 Partial Content\r\nContent-Range: bytes {s}-{e}/{total}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                                slice.len()
                            );
                            let _ = stream.write_all(header.as_bytes());
                            let _ = stream.write_all(slice);
                        }
                        None => {
                            let header = format!(
                                "HTTP/1.1 200 OK\r\nContent-Length: {total}\r\nConnection: close\r\n\r\n"
                            );
                            let _ = stream.write_all(header.as_bytes());
                            let _ = stream.write_all(&body);
                        }
                    }
                });
            }
        });
        (format!("http://127.0.0.1:{port}/model.gguf"), ranges)
    }

    /// A Hugging Face **Xet**-shaped server, and the only one here that can catch the pinning bug.
    ///
    /// `/resolve/...` 302s to a CDN link whose signature encodes *the byte range that request asked
    /// for*; the CDN then answers **403** to any other range, and to a full GET. That is what
    /// `us.aws.cdn.hf.co` does — its policy carries a `ByteRange` condition — and it is why
    /// `models pull` failed in the field against a repo migrated to Xet.
    ///
    /// [`serve_ranges`] structurally cannot catch it: it honors every range on one URL, which is
    /// precisely the assumption Xet violates. Counts refusals so the test can assert **zero**,
    /// rather than merely that the download somehow completed.
    fn serve_range_bound_redirect(body: Vec<u8>) -> (String, Arc<AtomicUsize>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let body = Arc::new(body);
        let refusals = Arc::new(AtomicUsize::new(0));
        let refusals_srv = Arc::clone(&refusals);
        std::thread::spawn(move || {
            for conn in listener.incoming() {
                let Ok(mut stream) = conn else { break };
                let body = Arc::clone(&body);
                let refusals = Arc::clone(&refusals_srv);
                std::thread::spawn(move || {
                    let req = read_request(&mut stream);
                    let text = String::from_utf8_lossy(&req);
                    let path = text
                        .lines()
                        .next()
                        .and_then(|l| l.split_whitespace().nth(1))
                        .unwrap_or("/")
                        .to_string();
                    let range = text.lines().find_map(parse_range_header);
                    let total = body.len() as u64;
                    let asked = match range {
                        Some((s, e)) => format!("{s}-{e}"),
                        None => "full".to_string(),
                    };

                    // The signing step: hand back a link valid ONLY for the range just requested.
                    if path.starts_with("/resolve") {
                        let header = format!(
                            "HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{port}/cdn/{asked}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        );
                        let _ = stream.write_all(header.as_bytes());
                        return;
                    }

                    if path.trim_start_matches("/cdn/") != asked {
                        refusals.fetch_add(1, Ordering::SeqCst);
                        let _ = stream.write_all(
                            b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                        );
                        return;
                    }

                    match range {
                        Some((s, e)) => {
                            let slice = &body[s as usize..=e as usize];
                            let header = format!(
                                "HTTP/1.1 206 Partial Content\r\nContent-Range: bytes {s}-{e}/{total}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                                slice.len()
                            );
                            let _ = stream.write_all(header.as_bytes());
                            let _ = stream.write_all(slice);
                        }
                        None => {
                            let header = format!(
                                "HTTP/1.1 200 OK\r\nContent-Length: {total}\r\nConnection: close\r\n\r\n"
                            );
                            let _ = stream.write_all(header.as_bytes());
                            let _ = stream.write_all(&body);
                        }
                    }
                });
            }
        });
        (
            format!("http://127.0.0.1:{port}/resolve/model.gguf"),
            refusals,
        )
    }

    fn parse_range_header(line: &str) -> Option<(u64, u64)> {
        let rest = line
            .strip_prefix("Range:")
            .or_else(|| line.strip_prefix("range:"))?;
        let spec = rest.trim().strip_prefix("bytes=")?;
        let (s, e) = spec.split_once('-')?;
        Some((s.trim().parse().ok()?, e.trim().parse().ok()?))
    }

    #[test]
    fn a_cdn_that_binds_its_signature_to_the_probed_range_still_downloads() {
        // The regression this file exists for. HF migrated `blackdeep/knaif` to Xet storage, whose
        // signed CDN links carry a `ByteRange` policy condition. The fetcher used to pin the URL it
        // got back from the `bytes=0-0` probe and reuse it for every chunk, so the first chunk GET
        // was refused with 403 and `knaif models pull` could not download a model at all.
        //
        // Multi-chunk on purpose: a single-chunk body would take the `stream_whole` path and miss
        // the parallel one, and both used the pinned URL.
        let body: Vec<u8> = (0..160u32).map(|i| (i % 251) as u8).collect();
        let (url, refusals) = serve_range_bound_redirect(body.clone());
        let dest = tmp_path("xet_range_bound");

        HttpFetcher::new()
            .with_backoff(0, Duration::from_millis(1))
            .with_chunk_size(32) // 160 / 32 = 5 chunks
            .with_parallelism(2)
            .fetch_to_file(&url, &dest, &mut |_, _| {})
            .unwrap();

        assert_eq!(
            std::fs::read(&dest).unwrap(),
            body,
            "every chunk must land at its own offset"
        );
        assert_eq!(
            refusals.load(Ordering::SeqCst),
            0,
            "the CDN refused a request, which means a signed link was reused for a range it was \
             not issued for — re-resolve the original URL per request instead of pinning"
        );
        let _ = std::fs::remove_file(&dest);
        let _ = std::fs::remove_file(resume_sidecar_path(&dest));
    }

    #[test]
    fn retryable_statuses() {
        assert!(is_retryable_status(429));
        assert!(is_retryable_status(503));
        assert!(!is_retryable_status(200));
        assert!(!is_retryable_status(404));
        assert!(!is_retryable_status(500));
    }

    #[test]
    fn backoff_is_exponential_and_honors_retry_after() {
        let base = Duration::from_secs(1);
        assert_eq!(backoff_delay(0, None, base), Duration::from_secs(1));
        assert_eq!(backoff_delay(1, None, base), Duration::from_secs(2));
        assert_eq!(backoff_delay(2, None, base), Duration::from_secs(4));
        assert_eq!(backoff_delay(0, Some(7), base), Duration::from_secs(7));
        assert_eq!(backoff_delay(20, None, base), MAX_BACKOFF);
        assert_eq!(backoff_delay(0, Some(9999), base), MAX_BACKOFF);
    }

    #[test]
    fn retries_on_429_then_succeeds() {
        // Probe (bytes=0-0) is the first request; the server ignores the range and answers 200, so
        // the fetcher falls back to a single-stream download of the whole body.
        let body = b"gguf-after-retry".to_vec();
        let url = serve_sequence(vec![
            ("429 Too Many Requests", b"slow down".to_vec()),
            ("200 OK", body.clone()),
        ]);
        let dest = tmp_path("retry200");
        HttpFetcher::new()
            .with_backoff(3, Duration::from_millis(1))
            .fetch_to_file(&url, &dest, &mut |_, _| {})
            .unwrap();
        assert_eq!(std::fs::read(&dest).unwrap(), body);
        let _ = std::fs::remove_file(&dest);
    }

    #[test]
    fn gives_up_after_max_retries_and_surfaces_429() {
        let url = serve_sequence(vec![
            ("429 Too Many Requests", b"no".to_vec()),
            ("429 Too Many Requests", b"no".to_vec()),
        ]);
        let dest = tmp_path("retry429");
        let err = HttpFetcher::new()
            .with_backoff(1, Duration::from_millis(1))
            .fetch_to_file(&url, &dest, &mut |_, _| {})
            .unwrap_err();
        assert!(
            err.to_string().contains("429"),
            "exhausted-retry error should name the status: {err}"
        );
    }

    #[test]
    fn streams_full_body_when_server_ignores_range() {
        let body = b"the-gguf-bytes".to_vec();
        let url = serve_once("200 OK", body.clone());
        let dest = tmp_path("whole");
        let mut last: (u64, Option<u64>) = (0, None);
        HttpFetcher::new()
            .fetch_to_file(&url, &dest, &mut |done, total| last = (done, total))
            .unwrap();
        assert_eq!(std::fs::read(&dest).unwrap(), body);
        assert_eq!(last, (body.len() as u64, Some(body.len() as u64)));
        let _ = std::fs::remove_file(&dest);
    }

    #[test]
    fn errors_on_http_error_status() {
        let url = serve_once("404 Not Found", b"nope".to_vec());
        let dest = tmp_path("404");
        let err = HttpFetcher::new()
            .fetch_to_file(&url, &dest, &mut |_, _| {})
            .unwrap_err();
        let msg = err.to_string().to_lowercase();
        assert!(
            msg.contains("404") || msg.contains("status"),
            "unhelpful: {msg:?}"
        );
    }

    #[test]
    fn parallel_download_assembles_full_file_and_clears_sidecar() {
        // 25 bytes over a 4-byte grid → 7 chunks pulled by several workers, out of order.
        let body: Vec<u8> = (0..25u8).collect();
        let (url, _ranges) = serve_ranges(body.clone());
        let dest = tmp_path("parallel");
        let mut last = (0u64, None);
        HttpFetcher::new()
            .with_chunk_size(4)
            .with_parallelism(4)
            .fetch_to_file(&url, &dest, &mut |d, t| last = (d, t))
            .unwrap();
        assert_eq!(
            std::fs::read(&dest).unwrap(),
            body,
            "reassembled file must match"
        );
        assert_eq!(
            last,
            (25, Some(25)),
            "final progress accounts for every byte"
        );
        assert!(
            !resume_sidecar_path(&dest).exists(),
            "a completed download removes its resume sidecar"
        );
        let _ = std::fs::remove_file(&dest);
    }

    #[test]
    fn resume_skips_already_completed_chunks() {
        // Pre-stage a partial `.part`: full length, chunks 0 and 1 (bytes 0..8) already written and
        // marked done in the sidecar. The resumed pull must fetch only chunks 2..=6.
        let body: Vec<u8> = (0..25u8).collect();
        let dest = tmp_path("resume");
        {
            let f = OpenOptions::new()
                .create(true)
                .truncate(true)
                .write(true)
                .open(&dest)
                .unwrap();
            f.set_len(25).unwrap();
        }
        // Write the "already downloaded" prefix (chunks 0,1 = bytes 0..8).
        {
            use std::io::Write as _;
            let mut f = OpenOptions::new().write(true).open(&dest).unwrap();
            f.write_all(&body[0..8]).unwrap();
        }
        let mut resume = Resume::fresh(&resume_sidecar_path(&dest), 25, 4, 7);
        resume.mark(0);
        resume.mark(1);
        resume.save().unwrap();

        let (url, ranges) = serve_ranges(body.clone());
        HttpFetcher::new()
            .with_chunk_size(4)
            .with_parallelism(4)
            .fetch_to_file(&url, &dest, &mut |_, _| {})
            .unwrap();

        assert_eq!(
            std::fs::read(&dest).unwrap(),
            body,
            "resumed file must match"
        );
        let requested = ranges.lock().unwrap().clone();
        // The (0,0) probe that discovers range support + total is expected; only real chunk
        // fetches (length > 1) are held to "don't re-fetch what we already have".
        let chunk_fetches: Vec<(u64, u64)> =
            requested.iter().copied().filter(|&(s, e)| e > s).collect();
        assert!(
            chunk_fetches.iter().all(|&(s, _)| s >= 8),
            "resume must not re-fetch bytes it already had; got {chunk_fetches:?}"
        );
        assert!(
            chunk_fetches.iter().any(|&(s, _)| s == 8),
            "resume must fetch the first missing chunk; got {chunk_fetches:?}"
        );
        let _ = std::fs::remove_file(&dest);
    }

    #[test]
    fn resume_sidecar_roundtrips_completed_chunks() {
        let dest = tmp_path("sidecar");
        let sidecar = resume_sidecar_path(&dest);
        let mut r = Resume::fresh(&sidecar, 25, 4, 7);
        r.mark(0);
        r.mark(3);
        r.mark(6);
        r.save().unwrap();

        let loaded = Resume::load(&sidecar, 25, 4, 7);
        assert_eq!(
            loaded.done,
            vec![true, false, false, true, false, false, true]
        );
        // chunks 0 (4B) + 3 (4B) + 6 (last, 1B) = 9 bytes.
        assert_eq!(loaded.completed_bytes(), 9);

        // A different total (URL now points at another file) invalidates the sidecar.
        let stale = Resume::load(&sidecar, 999, 4, 250);
        assert!(
            stale.done.iter().all(|&d| !d),
            "mismatched total must reset progress"
        );
        let _ = std::fs::remove_file(&sidecar);
    }
}
