//! Daemon mode: keep the model loaded between `knaif run` invocations.
//!
//! The daemon is an ordinary `knaif` process (`knaif daemon serve`, started by `daemon start` or
//! `run --daemon`) that owns one [`LlmBackend`] and answers `generate` requests on a loopback TCP
//! socket. Only the *inference* crosses the process boundary — the CLI still builds the prompt and
//! validates, repairs, gates and executes the plan itself — so a plan produced through the daemon is
//! the plan the same model would have produced in-process, by construction.
//!
//! **Who may talk to it, and to whom.** The socket is bound to `127.0.0.1` on an OS-chosen port,
//! which any local user can reach, so a connection proves nothing by itself. Both ends prove they
//! hold the random token in `~/.knaif/daemon.json` (readable by the owner only) *without sending
//! it*: the client opens with a nonce, the server answers with a proof of the token over both
//! nonces, and only a client that has verified that proof sends its request, carrying its own proof.
//! Another user can neither use the daemon nor impersonate it (a process that grabs the port after
//! the daemon dies learns nothing — no token, no prompt).
//!
//! **Failure is a fallback, never an error.** A missing, stale, crashed, mismatched (model, build,
//! generation settings) or unreachable daemon, or one that dies mid-request, makes the caller load
//! the model itself, exactly as before the daemon existed. A daemon that answers but *refuses* (the
//! model itself failed) is a real error and is reported, not retried locally.

use std::cell::RefCell;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use knaif_llm::LlmBackend;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};

/// How long an idle daemon keeps the model in memory before shutting itself down.
pub const DEFAULT_IDLE_MINUTES: u64 = 10;

/// Settings that change what the model produces. A client that has overridden any of them must not
/// borrow a daemon loaded without the override, or "no plan may change" would be untrue.
const GENERATION_ENV: &[&str] = &[
    "KNAIF_MAX_TOKENS",
    "KNAIF_N_CTX",
    "KNAIF_N_GPU_LAYERS",
    "KNAIF_N_THREADS",
    "KNAIF_N_THREADS_BATCH",
];

/// A connection must finish its opening exchange within this long, and send no more than the caps.
const HANDSHAKE_DEADLINE: Duration = Duration::from_secs(5);
const MAX_HELLO_BYTES: usize = 4 * 1024;
const MAX_REQUEST_BYTES: usize = 8 * 1024 * 1024;
/// `status` and `stop` are quick; only `generate` may take as long as the model needs.
const CONTROL_TIMEOUT: Duration = Duration::from_secs(10);
/// A start that holds the lifecycle lock this long is presumed dead.
const LOCK_STALE: Duration = Duration::from_secs(150);

/// The daemon's record of itself: where to reach it and the token that opens it.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Info {
    pub pid: u32,
    pub port: u16,
    /// Unique per daemon instance; also the shared secret.
    pub token: String,
    /// The binary's identity (see [`build_id`]).
    pub version: String,
    /// The GGUF the daemon has loaded.
    pub model: String,
    /// What else decides the plans it produces (see [`settings_fingerprint`]).
    #[serde(default)]
    pub settings: String,
    /// Seconds since the Unix epoch.
    pub started: u64,
    pub idle_minutes: u64,
}

/// What `status` reports (the [`Info`] minus the token, plus live counters).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Status {
    pub pid: u32,
    pub version: String,
    pub model: String,
    #[serde(default)]
    pub settings: String,
    pub backend: String,
    pub started: u64,
    pub idle_minutes: u64,
    pub requests: u64,
    pub idle_seconds: u64,
}

/// The daemon answered and said no: the model failed. Distinct from a transport error, which means
/// the daemon is gone and the caller should load the model itself.
#[derive(Debug)]
pub struct Refused(pub String);

impl std::fmt::Display for Refused {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Refused {}

/// A read ran out of time. Means "busy or unresponsive", never "dead": a daemon in the middle of a
/// generation cannot answer a `status` or `stop`, and its record must survive that.
#[derive(Debug)]
pub struct TimedOut;

impl std::fmt::Display for TimedOut {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("timed out waiting for the daemon")
    }
}

impl std::error::Error for TimedOut {}

fn home() -> PathBuf {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

/// The folder holding `daemon.json` and `daemon.log`: `$KNAIF_DAEMON_DIR`, else `~/.knaif` — the
/// user's own profile, never a folder derived from a shared override such as `KNAIF_MODELS_DIR`,
/// because the record holds the secret.
pub fn state_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("KNAIF_DAEMON_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    home().join(".knaif")
}

pub fn info_path(dir: &Path) -> PathBuf {
    dir.join("daemon.json")
}

pub fn log_path(dir: &Path) -> PathBuf {
    dir.join("daemon.log")
}

fn lock_path(dir: &Path) -> PathBuf {
    dir.join("daemon.lock")
}

/// Identifies this exact binary: the version plus the executable's size and modification time. A
/// rebuilt or upgraded `knaif` therefore never borrows a daemon started by the previous one, even
/// when the version number did not change (a development build).
pub fn build_id() -> String {
    let stamp = std::env::current_exe()
        .ok()
        .and_then(|p| std::fs::metadata(p).ok())
        .map(|m| format!("{}-{}", m.len(), mtime_secs(&m)))
        .unwrap_or_default();
    format!("{}+{stamp}", env!("CARGO_PKG_VERSION"))
}

fn mtime_secs(m: &std::fs::Metadata) -> u64 {
    m.modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_secs())
}

/// Everything besides the binary and the generation variables that decides which plans a loaded
/// model produces: the model file's content stamp (a GGUF replaced under the same name), the
/// loadable-backend folder (installing the CUDA payload changes the device inference runs on) and
/// whether prefix reuse is on (which changes plans, see the daemon plan, D2). A daemon whose
/// fingerprint differs from the client's is not borrowed.
pub fn settings_fingerprint(model: &Path) -> String {
    settings_fingerprint_with(
        model,
        &std::env::var("KNAIF_PREFIX_REUSE").unwrap_or_default(),
    )
}

/// [`settings_fingerprint`] with the prefix-reuse setting given, so it can be varied in a test.
pub fn settings_fingerprint_with(model: &Path, reuse: &str) -> String {
    let nanos = |m: &std::fs::Metadata| {
        m.modified()
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map_or(0, |d| d.as_nanos())
    };
    let model_stamp = std::fs::metadata(model)
        .map(|m| format!("{}-{}", m.len(), nanos(&m)))
        .unwrap_or_default();
    // Every library in the backend folder, by name, size and time: overwriting one in place does
    // not touch the folder's own timestamp.
    let backends = knaif_models::backends_dir();
    let mut libs: Vec<String> = std::fs::read_dir(&backends)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| {
            let m = e.metadata().ok()?;
            Some(format!(
                "{}:{}:{}",
                e.file_name().to_string_lossy(),
                m.len(),
                nanos(&m)
            ))
        })
        .collect();
    libs.sort();
    format!(
        "model={model_stamp};backends={}[{}];reuse={reuse}",
        backends.display(),
        libs.join(",")
    )
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// 16 random bytes, hex-encoded.
fn nonce() -> Result<String> {
    random_hex(16)
}

fn random_hex(n: usize) -> Result<String> {
    let mut bytes = vec![0u8; n];
    getrandom::fill(&mut bytes).map_err(|e| anyhow::anyhow!("no secure randomness: {e}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

fn new_token() -> Result<String> {
    random_hex(32)
}

/// `sha256(role | token | client nonce | server nonce)`, hex. `role` keeps a proof made for one
/// direction from being replayed as the other.
fn proof(role: &str, token: &str, client_nonce: &str, server_nonce: &str) -> String {
    let mut h = Sha256::new();
    for part in [role, token, client_nonce, server_nonce] {
        h.update((part.len() as u64).to_le_bytes());
        h.update(part.as_bytes());
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

fn token_matches(given: &str, expected: &str) -> bool {
    // Fold every byte so the comparison time does not depend on where the first difference is.
    given.len() == expected.len()
        && given
            .bytes()
            .zip(expected.bytes())
            .fold(0u8, |acc, (a, b)| acc | (a ^ b))
            == 0
}

/// Write `info` where only the current user can read it. The temporary name is per process and is
/// created exclusively, so a pre-planted file or symlink there is an error rather than a target.
fn write_info(dir: &Path, info: &Info) -> Result<()> {
    ensure_private_dir(dir)?;
    let path = info_path(dir);
    let tmp = dir.join(format!("daemon.json.{}.tmp", std::process::id()));
    let _ = std::fs::remove_file(&tmp);
    let body = serde_json::to_vec_pretty(info)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&tmp)?;
        f.write_all(&body)?;
    }
    #[cfg(not(unix))]
    {
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)?;
        f.write_all(&body)?;
    }
    std::fs::rename(&tmp, &path).with_context(|| format!("writing {}", path.display()))
}

/// Create `dir` if needed, owner-only on Unix. (On Windows the folder inherits its parent's ACL —
/// the user's profile, private by default; the state folder must not be pointed at a shared one.)
fn ensure_private_dir(dir: &Path) -> Result<()> {
    let existed = dir.exists();
    std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if !existed {
            std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))
                .with_context(|| format!("restricting {}", dir.display()))?;
        }
        // The record is trusted as written: a folder others can write to lets them replace it.
        let mode = std::fs::metadata(dir)?.permissions().mode();
        if mode & 0o022 != 0 {
            anyhow::bail!(
                "{} is writable by other users (mode {:o}); the daemon keeps its secret there. \
                 Use a private folder (chmod go-w), or set KNAIF_DAEMON_DIR.",
                dir.display(),
                mode & 0o777
            );
        }
    }
    #[cfg(not(unix))]
    let _ = existed;
    Ok(())
}

pub fn read_info(dir: &Path) -> Option<Info> {
    serde_json::from_slice(&std::fs::read(info_path(dir)).ok()?).ok()
}

/// Remove the record, but only if it still belongs to the daemon holding `token`: a newer daemon
/// may have replaced it while an older connection was failing.
///
/// The record is first moved aside (atomically, so a concurrent writer cannot be deleted between a
/// check and an unlink), then inspected: ours is deleted, anyone else's is put back.
pub fn remove_info_if(dir: &Path, token: &str) {
    let path = info_path(dir);
    let aside = dir.join(format!("daemon.json.{}.gone", std::process::id()));
    if std::fs::rename(&path, &aside).is_err() {
        return;
    }
    let ours = std::fs::read(&aside)
        .ok()
        .and_then(|b| serde_json::from_slice::<Info>(&b).ok())
        .is_some_and(|i| i.token == token);
    if !ours {
        // Someone else's: restore it unless an even newer record has appeared (never clobber it).
        let _ = std::fs::hard_link(&aside, &path);
    }
    let _ = std::fs::remove_file(&aside);
}

/// Whether the environment asks for generation settings a running daemon cannot honour, or turns
/// the daemon off (`KNAIF_NO_DAEMON`).
pub fn disabled_by_env() -> bool {
    let set = |k: &str| std::env::var(k).map(|v| !v.is_empty()).unwrap_or(false);
    set("KNAIF_NO_DAEMON") || GENERATION_ENV.iter().any(|k| set(k))
}

// ---------------------------------------------------------------- wire

/// Read one `\n`-terminated line from `stream`, at most `max` bytes, finishing before `deadline`.
/// A trickle of bytes cannot hold the connection past it, and an endless line cannot exhaust memory.
fn read_line_bounded(
    stream: &mut TcpStream,
    max: usize,
    deadline: Option<Instant>,
) -> Result<String> {
    let mut line: Vec<u8> = Vec::new();
    let mut chunk = [0u8; 8192];
    loop {
        if let Some(d) = deadline {
            let left = d.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err(TimedOut.into());
            }
            stream.set_read_timeout(Some(left))?;
        }
        let n = match stream.read(&mut chunk) {
            Ok(0) => anyhow::bail!("the connection closed before a complete line"),
            Ok(n) => n,
            Err(e)
                if matches!(
                    e.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                return Err(TimedOut.into());
            }
            Err(e) => return Err(e.into()),
        };
        if let Some(pos) = chunk[..n].iter().position(|b| *b == b'\n') {
            line.extend_from_slice(&chunk[..pos]);
            break;
        }
        line.extend_from_slice(&chunk[..n]);
        if line.len() > max {
            anyhow::bail!("line longer than {max} bytes");
        }
    }
    if line.len() > max {
        anyhow::bail!("line longer than {max} bytes");
    }
    Ok(String::from_utf8(line)?)
}

fn write_line(stream: &mut TcpStream, value: &serde_json::Value) -> std::io::Result<()> {
    stream.write_all(value.to_string().as_bytes())?;
    stream.write_all(b"\n")?;
    stream.flush()
}

// ---------------------------------------------------------------- server

/// What [`serve`] needs to know about itself.
pub struct ServeConfig {
    pub dir: PathBuf,
    pub model: String,
    pub version: String,
    pub settings: String,
    pub idle: Duration,
}

/// Bind a loopback port and record the daemon's [`Info`]. Split from [`serve`] so a caller can
/// report readiness (or a bind failure) before it starts blocking.
pub fn bind(cfg: &ServeConfig) -> Result<(TcpListener, Info)> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).context("binding a loopback port")?;
    let info = Info {
        pid: std::process::id(),
        port: listener.local_addr()?.port(),
        token: new_token()?,
        version: cfg.version.clone(),
        model: cfg.model.clone(),
        settings: cfg.settings.clone(),
        started: unix_now(),
        idle_minutes: cfg.idle.as_secs() / 60,
    };
    write_info(&cfg.dir, &info)?;
    Ok((listener, info))
}

/// Answer requests until told to stop or the idle timeout passes. One request at a time: a second
/// client waits in the listener's backlog, which is the queue. Returns with the record still in
/// place: the caller drops the backend (freeing the model) and then calls [`retire`], so "the
/// record is gone" means the model's memory is free.
pub fn serve(
    listener: TcpListener,
    info: &Info,
    cfg: &ServeConfig,
    backend: &dyn LlmBackend,
) -> Result<()> {
    listener.set_nonblocking(true)?;
    let mut requests = 0u64;
    let mut last_activity = Instant::now();
    loop {
        // Checked every pass, not only when nobody is waiting: a steady stream of strangers must
        // not keep the model resident.
        if last_activity.elapsed() >= cfg.idle {
            break;
        }
        match listener.accept() {
            Ok((stream, _)) => {
                let _ = stream.set_nonblocking(false);
                match handle(stream, info, backend, &mut requests, last_activity) {
                    // Only an authenticated client counts as activity: a stranger poking the port
                    // must not keep the model resident.
                    Outcome::Served => last_activity = Instant::now(),
                    Outcome::Stop => break,
                    Outcome::Ignored => {}
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(e) => return Err(e).context("accepting a connection"),
        }
    }
    Ok(())
}

/// Remove this daemon's record (if it is still the current one).
pub fn retire(dir: &Path, info: &Info) {
    remove_info_if(dir, &info.token);
}

enum Outcome {
    Served,
    Stop,
    Ignored,
}

/// Serve one connection.
fn handle(
    mut stream: TcpStream,
    info: &Info,
    backend: &dyn LlmBackend,
    requests: &mut u64,
    last_activity: Instant,
) -> Outcome {
    let deadline = Instant::now() + HANDSHAKE_DEADLINE;
    let outcome = (|| -> Result<Outcome> {
        // 1. The client's nonce, then our proof of the token over both nonces.
        let hello: serde_json::Value = serde_json::from_str(&read_line_bounded(
            &mut stream,
            MAX_HELLO_BYTES,
            Some(deadline),
        )?)?;
        let client_nonce = hello["hello"].as_str().context("no hello")?.to_string();
        if client_nonce.len() > 128 {
            anyhow::bail!("hello nonce too long");
        }
        let server_nonce = nonce()?;
        write_line(
            &mut stream,
            &json!({
                "nonce": server_nonce,
                "proof": proof("srv", &info.token, &client_nonce, &server_nonce),
            }),
        )?;
        // 2. The request, which must carry the client's proof. The size cap bounds memory; the
        // deadline bounds the wait for a client that never finishes its line.
        let req: serde_json::Value = serde_json::from_str(&read_line_bounded(
            &mut stream,
            MAX_REQUEST_BYTES,
            Some(deadline),
        )?)?;
        let given = req["proof"].as_str().unwrap_or("");
        if !token_matches(
            given,
            &proof("cli", &info.token, &client_nonce, &server_nonce),
        ) {
            let _ = write_line(&mut stream, &json!({"ok": false, "error": "bad proof"}));
            return Ok(Outcome::Ignored);
        }
        let _ = stream.set_read_timeout(None);
        let (body, stop) = respond(&req, info, backend, requests, last_activity);
        write_line(&mut stream, &body)?;
        Ok(if stop { Outcome::Stop } else { Outcome::Served })
    })();
    let _ = stream.shutdown(Shutdown::Both);
    outcome.unwrap_or(Outcome::Ignored)
}

fn respond(
    req: &serde_json::Value,
    info: &Info,
    backend: &dyn LlmBackend,
    requests: &mut u64,
    last_activity: Instant,
) -> (serde_json::Value, bool) {
    let text = |k: &str| {
        req.get(k)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    match req.get("op").and_then(|v| v.as_str()) {
        Some("status") => {
            let status = Status {
                pid: info.pid,
                version: info.version.clone(),
                model: info.model.clone(),
                settings: info.settings.clone(),
                backend: backend.name().to_string(),
                started: info.started,
                idle_minutes: info.idle_minutes,
                requests: *requests,
                idle_seconds: last_activity.elapsed().as_secs(),
            };
            (json!({"ok": true, "status": status}), false)
        }
        Some("generate") => {
            *requests += 1;
            match backend.generate_plan(&text("system"), &text("user")) {
                Ok(raw) => (json!({"ok": true, "text": raw}), false),
                Err(e) => (json!({"ok": false, "error": format!("{e:#}")}), false),
            }
        }
        Some("stop") => (json!({"ok": true}), true),
        _ => (json!({"ok": false, "error": "unknown op"}), false),
    }
}

// ---------------------------------------------------------------- client

/// One authenticated request. `Err` is a transport or authentication failure (the daemon is gone,
/// stale, or not the daemon); an answered `{"ok": false}` is returned as `Ok` for the caller to read.
fn call(
    info: &Info,
    mut request: serde_json::Value,
    timeout: Option<Duration>,
) -> Result<serde_json::Value> {
    let mut stream = TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], info.port)),
        Duration::from_secs(2),
    )?;
    let deadline = timeout.map(|t| Instant::now() + t);
    let client_nonce = nonce()?;
    write_line(&mut stream, &json!({ "hello": client_nonce }))?;
    let reply: serde_json::Value = serde_json::from_str(&read_line_bounded(
        &mut stream,
        MAX_HELLO_BYTES,
        deadline.or(Some(Instant::now() + HANDSHAKE_DEADLINE)),
    )?)?;
    let server_nonce = reply["nonce"]
        .as_str()
        .context("no server nonce")?
        .to_string();
    // The server must show it holds the token before it is sent anything.
    if !token_matches(
        reply["proof"].as_str().unwrap_or(""),
        &proof("srv", &info.token, &client_nonce, &server_nonce),
    ) {
        anyhow::bail!("whatever is listening on the daemon's port is not the daemon");
    }
    request["proof"] = json!(proof("cli", &info.token, &client_nonce, &server_nonce));
    write_line(&mut stream, &request)?;
    // `generate` has no deadline: a CPU-only generation legitimately takes minutes. If the daemon
    // dies, the connection closes and the read ends. The handshake left its own read timeout on the
    // socket; clear it, or a generation slower than the handshake deadline would be given up on
    // while the daemon is still working on it.
    stream.set_read_timeout(None)?;
    let line = read_line_bounded(&mut stream, MAX_REQUEST_BYTES, deadline)?;
    Ok(serde_json::from_str(&line)?)
}

/// An [`LlmBackend`] that asks a running daemon.
pub struct RemoteBackend {
    info: Info,
}

impl LlmBackend for RemoteBackend {
    fn generate_plan(&self, system: &str, user: &str) -> Result<String> {
        let reply = call(
            &self.info,
            json!({"op": "generate", "system": system, "user": user}),
            None,
        )?;
        if reply["ok"].as_bool() == Some(true) {
            Ok(reply["text"].as_str().unwrap_or("").to_string())
        } else {
            Err(Refused(
                reply["error"]
                    .as_str()
                    .unwrap_or("the daemon refused the request")
                    .to_string(),
            )
            .into())
        }
    }

    fn name(&self) -> &str {
        "llama.cpp (daemon)"
    }
}

/// A daemon-backed backend that loads the model itself if the daemon dies mid-session. Only a
/// transport failure falls back; the daemon *answering* that the model failed is reported as is.
pub struct Resident {
    remote: RemoteBackend,
    model: PathBuf,
    verbose: bool,
    local: RefCell<Option<Box<dyn LlmBackend>>>,
}

impl LlmBackend for Resident {
    fn generate_plan(&self, system: &str, user: &str) -> Result<String> {
        if self.local.borrow().is_none() {
            match self.remote.generate_plan(system, user) {
                Err(e) if e.downcast_ref::<Refused>().is_none() => {
                    if verbose_trace() {
                        eprintln!("knaif: the daemon went away ({e:#}); loading the model here");
                    }
                    *self.local.borrow_mut() =
                        Some(knaif_llm::backend_for(Some(&self.model), self.verbose)?);
                }
                other => return other,
            }
        }
        match self.local.borrow().as_ref() {
            Some(local) => local.generate_plan(system, user),
            None => unreachable!("the local backend was just set"),
        }
    }

    fn name(&self) -> &str {
        "llama.cpp (daemon)"
    }
}

fn verbose_trace() -> bool {
    std::env::var("KNAIF_DEBUG")
        .map(|v| !v.is_empty())
        .unwrap_or(false)
}

/// Ask the daemon recorded in `dir` how it is. `None` when none answers. A record nobody answers
/// is removed — but only if it is still the same one, so an older failing connection cannot delete
/// a newer daemon's record.
pub fn query(dir: &Path) -> Option<(Info, Status)> {
    match probe(dir, CONTROL_TIMEOUT) {
        Probe::Running(info, status) => Some((*info, *status)),
        Probe::Busy | Probe::Absent => None,
    }
}

/// What a look at the recorded daemon found.
pub enum Probe {
    Running(Box<Info>, Box<Status>),
    /// A daemon is recorded and its port accepts connections, but it did not answer in time —
    /// typically mid-generation. Not dead: its record is left alone.
    Busy,
    /// No record, or nobody (or nobody genuine) answers it; a dead record has been removed.
    Absent,
}

pub fn probe(dir: &Path, timeout: Duration) -> Probe {
    let Some(info) = read_info(dir) else {
        return Probe::Absent;
    };
    match call(&info, json!({"op": "status"}), Some(timeout)) {
        Ok(reply) if reply["ok"].as_bool() == Some(true) => {
            match serde_json::from_value::<Status>(reply["status"].clone()) {
                Ok(status) => Probe::Running(Box::new(info), Box::new(status)),
                Err(_) => Probe::Absent,
            }
        }
        Ok(_) => Probe::Absent,
        Err(e) if e.downcast_ref::<TimedOut>().is_some() => Probe::Busy,
        Err(e) => {
            // Refused or reset: nobody is listening, so the record is stale. An impostor on the
            // port (the daemon crashed and something else bound it) is just as dead to us.
            if verbose_trace() {
                eprintln!("knaif: daemon record is stale: {e:#}");
            }
            remove_info_if(dir, &info.token);
            Probe::Absent
        }
    }
}

/// A backend for `model` if a daemon that can stand in for an in-process load is running: same
/// model, same build, same settings fingerprint, and no generation settings overridden here.
pub fn connect(dir: &Path, model: &Path, version: &str, verbose: bool) -> Option<Resident> {
    if disabled_by_env() {
        return None;
    }
    let (info, status) = query(dir)?;
    if status.version != version
        || !same_file(Path::new(&status.model), model)
        || status.settings != settings_fingerprint(model)
    {
        return None;
    }
    Some(Resident {
        remote: RemoteBackend { info },
        model: model.to_path_buf(),
        verbose,
        local: RefCell::new(None),
    })
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (std::fs::canonicalize(a), std::fs::canonicalize(b)) {
        (Ok(a), Ok(b)) => a == b,
        _ => a == b,
    }
}

/// Ask the daemon to stop and wait until its process has exited (so its files are unlocked).
/// `Ok(false)` when none was running.
pub fn stop(dir: &Path) -> Result<bool> {
    let Some(info) = read_info(dir) else {
        return Ok(false);
    };
    match call(&info, json!({"op": "stop"}), Some(CONTROL_TIMEOUT)) {
        Ok(_) => {}
        // Busy (mid-generation) is not dead: say so rather than delete a live daemon's record.
        Err(e) if e.downcast_ref::<TimedOut>().is_some() => {
            anyhow::bail!(
                "the daemon (pid {}) is busy with a request; try `knaif daemon stop` again in a moment",
                info.pid
            );
        }
        Err(_) => {
            // Refused or not the daemon: the record is stale (a crash, a reboot).
            remove_info_if(dir, &info.token);
            return Ok(false);
        }
    }
    // The daemon removes its record once the model is freed, then exits; wait for both.
    let deadline = Instant::now() + Duration::from_secs(15);
    while read_info(dir).is_some_and(|i| i.token == info.token) {
        if Instant::now() > deadline {
            anyhow::bail!(
                "the daemon (pid {}) did not stop within 15 seconds",
                info.pid
            );
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    // (A daemon served from a thread of this very process — the unit tests — has no process of its own to wait for.)
    if info.pid != std::process::id()
        && !wait_for_exit(info.pid, deadline.saturating_duration_since(Instant::now()))
    {
        anyhow::bail!("the daemon (pid {}) is still running", info.pid);
    }
    Ok(true)
}

#[cfg(windows)]
fn wait_for_exit(pid: u32, timeout: Duration) -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
    };
    // SAFETY: opening a handle to a process by id, waiting on it, and closing it.
    unsafe {
        let handle = OpenProcess(PROCESS_SYNCHRONIZE, 0, pid);
        if handle.is_null() {
            return true; // no such process (any more)
        }
        let ms = u32::try_from(timeout.as_millis()).unwrap_or(u32::MAX);
        let waited = WaitForSingleObject(handle, ms);
        CloseHandle(handle);
        waited == WAIT_OBJECT_0
    }
}

#[cfg(unix)]
fn wait_for_exit(pid: u32, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        // SAFETY: signal 0 only checks that the process exists.
        let alive = unsafe { libc::kill(pid as libc::pid_t, 0) } == 0
            || std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH);
        if !alive {
            return true;
        }
        if Instant::now() > deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// Exclusive right to start a daemon: `daemon.lock`, created atomically and removed on drop. A lock
/// older than [`LOCK_STALE`] belongs to a start that died and is taken over.
struct StartLock {
    path: PathBuf,
    /// What this holder wrote into the file, so it only ever removes its own lock.
    id: String,
}

impl StartLock {
    fn acquire(dir: &Path) -> Result<Self> {
        let path = lock_path(dir);
        let deadline = Instant::now() + Duration::from_secs(150);
        loop {
            match std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
            {
                Ok(mut f) => {
                    let id = format!("{}-{}", std::process::id(), nonce()?);
                    let _ = f.write_all(id.as_bytes());
                    return Ok(Self { path, id });
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    let age = std::fs::metadata(&path)
                        .and_then(|m| m.modified())
                        .ok()
                        .and_then(|t| t.elapsed().ok());
                    if age.is_some_and(|a| a > LOCK_STALE) {
                        // Take over by moving the stale file aside: of several contenders exactly
                        // one rename succeeds, and none can delete a lock another has just made.
                        let tomb = dir.join(format!("daemon.lock.{}.stale", std::process::id()));
                        if std::fs::rename(&path, &tomb).is_ok() {
                            let fresh = std::fs::metadata(&tomb)
                                .and_then(|m| m.modified())
                                .ok()
                                .and_then(|t| t.elapsed().ok())
                                .is_some_and(|a| a <= LOCK_STALE);
                            if fresh {
                                // We moved a live lock (it was replaced between look and move).
                                let _ = std::fs::hard_link(&tomb, &path);
                            }
                            let _ = std::fs::remove_file(&tomb);
                        }
                        continue;
                    }
                    if Instant::now() > deadline {
                        anyhow::bail!("another knaif is starting the daemon; try again shortly");
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                Err(e) => return Err(e).context("taking the daemon start lock"),
            }
        }
    }
}

impl Drop for StartLock {
    fn drop(&mut self) {
        // Only if the file is still ours: after a takeover it belongs to someone else.
        if std::fs::read_to_string(&self.path).is_ok_and(|s| s == self.id) {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

/// Start `knaif daemon serve` for `model` in the background and wait until it answers.
///
/// Starts are serialised, and a daemon already running for the same model, build and settings is
/// left alone; one for a *different* model, build or settings is stopped first (one resident model
/// at a time, and a stale binary must not keep serving).
pub fn start(dir: &Path, model: &Path, idle_minutes: u64, version: &str) -> Result<()> {
    ensure_private_dir(dir)?;
    let _lock = StartLock::acquire(dir)?;
    match probe(dir, CONTROL_TIMEOUT) {
        Probe::Running(_, status) => {
            if status.version == version
                && same_file(Path::new(&status.model), model)
                && status.settings == settings_fingerprint(model)
            {
                return Ok(());
            }
            stop(dir)?;
        }
        Probe::Busy => anyhow::bail!(
            "the daemon is busy with a request; try again in a moment (or `knaif daemon stop`)"
        ),
        Probe::Absent => {}
    }
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path(dir))
        .with_context(|| format!("opening {}", log_path(dir).display()))?;
    let mut cmd = std::process::Command::new(std::env::current_exe()?);
    cmd.args(["daemon", "serve", "--model"])
        .arg(model)
        .args(["--idle-minutes", &idle_minutes.to_string()])
        .env("KNAIF_DAEMON_DIR", dir)
        .stdin(std::process::Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log);
    detach(&mut cmd);
    let mut child = spawn_detached(&mut cmd).context("starting the daemon process")?;
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        if let Some((_, status)) = query(dir) {
            if status.pid == child.id() {
                return Ok(());
            }
        }
        if let Ok(Some(code)) = child.try_wait() {
            anyhow::bail!(
                "the daemon exited during start-up ({code}); see {}",
                log_path(dir).display()
            );
        }
        if Instant::now() > deadline {
            // Do not leave a half-started daemon to publish its record later.
            let _ = child.kill();
            let _ = child.wait();
            anyhow::bail!(
                "the daemon did not come up within 120 seconds; see {}",
                log_path(dir).display()
            );
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// Spawn `cmd`, making sure the child inherits none of this process's own standard handles.
///
/// Windows hands a child every inheritable handle the parent has, whatever its stdio was set to.
/// Without this the daemon kept the *parent's* stdout pipe open, so `knaif run ... | anything` or a
/// terminal host waiting for end-of-output never saw it, for as long as the daemon lived. Each
/// handle's own flags are saved and put back (a handle that was not inheritable stays so), and the
/// window is the spawn call only.
#[cfg(windows)]
fn spawn_detached(cmd: &mut std::process::Command) -> std::io::Result<std::process::Child> {
    use windows_sys::Win32::Foundation::{
        GetHandleInformation, SetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT,
    };
    use windows_sys::Win32::System::Console::{
        GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
    };
    let mut cleared: Vec<(HANDLE, u32)> = Vec::new();
    for id in [STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
        // SAFETY: queries and flag changes on the process's own standard handles.
        unsafe {
            let h = GetStdHandle(id);
            if h.is_null() || h as isize == -1 {
                continue;
            }
            let mut flags = 0u32;
            if GetHandleInformation(h, &mut flags) != 0
                && flags & HANDLE_FLAG_INHERIT != 0
                && SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0) != 0
            {
                cleared.push((h, flags));
            }
        }
    }
    let child = cmd.spawn();
    for (h, flags) in cleared {
        // SAFETY: restoring exactly what was cleared above.
        unsafe { SetHandleInformation(h, HANDLE_FLAG_INHERIT, flags & HANDLE_FLAG_INHERIT) };
    }
    child
}

#[cfg(not(windows))]
fn spawn_detached(cmd: &mut std::process::Command) -> std::io::Result<std::process::Child> {
    cmd.spawn()
}

/// Run the child in its own session / without a console, so closing the terminal that started it
/// does not take the model down with it.
fn detach(cmd: &mut std::process::Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // SAFETY: `setsid` is async-signal-safe and touches no memory of ours.
        unsafe {
            cmd.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
    }
}

/// `4 min`-style rendering of seconds, for `status`.
pub fn human_duration(secs: u64) -> String {
    match secs {
        0..=59 => format!("{secs} s"),
        60..=3599 => format!("{} min", secs / 60),
        _ => format!("{} h {} min", secs / 3600, (secs % 3600) / 60),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use knaif_llm::MockBackend;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "knaif-daemon-test-{tag}-{}-{}",
            std::process::id(),
            unix_now()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// A daemon on a thread answering with `reply`; returns its state dir and the model path it
    /// claims (a real file, so the fingerprint is stable).
    fn spawn(
        reply: &'static str,
        idle: Duration,
        tag: &str,
    ) -> (PathBuf, PathBuf, std::thread::JoinHandle<()>) {
        let dir = temp_dir(tag);
        let model = dir.join("model.gguf");
        std::fs::write(&model, b"weights").unwrap();
        let cfg = ServeConfig {
            dir: dir.clone(),
            model: model.display().to_string(),
            version: "9.9.9".into(),
            settings: settings_fingerprint(&model),
            idle,
        };
        let (listener, info) = bind(&cfg).unwrap();
        let handle = std::thread::spawn(move || {
            serve(listener, &info, &cfg, &MockBackend::new(reply)).unwrap();
            retire(&cfg.dir, &info);
        });
        (dir, model, handle)
    }

    #[test]
    fn a_request_is_answered_by_the_daemons_backend() {
        let (dir, model, handle) = spawn(r#"{"plan": []}"#, Duration::from_secs(60), "answer");
        let backend = connect(&dir, &model, "9.9.9", false).expect("daemon is up");
        assert_eq!(backend.generate_plan("s", "u").unwrap(), r#"{"plan": []}"#);
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
        assert!(read_info(&dir).is_none(), "the record goes with the daemon");
    }

    #[test]
    fn a_client_without_the_token_gets_nothing() {
        let (dir, _model, handle) = spawn("{}", Duration::from_secs(60), "token");
        let mut info = read_info(&dir).unwrap();
        let real = info.token.clone();
        info.token = "not-the-token".into();
        // The client cannot even verify the server, so it never sends a request.
        let err = call(&info, json!({"op": "generate"}), Some(CONTROL_TIMEOUT)).unwrap_err();
        assert!(err.to_string().contains("not the daemon"), "{err:#}");
        info.token = real;
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
    }

    #[test]
    fn an_impostor_on_the_port_never_receives_the_token_or_the_prompt() {
        // Something that is not the daemon listens where the record says the daemon is.
        let fake = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = fake.local_addr().unwrap().port();
        let seen = std::thread::spawn(move || {
            let (mut s, _) = fake.accept().unwrap();
            let mut got = String::new();
            let mut buf = [0u8; 4096];
            // It can answer the hello with anything, but cannot forge the proof.
            let n = s.read(&mut buf).unwrap();
            got.push_str(&String::from_utf8_lossy(&buf[..n]));
            s.write_all(b"{\"nonce\":\"x\",\"proof\":\"forged\"}\n")
                .unwrap();
            // Anything further the client sends would arrive here.
            s.set_read_timeout(Some(Duration::from_millis(500)))
                .unwrap();
            if let Ok(n) = s.read(&mut buf) {
                got.push_str(&String::from_utf8_lossy(&buf[..n]));
            }
            got
        });
        let info = Info {
            pid: 1,
            port,
            token: "the-secret-token".into(),
            version: "9.9.9".into(),
            model: "m".into(),
            settings: String::new(),
            started: 0,
            idle_minutes: 10,
        };
        let backend = RemoteBackend { info };
        assert!(backend
            .generate_plan("SYSTEM PROMPT", "USER REQUEST")
            .is_err());
        let got = seen.join().unwrap();
        assert!(!got.contains("the-secret-token"), "token leaked: {got}");
        assert!(
            !got.contains("USER REQUEST") && !got.contains("SYSTEM PROMPT"),
            "prompt leaked: {got}"
        );
    }

    #[test]
    fn a_different_model_build_or_settings_gets_no_backend() {
        let (dir, model, handle) = spawn("{}", Duration::from_secs(60), "mismatch");
        assert!(connect(&dir, &dir.join("other.gguf"), "9.9.9", false).is_none());
        assert!(connect(&dir, &model, "1.0.0", false).is_none());
        // The model file changed under the same name.
        std::fs::write(&model, b"different weights, different size").unwrap();
        assert!(connect(&dir, &model, "9.9.9", false).is_none());
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
    }

    #[test]
    fn an_idle_daemon_shuts_itself_down() {
        let (dir, _model, handle) = spawn("{}", Duration::from_millis(200), "idle");
        handle.join().unwrap();
        assert!(read_info(&dir).is_none());
    }

    #[test]
    fn a_stranger_poking_the_port_does_not_keep_the_model_resident() {
        let (dir, _model, handle) = spawn("{}", Duration::from_millis(600), "poke");
        let port = read_info(&dir).unwrap().port;
        let started = Instant::now();
        // Traffic that outlasts the idle timeout: the daemon must still go.
        while !handle.is_finished() && started.elapsed() < Duration::from_secs(4) {
            if let Ok(mut s) = TcpStream::connect(("127.0.0.1", port)) {
                let _ = s.write_all(
                    b"{\"hello\":\"x\"}
",
                );
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(handle.is_finished(), "strangers kept the daemon alive");
        handle.join().unwrap();
        assert!(read_info(&dir).is_none());
    }

    #[test]
    fn a_connection_that_never_finishes_its_line_cannot_hold_the_worker() {
        let (dir, model, handle) = spawn("{}", Duration::from_secs(60), "trickle");
        let port = read_info(&dir).unwrap().port;
        // Opens a connection and says nothing; a second, real client must still be served once
        // the stalled one times out (the opening exchange has a deadline).
        let _stalled = TcpStream::connect(("127.0.0.1", port)).unwrap();
        let t = Instant::now();
        let backend = connect(&dir, &model, "9.9.9", false).expect("served after the deadline");
        assert!(backend.generate_plan("s", "u").is_ok());
        assert!(t.elapsed() < HANDSHAKE_DEADLINE + Duration::from_secs(5));
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
    }

    #[test]
    fn an_oversized_opening_line_is_dropped() {
        let (dir, _model, handle) = spawn("{}", Duration::from_secs(60), "oversize");
        let port = read_info(&dir).unwrap().port;
        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        let junk = vec![b'a'; MAX_HELLO_BYTES * 4];
        let _ = s.write_all(&junk);
        let mut buf = [0u8; 64];
        // The server hangs up rather than reading on: a read ends (EOF or reset), no answer.
        let n = s.read(&mut buf).unwrap_or(0);
        assert_eq!(n, 0);
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
    }

    #[test]
    fn a_stale_record_is_removed_and_means_no_daemon() {
        let dir = temp_dir("stale");
        let port = TcpListener::bind(("127.0.0.1", 0))
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        let info = Info {
            pid: 1,
            port,
            token: "t".into(),
            version: "9.9.9".into(),
            model: "m".into(),
            settings: String::new(),
            started: 0,
            idle_minutes: 10,
        };
        write_info(&dir, &info).unwrap();
        assert!(query(&dir).is_none());
        assert!(read_info(&dir).is_none(), "a dead record is cleaned up");
        assert!(!stop(&dir).unwrap());
    }

    #[test]
    fn a_failing_old_connection_cannot_delete_a_newer_daemons_record() {
        let dir = temp_dir("replace");
        let mut info = Info {
            pid: 1,
            port: 1,
            token: "old".into(),
            version: "9.9.9".into(),
            model: "m".into(),
            settings: String::new(),
            started: 0,
            idle_minutes: 10,
        };
        write_info(&dir, &info).unwrap();
        info.token = "new".into();
        write_info(&dir, &info).unwrap();
        remove_info_if(&dir, "old");
        assert_eq!(read_info(&dir).unwrap().token, "new");
        remove_info_if(&dir, "new");
        assert!(read_info(&dir).is_none());
    }

    #[test]
    fn status_reports_the_request_count() {
        let (dir, model, handle) = spawn("{}", Duration::from_secs(60), "status");
        let backend = connect(&dir, &model, "9.9.9", false).unwrap();
        backend.generate_plan("s", "u").unwrap();
        backend.generate_plan("s", "u").unwrap();
        let (_, status) = query(&dir).unwrap();
        assert_eq!(status.requests, 2);
        assert_eq!(status.backend, "mock");
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
    }

    #[test]
    fn a_daemon_that_dies_mid_session_makes_the_caller_load_the_model_itself() {
        let (dir, model, handle) = spawn("{}", Duration::from_secs(60), "dies");
        let backend = connect(&dir, &model, "9.9.9", false).unwrap();
        assert!(backend.generate_plan("s", "u").is_ok());
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
        // The daemon is gone: this must be the local load being attempted (a mock-only build has
        // no llama.cpp to load with, which is exactly the error that proves the fallback ran) —
        // not a transport error surfacing to the user.
        let err = backend.generate_plan("s", "u").unwrap_err();
        if !cfg!(feature = "llama") {
            assert!(format!("{err:#}").contains("llama.cpp"), "{err:#}");
        }
    }

    #[test]
    fn only_one_start_holds_the_lock_at_a_time() {
        let dir = temp_dir("lock");
        let first = StartLock::acquire(&dir).unwrap();
        assert!(lock_path(&dir).exists());
        // A stale lock (a start that died) is taken over.
        let old = std::time::SystemTime::now() - LOCK_STALE - Duration::from_secs(5);
        std::fs::File::options()
            .write(true)
            .open(lock_path(&dir))
            .unwrap()
            .set_modified(old)
            .unwrap();
        let second = StartLock::acquire(&dir).unwrap();
        // The first holder resuming must not delete the lock that now belongs to the second.
        drop(first);
        assert!(lock_path(&dir).exists(), "the successor's lock was deleted");
        drop(second);
        assert!(!lock_path(&dir).exists());
    }

    #[test]
    fn a_slow_generation_is_not_given_up_on_at_the_handshake_deadline() {
        struct Slow;
        impl LlmBackend for Slow {
            fn generate_plan(&self, _: &str, _: &str) -> Result<String> {
                std::thread::sleep(HANDSHAKE_DEADLINE + Duration::from_secs(1));
                Ok("slow answer".into())
            }
            fn name(&self) -> &str {
                "slow"
            }
        }
        let dir = temp_dir("slow");
        let model = dir.join("model.gguf");
        std::fs::write(&model, b"w").unwrap();
        let cfg = ServeConfig {
            dir: dir.clone(),
            model: model.display().to_string(),
            version: "9.9.9".into(),
            settings: settings_fingerprint(&model),
            idle: Duration::from_secs(60),
        };
        let (listener, info) = bind(&cfg).unwrap();
        let handle = std::thread::spawn(move || {
            serve(listener, &info, &cfg, &Slow).unwrap();
            retire(&cfg.dir, &info);
        });
        let backend = connect(&dir, &model, "9.9.9", false).unwrap();
        // Were the handshake's socket timeout still in force this would fail (and, in a build
        // with llama.cpp, silently load a second copy of the model).
        assert_eq!(backend.generate_plan("s", "u").unwrap(), "slow answer");
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
    }

    #[test]
    fn a_busy_daemon_keeps_its_record_and_is_not_started_twice() {
        struct Slow;
        impl LlmBackend for Slow {
            fn generate_plan(&self, _: &str, _: &str) -> Result<String> {
                std::thread::sleep(Duration::from_secs(2));
                Ok("x".into())
            }
            fn name(&self) -> &str {
                "slow"
            }
        }
        let dir = temp_dir("busy");
        let model = dir.join("model.gguf");
        std::fs::write(&model, b"w").unwrap();
        let cfg = ServeConfig {
            dir: dir.clone(),
            model: model.display().to_string(),
            version: "9.9.9".into(),
            settings: settings_fingerprint(&model),
            idle: Duration::from_secs(60),
        };
        let (listener, info) = bind(&cfg).unwrap();
        let handle = std::thread::spawn(move || {
            serve(listener, &info, &cfg, &Slow).unwrap();
            retire(&cfg.dir, &info);
        });
        let info = read_info(&dir).unwrap();
        let worker = std::thread::spawn(move || {
            call(
                &info,
                json!({"op": "generate", "system": "s", "user": "u"}),
                None,
            )
        });
        std::thread::sleep(Duration::from_millis(500)); // the generation is under way
        assert!(matches!(
            probe(&dir, Duration::from_millis(300)),
            Probe::Busy
        ));
        assert!(
            read_info(&dir).is_some(),
            "a busy daemon's record was deleted"
        );
        assert!(worker.join().unwrap().is_ok());
        assert!(stop(&dir).unwrap());
        handle.join().unwrap();
    }

    #[test]
    fn a_record_moved_aside_is_put_back_when_it_is_not_ours() {
        let dir = temp_dir("aside");
        let info = Info {
            pid: 1,
            port: 1,
            token: "theirs".into(),
            version: "v".into(),
            model: "m".into(),
            settings: String::new(),
            started: 0,
            idle_minutes: 10,
        };
        write_info(&dir, &info).unwrap();
        remove_info_if(&dir, "mine");
        assert_eq!(read_info(&dir).unwrap().token, "theirs");
        assert!(!dir
            .join(format!("daemon.json.{}.gone", std::process::id()))
            .exists());
    }

    #[test]
    fn overridden_generation_settings_skip_the_daemon() {
        assert!(GENERATION_ENV.contains(&"KNAIF_MAX_TOKENS"));
        assert!(GENERATION_ENV.contains(&"KNAIF_N_CTX"));
    }

    #[test]
    fn the_fingerprint_changes_with_the_model_file_and_prefix_reuse() {
        let dir = temp_dir("fp");
        let model = dir.join("m.gguf");
        std::fs::write(&model, b"a").unwrap();
        let a = settings_fingerprint_with(&model, "");
        assert_ne!(a, settings_fingerprint_with(&model, "1"));
        std::fs::write(&model, b"bb").unwrap();
        assert_ne!(a, settings_fingerprint_with(&model, ""));
    }

    #[test]
    fn proofs_are_bound_to_direction_token_and_both_nonces() {
        let p = proof("srv", "tok", "c", "s");
        assert_ne!(p, proof("cli", "tok", "c", "s"));
        assert_ne!(p, proof("srv", "tok2", "c", "s"));
        assert_ne!(p, proof("srv", "tok", "c2", "s"));
        assert_ne!(p, proof("srv", "tok", "c", "s2"));
        // Length-prefixed, so field boundaries cannot be shifted.
        assert_ne!(proof("srv", "tok", "cs", ""), proof("srv", "tok", "c", "s"));
    }

    #[test]
    fn token_comparison_needs_equal_text() {
        assert!(token_matches("abc", "abc"));
        assert!(!token_matches("abc", "abd"));
        assert!(!token_matches("abc", "abcd"));
        assert!(!token_matches("", "abc"));
    }

    #[test]
    fn tokens_are_long_and_distinct() {
        let (a, b) = (new_token().unwrap(), new_token().unwrap());
        assert_eq!(a.len(), 64);
        assert_ne!(a, b);
    }

    #[test]
    fn the_state_dir_is_the_profile_not_a_shared_override() {
        // A shared KNAIF_MODELS_DIR must not drag the secret's folder with it.
        let dir = state_dir();
        if std::env::var("KNAIF_DAEMON_DIR").is_err() {
            assert!(dir.ends_with(".knaif"));
        }
    }

    #[test]
    fn durations_read_naturally() {
        assert_eq!(human_duration(5), "5 s");
        assert_eq!(human_duration(120), "2 min");
        assert_eq!(human_duration(3720), "1 h 2 min");
    }
}
