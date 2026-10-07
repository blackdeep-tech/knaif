//! knaif native CLI.
//!
//! Thin wrapper over the engine crates — the binary owns argument parsing and output
//! formatting only; all logic lives in `knaif-core` / `knaif-models` / `knaif-llm` /
//! `knaif-skill-*`. Commands: `--version`, `skills list`, `models`, `plan --json` (parse →
//! validate path), and `run <skill> "<req>"` (safety gate → prompt → inference → plan → expand →
//! dry-run preview or confirmed execution). `--model <gguf>` drives real llama.cpp inference (build
//! `--features llama`); without it the recommended model is auto-selected (see `select_model`),
//! falling back to the mock (offline via `KNAIF_LLM_MOCK_RESPONSE`).

use std::path::{Path, PathBuf};

use anyhow::Context as _;
use clap::{Args, Parser, Subcommand};
use indicatif::{ProgressBar, ProgressStyle};
use knaif_models::{BackendState, BackendStore, CudaOffer, HttpFetcher, ModelStore, VerifyOutcome};

/// `--version` reports the compiled inference backend next to the release number, so a
/// mock-only build is identifiable without loading a 2.5 GB GGUF first. `just parity`
/// preflights on this string: pointed at a mock-only binary it would otherwise score a
/// false 0/N instead of naming the real problem. (`installers/smoke.sh` substring-matches
/// the version, so the suffix is safe to append.)
#[cfg(feature = "llama")]
const VERSION: &str = concat!(env!("CARGO_PKG_VERSION"), " (inference: llama.cpp)");
#[cfg(not(feature = "llama"))]
const VERSION: &str = concat!(
    env!("CARGO_PKG_VERSION"),
    " (inference: mock only - this build has no llama.cpp backend)"
);

#[derive(Parser)]
#[command(
    name = "knaif",
    version = VERSION,
    about = "knaif — natural-language command agent (native)"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Inspect available skills.
    Skills {
        #[command(subcommand)]
        action: SkillsAction,
    },
    /// Manage local GGUF models in the shared store.
    Models {
        #[command(subcommand)]
        action: ModelsAction,
    },
    /// Manage optional GPU backend payloads (CUDA) in ~/.knaif/backends.
    Backend {
        #[command(subcommand)]
        action: BackendAction,
    },
    /// Keep the model loaded between runs, so repeat requests skip the load.
    Daemon {
        #[command(subcommand)]
        action: DaemonAction,
    },
    /// Produce a validated plan envelope for a request (JSON to stdout).
    Plan(PlanArgs),
    /// Render (dry-run) or execute a skill workflow from a natural-language request.
    Run(RunArgs),
}

#[derive(Subcommand)]
enum SkillsAction {
    /// List skills discovered from the bundle `skill.yaml` files and their native status.
    List {
        /// Include stale skills (partial / under rebuild), hidden by default.
        #[arg(long)]
        include_stale: bool,
    },
    /// Report each skill's declared external tools and whether they're installed (a "doctor"
    /// check). Names one skill, or all when omitted. Detection only — never touches PATH.
    Deps {
        /// A single skill to check; all active skills when omitted.
        name: Option<String>,
        /// Include stale skills (partial / under rebuild), hidden by default.
        #[arg(long)]
        include_stale: bool,
    },
}

#[derive(Subcommand)]
enum ModelsAction {
    /// List models (installed + available from the manifest).
    List,
    /// Verify an installed model's SHA-256 against the manifest.
    Verify { name: String },
    /// Download a model from its manifest URL into the store (verifies checksum).
    Pull { name: String },
    /// Re-download any installed model whose bytes no longer match the manifest checksum.
    Update,
    /// Remove a model (`--all` clears the whole store).
    Rm {
        name: Option<String>,
        #[arg(long)]
        all: bool,
    },
}

#[derive(Subcommand)]
enum BackendAction {
    /// List the optional GPU backend payloads and whether they're installed here.
    List {
        /// Emit JSON: the compiled feature set, the backends directory and the payload states.
        /// Always answers, even on a build with no backend manifest.
        #[arg(long)]
        json: bool,
    },
    /// Download a backend payload and install it where the runtime scans for it.
    Install { name: String },
    /// Verify an installed payload's files against the manifest checksums.
    Verify { name: String },
    /// Remove an installed backend payload (falls back to CPU/Vulkan).
    Remove { name: String },
}

#[derive(Subcommand)]
enum DaemonAction {
    /// Load the model in a background process that later runs reuse.
    Start {
        /// Model to keep loaded: an installed/manifest NAME or a GGUF file PATH. Without it, the
        /// recommended model is used when installed.
        #[arg(long, value_name = "NAME|PATH")]
        model: Option<String>,
        /// Shut down after this many idle minutes, freeing the model's memory.
        #[arg(long, value_name = "MINUTES", default_value_t = daemon::DEFAULT_IDLE_MINUTES)]
        idle_minutes: u64,
    },
    /// Stop the daemon and free its memory.
    Stop,
    /// Show whether a daemon is running and what it has loaded.
    Status {
        /// Emit JSON.
        #[arg(long)]
        json: bool,
    },
    /// The daemon process itself. Started by `daemon start` and `run --daemon`; not for direct use.
    #[command(hide = true)]
    Serve {
        #[arg(long)]
        model: PathBuf,
        #[arg(long, default_value_t = daemon::DEFAULT_IDLE_MINUTES)]
        idle_minutes: u64,
    },
}

#[derive(Args)]
struct PlanArgs {
    /// Skill to plan against.
    #[arg(long)]
    skill: String,
    /// Emit the plan envelope as JSON (currently the only supported format).
    #[arg(long)]
    json: bool,
    /// Model for real inference: an installed/manifest NAME or a GGUF file PATH (needs a build
    /// with `--features llama`). Without it, an installed recommended model is auto-selected,
    /// else the offline mock runs. Mirrors `run --model`; used by the parity harness.
    #[arg(long, value_name = "NAME|PATH", overrides_with = "model")]
    model: Option<String>,
    /// Show llama.cpp's native trace on stderr (quiet by default).
    #[arg(long)]
    verbose: bool,
    /// Download the recommended model without asking when no `--model` is given and none is
    /// installed. `plan` never prompts, so this is the only way it will auto-download; ignored
    /// with `--batch`/`--json`, which never download.
    #[arg(long)]
    yes: bool,
    /// Batch mode: read one utterance per line from this file, load the model ONCE, and emit one
    /// JSON plan envelope per line in the same order. Avoids the per-utterance model reload that
    /// dominates large parity runs. A per-line inference error emits `{"plan":[],"error":…}` and
    /// continues, so output stays line-aligned with the input.
    #[arg(long, value_name = "FILE")]
    batch: Option<PathBuf>,
    /// Natural-language request (optional in this skeleton).
    utterance: Vec<String>,
}

#[derive(Args)]
struct RunArgs {
    /// Skill to run (`ffmpeg` or `documents`).
    skill: String,
    /// Preview the command(s) without executing anything.
    #[arg(long)]
    dry_run: bool,
    /// Restrict input/output paths to this directory (open/CLI mode when omitted).
    #[arg(long)]
    sandbox: Option<PathBuf>,
    /// Download the recommended model without prompting when none is given or installed.
    /// Steps already run without asking (see `--confirm`), so this no longer changes them; it is
    /// kept so existing scripts and `--yes` habits keep working. It never approves replacing a
    /// file: that is `--overwrite`.
    #[arg(long)]
    yes: bool,
    /// Ask `Proceed? [Y/n]` before each step instead of acting straight away (Enter approves).
    /// Without a terminal there is nobody to ask, so combine it with `--yes` or leave it off.
    #[arg(long)]
    confirm: bool,
    /// Replace a file that already exists. Without it, a step whose output exists asks
    /// `Replace <file>? [y/N]` (Enter keeps the file), and without a terminal it stops.
    #[arg(long)]
    overwrite: bool,
    /// Model for real inference: an installed/manifest NAME (e.g. `knaif-qwen3-4b-v2`) or a GGUF file
    /// PATH. Needs a build with `--features llama`. Without it, the recommended model is
    /// auto-selected — installed ones silently, a missing one after a download prompt — falling
    /// back to the mock (drive it offline with `KNAIF_LLM_MOCK_RESPONSE`). Last one wins.
    #[arg(long, value_name = "NAME|PATH", overrides_with = "model")]
    model: Option<String>,
    /// Show llama.cpp's native trace (model metadata, tensor load/repack, context construction)
    /// on stderr. Quiet by default.
    #[arg(long)]
    verbose: bool,
    /// Serve this request through the model daemon, starting it first if none is running. Later
    /// runs use the daemon without the flag; stop it with `knaif daemon stop`.
    #[arg(long)]
    daemon: bool,
    /// Natural-language request.
    request: Vec<String>,
}

/// Switch the Windows console to UTF-8 so the non-ASCII we print everywhere — the `—` in the
/// help banner, `✓`/`⚠`, the box borders — renders as itself instead of cp1252 mojibake.
/// Rust always emits UTF-8 bytes; the console decodes them with the active output code page,
/// which is cp1252 on a default install. Best-effort: a redirected or unavailable console
/// simply leaves the call failing, which is fine because a pipe carries the bytes through.
#[cfg(windows)]
fn enable_utf8_console() {
    // SAFETY: an FFI call taking a code page constant and touching no memory we own.
    unsafe { windows_sys::Win32::System::Console::SetConsoleOutputCP(65001) };
}

#[cfg(not(windows))]
fn enable_utf8_console() {}

/// Hold a named mutex for the life of the process so the Windows installer's `AppMutex` can see
/// that knaif is running. Without it, an upgrade started while a `run` is in flight hits a locked
/// `bin\knaif.exe`, silently defers the replacement to the next reboot, and leaves the user on the
/// old build with no indication why. The name must match `AppMutex` in installers/windows/knaif.iss.
/// Session-local (no `Global\`) because the install is per-user.
#[cfg(windows)]
fn hold_app_mutex() {
    let name: Vec<u16> = "knaif-cli-running\0".encode_utf16().collect();
    // SAFETY: an FFI call taking a null security descriptor and a NUL-terminated UTF-16 name.
    // The handle is deliberately never closed — it must live as long as the process, and Windows
    // releases it on exit. Best-effort: a failure here only costs the installer its detection.
    unsafe {
        windows_sys::Win32::System::Threading::CreateMutexW(std::ptr::null(), 0, name.as_ptr());
    }
}

#[cfg(not(windows))]
fn hold_app_mutex() {}

mod daemon;
mod ui;

/// Whether this process is the daemon itself (`knaif daemon serve`). It must NOT hold the
/// installer's `AppMutex`: it is meant to outlive every run, and a resident holder would make setup
/// refuse to start for as long as the model stays loaded. Setup stops it instead (`daemon stop`).
fn is_daemon_process() -> bool {
    let mut args = std::env::args().skip(1);
    args.next().as_deref() == Some("daemon") && args.next().as_deref() == Some("serve")
}

fn main() -> anyhow::Result<()> {
    enable_utf8_console();
    if !is_daemon_process() {
        hold_app_mutex();
    }
    // The wordmark, before clap prints help or a usage error: bare `knaif` and `--help` only, and
    // only in the terminal view.
    ui::init(false);
    if let Some(style) = ui::view() {
        let args: Vec<String> = std::env::args().skip(1).collect();
        if args.is_empty()
            || args
                .iter()
                .any(|a| a == "--help" || a == "-h" || a == "help")
        {
            println!(
                "{}
",
                ui::render_logo(&style)
            );
        }
    }
    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(e) => {
            // Help, `--version` and usage errors: clap's own text, then the same closing empty
            // line as every other command in the terminal view.
            let _ = e.print();
            if ui::view().is_some() {
                println!();
            }
            std::process::exit(e.exit_code());
        }
    };
    let verbose = matches!(&cli.command, Command::Run(a) if a.verbose);
    ui::init(verbose);
    let result = match cli.command {
        Command::Skills { action } => match action {
            SkillsAction::List { include_stale } => cmd_skills_list(include_stale),
            SkillsAction::Deps {
                name,
                include_stale,
            } => cmd_skills_deps(name.as_deref(), include_stale),
        },
        Command::Models { action } => cmd_models(action),
        Command::Backend { action } => cmd_backend(action),
        Command::Daemon { action } => cmd_daemon(action),
        Command::Plan(args) => cmd_plan(args),
        Command::Run(args) => {
            let dry_run = args.dry_run;
            finish_run(cmd_run(args), dry_run)
        }
    };
    end_view(result)
}

/// End every command in the terminal view with one empty line, so the shell prompt does not sit
/// against the output — help and errors included, not only `run`. The plain view returns the
/// result untouched, so a pipe sees exactly what 1.2.0 printed.
fn end_view(result: anyhow::Result<()>) -> anyhow::Result<()> {
    if ui::view().is_none() {
        return result;
    }
    if let Err(e) = result {
        // anyhow's own report from `main`, followed by the empty line it cannot add.
        eprintln!("Error: {e:?}");
        eprintln!();
        std::process::exit(1);
    }
    println!();
    Ok(())
}

/// Close a `run` in the terminal view: the last line of the tree, with the total time (prompt
/// waits excluded). The plain view returns the result untouched, so a pipe sees exactly what
/// 1.2.0 printed, including anyhow's `Error:` report on stderr.
fn finish_run(result: anyhow::Result<()>, dry_run: bool) -> anyhow::Result<()> {
    let Some(style) = ui::view() else {
        return result;
    };
    if !ui::header_shown() {
        return result;
    }
    // `end_view` adds the closing empty line; the stopped branch exits here, so it adds its own.
    match result {
        Ok(()) => {
            if !ui::closed() {
                let summary = if dry_run {
                    "dry run, nothing executed".to_string()
                } else {
                    ui::files_written(ui::written())
                };
                let last = match ui::declined() {
                    Some((step, steps)) => {
                        ui::render_declined_close(&style, step, steps, &summary, ui::total())
                    }
                    None => ui::render_done(&style, &summary, ui::total()),
                };
                println!("{last}");
            }
            Ok(())
        }
        Err(e) => {
            let chain: Vec<String> = e.chain().map(|c| c.to_string()).collect();
            let detail: Vec<String> = if ui::verbose() {
                chain.iter().skip(1).cloned().collect()
            } else {
                // The outermost line says where it stopped; the innermost says why.
                chain
                    .last()
                    .filter(|_| chain.len() > 1)
                    .cloned()
                    .into_iter()
                    .collect()
            };
            println!(
                "{}",
                ui::render_stopped(&style, &chain[0], &detail, ui::total())
            );
            println!();
            std::process::exit(1);
        }
    }
}

/// `knaif daemon ...`.
fn cmd_daemon(action: DaemonAction) -> anyhow::Result<()> {
    let dir = daemon::state_dir();
    match action {
        DaemonAction::Start {
            model,
            idle_minutes,
        } => {
            let path =
                select_model(model.as_deref(), false, DownloadPolicy::Never)?.ok_or_else(|| {
                    anyhow::anyhow!(
                        "no model to load: install one with `knaif models pull <name>` or pass --model"
                    )
                })?;
            ensure_daemon(&dir, &path, idle_minutes)?;
            println!("knaif daemon is running with {}.", model_stem(&path));
            println!("Later runs use it automatically; stop it with `knaif daemon stop`.");
            Ok(())
        }
        DaemonAction::Stop => {
            if daemon::stop(&dir)? {
                println!("knaif daemon stopped.");
            } else {
                println!("knaif daemon is not running.");
            }
            Ok(())
        }
        DaemonAction::Status { json } => {
            let found = daemon::query(&dir);
            if json {
                let body = match &found {
                    Some((_, s)) => serde_json::json!({"running": true, "status": s}),
                    None => serde_json::json!({"running": false}),
                };
                println!("{}", serde_json::to_string_pretty(&body)?);
                return Ok(());
            }
            match found {
                Some((_, s)) => {
                    println!("knaif daemon is running (pid {}).", s.pid);
                    println!("  model:    {}", model_stem(Path::new(&s.model)));
                    println!("  requests: {}", s.requests);
                    println!(
                        "  idle:     {} (stops after {} min)",
                        daemon::human_duration(s.idle_seconds),
                        s.idle_minutes
                    );
                }
                None => println!("knaif daemon is not running."),
            }
            Ok(())
        }
        DaemonAction::Serve {
            model,
            idle_minutes,
        } => serve_daemon(&dir, &model, idle_minutes),
    }
}

/// A model file's name without the folder or `.gguf`, for messages.
fn model_stem(path: &Path) -> String {
    path.file_stem()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.display().to_string())
}

/// Make sure a daemon holding `model` is running, starting one when it is not.
fn ensure_daemon(dir: &Path, model: &Path, idle_minutes: u64) -> anyhow::Result<()> {
    if daemon::connect(dir, model, &daemon::build_id(), false).is_some() {
        return Ok(());
    }
    if daemon::disabled_by_env() {
        anyhow::bail!(
            "the daemon is switched off here: unset KNAIF_NO_DAEMON and the KNAIF_MAX_TOKENS / \
             KNAIF_N_CTX / KNAIF_N_GPU_LAYERS / KNAIF_N_THREADS* overrides first"
        );
    }
    note(&format!(
        "Starting the model daemon ({})...",
        model_stem(model)
    ));
    daemon::start(dir, model, idle_minutes, &daemon::build_id())
}

/// The daemon process: load the model once, then answer until stopped or idle.
fn serve_daemon(dir: &Path, model: &Path, idle_minutes: u64) -> anyhow::Result<()> {
    // A daemon serves every client with whatever it was loaded with, so it must be loaded with the
    // defaults: refuse generation overrides (clients with overrides never borrow a daemon anyway).
    if daemon::disabled_by_env() {
        anyhow::bail!(
            "the daemon must be started without KNAIF_NO_DAEMON or KNAIF_MAX_TOKENS / KNAIF_N_CTX / \
             KNAIF_N_GPU_LAYERS / KNAIF_N_THREADS* set"
        );
    }
    // Stamp the model and backends *before* loading them: a file replaced while it loads then shows
    // up as a mismatch, instead of publishing the new stamp for the old bytes.
    let settings = daemon::settings_fingerprint(model);
    let backend = knaif_llm::backend_for(Some(model), false)?;
    let cfg = daemon::ServeConfig {
        dir: dir.to_path_buf(),
        model: model.display().to_string(),
        version: daemon::build_id(),
        settings,
        idle: std::time::Duration::from_secs(idle_minutes.max(1) * 60),
    };
    let (listener, info) = daemon::bind(&cfg)?;
    eprintln!(
        "knaif daemon: pid {} serving {} on port {} (idle stop after {} min)",
        info.pid,
        model_stem(model),
        info.port,
        idle_minutes.max(1)
    );
    let served = daemon::serve(listener, &info, &cfg, backend.as_ref());
    // Free the model first, then withdraw the record: `daemon stop` and the installer treat "the
    // record is gone" as "the model's memory and files are released".
    drop(backend);
    daemon::retire(dir, &info);
    served?;
    eprintln!("knaif daemon: stopped");
    Ok(())
}

fn cmd_skills_list(include_stale: bool) -> anyhow::Result<()> {
    let root = knaif_core::resolve_skills_root().ok_or_else(|| {
        anyhow::anyhow!(
            "no skills/ directory found (run from a repo checkout or set KNAIF_SKILLS_ROOT)"
        )
    })?;
    let skills = knaif_core::list_skills(&root, include_stale);
    if skills.is_empty() {
        println!("No skills found under {}", root.display());
        return Ok(());
    }
    for s in skills {
        let native = match s.native_status.as_deref() {
            Some("supported") => {
                format!(
                    "native:{}",
                    s.native_crate.as_deref().unwrap_or("supported")
                )
            }
            Some(other) => format!("native:{other}"),
            None => "native:-".to_string(),
        };
        let stale = if s.stale { " (stale)" } else { "" };
        println!("{:<12} {:<28} {}{}", s.name, native, s.description, stale);
    }
    Ok(())
}

/// Report each skill's declared external tools and whether they resolve on PATH. This is the
/// runtime "doctor" — the same detection that drives the installer's component tree (Phase 9) and
/// the `run` preflight. Detection only; it never launches a tool or modifies PATH.
fn cmd_skills_deps(name: Option<&str>, include_stale: bool) -> anyhow::Result<()> {
    let root = knaif_core::resolve_skills_root().ok_or_else(|| {
        anyhow::anyhow!(
            "no skills/ directory found (run from a repo checkout or set KNAIF_SKILLS_ROOT)"
        )
    })?;
    let skills = knaif_core::list_skills(&root, include_stale);
    let selected: Vec<_> = match name {
        Some(want) => {
            if !skills.iter().any(|s| s.name == want) {
                let names: Vec<_> = skills.iter().map(|s| s.name.as_str()).collect();
                anyhow::bail!("unknown skill {want:?}. Available: {}", names.join(", "));
            }
            skills.into_iter().filter(|s| s.name == want).collect()
        }
        None => skills,
    };

    let sac_on = smart_app_control_on();
    let mut all = Vec::new();
    for skill in &selected {
        let statuses = knaif_core::detect_skill_deps(&root.join(&skill.name));
        println!("{}:", skill.name);
        if statuses.is_empty() {
            println!("  (no external tools declared — runs in-process)");
            continue;
        }
        for s in &statuses {
            let mark = if s.satisfied { "OK  " } else { "MISS" };
            let kind = if s.required { "required" } else { "optional" };
            println!(
                "  [{mark}] {:<14} ({kind}) {}",
                s.name,
                deps_detail(s, sac_on)
            );
        }
        // Call out the blocking set explicitly so it's actionable, not just tabular.
        if let Some(msg) = required_tools_advice(&skill.name, &statuses, sac_on) {
            println!("{msg}");
        }
        all.extend(statuses);
    }
    if let Some(note) = smart_app_control_note(&all.iter().collect::<Vec<_>>(), sac_on) {
        println!("\n{note}");
    }
    Ok(())
}

/// One `skills deps` row's detail: where the tool is, or how to get it — and, where Smart App
/// Control is on and the tool declares it blocks it, that neither will help.
fn deps_detail(s: &knaif_core::ToolStatus, sac_on: bool) -> String {
    let blocked = sac_on && s.smart_app_control_blocks;
    if s.satisfied {
        let paths: Vec<_> = s
            .found
            .iter()
            .map(|(_, p)| p.display().to_string())
            .collect();
        let paths = paths.join(", ");
        if blocked {
            format!("{paths} — but Smart App Control blocks it on this PC")
        } else {
            paths
        }
    } else if blocked {
        "not installed — Smart App Control blocks it on this PC".to_string()
    } else {
        match &s.install_hint {
            Some(hint) => format!("install: {hint}"),
            None => "not found".to_string(),
        }
    }
}

/// What to say about a skill's required tools before it can run: where Smart App Control is on
/// and blocks one, that (installing would not help, and an installed one would not start);
/// otherwise the usual missing-tool advice.
fn required_tools_advice(
    skill: &str,
    statuses: &[knaif_core::ToolStatus],
    sac_on: bool,
) -> Option<String> {
    if sac_on {
        let blocked: Vec<&str> = statuses
            .iter()
            .filter(|s| s.required && s.smart_app_control_blocks)
            .map(|s| s.name.as_str())
            .collect();
        if !blocked.is_empty() {
            return Some(format!(
                "The `{skill}` skill needs {}, which Smart App Control blocks on this PC: \
                 Windows runs only programs that are validly signed or that it trusts, and this \
                 one is not. It works only with Smart App Control off: Windows Security > App & \
                 browser control > Smart App Control.",
                blocked.join(", ")
            ));
        }
    }
    knaif_core::missing_required_message(skill, statuses)
}

/// Said once under the table when Smart App Control is on and blocks a listed tool: why, and
/// that it is Windows' decision, not something knaif can work around.
fn smart_app_control_note(statuses: &[&knaif_core::ToolStatus], sac_on: bool) -> Option<String> {
    if !sac_on {
        return None;
    }
    let mut names: Vec<&str> = Vec::new();
    for s in statuses.iter().filter(|s| s.smart_app_control_blocks) {
        if !names.contains(&s.name.as_str()) {
            names.push(&s.name);
        }
    }
    if names.is_empty() {
        return None;
    }
    Some(format!(
        "Smart App Control is on. Windows then runs only programs that are validly signed or that \
         it trusts, and it blocks {} (knaif itself is signed). They work only with Smart App \
         Control off: Windows Security > App & browser control > Smart App Control.",
        names.join(", ")
    ))
}

/// Is Windows' Smart App Control enforcing? `VerifiedAndReputablePolicyState` is 0 off, 1 on,
/// 2 evaluation (which only watches); anything unreadable counts as off.
#[cfg(windows)]
fn smart_app_control_on() -> bool {
    use windows_sys::Win32::System::Registry::{
        RegGetValueW, HKEY_LOCAL_MACHINE, RRF_RT_REG_DWORD,
    };
    let key: Vec<u16> = "SYSTEM\\CurrentControlSet\\Control\\CI\\Policy\0"
        .encode_utf16()
        .collect();
    let value: Vec<u16> = "VerifiedAndReputablePolicyState\0".encode_utf16().collect();
    let mut data: u32 = 0;
    let mut size = std::mem::size_of::<u32>() as u32;
    // SAFETY: both names are NUL-terminated UTF-16, `data` is a u32 and `size` says so.
    let status = unsafe {
        RegGetValueW(
            HKEY_LOCAL_MACHINE,
            key.as_ptr(),
            value.as_ptr(),
            RRF_RT_REG_DWORD,
            std::ptr::null_mut(),
            (&mut data as *mut u32).cast(),
            &mut size,
        )
    };
    status == 0 && data == 1
}

#[cfg(not(windows))]
fn smart_app_control_on() -> bool {
    false
}

/// A byte-oriented progress bar for a model download (percent, rate, ETA). The length is set
/// once the server's `Content-Length` is known; until then it renders as an in-progress spinner.
fn download_bar(name: &str) -> ProgressBar {
    let bar = ProgressBar::new(0);
    // `with_template` only fails on a malformed template literal, so this is effectively infallible.
    if let Ok(style) = ProgressStyle::with_template(
        "{msg}\n{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] \
         {bytes}/{total_bytes} ({bytes_per_sec}, ETA {eta})",
    ) {
        bar.set_style(style.progress_chars("#>-"));
    }
    bar.set_message(format!("Downloading {name}"));
    bar
}

/// A steady-tick spinner (on stderr) for the one stretch of a real `run` that is otherwise silent:
/// loading the GGUF and running inference. llama.cpp is quiet by default (`void_logs`), so between
/// the download bar clearing and the plan appearing the terminal shows nothing — and on a CPU-only
/// machine that gap is a minute-plus (a 4B model, a multi-thousand-token planner prompt), which
/// reads as a hang. Steady tick keeps it animating even though `generate_plan` blocks the calling
/// thread; the elapsed timer makes a long wait visibly *progressing* rather than dead. Caller
/// `finish_and_clear`s it before printing the result.
///
/// It says nothing about WHICH device, deliberately. It is created before llama.cpp initialises, so
/// at this point the device is not yet chosen — and naming one here is how the message came to tell
/// every CUDA user their GPU run was "on CPU", which is the opposite of reassuring for someone who
/// installed a ~700 MB payload precisely to avoid that. The accurate statement is already made,
/// conditionally, by the `gpu == Some(false)` warning at the call site; repeating it here could only
/// ever duplicate that warning or contradict it.
///
/// It says nothing about WHICH RUN either. The message is emitted on every `run`, so calling this
/// one "the first run" was wrong every time after the first — and the claim was never doing the
/// work: what stops the silence reading as a hang is that a wait is expected AT ALL, which "this can
/// take a minute" states without asserting anything the process cannot know.
fn thinking_spinner() -> ProgressBar {
    let bar = ProgressBar::new_spinner();
    if let Ok(style) = ProgressStyle::with_template("{spinner:.green} {msg} [{elapsed_precise}]") {
        bar.set_style(style);
    }
    bar.set_message("Loading model and planning (this can take a minute)…");
    bar.enable_steady_tick(std::time::Duration::from_millis(120));
    bar
}

fn cmd_models(action: ModelsAction) -> anyhow::Result<()> {
    let store = ModelStore::open(&resolve_manifest_path()?)?;
    match action {
        ModelsAction::List => {
            let entries = store.list();
            if entries.is_empty() {
                println!(
                    "No models in the manifest. Store: {}",
                    store.dir().display()
                );
                return Ok(());
            }
            println!("Store: {}", store.dir().display());
            for e in entries {
                let mark = if e.installed {
                    "installed"
                } else {
                    "available"
                };
                let src = if e.in_manifest { "" } else { " (orphan)" };
                let skills = if e.skills.is_empty() {
                    String::new()
                } else {
                    format!("  skills: {}", e.skills.join(", "))
                };
                println!("  {:<32} {:<10}{}{}", e.name, mark, src, skills);
            }
            Ok(())
        }
        ModelsAction::Verify { name } => {
            match store.verify(&name)? {
                VerifyOutcome::Ok => println!("{name}: ok (checksum matches)"),
                VerifyOutcome::Mismatch { expected, actual } => {
                    anyhow::bail!(
                        "{name}: CHECKSUM MISMATCH\n  expected {expected}\n  actual   {actual}"
                    )
                }
                VerifyOutcome::NotInstalled => println!("{name}: not installed"),
                VerifyOutcome::NoChecksum => {
                    println!(
                        "{name}: installed, but the manifest has no checksum to verify against"
                    )
                }
            }
            Ok(())
        }
        ModelsAction::Pull { name } => {
            let bar = download_bar(&name);
            let path = store.pull_with_progress(&name, &HttpFetcher::new(), &mut |done, total| {
                if let Some(t) = total {
                    if bar.length() != Some(t) {
                        bar.set_length(t);
                    }
                }
                bar.set_position(done);
            });
            match path {
                Ok(path) => {
                    bar.finish_and_clear();
                    println!("Installed {name} -> {}", path.display());
                    Ok(())
                }
                Err(e) => {
                    bar.abandon();
                    Err(e)
                }
            }
        }
        ModelsAction::Update => {
            let updated = store.update(&HttpFetcher::new())?;
            if updated.is_empty() {
                println!("All installed models match the manifest; nothing to update.");
            } else {
                println!("Updated: {}", updated.join(", "));
            }
            Ok(())
        }
        ModelsAction::Rm { name, all } => {
            if all {
                let n = store.delete_all()?;
                println!("Removed {n} model file(s) from {}", store.dir().display());
            } else if let Some(name) = name {
                if store.delete(&name)? {
                    println!("Removed {name}");
                } else {
                    println!("{name}: not installed");
                }
            } else {
                anyhow::bail!("specify a model name or --all");
            }
            Ok(())
        }
    }
}

/// A byte-oriented progress bar for one file of a multi-file backend payload. The message carries
/// the position in the set (`[2/4] libcudart.so.13`), because a CUDA payload is ~668 MB across
/// several files and a bar with no such context looks stalled between files.
fn backend_bar() -> ProgressBar {
    let bar = ProgressBar::new(0);
    if let Ok(style) = ProgressStyle::with_template(
        "{msg}\n{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] \
         {bytes}/{total_bytes} ({bytes_per_sec}, ETA {eta})",
    ) {
        bar.set_style(style.progress_chars("#>-"));
    }
    bar
}

/// The cargo features this binary was compiled with.
///
/// The exe knows; until now it never said. `--version` is bare `CARGO_PKG_VERSION`, so nothing
/// outside the build could tell a CUDA build from a Vulkan one — and a build DIRECTORY name is
/// not evidence, since anyone can put a binary in one.
fn built_with() -> Vec<&'static str> {
    let mut features = Vec::new();
    if cfg!(feature = "llama") {
        features.push("llama");
    }
    if cfg!(feature = "dynamic-backends") {
        features.push("dynamic-backends");
    }
    if cfg!(feature = "cuda") {
        features.push("cuda");
    }
    if cfg!(feature = "vulkan") {
        features.push("vulkan");
    }
    if cfg!(feature = "pdfium") {
        features.push("pdfium");
    }
    features
}

/// Machine-readable `backend list`. **Always answers**, store or not: a static build has no
/// backend manifest, and a consumer that has to special-case "this binary could not tell me"
/// ends up guessing from the path instead. `store: None` reports an empty payload list and a
/// null directory, which is the truth about such a build.
///
/// The key names are a parsed interface — see the tests that pin them.
fn backend_list_json(store: Option<&BackendStore>) -> serde_json::Value {
    let entries: Vec<serde_json::Value> = store
        .map(|s| {
            s.list()
                .into_iter()
                .map(|e| {
                    serde_json::json!({
                        "name": e.name,
                        "state": format!("{:?}", e.state),
                        "platform_supported": e.platform_supported,
                        "total_bytes": e.total_bytes,
                        "description": e.description,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    serde_json::json!({
        "version": env!("CARGO_PKG_VERSION"),
        "built_with": built_with(),
        // A compile-time fact, deliberately not read from the store: a static build has no store
        // to read it from, and `false` is the answer that matters to a caller.
        "dynamic_backends": cfg!(feature = "dynamic-backends"),
        "backends_dir": store.map(|s| s.dir().display().to_string()),
        "platform": store.map(|s| s.platform().to_string()),
        "entries": entries,
    })
}

fn cmd_backend(action: BackendAction) -> anyhow::Result<()> {
    // `list --json` must answer even when the manifest cannot be resolved, so the store is
    // optional for that one path and required for the rest.
    if let BackendAction::List { json: true } = action {
        let store = resolve_backend_manifest_path()
            .and_then(|p| BackendStore::open(&p))
            .ok();
        println!(
            "{}",
            serde_json::to_string_pretty(&backend_list_json(store.as_ref()))?
        );
        return Ok(());
    }

    let store = BackendStore::open(&resolve_backend_manifest_path()?)?;
    match action {
        BackendAction::List { .. } => {
            let entries = store.list();
            println!("Built with: {}", built_with().join(", "));
            if entries.is_empty() {
                println!("No backend payloads in the manifest.");
                return Ok(());
            }
            println!(
                "Backends dir: {}   (platform: {})",
                store.dir().display(),
                store.platform()
            );
            for e in entries {
                let state = match &e.state {
                    BackendState::Installed => "installed".to_string(),
                    BackendState::NotInstalled if !e.platform_supported => {
                        "unavailable on this platform".to_string()
                    }
                    BackendState::NotInstalled
                        if e.status != knaif_models::PublishStatus::Published =>
                    {
                        "not published yet".to_string()
                    }
                    BackendState::NotInstalled => "available".to_string(),
                    BackendState::Stale { installed_by } => {
                        format!("STALE (installed by knaif {installed_by}; re-install to use it)")
                    }
                    BackendState::Interrupted => {
                        "INCOMPLETE (a previous install did not finish; re-install)".to_string()
                    }
                };
                let size = match e.total_bytes {
                    Some(b) if b > 0 => format!("  ~{} MB", b / 1_000_000),
                    _ => String::new(),
                };
                println!("  {:<10} {}{}", e.name, state, size);
                if let Some(d) = &e.description {
                    println!("             {d}");
                }
            }
            Ok(())
        }
        BackendAction::Install { name } => {
            let bar = backend_bar();
            let mut current = String::new();
            let result = store.install_with_progress(&name, &HttpFetcher::new(), &mut |p| {
                if current != p.file {
                    current = p.file.to_string();
                    bar.set_length(p.total_bytes.unwrap_or(0));
                    bar.set_message(format!("[{}/{}] {}", p.index, p.total_files, p.file));
                }
                if let Some(t) = p.total_bytes {
                    if bar.length() != Some(t) {
                        bar.set_length(t);
                    }
                }
                bar.set_position(p.done_bytes);
            });
            match result {
                Ok(dir) => {
                    bar.finish_and_clear();
                    println!("Installed the {name} backend -> {}", dir.display());
                    println!(
                        "It is picked up on the next run; no restart of anything else needed."
                    );
                    Ok(())
                }
                Err(e) => {
                    bar.abandon();
                    Err(e)
                }
            }
        }
        BackendAction::Verify { name } => {
            match store.verify(&name)? {
                VerifyOutcome::Ok => println!("{name}: ok (every file matches the manifest)"),
                VerifyOutcome::Mismatch { expected, actual } => anyhow::bail!(
                    "{name}: CHECKSUM MISMATCH\n  expected {expected}\n  actual   {actual}\n  \
                     Re-run `knaif backend install {name}`."
                ),
                VerifyOutcome::NotInstalled => println!("{name}: not installed"),
                VerifyOutcome::NoChecksum => {
                    println!(
                        "{name}: installed, but the manifest has no checksums to verify against"
                    )
                }
            }
            Ok(())
        }
        BackendAction::Remove { name } => {
            if store.remove(&name)? {
                println!("Removed the {name} backend. Runs fall back to CPU/Vulkan.");
            } else {
                println!("{name}: not installed");
            }
            Ok(())
        }
    }
}

fn cmd_plan(args: PlanArgs) -> anyhow::Result<()> {
    let root = resolve_known_skill(&args.skill)?;
    // Open/CLI mode: resolve relative paths against cwd, no sandbox boundary.
    let cwd = std::env::current_dir()?;
    // `--json` is accepted but JSON is currently the only output format.
    //
    // `plan` is non-prompting: it auto-selects an installed model silently, but only downloads a
    // missing one under `--yes`. `--batch`/`--json` never download at all — batch loads the model
    // once up front, and neither may risk a prompt interleaving with streamed output.
    let policy = if args.batch.is_some() || args.json {
        DownloadPolicy::Never
    } else {
        DownloadPolicy::YesOnly
    };
    let model = select_model(args.model.as_deref(), args.yes, policy)?;

    // Load the model + registry once, then plan each utterance against that live session.
    let session = PlanSession::new(&root, &args.skill, model.as_deref(), args.verbose)?;

    if let Some(batch) = &args.batch {
        let content = std::fs::read_to_string(batch)
            .map_err(|e| anyhow::anyhow!("reading batch file {}: {e}", batch.display()))?;
        let mut out = std::io::stdout().lock();
        use std::io::Write;
        for line in content.lines() {
            // Keep output line-aligned with input: a bad plan becomes an error envelope, not a bail.
            let payload = match session.plan(line, &cwd, None) {
                Ok(p) => p,
                Err(e) => serde_json::json!({ "plan": [], "error": e.to_string() }),
            };
            writeln!(out, "{}", serde_json::to_string(&payload)?)?;
            out.flush()?; // stream: emit each plan immediately so a reader can show live progress
        }
    } else {
        let payload = session.plan(&args.utterance.join(" "), &cwd, None)?;
        println!("{}", serde_json::to_string(&payload)?);
    }
    Ok(())
}

/// What `cmd_run` should do with a parsed plan's step list, before dispatching to a skill.
///
/// **History, because the variant that is gone matters.** `cmd_run` originally took
/// `steps.first()` and discarded every later step in silence: a valid two-step plan executed one
/// command and still exited 0, reporting full success for partial completion — and for a
/// destructive plan, silently skipping a side effect the request asked for
/// (docs/audits/2026-09-07-core-principles-and-rtx5080.md, F5). `Unsupported` replaced that with
/// an honest refusal, which was right while there was no executor.
///
/// E2 removed it: native now runs the steps in order, so a chain is executed rather than
/// declined. What remains is the one case that still needs a decision — a plan with no steps.
#[derive(Debug, PartialEq, Eq)]
enum StepDecision {
    /// No steps at all.
    Empty,
    /// `total` steps to run, in plan order.
    Run { total: usize },
}

/// Marks output the runtime produced because a capability is **not built**, as opposed to a
/// `reject:` — a request the runtime understood and declined. The two look alike to a user but
/// are opposite facts about the product: a coverage gap versus the safety model working. Kept
/// distinct in the machine-readable output so acceptance records can count coverage at all
/// (docs/plans/2026-09-10-skill-quality-lifecycle.md, L4d).
///
/// Defined in `knaif-skill-api` rather than here, because the skills are where the gaps are:
/// while this was the host's private constant, `ffmpeg` could not reach it and bailed with a
/// bare error instead, which is how the first L4 run reported full coverage over a corpus it
/// could not fully attempt (N6).
use knaif_skill_api::capability::not_implemented_message;

fn decide_steps(steps: &[serde_json::Value]) -> StepDecision {
    match steps.len() {
        0 => StepDecision::Empty,
        total => StepDecision::Run { total },
    }
}

fn cmd_run(args: RunArgs) -> anyhow::Result<()> {
    if !matches!(args.skill.as_str(), "ffmpeg" | "documents") {
        anyhow::bail!(
            "native `run` supports the ffmpeg and documents skills; got {:?}",
            args.skill
        );
    }
    ui::start_clock();
    let root = resolve_known_skill(&args.skill)?;
    let bundle = root.join(&args.skill);

    let request = args.request.join(" ");
    if request.trim().is_empty() {
        anyhow::bail!(
            "no request given. Usage: just native {0} \"<what to do>\" [--model <name|path>]\n  \
             e.g. just native {0} \"compress clip.mp4 for email\" --model knaif-qwen3-4b-v2",
            args.skill
        );
    }

    // Pre-inference safety gate: an unsafe phrase never reaches the model.
    let unsafe_phrases = knaif_core::load_unsafe_phrases(&bundle.join("skill.yaml"));
    if knaif_core::is_unsafe_request(&request, &unsafe_phrases) {
        let message = format!(
            "this request is blocked by the {} skill's safety policy.",
            args.skill
        );
        if let Some(style) = ui::view() {
            ui::header_once(&args.skill, None);
            ui::closing(&style, ui::Closing::Reject, &message);
        } else {
            println!("reject: {message}");
        }
        return Ok(());
    }

    // Dependency preflight: fail fast (before spending inference) when a REQUIRED external tool is
    // missing and we intend to execute. Dry-run still previews the command without the binary.
    if !args.dry_run {
        let statuses = knaif_core::detect_skill_deps(&bundle);
        if let Some(msg) = required_tools_advice(&args.skill, &statuses, smart_app_control_on()) {
            println!("{msg}");
            return Ok(());
        }
    }

    // Only now that the request has survived every cheap rejection may we resolve (and possibly
    // download) a model — see `select_model`.
    let model = select_model(args.model.as_deref(), args.yes, DownloadPolicy::Prompt)?;
    if args.daemon {
        match model.as_deref() {
            Some(m) => ensure_daemon(&daemon::state_dir(), m, daemon::DEFAULT_IDLE_MINUTES)?,
            None => note("--daemon needs a real model; this run uses the offline mock."),
        }
    }
    ui::header_once(
        &args.skill,
        model
            .as_deref()
            .and_then(|m| m.file_stem())
            .map(|n| ui::model_label(&n.to_string_lossy()))
            .as_deref(),
    );

    // Resolve paths against the sandbox when given (and enforce its boundary), else cwd/open mode.
    let base = match &args.sandbox {
        Some(s) => s.clone(),
        None => std::env::current_dir()?,
    };
    let sandbox = args.sandbox.as_deref();
    // Probe the GPU ONCE and reuse the answer. Calling `gpu_present` twice would double-print its
    // device trace under `--verbose`, and the two messages below both need the same fact.
    //
    // `None` means this build has no inference backend at all (a mock-only build), which is the
    // distinction both messages depend on: `Some(false)` is "a real backend, and it found no GPU".
    let gpu = if model.is_some() {
        knaif_llm::gpu_present(args.verbose)
    } else {
        None
    };
    // Before the (silent) model load + inference, tell the user if it will run entirely on the CPU:
    // no GPU device means a minute-plus wait for a 4B model, and saying so up front turns a
    // baffling "nothing happened" into an expected slow run. Only meaningful for real inference.
    if gpu == Some(false) {
        advisory(
            true,
            "⚠  No GPU backend is active — running on CPU. Inference will be slow \
             (the first request can take a minute or more).",
        );
    }
    // Tell an NVIDIA user about the opt-in CUDA payload, once, before the slow run rather than
    // after it. Where Vulkan measures at CPU speed (Blackwell did until a 2026-09 re-measurement), a
    // user who runs first and reads later gets one CPU-speed request and may reasonably conclude the
    // product is broken.
    //
    // `gpu.is_some()` is the "this build can actually infer" test. Offering a GPU backend to a
    // mock-only binary is noise — there is nothing for it to accelerate — and the CPU warning above
    // has always been silent in that case, so this keeps the two consistent.
    if gpu.is_some() {
        print_cuda_offer(gpu == Some(true));
    }
    // Model load + inference is the one silent stretch of a real run; show a live spinner so a
    // slow CPU-only run is not mistaken for a hang. Skip it for the mock (instant) and in verbose
    // mode (llama.cpp prints its own trace, which the spinner would fight).
    let rich = ui::view();
    let show_spinner = model.is_some() && !args.verbose && rich.is_none();
    let spinner = show_spinner.then(thinking_spinner);
    // Terminal view: our own spinner, drawn on a private copy of stderr, while the process's
    // stderr itself is pointed at the null device so llama.cpp and the GPU backends cannot write
    // over the tree. Never under `--verbose`, and not when a debug dump is on (those are stderr
    // by contract). Piped runs are left alone: the eval lane reads their stderr.
    let quiet = rich.is_some()
        && model.is_some()
        && !args.verbose
        && !debug_enabled()
        && !plan_dump_enabled()
        && !prompt_dump_enabled();
    let private_stderr = if quiet { ui::real_stderr() } else { None };
    let live = match (&rich, private_stderr) {
        (Some(style), Some(out)) => Some(ui::Spinner::start(*style, "Planning…", out)),
        _ => None,
    };
    let silence = live.as_ref().and_then(|_| ui::StderrGuard::silence());
    let built = build_plan(
        &root,
        &args.skill,
        &request,
        &base,
        sandbox,
        model.as_deref(),
        args.verbose,
    );
    drop(silence);
    if let Some(l) = live {
        l.stop();
    }
    if let Some(s) = spinner {
        s.finish_and_clear();
    }
    // Anything the user typed during that long silent wait is still queued in the terminal's input
    // buffer; discard it so those stray keystrokes can't answer a pending confirm prompt or spill
    // onto the shell after we exit. Gated to the slow path (a spinner was shown) so a fast run
    // never drops a deliberately typed-ahead command.
    if show_spinner || rich.is_some() && model.is_some() && !args.verbose {
        flush_terminal_input();
    }
    // Nothing was loaded or inferred without a model (the mock), so there is no time to report.
    if let (Some(style), Ok(_), true) = (&rich, &built, model.is_some()) {
        let (load, infer) = ui::timings();
        println!("{}", ui::render_planning(style, load, infer));
    }
    let payload = built?;

    let mut steps = payload
        .get("plan")
        .and_then(serde_json::Value::as_array)
        .cloned()
        .unwrap_or_default();
    // Two plan-level naming repairs, in one pass and in this order: a name the filesystem would
    // reject is not a path at all, so it is rewritten before anything resolves one, and only then
    // is an output that would truncate its own input moved aside. Every later step referencing a
    // rewritten name is rebound, or the chain quietly unlinks.
    //
    // Scoped to ffmpeg because that is exactly where Python applies it (only ffmpeg overrides
    // `Skill.resolve_output_collisions`); extending it to documents here would create the runtime
    // divergence this pass exists to avoid. This is the native half of that hook — the Python
    // side reports the renames through `format_results`, so they are reported here too: a rename
    // the user is not told about leaves them looking for a file that was never written.
    if args.skill == "ffmpeg" {
        let renames = knaif_skill_ffmpeg::binding::rebind_colliding_outputs(&mut steps, sandbox);
        for (requested, used) in renames.illegal {
            note(&format!(
                "note: {requested:?} is not a valid file name here — writing {used:?}"
            ));
        }
        for (requested, used) in renames.collisions {
            note(&format!(
                "note: {requested:?} would have been overwritten by the step that reads it, so \
                 the result goes to {used:?}"
            ));
        }
    }
    let total = match decide_steps(&steps) {
        StepDecision::Empty => {
            if model.is_none() {
                let (recommended, installed) = recommended_model_status();
                println!(
                    "{}",
                    first_run_model_message(&args.skill, recommended.as_deref(), installed)
                );
            } else {
                println!(
                    "No plan produced: the model returned no usable plan for this request. Try \
                     rephrasing it, or a different --model."
                );
            }
            return Ok(());
        }
        StepDecision::Run { total } => total,
    };

    if let Some(style) = ui::view() {
        // A control step ends the plan by itself, so there is no plan to summarize.
        if !steps.iter().any(is_control_step) {
            println!("{}", ui::render_plan(&style, &plan_rows(&steps)));
        }
    }
    let ctx = StepContext {
        skill: &args.skill,
        bundle: &bundle,
        base: &base,
        sandbox,
        dry_run: args.dry_run,
        yes: approves_without_asking(args.yes, args.confirm),
        overwrite: args.overwrite,
    };
    execute_plan(&steps, total, &ctx)
}

/// A line the run wants the user to see that is not an outcome: in the terminal view it sits in
/// the tree, in the plain view it stays the stderr line it always was.
/// A declined confirmation. The plain view keeps 1.2.0's line; the terminal view closes the step
/// inside the tree.
fn print_declined() {
    match ui::view() {
        Some(style) => println!("{}", ui::render_declined(&style)),
        None => println!("Aborted (no changes made)."),
    }
}

fn note(text: &str) {
    match ui::view() {
        Some(style) => println!("{}", ui::render_detail(&style, text)),
        None => eprintln!("{text}"),
    }
}

/// A warning or tip about the machine rather than the request (no GPU, a CUDA offer). The plain
/// view keeps the stderr text it always had; the terminal view frames it in color.
fn advisory(warning: bool, plain: &str) {
    match ui::view() {
        Some(style) => {
            // The plain text carries a symbol prefix and hand-wrapped continuation lines.
            let text: String = plain
                .trim_start_matches(['⚠', 'ℹ'])
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ");
            let (tone, title) = if warning {
                (ui::Tone::Warn, "Heads up")
            } else {
                (ui::Tone::Accent, "Tip")
            };
            println!("{}", ui::render_box(&style, tone, title, &text));
        }
        None => eprintln!("{plain}"),
    }
}

fn is_control_step(step: &serde_json::Value) -> bool {
    matches!(
        step.get("tool").and_then(serde_json::Value::as_str),
        Some("clarify" | "reject" | "done")
    )
}

/// What a step works on, for the plan summary: `clip.mp4 → out.mp4`, from whichever of the
/// common input/output args the step carries.
fn step_detail(args: &serde_json::Map<String, serde_json::Value>) -> String {
    fn first(v: &serde_json::Value) -> Option<String> {
        match v {
            serde_json::Value::String(s) => Some(s.clone()),
            serde_json::Value::Array(items) => {
                let names: Vec<String> = items.iter().filter_map(first).collect();
                match names.len() {
                    0 => None,
                    1 => names.into_iter().next(),
                    n => Some(format!("{} (+{} more)", names[0], n - 1)),
                }
            }
            _ => None,
        }
    }
    let input = ["inputs", "input", "files", "pdf", "paths"]
        .iter()
        .find_map(|k| args.get(*k).and_then(first));
    let output = args.get("output").and_then(first);
    match (input, output) {
        (Some(i), Some(o)) => format!("{i} → {o}"),
        (Some(i), None) => i,
        (None, Some(o)) => format!("→ {o}"),
        (None, None) => String::new(),
    }
}

fn plan_rows(steps: &[serde_json::Value]) -> Vec<ui::PlanRow> {
    steps
        .iter()
        .map(|step| {
            let empty = serde_json::Map::new();
            let args = step
                .get("args")
                .and_then(serde_json::Value::as_object)
                .unwrap_or(&empty);
            ui::PlanRow {
                tool: step
                    .get("tool")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("?")
                    .to_string(),
                detail: step_detail(args),
            }
        })
        .collect()
}

/// Run a plan's steps in order (E2/E3).
///
/// The semantics are deliberately narrow:
///
/// - **A control tool ends the whole plan**, wherever it sits. `clarify`/`reject`/`done` are
///   statements about the *request*, not work to run past.
/// - **Stop at the first failure**, and say which steps ran. A chain that fails at step 2 of 3 is
///   neither a success nor a clean failure: step 1 already wrote a file to the user's disk, and a
///   bare error would leave the user guessing what is on it.
/// - **Confirmation stays per step** — each dispatch runs its own gate, so a destructive step in
///   the middle of a chain is still confirmed as one.
///
/// Chains are file-mediated, not variable-bound: `skills/ffmpeg/prompt.yaml` instructs the model
/// to give an earlier step an explicit `output` filename and reuse it as the later step's input
/// ("Never chain steps with `$variable` references"), and `apply_clarify_gate` binds intermediates
/// the model left undeclared. So ordering *is* the dependency mechanism — which is why the L2
/// cases pin the order and not just the count.
///
/// Recovery, rollback and resumption of a half-run chain are deliberately **not** here; see E4 and
/// the limitations section of `docs/NATIVE.md`.
/// The context line attached to a failing step: which step failed, what had already run, and what
/// did not.
///
/// A bare "ffmpeg exited 1" after a chain leaves the user guessing whether anything reached their
/// disk. Kept pure so the wording is unit-testable without executing anything.
fn chain_failure_context(idx: usize, total: usize) -> String {
    let ordinal = idx + 1;
    if total == 1 {
        return String::from("the step failed");
    }
    let completed = match idx {
        0 => "nothing had run yet".to_string(),
        1 => "step 1 had already completed".to_string(),
        _ => format!("steps 1-{idx} had already completed"),
    };
    let skipped = match total - ordinal {
        0 => "it was the last step".to_string(),
        1 => format!("step {total} was not run"),
        _ => format!("steps {}-{total} were not run", ordinal + 1),
    };
    format!("step {ordinal} of {total} failed; {completed}, and {skipped}")
}

fn execute_plan(
    steps: &[serde_json::Value],
    total: usize,
    ctx: &StepContext,
) -> anyhow::Result<()> {
    execute_steps(steps, total, |step| run_step(step, ctx))
}

/// The ordered loop behind [`execute_plan`], with the step runner injected so the stop-on-decline
/// and stop-on-control rules are testable without executing anything.
fn execute_steps(
    steps: &[serde_json::Value],
    total: usize,
    mut run: impl FnMut(&serde_json::Value) -> anyhow::Result<StepOutcome>,
) -> anyhow::Result<()> {
    for (idx, step) in steps.iter().enumerate() {
        let ordinal = idx + 1;
        // Announce the position only for a real chain: a one-step plan reads better without a
        // "step 1 of 1" preamble, and every existing single-step test asserts that output.
        match ui::view() {
            Some(style) if !is_control_step(step) => {
                let tool = step
                    .get("tool")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("?");
                println!("{}", ui::render_step_head(&style, ordinal, total, tool));
            }
            Some(_) => {}
            None if total > 1 => println!("step {ordinal} of {total}:"),
            None => {}
        }
        match run(step).with_context(|| chain_failure_context(idx, total))? {
            StepOutcome::Continue => {}
            StepOutcome::ShortCircuit => return Ok(()),
            StepOutcome::Declined => {
                match ui::view() {
                    // The closing line says where the run stopped, which covers the steps after.
                    Some(_) => ui::mark_declined(ordinal, total),
                    None => {
                        if let Some(text) = declined_note(idx, total) {
                            println!("{text}");
                        }
                    }
                }
                return Ok(());
            }
        }
    }
    Ok(())
}

/// What a declined step means for the rest of a chain. `None` for the last (or only) step: there
/// is nothing left to say beyond "Aborted".
fn declined_note(idx: usize, total: usize) -> Option<String> {
    let ordinal = idx + 1;
    match total - ordinal {
        0 => None,
        1 => Some(format!("Step {total} was not run.")),
        _ => Some(format!("Steps {}-{total} were not run.", ordinal + 1)),
    }
}

/// Everything one step needs that does not vary between the steps of a plan.
///
/// Extracted in E1 so the ordered executor (E2) is a loop over [`run_step`] rather than a second
/// copy of the dispatch. Dependency preflight and model resolution are deliberately *not* here:
/// they run once per invocation, before any step, and nothing about chaining changes them.
struct StepContext<'a> {
    skill: &'a str,
    bundle: &'a Path,
    base: &'a Path,
    sandbox: Option<&'a Path>,
    dry_run: bool,
    /// Act without asking `Proceed?` — the default; `--confirm` turns the question on.
    yes: bool,
    /// `--overwrite`: replacing an existing file needs no question.
    overwrite: bool,
}

/// What one step means for the steps after it.
#[derive(Debug, PartialEq, Eq)]
enum StepOutcome {
    /// The step ran (or previewed). Carry on with the next one.
    Continue,
    /// A core control tool answered the *request*, not this position in it — so nothing after it
    /// is meaningful. See E3: `clarify` / `reject` / `done` end the plan wherever they appear.
    ShortCircuit,
    /// The user declined this step's confirmation. Nothing after it may run: a later step usually
    /// consumes what this one was going to write (Python's executor stops here too).
    Declined,
}

/// Run (or preview) exactly one step: control-tool short-circuit, then skill dispatch.
///
/// A pure lift of what `cmd_run` did inline for its single step (E1) — no behavior change beyond
/// reporting *why* it stopped, which E2 needs and a single-step caller can ignore.
fn run_step(step: &serde_json::Value, ctx: &StepContext) -> anyhow::Result<StepOutcome> {
    let tool = step
        .get("tool")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let empty = serde_json::Map::new();
    let step_args = step
        .get("args")
        .and_then(serde_json::Value::as_object)
        .unwrap_or(&empty);

    // Core control tools short-circuit before any ffmpeg work.
    match tool {
        "clarify" => {
            let q = step_args
                .get("question")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("(no question)");
            match ui::view() {
                Some(style) => ui::closing(&style, ui::Closing::Clarify, q),
                None => println!("clarify: {q}"),
            }
            return Ok(StepOutcome::ShortCircuit);
        }
        "reject" => {
            let r = step_args
                .get("reason")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("(no reason)");
            match ui::view() {
                Some(style) => ui::closing(&style, ui::Closing::Reject, r),
                None => println!("reject: {r}"),
            }
            return Ok(StepOutcome::ShortCircuit);
        }
        // `done` says the request is already satisfied. It reached a skill dispatch before E3,
        // where it could only ever produce "unknown tool" — a control tool leaking out as an
        // error. It is now what it always meant: nothing to do, and nothing after it to do.
        "done" => {
            match ui::view() {
                Some(style) => ui::closing(&style, ui::Closing::Nothing, ""),
                None => println!("Nothing to do."),
            }
            return Ok(StepOutcome::ShortCircuit);
        }
        _ => {}
    }

    match ctx.skill {
        "ffmpeg" => run_ffmpeg_step(
            ctx.bundle,
            tool,
            step_args,
            ctx.sandbox,
            ctx.dry_run,
            ctx.yes,
            ctx.overwrite,
        ),
        "documents" => run_documents_step(
            ctx.bundle,
            tool,
            step_args,
            ctx.base,
            ctx.sandbox,
            ctx.dry_run,
            ctx.yes,
            ctx.overwrite,
        ),
        _ => unreachable!("skill guarded above"),
    }
}

/// ffmpeg dispatch: expand the intent → dry-run preview or confirmed subprocess execution.
///
/// An expansion that needs a clarify (an unknown platform) ends the plan, as a `clarify` step
/// does: Python's executor stops at the first clarify leaf. Native used to print the question
/// and run on, so a later step acted on a file this one never produced (R5c L3, `ffmpeg_136`).
fn run_ffmpeg_step(
    bundle: &Path,
    tool: &str,
    step_args: &serde_json::Map<String, serde_json::Value>,
    sandbox: Option<&Path>,
    dry_run: bool,
    yes: bool,
    overwrite: bool,
) -> anyhow::Result<StepOutcome> {
    let data = knaif_skill_ffmpeg::FfmpegData::load(bundle)?;
    // Dry-run stubs missing files; execution real-probes every input (missing/unprobeable → error).
    let expansion = if dry_run {
        knaif_skill_ffmpeg::run::expand_dry_run(tool, step_args, &data, sandbox)?
    } else {
        knaif_skill_ffmpeg::run::expand_execute(tool, step_args, &data, sandbox)?
    };
    let commands = match expansion {
        knaif_skill_ffmpeg::run::Expansion::Commands(cmds) => cmds,
        knaif_skill_ffmpeg::run::Expansion::Clarify(q) => {
            match ui::view() {
                Some(style) => ui::closing(&style, ui::Closing::Clarify, &q),
                None => println!("clarify: {q}"),
            }
            return Ok(StepOutcome::ShortCircuit);
        }
    };
    if commands.is_empty() {
        println!("Nothing to do.");
        return Ok(StepOutcome::Continue);
    }
    let dump = plan_dump_enabled();
    for cmd in &commands {
        if let Some(msg) = argv_dump(dump, cmd) {
            eprintln!("{msg}");
        }
    }

    // Dry-run: print the copy-pasteable command line(s) and stop — no side effects.
    if dry_run {
        for cmd in &commands {
            match ui::view() {
                Some(style) => println!("{}", ui::render_command(&style, &shell_join(cmd))),
                None => println!("{}", shell_join(cmd)),
            }
        }
        return Ok(StepOutcome::Continue);
    }

    // Execution: every ffmpeg intent is `safety_category: destructive`, so it needs explicit
    // consent (the native equivalent of `ctx.confirmed`). `--yes`, an interactive yes, or nothing.
    let previews: Vec<String> = commands.iter().map(|c| shell_join(c)).collect();
    // Python asks before reversing and says why; native used to list the command and nothing else.
    // Said only when a person is about to answer: `--yes` has already decided.
    // Terminal view: the commands are the step's content, shown before the question. (The plain
    // view lists them inside the confirmation and again as `running:` lines, as it always has.)
    if let Some(style) = ui::view() {
        for p in &previews {
            println!("{}", ui::render_command(&style, p));
        }
        // Say it in the tree too, before the question below asks.
        for cmd in &commands {
            if let Some(out) = cmd.last().filter(|o| std::path::Path::new(o).exists()) {
                println!(
                    "{}",
                    ui::render_detail(
                        &style,
                        &style.paint(ui::Tone::Warn, &format!("⚠ {out} already exists"))
                    )
                );
            }
        }
    }
    // Every command carries `-y` (the rendered command is part of the parity contract), so a
    // file that is already there would be replaced without a word: ask first, whatever `--yes`
    // says. The chain's own intermediate files are not "existing" until an earlier step wrote
    // them, so only what is on disk now is asked about.
    let outputs: Vec<String> = commands.iter().filter_map(|c| c.last().cloned()).collect();
    if !overwrite_gate(
        &existing_outputs(&outputs),
        overwrite,
        &mut ask_yes_no_default,
    )? {
        print_declined();
        return Ok(StepOutcome::Declined);
    }
    if !yes {
        if let Some(warning) = confirm_warning(tool, commands.len()) {
            note(&warning);
        }
    }
    if !confirm_action(yes, &previews, "ffmpeg command")? {
        print_declined();
        return Ok(StepOutcome::Declined);
    }

    let mut failures = 0;
    for cmd in &commands {
        let output = cmd.last().cloned().unwrap_or_default();
        // An `output` that named a destination directory (`videos_hevc/clip.mkv`) has a parent
        // that need not exist yet; ffmpeg does not create one and fails on open. The path is
        // already sandbox-checked by the engine.
        // Create the RESOLVED parent: the stored path keeps the spelling the plan supplied,
        // so `../escaped/../sb/out.mp4` passes containment while creating its unnormalised
        // parent walks through `../escaped` and creates it on POSIX.
        if let Some(parent) = std::path::Path::new(&output).parent() {
            if !parent.as_os_str().is_empty() {
                let target = std::fs::canonicalize(parent)
                    .unwrap_or_else(|_| knaif_skill_ffmpeg::engine::lexically_normalize(parent));
                std::fs::create_dir_all(target).ok();
            }
        }
        if ui::view().is_none() {
            eprintln!("running: {}", shell_join(cmd));
        }
        let started = std::time::Instant::now();
        let result = match ui::view() {
            Some(style) => run_ffmpeg_live(&style, cmd, started)?,
            None => knaif_skill_ffmpeg::exec::run_ffmpeg(cmd)?,
        };
        if result.status.success() {
            // Exit 0 is not evidence of output: a trim past the end writes an empty container.
            knaif_skill_ffmpeg::exec::require_streams(std::path::Path::new(&output))?;
            ui::note_written(1);
            match ui::view() {
                Some(style) => println!(
                    "{}",
                    ui::render_ok(&style, &output, Some(started.elapsed()))
                ),
                None => println!("✓ {output}"),
            }
        } else if let Some(style) = ui::view() {
            failures += 1;
            let stderr = String::from_utf8_lossy(&result.stderr);
            let cause = knaif_skill_ffmpeg::exec::failure_reason(&stderr, result.status.code())
                .or_else(|| {
                    stderr
                        .lines()
                        .rev()
                        .find(|l| !l.trim().is_empty())
                        .map(|l| l.trim().to_string())
                })
                .unwrap_or_else(|| format!("ffmpeg exited {}", result.status));
            println!("{}", ui::render_fail(&style, &output, Some(&cause)));
            if ui::verbose() {
                println!(
                    "{}",
                    ui::render_detail(&style, &format!("ffmpeg exited {}", result.status))
                );
                for line in stderr.lines() {
                    println!("{}", ui::render_detail(&style, line));
                }
            }
        } else {
            failures += 1;
            let stderr = String::from_utf8_lossy(&result.stderr);
            let tail: String = stderr
                .lines()
                .rev()
                .take(3)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
                .join("\n");
            // The cause first, in a sentence: the tail alone can end on a generic "Conversion
            // failed!" with the line that explains it cut off above.
            let cause = knaif_skill_ffmpeg::exec::failure_reason(&stderr, result.status.code())
                .map(|c| format!("\n  cause: {c}"))
                .unwrap_or_default();
            println!(
                "✗ {output} (ffmpeg exited {}){cause}\n{tail}",
                result.status
            );
        }
    }
    if failures > 0 {
        anyhow::bail!("{failures} of {} command(s) failed", commands.len());
    }
    Ok(StepOutcome::Continue)
}

/// The caution shown before a step's confirmation, for the tools whose cost is not obvious from
/// the command. Mirrors the prompt in Python's `ReverseVideoIntent` (`skills/ffmpeg/python/intents.py`).
fn confirm_warning(tool: &str, clips: usize) -> Option<String> {
    match tool {
        "reverse_video" => Some(format!(
            "Reversing {clips} clip(s) re-encodes the full file and buffers it entirely in RAM — \
             long clips may exhaust memory."
        )),
        _ => None,
    }
}

/// documents dispatch: safe read tools print their result; destructive write tools preview the
/// output path(s), then (after confirmation) commit the in-process op.
#[allow(clippy::too_many_arguments)]
fn run_documents_step(
    bundle: &Path,
    tool: &str,
    step_args: &serde_json::Map<String, serde_json::Value>,
    base: &Path,
    sandbox: Option<&Path>,
    dry_run: bool,
    yes: bool,
    overwrite: bool,
) -> anyhow::Result<StepOutcome> {
    use knaif_skill_documents::run::{commit, is_supported, preview, Preview, ReadResult};

    if !is_supported(tool) {
        // The `not_implemented:` prefix, not a bare error: this is a capability the native
        // runtime does not have, which is a different fact from a `reject:` and has to stay
        // countable in the machine-readable output (see [`NOT_IMPLEMENTED_PREFIX`]).
        // The "(image watermark is deferred)" this message used to carry was stale — `watermark`
        // has been in `is_supported` for a while. Naming no example is better than naming a
        // wrong one; `is_supported` is the list.
        anyhow::bail!(not_implemented_message(&format!(
            "the documents tool {tool:?} is not built into the native runtime yet"
        )));
    }

    match preview(tool, step_args, base, sandbox, bundle)? {
        Preview::Read(result) => {
            // Stderr, beside the plan dump, so stdout stays the human answer.
            if let Some(line) = result_dump(plan_dump_enabled(), tool, &read_result_json(&result)) {
                eprintln!("{line}");
            }
            if let Some(style) = ui::view() {
                print_read_result(&style, &result);
                return Ok(StepOutcome::Continue);
            }
            match result {
                ReadResult::Inspection(i) => {
                    println!(
                        "{}: {} page(s), {} bytes, encrypted={}, text_layer={}",
                        i.format, i.pages, i.size_bytes, i.encrypted, i.has_text_layer
                    );
                    // An encrypted PDF reports 0 pages because its page tree is unreadable; say
                    // why, or "0 page(s)" reads as an empty file.
                    if i.encrypted {
                        println!("This file is password-protected, so its pages cannot be counted. Unlock it first.");
                    }
                }
                ReadResult::Text(records) => {
                    for r in records {
                        println!("--- page {} ---\n{}", r.page, r.text.trim_end());
                    }
                }
                ReadResult::Matches(matches) => {
                    if matches.is_empty() {
                        println!("No matches.");
                    } else {
                        for m in matches {
                            println!("p{} [{}..{}]: {}", m.page, m.span.0, m.span.1, m.snippet);
                        }
                    }
                }
            }
            Ok(StepOutcome::Continue)
        }
        Preview::Write { outputs, summary } => {
            if let Some(style) = ui::view() {
                let verb = if dry_run { "would " } else { "" };
                println!("{}", ui::render_detail(&style, &format!("{verb}{summary}")));
                for o in &outputs {
                    println!(
                        "{}",
                        ui::render_detail(&style, &format!("→ {}", ui::display_rel(o, base)))
                    );
                    if o.exists() {
                        println!(
                            "{}",
                            ui::render_detail(
                                &style,
                                &style.paint(ui::Tone::Warn, "⚠ that file already exists")
                            )
                        );
                    }
                }
            }
            if dry_run {
                if ui::view().is_none() {
                    println!("would {summary} →");
                    for o in &outputs {
                        println!("  {}", o.display());
                    }
                }
                return Ok(StepOutcome::Continue);
            }
            // Surface the operation (incl. the compress method / text-loss warning) before acting.
            if ui::view().is_none() {
                println!("{summary}");
            }
            let previews: Vec<String> = outputs.iter().map(|p| p.display().to_string()).collect();
            if !overwrite_gate(
                &existing_outputs(&previews),
                overwrite,
                &mut ask_yes_no_default,
            )? {
                print_declined();
                return Ok(StepOutcome::Declined);
            }
            if !confirm_action(yes, &previews, "output file")? {
                print_declined();
                return Ok(StepOutcome::Declined);
            }
            let started = std::time::Instant::now();
            let written = commit(tool, step_args, base, sandbox, bundle)?;
            ui::note_written(written.len());
            for w in &written {
                match ui::view() {
                    Some(style) => println!(
                        "{}",
                        ui::render_ok(&style, &ui::display_rel(w, base), Some(started.elapsed()))
                    ),
                    None => println!("✓ wrote {}", w.display()),
                }
            }
            Ok(StepOutcome::Continue)
        }
    }
}

/// Run one ffmpeg command with a live progress line: media time done (of the total, from probing
/// the input), speed and wall time. The line is redrawn in place and erased when ffmpeg exits, so
/// the step's `✓`/`✗` line replaces it. A command whose output has no timeline (a single frame)
/// simply shows the wall time ticking only when ffmpeg reports.
fn run_ffmpeg_live(
    style: &ui::Style,
    cmd: &[String],
    started: std::time::Instant,
) -> anyhow::Result<std::process::Output> {
    use std::io::Write;
    let total = knaif_skill_ffmpeg::exec::input_duration(cmd);
    let mut frame = 0usize;
    let mut draw = |done: f64, speed: Option<f64>| {
        let line = ui::render_progress(style, frame, started.elapsed(), done, total, speed);
        frame += 1;
        let mut out = std::io::stdout();
        let _ = write!(out, "\r{line}");
        let _ = out.flush();
    };
    draw(0.0, None);
    let result =
        knaif_skill_ffmpeg::exec::run_ffmpeg_with_progress(cmd, |p| draw(p.out_seconds, p.speed));
    print!("\r\x1b[2K");
    let _ = std::io::stdout().flush();
    result
}

/// A documents read result, in the terminal view.
fn print_read_result(style: &ui::Style, result: &knaif_skill_documents::run::ReadResult) {
    use knaif_skill_documents::run::ReadResult;
    let line = |text: &str| println!("{}", ui::render_detail(style, text));
    match result {
        ReadResult::Inspection(i) => {
            let kind = i.format.to_uppercase();
            if i.encrypted {
                line(&format!(
                    "{kind} · {} · password-protected, so its pages cannot be counted. Unlock it first.",
                    ui::fmt_bytes(i.size_bytes)
                ));
            } else {
                let text = if i.has_text_layer {
                    "has a text layer"
                } else {
                    "no text layer"
                };
                let pages = if i.pages == 1 {
                    "1 page".to_string()
                } else {
                    format!("{} pages", i.pages)
                };
                line(&format!(
                    "{kind} · {pages} · {} · {text}",
                    ui::fmt_bytes(i.size_bytes)
                ));
            }
        }
        ReadResult::Text(records) => {
            for r in records {
                line(&format!("page {}", r.page));
                for l in r.text.trim_end().lines() {
                    line(&format!("  {l}"));
                }
            }
        }
        ReadResult::Matches(matches) => {
            if matches.is_empty() {
                line("No matches.");
            }
            for m in matches {
                line(&format!("page {}: {}", m.page, m.snippet));
            }
        }
    }
}

/// Ask a `[y/N]` question on the terminal. `Ok(None)` when stdin is not a tty — no answer can be
/// obtained, and each caller decides what that means (a destructive action errors; an optional
/// download silently declines). Prompt goes to stderr so stdout stays machine-readable.
fn ask_yes_no(question: &str) -> anyhow::Result<Option<bool>> {
    ask_yes_no_default(question, false)
}

/// What a typed line means: Enter takes `default_yes`; `y`/`yes` is yes; anything else is no.
fn answer_from_line(line: &str, default_yes: bool) -> bool {
    match line.trim().to_lowercase().as_str() {
        "" => default_yes,
        "y" | "yes" => true,
        _ => false,
    }
}

/// [`ask_yes_no`] with the answer Enter gives: `[Y/n]` when `default_yes`, else `[y/N]`.
fn ask_yes_no_default(question: &str, default_yes: bool) -> anyhow::Result<Option<bool>> {
    use std::io::{IsTerminal, Write};
    if !std::io::stdin().is_terminal() {
        return Ok(None);
    }
    eprint!(
        "{question} {} ",
        if default_yes { "[Y/n]" } else { "[y/N]" }
    );
    std::io::stderr().flush()?;
    // Drop anything typed *before* the prompt appeared (e.g. during a long inference wait) so a
    // stray keystroke can't silently answer this gate — the user must respond to the prompt itself.
    flush_terminal_input();
    let mut line = String::new();
    // The time a person takes to answer is theirs, not the run's.
    let read = ui::timed(|| std::io::stdin().read_line(&mut line))?;
    Ok(Some(answer_from_input(read, &line, default_yes)))
}

/// [`answer_from_line`] for what `read_line` returned: zero bytes is end of input (a closed
/// terminal), not Enter, and never approves anything.
fn answer_from_input(bytes_read: usize, line: &str, default_yes: bool) -> bool {
    bytes_read > 0 && answer_from_line(line, default_yes)
}

/// Whether a step may run without the `Proceed?` question: the default, unless `--confirm` asked
/// for it. `--yes` always skips it, so `--confirm --yes` is `--yes`.
fn approves_without_asking(yes: bool, confirm: bool) -> bool {
    yes || !confirm
}

/// The files an output path names that already exist on disk. A plain path names itself. An
/// image-sequence pattern (`frame_%03d.png`, `%d`) is expanded by ffmpeg's image2 muxer into many
/// files, so it names every file in its folder that fits: the literal text never exists, and the
/// check would pass while `frame_001.png` is replaced.
fn existing_outputs(paths: &[String]) -> Vec<String> {
    let mut found = Vec::new();
    for p in paths {
        match split_sequence_pattern(p) {
            Some((prefix, suffix)) => {
                let path = std::path::Path::new(prefix);
                let dir = path
                    .parent()
                    .filter(|d| !d.as_os_str().is_empty())
                    .unwrap_or(std::path::Path::new("."));
                let stem = path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or_default();
                let Ok(entries) = std::fs::read_dir(dir) else {
                    continue;
                };
                let mut hits: Vec<String> = entries
                    .flatten()
                    .filter_map(|e| {
                        let name = e.file_name().into_string().ok()?;
                        let middle = name.strip_prefix(stem)?.strip_suffix(suffix)?;
                        let digits =
                            !middle.is_empty() && middle.chars().all(|c| c.is_ascii_digit());
                        digits.then(|| {
                            if path.parent().is_some_and(|d| !d.as_os_str().is_empty()) {
                                dir.join(&name).display().to_string()
                            } else {
                                name
                            }
                        })
                    })
                    .collect();
                hits.sort();
                found.extend(hits);
            }
            None if std::path::Path::new(p.as_str()).exists() => found.push(p.clone()),
            None => {}
        }
    }
    found
}

/// `(before, after)` around a `%d` / `%0Nd` frame-number token in the file name, if there is one.
fn split_sequence_pattern(path: &str) -> Option<(&str, &str)> {
    let name_start = path.rfind(['/', '\\']).map_or(0, |i| i + 1);
    let mut from = name_start;
    while let Some(i) = path[from..].find('%') {
        let at = from + i;
        let rest = &path[at + 1..];
        let digits = rest.chars().take_while(char::is_ascii_digit).count();
        if rest[digits..].starts_with('d') {
            return Some((&path[..at], &path[at + 1 + digits + 1..]));
        }
        from = at + 1;
    }
    None
}

/// Replacing a file is the one thing knaif asks about whatever `--yes` or the default says:
/// `Replace <file>? [y/N]`, Enter keeps it. `--overwrite` is the only way to approve it up front.
/// With nobody to ask it stops, naming the flag, rather than replacing silently. `Ok(false)` is a
/// "no" — the step is declined.
fn overwrite_gate(
    existing: &[String],
    overwrite: bool,
    ask: &mut dyn FnMut(&str, bool) -> anyhow::Result<Option<bool>>,
) -> anyhow::Result<bool> {
    if existing.is_empty() || overwrite {
        return Ok(true);
    }
    for path in existing {
        match ask(&format!("Replace {path}?"), false)? {
            Some(true) => {}
            Some(false) => return Ok(false),
            None => anyhow::bail!(
                "{path} already exists. Re-run with --overwrite to replace it, or name a different output."
            ),
        }
    }
    Ok(true)
}

/// Discard any pending terminal input. During the long, silent model-load + inference wait a user
/// may type (assuming nothing is happening); those keystrokes linger in the tty input buffer and
/// would otherwise be consumed by the next confirm prompt or handed to the shell on exit. Flushing
/// afterwards drops them. No-op when stdin is not a terminal.
#[cfg(unix)]
fn flush_terminal_input() {
    use std::io::IsTerminal;
    use std::os::fd::AsRawFd;
    let stdin = std::io::stdin();
    if !stdin.is_terminal() {
        return;
    }
    // SAFETY: `tcflush` on stdin's fd; `TCIFLUSH` only drops unread input and cannot corrupt
    // process state. A failed flush is ignored — the worst case is the pre-existing leak.
    unsafe {
        libc::tcflush(stdin.as_raw_fd(), libc::TCIFLUSH);
    }
}

/// Windows keeps typed-ahead keys in the console input buffer too, so an Enter pressed during a
/// long CPU inference would answer a `[Y/n]` prompt as Yes.
#[cfg(windows)]
fn flush_terminal_input() {
    use std::io::IsTerminal;
    use std::os::windows::io::AsRawHandle;
    let stdin = std::io::stdin();
    if !stdin.is_terminal() {
        return;
    }
    // SAFETY: `FlushConsoleInputBuffer` on stdin's console handle only drops unread input events.
    // A failed flush is ignored — the worst case is the pre-existing leak.
    unsafe {
        windows_sys::Win32::System::Console::FlushConsoleInputBuffer(
            stdin.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE
        );
    }
}

#[cfg(not(any(unix, windows)))]
fn flush_terminal_input() {}

/// Ask `Proceed? [Y/n]` before a step. Only reached under `--confirm`: the default (and `--yes`)
/// acts without asking. With `--confirm` and no terminal there is nobody to ask, so it errors with
/// the preview + how to proceed (never acts silently). `noun` names the previewed items (e.g. "ffmpeg command", "output file").
fn confirm_action(yes: bool, previews: &[String], noun: &str) -> anyhow::Result<bool> {
    use std::io::IsTerminal;
    if yes {
        return Ok(true);
    }
    if !std::io::stdin().is_terminal() {
        for line in previews {
            eprintln!("  {line}");
        }
        anyhow::bail!(
            "{} destructive {noun}(s) pending. Re-run with --yes to proceed, or --dry-run to preview.",
            previews.len()
        );
    }
    if ui::view().is_some() {
        // The step already listed what it will do; just ask, inside the tree.
        return Ok(ask_yes_no_default(" │   Proceed?", true)?.unwrap_or(false));
    }
    eprintln!("About to act on {} {noun}(s):", previews.len());
    for line in previews {
        eprintln!("  {line}");
    }
    // Tty confirmed above, so a `None` (non-interactive) answer is unreachable; decline defensively.
    Ok(ask_yes_no_default("Proceed?", true)?.unwrap_or(false))
}

/// Resolve the skills root and confirm `skill` is a known bundle.
fn resolve_known_skill(skill: &str) -> anyhow::Result<PathBuf> {
    let root = knaif_core::resolve_skills_root()
        .ok_or_else(|| anyhow::anyhow!("no skills/ directory found (set KNAIF_SKILLS_ROOT)"))?;
    let known = knaif_core::list_skills(&root, true);
    if !known.iter().any(|s| s.name == skill) {
        let names: Vec<_> = known.iter().map(|s| s.name.as_str()).collect();
        anyhow::bail!("unknown skill {skill:?}. Available: {}", names.join(", "));
    }
    Ok(root)
}

/// Build a validated plan envelope for `skill` from `utterance`: registry = the skill's
/// `tools.yaml` ∪ the shared core control tools; construct the model prompt (skill `prompt.yaml`
/// header/examples + tool list) → select the backend (`model` path → llama.cpp, else the mock
/// honoring `KNAIF_LLM_MOCK_RESPONSE`) → infer → extract JSON → parse → normalize → apply defaults
/// → validate (paths resolved against `base`, `sandbox` boundary enforced when set).
fn build_plan(
    root: &Path,
    skill: &str,
    utterance: &str,
    base: &Path,
    sandbox: Option<&Path>,
    model: Option<&Path>,
    verbose: bool,
) -> anyhow::Result<serde_json::Value> {
    PlanSession::new(root, skill, model, verbose)?.plan_for_run(utterance, base, sandbox)
}

/// A loaded planning session: the expensive per-run setup (skill registry, prompt overrides, and
/// the model backend) built once and reused across utterances. The model is loaded exactly once in
/// [`knaif_llm::backend_for`]; `plan` creates a fresh inference context per utterance (see
/// `LlamaCppBackend::generate_plan`), so batching cannot leak state between utterances.
struct PlanSession {
    registry: knaif_core::Registry,
    overrides: knaif_core::PromptOverrides,
    output_capable: std::collections::HashSet<String>,
    /// The skill's `file_kinds:` — chain threading never crosses kinds.
    file_kinds: knaif_core::FileKinds,
    backend: Box<dyn knaif_llm::LlmBackend>,
    /// Repair only for a real model — the mock repeats its canned response, so a retry is pointless.
    repair: bool,
}

impl PlanSession {
    fn new(root: &Path, skill: &str, model: Option<&Path>, verbose: bool) -> anyhow::Result<Self> {
        let bundle = root.join(skill);
        let mut registry = knaif_core::load_registry(&bundle.join("tools.yaml"))?;
        // The control tools (`clarify`/`reject`/`done`) are how the model says "no" or "which
        // file?". Without them every such answer fails as `Unknown tool`, so a missing file is a
        // broken install, not something to plan around.
        let core = resolve_repo_file("contracts/runtime/core_tools.yaml").ok_or_else(|| {
            anyhow::anyhow!(
                "contracts/runtime/core_tools.yaml not found: knaif looks for it in the current \
                 folder and its parents, then beside the executable. Reinstall knaif, or run \
                 from a checkout."
            )
        })?;
        registry.extend(knaif_core::load_registry(&core)?);
        let overrides = knaif_core::load_prompt_yaml(&bundle.join("prompt.yaml"));
        let output_capable = knaif_core::output_capable_tools(&registry);
        let file_kinds =
            knaif_core::load_file_kinds(&bundle.join("skill.yaml")).map_err(anyhow::Error::msg)?;
        let loading = std::time::Instant::now();
        // A running daemon that holds this very model stands in for the load; anything else
        // (none running, another model or build, a generation setting overridden here) loads the
        // model in this process exactly as before.
        let resident = model
            .and_then(|m| daemon::connect(&daemon::state_dir(), m, &daemon::build_id(), verbose));
        let backend: Box<dyn knaif_llm::LlmBackend> = match resident {
            Some(remote) => Box::new(remote),
            None => knaif_llm::backend_for(model, verbose)?,
        };
        ui::set_load(loading.elapsed());
        Ok(Self {
            registry,
            overrides,
            output_capable,
            file_kinds,
            backend,
            repair: model.is_some(),
        })
    }

    /// The skill's prompt overrides with the examples block filtered for *this* utterance.
    ///
    /// Mirrors `CommandAgent.build_prompt`, including its fallback: when the corpus has no
    /// examples, or selection returns nothing, the unfiltered block stands. Cloning the header per
    /// utterance is deliberate — it keeps the loaded overrides immutable, and it is a rounding
    /// error next to the inference it precedes.
    fn examples_for(
        &self,
        utterance: &str,
        retrieved: &knaif_core::RetrievedTools<'_>,
    ) -> knaif_core::PromptOverrides {
        let names: std::collections::HashSet<String> = retrieved
            .iter()
            .filter(|(_, d)| !d.internal)
            .map(|(n, _)| n.clone())
            .collect();
        let selected = knaif_core::select_examples(
            &self.overrides.examples,
            &names,
            utterance,
            knaif_core::MAX_TOOL_EXAMPLES,
        );
        let block = (!selected.is_empty())
            .then(|| knaif_core::render_examples_block(&selected))
            .or_else(|| self.overrides.examples_block.clone());
        knaif_core::PromptOverrides {
            system_header: self.overrides.system_header.clone(),
            examples_block: block,
            examples: Vec::new(),
        }
    }

    /// Plan a single utterance: prompt → infer (+repair) → chain-link + hallucinated-filename gate.
    fn plan(
        &self,
        utterance: &str,
        base: &Path,
        sandbox: Option<&Path>,
    ) -> anyhow::Result<serde_json::Value> {
        // A model echoing a Windows path verbatim (e.g. `.\clip.mov`) would emit an illegal `\c`
        // JSON escape; normalize separators to forward slashes (accepted by ffmpeg + `std::path` on
        // Windows) before the utterance reaches the prompt so the emitted plan parses.
        let utterance = normalize_path_separators(utterance);
        // Retrieval (V1): show the model the tools relevant to *this* utterance, in relevance
        // order, rather than the whole registry. The port existed in knaif-core but nothing
        // called it, so native's prompt listed all 13 ffmpeg tools where the reference lists 5 —
        // the single largest prompt divergence between the runtimes. `retrieve_tools` returns a
        // ranked Vec, and `build_prompt_ordered` renders it as given.
        let retrieved =
            knaif_core::retrieve_tools(&utterance, &self.registry, knaif_core::DEFAULT_TOP_K, 0.0);
        let tools: Vec<&knaif_core::ToolDef> = retrieved.iter().map(|(_, d)| *d).collect();
        // Example selection (V2): the other half of the same divergence. `prompt.yaml`'s whole
        // block went to the model on every utterance — 28 examples for ffmpeg where the reference
        // sends 5, chosen against the tools retrieval just picked. The S3g factorial settled the
        // direction: static examples win the ffmpeg *aggregate* but push `concat_video` below its
        // acceptance floor, so Python keeps `select_examples` and native gains it.
        let overrides = self.examples_for(&utterance, &retrieved);
        let (system, user) = knaif_core::build_prompt_ordered(&utterance, &tools, &overrides);
        emit_prompt_dump(prompt_dump_enabled(), &system, &user);
        let payload = infer_with_repair(
            self.backend.as_ref(),
            &system,
            &user,
            &self.registry,
            base,
            sandbox,
            self.repair,
        )?;
        // Chain-intermediate linking + hallucinated-input-filename gate (ports of Python
        // `_link_chain_intermediates` + `_hallucinated_filename`): bind undeclared chain outputs,
        // then downgrade to a clarify when the model invented an input file the utterance never
        // named. Applied here so both `run` and `plan` inherit it, matching Python's `infer`.
        // The guard's stem exemption needs to know which files are really there. Read from the
        // same directory stem resolution uses two lines down — `sandbox` when configured, else
        // the cwd — so the guard cannot admit a name the resolver would then refuse.
        let known_files = listed_filenames(sandbox.unwrap_or(base));
        let gated = knaif_core::apply_clarify_gate(
            payload,
            &utterance,
            &self.output_capable,
            &known_files,
            &self.file_kinds,
        );
        // Extension-less stems (`clip_4k`, `silent_clip`) resolve against the working directory,
        // or become a clarify when it cannot decide — port of Python's `resolve_stems` call in
        // `CommandAgent._execute_steps`, applied at the same stage (N2). Without it native
        // rendered `-i clip_4k` verbatim and acted on an ambiguous reference where Python asked.
        //
        // **Resolved against `sandbox` when set, otherwise `base` (the cwd) — and the second half
        // is a deliberate widening of Python's rule**, which skips stem resolution entirely when
        // no sandbox is configured. Two reasons: open/CLI mode is exactly how the shipped binary
        // is used and how the L4 lane drives it, so gating on a sandbox would leave the defect in
        // place everywhere it actually bites; and native already resolves *relative inputs*
        // against the cwd in this mode, so resolving stems there too is consistent with how the
        // same path is already read rather than a new notion of where files live.
        let gated = resolve_plan_stems(gated, sandbox.unwrap_or(base));
        emit_plan_dump(plan_dump_enabled(), &gated);
        Ok(gated)
    }

    /// [`Self::plan`], then the NL clarify gate: the plan `run` executes. Python runs that gate
    /// in `execute_plan` right after `resolve_stems`, so it asks when the user never named an
    /// input the plan uses ("reverse the mov file" -> inputs ["mov"]) or when a grounded arg such
    /// as a password was invented. Without it native ran such plans and failed with "input not
    /// found" where Python asked (R5c L3, 2026-09-28).
    ///
    /// Execution only, as in Python, whose `plan` command stops at `infer` and never applies it
    /// (Codex audit, 2026-09-28): `plan` / `plan --batch` keep returning the ungated plan. And
    /// after the plan dump, since Python dumps before `execute_plan`: the dumped plans stay
    /// comparable across runtimes (L3's "same plan") and across builds (R5c T9a), and a gate
    /// that fires shows as the run's clarify outcome.
    fn plan_for_run(
        &self,
        utterance: &str,
        base: &Path,
        sandbox: Option<&Path>,
    ) -> anyhow::Result<serde_json::Value> {
        let planned = self.plan(utterance, base, sandbox)?;
        // The text the model was shown, as in Python (`agent.py` normalizes before the gate). A
        // grounded value the model copied from it (`a/b`) is only found in that spelling: against
        // the raw request a password written `a\b` was always "invented" and asked for again.
        let shown = normalize_path_separators(utterance);
        let gated = knaif_core::nl_clarify_gate(planned, &shown, &self.registry);
        Ok(restore_grounded_args(gated, utterance, &self.registry))
    }
}

/// Lowercased names of the files directly inside `dir`, for the clarify gate's stem exemption.
///
/// Non-recursive and files-only, matching what stem resolution globs. An unreadable directory
/// yields an empty set, which restores the strict substring rule rather than failing the plan.
fn listed_filenames(dir: &Path) -> std::collections::HashSet<String> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return std::collections::HashSet::new();
    };
    entries
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_file()).unwrap_or(false))
        .map(|e| e.file_name().to_string_lossy().to_lowercase())
        .collect()
}

/// Infer a plan and, on parse/validation failure, retry once with the error fed back (port of the
/// Python `repair_invalid_plans` loop). `repair=false` skips the retry (mock backend).
fn infer_with_repair(
    backend: &dyn knaif_llm::LlmBackend,
    system: &str,
    user: &str,
    registry: &knaif_core::Registry,
    base: &Path,
    sandbox: Option<&Path>,
    repair: bool,
) -> anyhow::Result<serde_json::Value> {
    let debug = debug_enabled();
    let thinking = std::time::Instant::now();
    let raw = backend.generate_plan(system, user)?;
    ui::add_infer(thinking.elapsed());
    match try_build_payload(&raw, registry, base, sandbox) {
        Ok(payload) => Ok(payload),
        Err(first_err) if repair => {
            let previous = knaif_core::extract_json(&raw).json;
            emit_debug(debug, &raw, &previous);
            let feedback =
                knaif_core::validator_feedback_prompt(user, &previous, &first_err.to_string());
            let thinking = std::time::Instant::now();
            let retry_raw = backend.generate_plan(system, &feedback)?;
            ui::add_infer(thinking.elapsed());
            // If the corrected plan is still bad, report the original error (as Python does).
            match try_build_payload(&retry_raw, registry, base, sandbox) {
                Ok(payload) => Ok(payload),
                Err(_) => {
                    emit_debug(
                        debug,
                        &retry_raw,
                        &knaif_core::extract_json(&retry_raw).json,
                    );
                    // The re-prompt had its chance. An arg no known tool can express is an
                    // inventory gap, not a malformed plan, so say so instead of surfacing
                    // "Tool 'adjust_volume' has unsupported args: [...]" to the user.
                    unsupported_gap(&retry_raw, &raw, registry).ok_or(first_err)
                }
            }
        }
        Err(e) => {
            emit_debug(debug, &raw, &knaif_core::extract_json(&raw).json);
            // No repair configured, so this is already the last word.
            unsupported_gap(&raw, &raw, registry).ok_or(e)
        }
    }
}

/// The unsupported-arg clarify, from whichever attempt still shows one.
///
/// Checks the retry first and falls back to the original: the retry is the more recent answer,
/// but a retry that failed some *other* way should not mask an inventory gap the first attempt
/// showed plainly.
fn unsupported_gap(
    latest: &str,
    original: &str,
    registry: &knaif_core::Registry,
) -> Option<serde_json::Value> {
    [latest, original]
        .iter()
        .filter_map(|raw| prepared_payload(raw, registry))
        .find_map(|p| knaif_core::unsupported_args_clarify(&p, registry))
}

/// Whether to dump raw model output on a parse/validation failure (`$KNAIF_DEBUG` non-empty).
fn debug_enabled() -> bool {
    std::env::var("KNAIF_DEBUG")
        .map(|v| !v.is_empty())
        .unwrap_or(false)
}

/// Print the debug dump to stderr when enabled (thin wrapper over [`debug_dump`]).
fn emit_debug(enabled: bool, raw: &str, extracted: &str) {
    if let Some(msg) = debug_dump(enabled, raw, extracted) {
        eprintln!("{msg}");
    }
}

/// Format the raw model output + extracted JSON for a failed inference, or `None` when disabled.
/// Kept pure (the enable gate is a parameter) so it is testable without mutating process env.
fn debug_dump(enabled: bool, raw: &str, extracted: &str) -> Option<String> {
    if !enabled {
        return None;
    }
    Some(format!(
        "[knaif debug] model output did not yield a valid plan.\n\
         --- raw model output ---\n{raw}\n\
         --- extracted JSON ---\n{extracted}\n\
         ------------------------"
    ))
}

/// Frame marker for [`plan_dump`]: the validated, post-gate plan `run` is about to execute.
///
/// L4 grades the **shipped** path — `run`, with real execution — but a scoreboard also carries
/// tool/argument metrics, which need the plan. Without this the L4 lane would have to either run
/// inference twice (once for `plan --json`, once for `run`, with no guarantee the two agree) or
/// report `predicted_tool = None` for every row, which scores as 0% tool accuracy and invents a
/// catastrophe. An env gate on the shared `PlanSession::plan` costs nothing when unset and covers
/// `run`, `plan` and `plan --batch` alike — same reasoning as [`PROMPT_DUMP_MARKER`].
const PLAN_DUMP_MARKER: &str = "===KNAIF-PLAN===";

fn plan_dump_enabled() -> bool {
    std::env::var("KNAIF_DUMP_PLAN")
        .map(|v| !v.is_empty())
        .unwrap_or(false)
}

/// Print the plan dump to stderr when enabled. stderr, not stdout: `run`'s stdout is the user-
/// facing preview/result, and a capture has to be able to keep the two apart.
fn emit_plan_dump(enabled: bool, payload: &serde_json::Value) {
    if let Some(msg) = plan_dump(enabled, payload) {
        eprintln!("{msg}");
    }
}

/// One line: the marker, then the plan envelope as compact JSON. Single-line by design — a
/// consumer scans stderr for the marker and parses the remainder, with no multi-line framing to
/// get wrong. Kept pure (the gate is a parameter) so it is testable without mutating process env.
fn plan_dump(enabled: bool, payload: &serde_json::Value) -> Option<String> {
    if !enabled {
        return None;
    }
    Some(format!(
        "{PLAN_DUMP_MARKER}{}",
        serde_json::to_string(payload).unwrap_or_else(|_| "{}".to_string())
    ))
}

/// Marker for [`argv_dump`]: one line per rendered ffmpeg command, the exact argv as JSON.
const ARGV_DUMP_MARKER: &str = "===KNAIF-ARGV===";

/// One line: the marker, then the argv as a JSON array. Gated with the plan dump. L3 compares it
/// instead of the display line, which cannot carry every argv once re-split (a space in a
/// filename, a filter's `\,`). Pure, so testable without process env.
fn argv_dump(enabled: bool, argv: &[String]) -> Option<String> {
    if !enabled {
        return None;
    }
    Some(format!(
        "{ARGV_DUMP_MARKER}{}",
        serde_json::to_string(argv).unwrap_or_else(|_| "[]".to_string())
    ))
}

/// Marker for [`result_dump`]: one line per read-tool result, as data.
const RESULT_DUMP_MARKER: &str = "===KNAIF-RESULT===";

/// A documents read result in Python's shape (`skills/documents/python/steps.py`), which the
/// documents verifier grades: `inspect_document` → format/size_bytes/encrypted/has_text_layer/pages;
/// `extract_text` → pages + joined text; `find_in_document` → matches + count.
fn read_result_json(result: &knaif_skill_documents::run::ReadResult) -> serde_json::Value {
    use knaif_skill_documents::run::ReadResult;
    match result {
        ReadResult::Inspection(i) => serde_json::json!({
            "format": i.format, "size_bytes": i.size_bytes, "encrypted": i.encrypted,
            "has_text_layer": i.has_text_layer, "pages": i.pages,
        }),
        ReadResult::Text(records) => serde_json::json!({
            "pages": records.iter().map(|r| serde_json::json!({"page": r.page, "text": r.text}))
                .collect::<Vec<_>>(),
            "text": records.iter().map(|r| r.text.as_str()).collect::<Vec<_>>().join("\n"),
        }),
        ReadResult::Matches(matches) => serde_json::json!({
            "matches": matches.iter().map(|m| serde_json::json!({
                "page": m.page, "snippet": m.snippet, "span": [m.span.0, m.span.1],
            })).collect::<Vec<_>>(),
            "count": matches.len(),
        }),
    }
}

/// One line: the marker, then `{"tool", "result"}` as compact JSON. Gated with the plan dump
/// (`$KNAIF_DUMP_PLAN`) because the consumer is the same: the L4 lane, which grades data and could
/// not read the prose answer a read tool prints (the 2026-09-24 documents run scored every
/// inspect/extract/find row as `None`). Pure, so testable without process env.
fn result_dump(enabled: bool, tool: &str, result: &serde_json::Value) -> Option<String> {
    if !enabled {
        return None;
    }
    let body = serde_json::json!({"tool": tool, "result": result});
    Some(format!(
        "{RESULT_DUMP_MARKER}{}",
        serde_json::to_string(&body).unwrap_or_else(|_| "{}".to_string())
    ))
}

/// Frame marker for [`prompt_dump`]. Both halves of the prompt are wrapped in `BEGIN`/`END` lines
/// carrying this prefix so a capture can cut them back out exactly; the marker is deliberately
/// unlikely to occur inside a prompt.
const PROMPT_DUMP_MARKER: &str = "===KNAIF-PROMPT-";

/// Whether to dump the built prompt before inference (`$KNAIF_DUMP_PROMPT` non-empty).
///
/// An env gate rather than a `--dump-prompt` flag on `plan`: the prompt is built inside
/// [`PlanSession::plan`], which `plan`, `plan --batch` and `run` all share, so a gate here covers
/// every path — including the batch path a corpus-wide capture needs — without threading a flag
/// through three commands. Mirrors [`debug_enabled`].
fn prompt_dump_enabled() -> bool {
    std::env::var("KNAIF_DUMP_PROMPT")
        .map(|v| !v.is_empty())
        .unwrap_or(false)
}

/// Print the prompt dump to stderr when enabled (thin wrapper over [`prompt_dump`]).
///
/// stderr, not stdout: `plan --json` and `plan --batch` put their envelopes on stdout, so a
/// capture can redirect the two streams to separate files and keep both machine-readable.
fn emit_prompt_dump(enabled: bool, system: &str, user: &str) {
    if let Some(msg) = prompt_dump(enabled, system, user) {
        eprintln!("{msg}");
    }
}

/// Frame the `(system, user)` messages for capture, or `None` when disabled. Kept pure (the enable
/// gate is a parameter) so it is testable without mutating process env, as [`debug_dump`] is.
///
/// **This is a dump, not a formatter.** Each message is written between its markers byte for byte
/// — no trimming, wrapping, escaping or re-encoding. Workstream P1 diffs this output against
/// Python's prompt for the same utterance, and R1 uses it to produce the native side of a golden;
/// any reshaping here would make that diff a diff of this function instead of of the prompts.
fn prompt_dump(enabled: bool, system: &str, user: &str) -> Option<String> {
    if !enabled {
        return None;
    }
    Some(format!(
        "{PROMPT_DUMP_MARKER}BEGIN system\n{system}\n{PROMPT_DUMP_MARKER}END system\n\
         {PROMPT_DUMP_MARKER}BEGIN user\n{user}\n{PROMPT_DUMP_MARKER}END user"
    ))
}

/// Normalize Windows-style backslash path separators in an utterance to forward slashes. A model
/// that echoes a path verbatim (`.\clip.mov`) would otherwise emit an illegal `\c` JSON escape;
/// forward slashes are accepted by ffmpeg and `std::path` on Windows, so this is lossless for the
/// file-path domain these skills operate in.
/// **Only path-shaped tokens are rewritten** (V3). This used to replace *every* backslash in
/// the utterance, which differs from Python on two shapes the prompt contract pins: a quoted
/// path (the quotes make it not a path token, and it is not a single token anyway) and a lone
/// backslash, which has no alphanumeric and stays literal. Splitting on `' '` rather than any
/// whitespace also mirrors the reference, so a tab is not silently normalized away.
fn normalize_path_separators(utterance: &str) -> String {
    if !utterance.contains('\\') {
        return utterance.to_string();
    }
    utterance
        .split(' ')
        .map(|token| {
            if token.contains('\\') && is_path_token(token) {
                token.replace('\\', "/")
            } else {
                token.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Undo [`normalize_path_separators`] on a value the user typed, such as a password.
///
/// Port of Python's `restore_grounded_spelling` (`prompt.py`). The model only saw the normalized
/// request, so a password typed `p\ss` comes back as `p/ss`: right for a path, wrong for a secret.
/// The rewrite is one byte for one (`\` and `/` are both ASCII), so the user's spelling sits at
/// the same offsets in the original token. A value found verbatim in the request is left alone.
fn restore_grounded_spelling(value: &str, raw_utterance: &str) -> String {
    if !value.contains('/') || !raw_utterance.contains('\\') || raw_utterance.contains(value) {
        return value.to_string();
    }
    for token in raw_utterance.split(' ') {
        let normalized = normalize_path_separators(token);
        if normalized == token {
            continue;
        }
        if let Some(at) = normalized.find(value) {
            return token[at..at + value.len()].to_string();
        }
    }
    value.to_string()
}

/// Give each `grounded_args` value in a plan the spelling the user typed. Runs after the clarify
/// gate, which grounds against the normalized text; paths keep their forward slashes.
fn restore_grounded_args(
    mut payload: serde_json::Value,
    raw_utterance: &str,
    registry: &knaif_core::Registry,
) -> serde_json::Value {
    if !raw_utterance.contains('\\') {
        return payload;
    }
    let Some(steps) = payload
        .get_mut("plan")
        .and_then(serde_json::Value::as_array_mut)
    else {
        return payload;
    };
    for step in steps {
        let tool = step
            .get("tool")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string();
        let Some(def) = registry.get(&tool) else {
            continue;
        };
        let Some(args) = step
            .get_mut("args")
            .and_then(serde_json::Value::as_object_mut)
        else {
            continue;
        };
        for arg in &def.grounded_args {
            if let Some(serde_json::Value::String(v)) = args.get_mut(arg) {
                *v = restore_grounded_spelling(v, raw_utterance);
            }
        }
    }
    payload
}

/// Is this space-delimited token shaped like a path?
///
/// Port of Python's `_PATH_TOKEN_RE` (`prompt.py`): made only of path characters (ASCII word
/// chars, `-`, `.`, `:`, backslash, `/`) with **at least one alphanumeric**, so a bare
/// backslash stays literal. Spelled out rather than pulling in a regex dependency — it is one
/// character-class test and the CLI has no other use for `regex`.
fn is_path_token(token: &str) -> bool {
    !token.is_empty()
        && token
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.' | ':' | '\\' | '/'))
        && token.chars().any(|c| c.is_ascii_alphanumeric())
}

/// Extract JSON → parse → normalize → apply defaults → validate. Errors describe the first failure.
///
/// The two arg-shape clarify gates sit either side of validation, and Python gives them
/// *different* retry semantics, which this mirrors rather than simplifying:
///
/// * `required_args_clarify` runs here, before validation, and short-circuits. The model
///   omitted something only the user can supply, so a corrective re-prompt would invite it to
///   invent the value and turn a correct clarify into a wrong plan. Python defers to this gate
///   by reporting the probe "clean" so no retry fires (`agent.py::_parse_and_check`).
/// * `unsupported_args_clarify` does NOT run here — see `build_payload_with_repair`. An
///   undeclared arg is worth one corrective re-prompt, because the model may well pick a
///   different tool or drop the arg; only once that has failed is it an inventory gap.
fn try_build_payload(
    raw: &str,
    registry: &knaif_core::Registry,
    base: &Path,
    sandbox: Option<&Path>,
) -> anyhow::Result<serde_json::Value> {
    let extracted = knaif_core::extract_json(raw);
    let mut payload = knaif_core::parse_plan(&extracted.json)?;
    knaif_core::normalize_plan(&mut payload, Some(registry));
    knaif_core::apply_defaults(&mut payload, registry);
    if let Some(clarify) = knaif_core::required_args_clarify(&payload, registry) {
        return Ok(clarify);
    }
    knaif_core::validate_plan(&payload, registry, base, sandbox)?;
    Ok(payload)
}

/// Re-derive a plan as far as `validate_plan` would see it, for the post-retry arg-shape gate.
///
/// Stops one step short of validation deliberately: this exists to inspect a plan that validation
/// has already refused, so running it again would only re-raise the error being handled.
fn prepared_payload(raw: &str, registry: &knaif_core::Registry) -> Option<serde_json::Value> {
    let extracted = knaif_core::extract_json(raw);
    let mut payload = knaif_core::parse_plan(&extracted.json).ok()?;
    knaif_core::normalize_plan(&mut payload, Some(registry));
    knaif_core::apply_defaults(&mut payload, registry);
    Some(payload)
}

/// Resolve a `--model` argument (a raw path, or a manifest/installed name via the shared store) to a
/// GGUF file. A direct path wins; otherwise the [`ModelStore`] resolves an installed name.
fn resolve_model_path(model: &str) -> anyhow::Result<PathBuf> {
    if Path::new(model).is_file() {
        return Ok(PathBuf::from(model));
    }
    let store = ModelStore::open(&resolve_manifest_path()?)?;
    store.path_for(model).ok_or_else(|| {
        anyhow::anyhow!(
            "model {model:?} not found — not a file path, and not installed in the store ({}). \
             Try `knaif models pull {model}`.",
            store.dir().display()
        )
    })
}

/// What a surface may do when no `--model` was given and the recommended model is not installed.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum DownloadPolicy {
    /// May ask for consent on a tty and download (`run`).
    Prompt,
    /// Never asks; downloads only when `--yes` was passed (`plan`, which is non-prompting).
    YesOnly,
    /// Never downloads (`plan --batch`/`--json`: a prompt mid-stream would corrupt the output).
    Never,
}

/// Render a byte count the way the manifest's own scale reads (decimal GB/MB, as vendors quote
/// model sizes), for the download consent prompt.
fn human_size(bytes: u64) -> String {
    const GB: f64 = 1_000_000_000.0;
    const MB: f64 = 1_000_000.0;
    let b = bytes as f64;
    if b >= GB {
        format!("{:.1} GB", b / GB)
    } else {
        format!("{:.0} MB", b / MB)
    }
}

/// Whether the user explicitly opted out of real inference via `$KNAIF_LLM_BACKEND=mock`.
fn mock_forced() -> bool {
    std::env::var("KNAIF_LLM_BACKEND").ok().as_deref() == Some("mock")
}

/// Pick the model backing this invocation. `None` means "run the offline mock".
///
/// Precedence (NATIVE.md §5.6):
/// 1. an explicit `--model` name/path always wins;
/// 2. `$KNAIF_LLM_BACKEND=mock` opts out of auto-select (offline/eval runs);
/// 3. the manifest's recommended model, when already installed, is used silently;
/// 4. otherwise `policy` decides whether to offer the download — a declined or impossible
///    download falls back to the mock, so a multi-GB pull never happens without consent.
///
/// Callers must invoke this only *after* cheap rejections (bad request, safety gate, dependency
/// preflight) so we never download 2.5 GB and then refuse the request.
fn select_model(
    explicit: Option<&str>,
    yes: bool,
    policy: DownloadPolicy,
) -> anyhow::Result<Option<PathBuf>> {
    select_model_with(explicit, yes, policy, cfg!(feature = "llama"))
}

/// [`select_model`] with the build's inference capability injected, so both build shapes
/// (mock-only and llama-enabled) are exercisable from a single default-feature test run.
fn select_model_with(
    explicit: Option<&str>,
    yes: bool,
    policy: DownloadPolicy,
    llama_available: bool,
) -> anyhow::Result<Option<PathBuf>> {
    if let Some(name) = explicit {
        // Authoritative even in a mock-only build, where it earns a "rebuild with --features
        // llama" error — an explicit request must never be silently downgraded to the mock.
        return Ok(Some(resolve_model_path(name)?));
    }
    if !llama_available {
        // No llama.cpp backend compiled in: the mock is the only thing that can run, so
        // auto-selecting a model could only turn a working mock run into a load error.
        return Ok(None);
    }
    if mock_forced() {
        return Ok(None);
    }
    let (recommended, installed) = recommended_model_status();
    let Some(name) = recommended else {
        return Ok(None); // no manifest recommendation → generic first-run guidance
    };
    if installed {
        return Ok(Some(resolve_model_path(&name)?));
    }
    match policy {
        DownloadPolicy::Never => return Ok(None),
        DownloadPolicy::YesOnly if !yes => return Ok(None),
        _ => {}
    }
    offer_recommended_download(&name, yes)
}

/// Offer to download the recommended model, then pull it with a progress bar. `Ok(None)` when the
/// user declines or cannot be asked — the caller falls back to the mock plus first-run guidance.
/// A failed pull is surfaced as an error rather than silently degrading to the mock.
fn offer_recommended_download(name: &str, yes: bool) -> anyhow::Result<Option<PathBuf>> {
    let store = ModelStore::open(&resolve_manifest_path()?)?;
    let size = store
        .list()
        .into_iter()
        .find(|e| e.name == name)
        .and_then(|e| e.size_bytes);
    let question = match size {
        Some(bytes) => format!(
            "Download recommended model {name} (~{})?",
            human_size(bytes)
        ),
        None => format!("Download recommended model {name}?"),
    };
    let approved = yes || ask_yes_no(&question)?.unwrap_or(false);
    if !approved {
        return Ok(None);
    }

    let bar = download_bar(name);
    let pulled = store.pull_with_progress(name, &HttpFetcher::new(), &mut |done, total| {
        if let Some(t) = total {
            if bar.length() != Some(t) {
                bar.set_length(t);
            }
        }
        bar.set_position(done);
    });
    match pulled {
        Ok(path) => {
            bar.finish_and_clear();
            eprintln!("Installed {name} -> {}", path.display());
            Ok(Some(path))
        }
        Err(e) => {
            bar.abandon();
            Err(e)
        }
    }
}

/// Join an argv into a copy-pasteable command line, quoting only tokens that need it.
fn shell_join(argv: &[String]) -> String {
    argv.iter()
        .map(|a| {
            if a.is_empty() || a.chars().any(|c| c.is_whitespace() || "\"'\\".contains(c)) {
                format!("\"{}\"", a.replace('\\', "\\\\").replace('"', "\\\""))
            } else {
                a.clone()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Locate a bundled data file (`contracts/runtime/core_tools.yaml`, `contracts/models/model-manifest.yaml`).
/// Dev checkout: walk up from the current dir. Installed: resolve relative to the executable, so a
/// packaged `knaif` run from any directory still finds its data.
fn resolve_repo_file(rel: &str) -> Option<PathBuf> {
    if let Ok(start) = std::env::current_dir() {
        if let Some(found) = start.ancestors().map(|a| a.join(rel)).find(|p| p.is_file()) {
            return Some(found);
        }
    }
    let exe = std::env::current_exe().ok()?;
    file_near(exe.parent()?, rel)
}

/// Find `rel` relative to the executable's directory: beside it, or one level up (the
/// `<install>/bin/knaif` + `<install>/contracts/...` layout). First existing file wins.
fn file_near(exe_dir: &Path, rel: &str) -> Option<PathBuf> {
    let mut candidates = vec![exe_dir.join(rel)];
    if let Some(parent) = exe_dir.parent() {
        candidates.push(parent.join(rel));
    }
    candidates.into_iter().find(|p| p.is_file())
}

/// Locate the model manifest: `$KNAIF_MODEL_MANIFEST` else walk up for
/// `contracts/models/model-manifest.yaml` in a checkout.
fn resolve_manifest_path() -> anyhow::Result<PathBuf> {
    if let Ok(p) = std::env::var("KNAIF_MODEL_MANIFEST") {
        if !p.is_empty() {
            return Ok(PathBuf::from(p));
        }
    }
    resolve_repo_file("contracts/models/model-manifest.yaml").ok_or_else(|| {
        anyhow::anyhow!(
            "model manifest not found (set KNAIF_MODEL_MANIFEST or run from a checkout)"
        )
    })
}

/// Print the CUDA offer for this machine, if there is one worth printing.
///
/// Deliberately **best-effort and silent on failure**: an unreadable manifest or a missing
/// `nvidia-smi` must never interfere with a run that was going to work. It is also silent for the
/// large majority of machines — no NVIDIA GPU, or the payload already installed — because an
/// unsolicited GPU message on an AMD laptop is noise.
///
/// `$KNAIF_NO_CUDA_NUDGE` suppresses the *offer* for anyone who has decided not to install the
/// payload and does not want to be told again. It does not suppress the stale/interrupted report:
/// that one is about a payload they already have.
/// Can this *build* actually use a downloaded CUDA payload?
///
/// Two independent facts decide it, and [`knaif_models::cuda_offer`] can see neither — it reads
/// the install receipt in `~/.knaif/backends`, which is empty in both failing cases below:
///
/// * **`cuda_compiled_in`** — `just eval-native` builds `llama,cuda,pdfium` and the release `cuda`
///   kind builds `llama,dynamic-backends,cuda`. The backend is already there (static in the first,
///   staged beside the exe in the second), so the offer told a CUDA-capable binary to install CUDA.
/// * **`can_load_payloads`** — without `dynamic-backends`, `load_dynamic_backends` is a no-op and
///   `~/.knaif/backends` is never scanned. Following the advice downloads ~668 MB that is never
///   `dlopen`ed.
///
/// Both mistakes reach the user as "CUDA didn't work", which the `DriverTooOld` branch already
/// calls the least debuggable outcome available; that branch exists precisely to avoid handing
/// someone a payload that cannot load, and these two cases are the same error from the other side.
///
/// Taken as parameters rather than read from `cfg!` inside, so every combination is testable in
/// the default (feature-free) test build — the configurations that are wrong are exactly the ones
/// CI never compiles.
fn cuda_payload_is_worth_offering(cuda_compiled_in: bool, can_load_payloads: bool) -> bool {
    can_load_payloads && !cuda_compiled_in
}

fn print_cuda_offer(gpu_active: bool) {
    // Nothing below is worth saying if this build could not use the payload anyway. This also
    // silences `NeedsReinstall`, deliberately: in a build that cannot load payloads the receipt is
    // irrelevant, and in a CUDA build a skipped payload changes nothing — CUDA still works, and
    // `knaif backend install cuda` would not be the fix in either case.
    if !cuda_payload_is_worth_offering(cfg!(feature = "cuda"), cfg!(feature = "dynamic-backends")) {
        return;
    }
    let Ok(store) = resolve_backend_manifest_path().and_then(|p| BackendStore::open(&p)) else {
        return;
    };
    // `KNAIF_NO_CUDA_NUDGE` answers an *offer* — "I know about the payload and I don't want it". It
    // must not silence `NeedsReinstall`, which is not an offer but a report that a payload the user
    // already downloaded is being ignored; the loader itself only says so under `--verbose`, so
    // this is the only place that fact reaches them.
    //
    // Passing no GPUs is what keeps the suppression cheap: every offer branch needs the probe and
    // collapses to `NotApplicable` without one, while both reinstall branches are decided by the
    // receipt alone. So a suppressed run still spawns no `nvidia-smi`.
    let suppressed = std::env::var("KNAIF_NO_CUDA_NUDGE").is_ok_and(|v| !v.is_empty());
    let gpus = if suppressed {
        Vec::new()
    } else {
        knaif_models::probe_nvidia()
    };
    if let Some((warning, text)) =
        cuda_offer_text(&knaif_models::cuda_offer(&store, &gpus), gpu_active)
    {
        advisory(warning, &text);
    }
}

/// What to say about the CUDA payload: `(is_warning, text)`, or `None` for nothing to say.
///
/// `gpu_active` is whether this run's own backend found a GPU. The offer only sees the NVIDIA
/// probe, so without it an optional offer told a user already running on the CPU that Vulkan
/// "already works here" (WSL with no Vulkan device, 2026-10-01). There the payload is the fix.
fn cuda_offer_text(offer: &CudaOffer, gpu_active: bool) -> Option<(bool, String)> {
    match offer {
        CudaOffer::NotApplicable | CudaOffer::AlreadyInstalled => None,
        // Stated in correctness terms, not speed terms: on this hardware the Vulkan fallback
        // generates at CPU speed, so the payload is what makes the product work.
        CudaOffer::Recommended { gpu } => Some((
            true,
            format!(
                "⚠  {gpu}: the bundled Vulkan backend runs at roughly CPU speed on this GPU \
                 generation.\n   Install the CUDA backend for usable performance:  \
                 knaif backend install cuda"
            ),
        )),
        CudaOffer::Optional { gpu } if !gpu_active => Some((
            true,
            format!(
                "⚠  {gpu}: the bundled Vulkan backend found no usable device here, so this run \
                 uses the CPU.\n   Install the CUDA backend to use this GPU:  \
                 knaif backend install cuda"
            ),
        )),
        // No number quoted. The "~3%" this used to claim was the generation column, and knaif's
        // workload is prompt-decode-dominated; no replacement figure is quotable until
        // PERFORMANCE.md §2 is reconciled.
        CudaOffer::Optional { gpu } => Some((
            false,
            format!(
                "ℹ  {gpu}: CUDA offload is available and faster than the bundled Vulkan backend, \
                 which\n   already works here. Optional:  knaif backend install cuda"
            ),
        )),
        // An offer would hand them ~668 MB that cannot load, which reaches the user as
        // "CUDA didn't work" — the least debuggable outcome available.
        CudaOffer::DriverTooOld { gpu, have, need } => Some((
            false,
            format!(
                "ℹ  {gpu}: CUDA offload needs NVIDIA driver R{need}+ and this machine has {have}.\n   \
                 Update the driver to enable it; the current run uses Vulkan or CPU."
            ),
        )),
        CudaOffer::NeedsReinstall { reason } => Some((
            true,
            format!("⚠  {reason}.\n   Run `knaif backend install cuda` to update it."),
        )),
    }
}

/// Locate the backend manifest: `$KNAIF_BACKEND_MANIFEST` else walk up for
/// `contracts/backends/backend-manifest.yaml` in a checkout / beside the installed exe.
fn resolve_backend_manifest_path() -> anyhow::Result<PathBuf> {
    if let Ok(p) = std::env::var("KNAIF_BACKEND_MANIFEST") {
        if !p.is_empty() {
            return Ok(PathBuf::from(p));
        }
    }
    resolve_repo_file("contracts/backends/backend-manifest.yaml").ok_or_else(|| {
        anyhow::anyhow!(
            "backend manifest not found (set KNAIF_BACKEND_MANIFEST or run from a checkout)"
        )
    })
}

/// The manifest's CLI-surface recommended model (public name the store understands) and whether
/// it's already installed. Best-effort: any failure to open the store yields `(None, false)`, so
/// the caller falls back to generic guidance. Note we deliberately use the manifest recommendation,
/// NOT the skill's `recommended_model` (that's the internal FT-cycle name, a different namespace).
fn recommended_model_status() -> (Option<String>, bool) {
    let Ok(store) = resolve_manifest_path().and_then(|p| ModelStore::open(&p)) else {
        return (None, false);
    };
    let recommended = store
        .manifest()
        .recommendations
        .cli
        .clone()
        .or_else(|| store.manifest().default_model().map(str::to_string));
    let installed = recommended
        .as_deref()
        .map(|n| store.is_installed(n))
        .unwrap_or(false);
    (recommended, installed)
}

/// First-run guidance when `run`/`plan` produced no plan because no real model was configured.
/// Points at the manifest's recommended model concretely: use it if installed, else `models pull`
/// it. Falls back to generic `--model` guidance when the manifest has no recommendation.
fn first_run_model_message(skill: &str, recommended: Option<&str>, installed: bool) -> String {
    match recommended {
        Some(name) if installed => format!(
            "No --model was given (the offline mock ran and produced no plan). The recommended \
             model `{name}` is installed — re-run with it:\n  \
             knaif run {skill} \"<request>\" --model {name}"
        ),
        Some(name) => format!(
            "First run: no model yet. Download the recommended model, then run:\n  \
             knaif models pull {name}\n  knaif run {skill} \"<request>\" --model {name}\n\
             (Or pass --model <path> to a local GGUF. See `knaif models list`.)"
        ),
        None => format!(
            "No --model was given. Pass --model <name|path> for real inference:\n  \
             knaif run {skill} \"<request>\" --model <name|path>\n\
             (See `knaif models list` for available models.)"
        ),
    }
}

/// Resolve extension-less stems in every non-terminal step, or downgrade the whole plan to a
/// clarify when the sandbox cannot pin one down.
///
/// Mirrors Python's loop in `CommandAgent._execute_steps`: terminal tools carry no file paths and
/// are skipped, and the FIRST unresolvable stem replaces the entire plan with a single clarify —
/// asking once beats half-running a plan whose inputs are in doubt.
fn resolve_plan_stems(payload: serde_json::Value, sandbox: &Path) -> serde_json::Value {
    const TERMINAL: [&str; 4] = ["clarify", "reject", "done", "wait_for_confirmation"];
    let Some(steps) = payload.get("plan").and_then(|p| p.as_array()) else {
        return payload;
    };
    let mut out = Vec::with_capacity(steps.len());
    for step in steps {
        let tool = step.get("tool").and_then(|t| t.as_str()).unwrap_or("");
        if TERMINAL.contains(&tool) {
            out.push(step.clone());
            continue;
        }
        let Some(args) = step.get("args") else {
            out.push(step.clone());
            continue;
        };
        match knaif_core::resolve_stems(args, sandbox) {
            knaif_core::StemOutcome::Resolved(resolved) => {
                let mut s = step.clone();
                if let Some(obj) = s.as_object_mut() {
                    obj.insert("args".into(), resolved);
                }
                out.push(s);
            }
            knaif_core::StemOutcome::Clarify(question) => {
                return serde_json::json!({
                    "plan": [{"tool": "clarify", "args": {"question": question}}]
                });
            }
        }
    }
    serde_json::json!({ "plan": out })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::VecDeque;

    fn tool_status(satisfied: bool, blocks: bool) -> knaif_core::ToolStatus {
        knaif_core::ToolStatus {
            name: "ffmpeg".into(),
            required: true,
            satisfied,
            found: if satisfied {
                vec![(
                    "ffmpeg".into(),
                    std::path::PathBuf::from("C:/tools/ffmpeg.exe"),
                )]
            } else {
                vec![]
            },
            missing: if satisfied {
                vec![]
            } else {
                vec!["ffmpeg".into()]
            },
            install_hint: Some("winget install -e --id Gyan.FFmpeg".into()),
            smart_app_control_blocks: blocks,
        }
    }

    #[test]
    fn deps_rows_say_when_smart_app_control_blocks_a_tool() {
        // Off, or a tool it does not block: the row is unchanged.
        assert_eq!(
            deps_detail(&tool_status(true, true), false),
            "C:/tools/ffmpeg.exe"
        );
        assert_eq!(
            deps_detail(&tool_status(false, false), true),
            "install: winget install -e --id Gyan.FFmpeg"
        );
        // On: a found tool will not start, and installing a missing one will not help.
        assert_eq!(
            deps_detail(&tool_status(true, true), true),
            "C:/tools/ffmpeg.exe — but Smart App Control blocks it on this PC"
        );
        assert_eq!(
            deps_detail(&tool_status(false, true), true),
            "not installed — Smart App Control blocks it on this PC"
        );
    }

    #[test]
    fn a_required_tool_smart_app_control_blocks_is_named_not_advised() {
        // Off: the usual missing-tool advice, install hint included.
        let missing = tool_status(false, true);
        let advice =
            required_tools_advice("ffmpeg", std::slice::from_ref(&missing), false).unwrap();
        assert!(advice.contains("winget install"), "{advice}");
        // On: installing would not help, and a found ffmpeg would not start either.
        for s in [missing, tool_status(true, true)] {
            let advice = required_tools_advice("ffmpeg", &[s], true).unwrap();
            assert!(advice.contains("Smart App Control"), "{advice}");
            assert!(!advice.contains("winget install"), "{advice}");
        }
        // On, but the tool is not one it blocks: nothing to say when it is installed.
        assert_eq!(
            required_tools_advice("ffmpeg", &[tool_status(true, false)], true),
            None
        );
    }

    #[test]
    fn the_smart_app_control_note_names_the_blocked_tools_once() {
        let blocked = tool_status(true, true);
        let fine = knaif_core::ToolStatus {
            name: "libreoffice".into(),
            ..tool_status(true, false)
        };
        assert_eq!(smart_app_control_note(&[&fine], true), None);
        assert_eq!(smart_app_control_note(&[&blocked], false), None);
        let note = smart_app_control_note(&[&blocked, &fine, &blocked], true).unwrap();
        assert!(note.contains("Smart App Control is on"), "{note}");
        assert_eq!(note.matches("ffmpeg").count(), 1, "{note}");
        assert!(!note.contains("libreoffice"), "{note}");
    }

    /// `select_model` reads process-global env, so its tests serialize on this lock rather than
    /// racing each other's `set_var`.
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    const ENV_KEYS: [&str; 3] = [
        "KNAIF_LLM_BACKEND",
        "KNAIF_MODEL_MANIFEST",
        "KNAIF_MODELS_DIR",
    ];

    /// A temp manifest + empty model store wired up via env, restored on drop.
    struct ModelEnv {
        _guard: std::sync::MutexGuard<'static, ()>,
        root: PathBuf,
        saved: Vec<(&'static str, Option<String>)>,
    }

    impl ModelEnv {
        /// `url` lands in the manifest verbatim. `"TODO"` means "not hosted", which makes `pull`
        /// fail before it opens a socket — that is what keeps the download tests hermetic.
        fn new(tag: &str, url: &str) -> Self {
            let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            let saved = ENV_KEYS
                .iter()
                .map(|k| (*k, std::env::var(k).ok()))
                .collect();
            let root =
                std::env::temp_dir().join(format!("knaif_selmodel_{tag}_{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(root.join("store")).unwrap();
            let manifest = root.join("model-manifest.yaml");
            std::fs::write(
                &manifest,
                format!(
                    "models:\n  \
                       rec-model:\n    \
                         file: rec-model.gguf\n    \
                         url: \"{url}\"\n    \
                         size_bytes: 2497280960\n\
                     recommendations:\n  \
                       cli: rec-model\n  \
                       default: rec-model\n"
                ),
            )
            .unwrap();
            std::env::remove_var("KNAIF_LLM_BACKEND");
            std::env::set_var("KNAIF_MODEL_MANIFEST", &manifest);
            std::env::set_var("KNAIF_MODELS_DIR", root.join("store"));
            Self {
                _guard: guard,
                root,
                saved,
            }
        }

        /// Make the recommended model "installed" — `is_installed` is just "file is in the store".
        fn install(&self) -> PathBuf {
            let path = self.root.join("store").join("rec-model.gguf");
            std::fs::write(&path, b"gguf").unwrap();
            path
        }
    }

    impl Drop for ModelEnv {
        fn drop(&mut self) {
            for (key, value) in &self.saved {
                match value {
                    Some(v) => std::env::set_var(key, v),
                    None => std::env::remove_var(key),
                }
            }
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    // --- 1.3.0: Yes by default, and an existing file always asks ---------------------------

    #[test]
    fn a_step_asks_only_when_confirm_is_opted_into() {
        assert!(approves_without_asking(false, false), "the default acts");
        assert!(
            !approves_without_asking(false, true),
            "--confirm asks first"
        );
        assert!(
            approves_without_asking(true, true),
            "--yes still skips the question"
        );
        assert!(approves_without_asking(true, false));
    }

    #[test]
    fn enter_follows_the_default_and_anything_else_is_no() {
        assert!(answer_from_line("", true));
        assert!(answer_from_line("  \n", true));
        assert!(
            !answer_from_line("", false),
            "an overwrite question defaults to No"
        );
        assert!(answer_from_line("Y", false));
        assert!(answer_from_line("yes", false));
        assert!(!answer_from_line("n", true));
        assert!(!answer_from_line("nope", true));
    }

    fn ask_returning(
        answer: Option<bool>,
        seen: &RefCell<Vec<(String, bool)>>,
    ) -> impl FnMut(&str, bool) -> anyhow::Result<Option<bool>> + '_ {
        move |q, default_yes| {
            seen.borrow_mut().push((q.to_string(), default_yes));
            Ok(answer)
        }
    }

    #[test]
    fn nothing_to_replace_never_asks() {
        let seen = RefCell::new(Vec::new());
        let mut ask = ask_returning(Some(false), &seen);
        assert!(overwrite_gate(&[], false, &mut ask).unwrap());
        assert!(seen.borrow().is_empty());
    }

    #[test]
    fn overwrite_flag_replaces_without_asking() {
        let seen = RefCell::new(Vec::new());
        let mut ask = ask_returning(Some(false), &seen);
        assert!(overwrite_gate(&["out.mp4".into()], true, &mut ask).unwrap());
        assert!(seen.borrow().is_empty());
    }

    #[test]
    fn an_existing_output_asks_with_no_as_the_default() {
        let seen = RefCell::new(Vec::new());
        let mut ask = ask_returning(Some(true), &seen);
        assert!(overwrite_gate(&["out.mp4".into()], false, &mut ask).unwrap());
        let seen = seen.borrow();
        assert_eq!(seen.len(), 1);
        assert!(
            seen[0].0.contains("out.mp4"),
            "names the file: {}",
            seen[0].0
        );
        assert!(!seen[0].1, "the default for replacing a file is No");
    }

    #[test]
    fn declining_to_replace_declines_the_step() {
        let seen = RefCell::new(Vec::new());
        let mut ask = ask_returning(Some(false), &seen);
        assert!(!overwrite_gate(&["a.mp4".into(), "b.mp4".into()], false, &mut ask).unwrap());
    }

    #[test]
    fn without_a_terminal_an_existing_output_is_an_error_naming_the_flag() {
        let seen = RefCell::new(Vec::new());
        let mut ask = ask_returning(None, &seen);
        let err = overwrite_gate(&["out.mp4".into()], false, &mut ask)
            .unwrap_err()
            .to_string();
        assert!(
            err.contains("out.mp4") && err.contains("--overwrite"),
            "{err}"
        );
    }

    #[test]
    fn existing_outputs_are_the_paths_that_exist() {
        let dir = std::env::temp_dir().join(format!("knaif_existing_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let there = dir.join("there.mp4");
        std::fs::write(&there, b"x").unwrap();
        let gone = dir.join("gone.mp4");
        let found = existing_outputs(&[there.display().to_string(), gone.display().to_string()]);
        assert_eq!(found, vec![there.display().to_string()]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_image_sequence_pattern_matches_the_files_it_would_write() {
        let dir = std::env::temp_dir().join(format!("knaif_pattern_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("frame_001.png"), b"x").unwrap();
        std::fs::write(dir.join("frame_x.png"), b"x").unwrap();
        std::fs::write(dir.join("other_002.png"), b"x").unwrap();
        let hit = dir.join("frame_001.png").display().to_string();
        for pattern in ["frame_%03d.png", "frame_%d.png"] {
            let found = existing_outputs(&[dir.join(pattern).display().to_string()]);
            assert_eq!(found, vec![hit.clone()], "{pattern}");
        }
        assert!(existing_outputs(&[dir.join("shot_%03d.png").display().to_string()]).is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn end_of_input_never_approves_a_prompt() {
        assert!(!answer_from_input(0, "", true), "EOF is not Enter");
        assert!(answer_from_input(1, "\n", true), "Enter takes the default");
        assert!(!answer_from_input(1, "\n", false));
        assert!(answer_from_input(2, "y\n", false));
    }

    #[test]
    fn run_accepts_confirm_and_overwrite_and_keeps_yes() {
        use clap::Parser;
        let cli = Cli::try_parse_from([
            "knaif",
            "run",
            "ffmpeg",
            "--confirm",
            "--overwrite",
            "--yes",
            "x",
        ])
        .unwrap();
        match cli.command {
            Command::Run(a) => assert!(a.confirm && a.overwrite && a.yes),
            _ => panic!("expected run"),
        }
    }

    #[test]
    fn human_size_reads_at_the_manifest_scale() {
        assert_eq!(human_size(2_497_280_960), "2.5 GB"); // knaif-qwen3-4b-v1's manifest size_bytes
        assert_eq!(human_size(1_417_754_976), "1.4 GB");
        assert_eq!(human_size(250_000_000), "250 MB");
    }

    // --- B1 precedence (NATIVE.md §5.6) -----------------------------------------------------
    // Note these run with stdin not a tty, so `ask_yes_no` yields None (= declined). That is
    // exactly the non-interactive case, and it keeps every test off the network.

    #[test]
    fn explicit_model_path_wins_over_everything() {
        let env = ModelEnv::new("explicit", "TODO");
        env.install();
        // Even with the mock forced and a recommended model installed, `--model` is authoritative.
        std::env::set_var("KNAIF_LLM_BACKEND", "mock");
        let gguf = env.root.join("explicit.gguf");
        std::fs::write(&gguf, b"gguf").unwrap();

        let chosen = select_model_with(
            Some(gguf.to_str().unwrap()),
            false,
            DownloadPolicy::Prompt,
            true,
        )
        .unwrap()
        .expect("explicit --model must be honored");
        assert_eq!(chosen, gguf);
    }

    #[test]
    fn mock_env_beats_auto_select() {
        let env = ModelEnv::new("mockenv", "TODO");
        let installed = env.install();
        std::env::set_var("KNAIF_LLM_BACKEND", "mock");

        // Installed and usable, but the explicit opt-out wins.
        assert_eq!(
            select_model_with(None, false, DownloadPolicy::Prompt, true).unwrap(),
            None
        );
        // Sanity: without the opt-out the same state auto-selects.
        std::env::remove_var("KNAIF_LLM_BACKEND");
        assert_eq!(
            select_model_with(None, false, DownloadPolicy::Prompt, true).unwrap(),
            Some(installed)
        );
    }

    #[test]
    fn installed_recommended_model_is_auto_selected() {
        let env = ModelEnv::new("installed", "TODO");
        let installed = env.install();

        for policy in [
            DownloadPolicy::Prompt,
            DownloadPolicy::YesOnly,
            DownloadPolicy::Never,
        ] {
            // Already on disk → every surface uses it silently, no download decision involved.
            assert_eq!(
                select_model_with(None, false, policy, true).unwrap(),
                Some(installed.clone()),
                "{policy:?} should auto-select the installed model"
            );
        }
    }

    #[test]
    fn missing_model_never_downloads_without_consent() {
        let _env = ModelEnv::new("noconsent", "TODO");

        // Non-interactive (tests have no tty) and no --yes → fall back to the mock. A `TODO` url
        // means any attempted pull would error, so `Ok(None)` proves no pull was attempted.
        assert_eq!(
            select_model_with(None, false, DownloadPolicy::Prompt, true).unwrap(),
            None
        );
    }

    #[test]
    fn plan_surfaces_do_not_download_a_missing_model() {
        let _env = ModelEnv::new("plansurf", "TODO");

        // `plan` is non-prompting: no --yes → mock, regardless of tty.
        assert_eq!(
            select_model_with(None, false, DownloadPolicy::YesOnly, true).unwrap(),
            None
        );
        // `--batch`/`--json` never download, even with --yes.
        assert_eq!(
            select_model_with(None, true, DownloadPolicy::Never, true).unwrap(),
            None
        );
    }

    #[test]
    fn yes_downloads_without_prompting() {
        let _env = ModelEnv::new("yespull", "TODO");

        // --yes skips the prompt and goes straight to the pull. The manifest's `TODO` url makes
        // that pull fail loudly (before any network) — which is the proof it was attempted at all,
        // and that a failed download is surfaced rather than silently degraded to the mock.
        let err = select_model_with(None, true, DownloadPolicy::Prompt, true)
            .expect_err("--yes must attempt the download");
        assert!(
            err.to_string().contains("no download URL"),
            "expected a pull attempt, got: {err}"
        );
    }

    #[test]
    fn mock_only_build_never_auto_selects() {
        let env = ModelEnv::new("nollama", "TODO");
        let installed = env.install();

        // A build without the llama.cpp backend can only run the mock. Auto-selecting here would
        // turn a working mock run into "this build has no llama.cpp backend" — a regression for
        // every default/dev build that happens to have the model on disk.
        assert_eq!(
            select_model_with(None, false, DownloadPolicy::Prompt, false).unwrap(),
            None
        );
        // ...but the same state does auto-select once the backend is compiled in.
        assert_eq!(
            select_model_with(None, false, DownloadPolicy::Prompt, true).unwrap(),
            Some(installed)
        );
    }

    #[test]
    fn no_recommendation_falls_back_to_the_mock() {
        let env = ModelEnv::new("norec", "TODO");
        // A manifest with models but no recommendations → nothing to auto-select.
        std::fs::write(
            env.root.join("model-manifest.yaml"),
            "models:\n  some-model:\n    file: some-model.gguf\n",
        )
        .unwrap();

        assert_eq!(
            select_model_with(None, true, DownloadPolicy::Prompt, true).unwrap(),
            None
        );
    }

    #[test]
    fn file_near_finds_data_beside_and_above_exe() {
        let root = std::env::temp_dir().join(format!("knaif_filenear_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let rel = "contracts/runtime/core_tools.yaml";
        let target = root.join(rel);
        std::fs::create_dir_all(target.parent().unwrap()).unwrap();
        std::fs::write(&target, "tools: []\n").unwrap();

        // exe in <root>/bin → finds ../contracts/...; exe in <root> → finds ./contracts/...
        assert_eq!(file_near(&bin, rel), Some(target.clone()));
        assert_eq!(file_near(&root, rel), Some(target));
        assert_eq!(file_near(&bin.join("nested"), rel), None);

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn first_run_uses_installed_recommended_model() {
        let msg = first_run_model_message("ffmpeg", Some("knaif-qwen3-4b-v1"), true);
        assert!(msg.contains("knaif-qwen3-4b-v1"));
        assert!(msg.contains("--model"));
        assert!(msg.to_lowercase().contains("installed"));
        // Already installed → don't tell the user to download it.
        assert!(!msg.contains("models pull"));
    }

    #[test]
    fn first_run_offers_pull_when_recommended_absent() {
        let msg = first_run_model_message("documents", Some("knaif-qwen3-4b-v1"), false);
        assert!(msg.contains("knaif models pull knaif-qwen3-4b-v1"));
        assert!(msg.contains("--model knaif-qwen3-4b-v1"));
    }

    #[test]
    fn first_run_falls_back_to_generic_when_no_recommendation() {
        let msg = first_run_model_message("ffmpeg", None, false);
        assert!(msg.contains("--model"));
        assert!(msg.contains("models list"));
        // No concrete model name to pull when the manifest has no recommendation.
        assert!(!msg.contains("models pull"));
    }

    /// A backend that returns queued responses in order — to drive the repair loop deterministically.
    struct ScriptedBackend {
        responses: RefCell<VecDeque<String>>,
    }
    impl ScriptedBackend {
        fn new(responses: &[&str]) -> Self {
            Self {
                responses: RefCell::new(responses.iter().map(|s| s.to_string()).collect()),
            }
        }
    }
    impl knaif_llm::LlmBackend for ScriptedBackend {
        fn generate_plan(&self, _system: &str, _user: &str) -> anyhow::Result<String> {
            self.responses
                .borrow_mut()
                .pop_front()
                .ok_or_else(|| anyhow::anyhow!("no more scripted responses"))
        }
        fn name(&self) -> &str {
            "scripted"
        }
    }

    fn registry() -> knaif_core::Registry {
        knaif_core::registry::load_registry_str(
            "compress_video:\n  description: Compress a video.\n  required_args: [inputs]\n",
        )
        .unwrap()
    }

    const INVALID: &str = r#"{"plan":[{"tool":"not_a_tool","args":{}}]}"#;
    const VALID: &str = r#"{"plan":[{"tool":"compress_video","args":{"inputs":"v.mp4"}}]}"#;

    #[test]
    fn repair_recovers_from_a_first_invalid_plan() {
        let backend = ScriptedBackend::new(&[INVALID, VALID]);
        let base = std::env::temp_dir();
        let payload =
            infer_with_repair(&backend, "sys", "compress", &registry(), &base, None, true).unwrap();
        assert_eq!(payload["plan"][0]["tool"], "compress_video");
        // both responses were consumed (initial + one repair)
        assert!(backend.responses.borrow().is_empty());
    }

    #[test]
    fn repair_disabled_returns_the_first_error() {
        let backend = ScriptedBackend::new(&[INVALID, VALID]);
        let base = std::env::temp_dir();
        assert!(infer_with_repair(&backend, "s", "u", &registry(), &base, None, false).is_err());
        // the retry response is left untouched (no repair attempted)
        assert_eq!(backend.responses.borrow().len(), 1);
    }

    #[test]
    fn repair_gives_up_after_one_retry() {
        let backend = ScriptedBackend::new(&[INVALID, INVALID]);
        let base = std::env::temp_dir();
        assert!(infer_with_repair(&backend, "s", "u", &registry(), &base, None, true).is_err());
        assert!(backend.responses.borrow().is_empty()); // exactly one retry
    }

    #[test]
    fn normalizes_windows_backslash_paths_to_forward_slashes() {
        // The crash class: a model echoing `.\clip.mov` emits an illegal `\c` JSON escape.
        assert_eq!(
            normalize_path_separators(r"convert .\clip.mov to mp4"),
            "convert ./clip.mov to mp4"
        );
        assert_eq!(
            normalize_path_separators(r"compress C:\Users\me\clip.mov"),
            "compress C:/Users/me/clip.mov"
        );
    }

    #[test]
    fn normalize_leaves_non_path_backslashes_alone() {
        // V3: converged on Python's `_PATH_TOKEN_RE`. Both shapes are cases in
        // contracts/parity/prompt_cases.json; the blanket replace got both wrong.
        assert_eq!(
            normalize_path_separators(r#"convert "C:\Users\me\my clip.mp4" to webm"#),
            r#"convert "C:\Users\me\my clip.mp4" to webm"#,
            "a quoted path is not a path *token* — quotes are not path characters"
        );
        assert_eq!(
            normalize_path_separators(r"what does \ mean here"),
            r"what does \ mean here",
            "a lone backslash has no alphanumeric and stays literal"
        );
    }

    #[test]
    fn normalize_leaves_forward_slash_and_bare_paths_untouched() {
        assert_eq!(
            normalize_path_separators("convert ./clip.mov to mp4"),
            "convert ./clip.mov to mp4"
        );
        assert_eq!(
            normalize_path_separators("convert clip.mov to mp4"),
            "convert clip.mov to mp4"
        );
    }

    // B5 (1.2.1): a password typed with a backslash came back from the model as `p/ss`, and the
    // file would have been locked with a password the user never typed. Mirrors Python's
    // `test_grounded_spelling.py`.
    #[test]
    fn a_backslash_password_gets_its_backslash_back() {
        let raw = r"password-protect sample.pdf with the password p\ss";
        assert_eq!(restore_grounded_spelling("p/ss", raw), r"p\ss");
        assert_eq!(
            restore_grounded_spelling("p/ss", r"lock it with password:p\ss"),
            r"p\ss"
        );
        // A slash the user typed stays; no backslash in the request changes nothing.
        assert_eq!(
            restore_grounded_spelling("a/b", "password-protect x.pdf with a/b"),
            "a/b"
        );
        assert_eq!(
            restore_grounded_spelling("hunter2", "protect x.pdf with hunter2"),
            "hunter2"
        );
    }

    #[test]
    fn only_grounded_args_get_the_users_spelling_back() {
        let mut def: knaif_core::ToolDef = serde_json::from_value(serde_json::json!({
            "description": "x", "required_args": ["input", "password"],
            "grounded_args": ["password"]
        }))
        .expect("a tool definition");
        def.name = "protect_pdf".to_string();
        let mut registry = knaif_core::Registry::new();
        registry.insert(def.name.clone(), def);
        let plan = serde_json::json!({"plan": [{"tool": "protect_pdf",
            "args": {"input": "docs/sample.pdf", "password": "p/ss"}}]});
        let raw = r"password-protect docs\sample.pdf with the password p\ss";
        let out = restore_grounded_args(plan, raw, &registry);
        assert_eq!(out["plan"][0]["args"]["password"], r"p\ss");
        // A path keeps its forward slashes: that rewrite is the point of the normalization.
        assert_eq!(out["plan"][0]["args"]["input"], "docs/sample.pdf");
    }

    #[test]
    fn debug_dump_includes_raw_and_extracted_when_enabled() {
        let msg = debug_dump(true, "RAW_OUTPUT", "EXTRACTED_JSON").expect("enabled → Some");
        assert!(msg.contains("RAW_OUTPUT"));
        assert!(msg.contains("EXTRACTED_JSON"));
    }

    #[test]
    fn debug_dump_is_none_when_disabled() {
        assert!(debug_dump(false, "RAW_OUTPUT", "EXTRACTED_JSON").is_none());
    }

    // ── P0: the prompt dump (native/Python planning parity, Workstream P) ───────────────────
    //
    // `$KNAIF_DEBUG` only fires on a parse/validation *failure* and prints model output, never
    // the prompt — so on a successful plan (most of the corpus, and the interesting case) there
    // was no way to see what the model was asked. P1 diffs this dump against Python's, and R1
    // later uses it to produce the native side of a golden, so the one property that matters is
    // that it reproduces `(system, user)` **verbatim**: a dump that reshapes the string turns the
    // P1 diff into a diff of the dumper.

    #[test]
    fn prompt_dump_is_none_when_disabled() {
        assert!(prompt_dump(false, "SYSTEM", "USER").is_none());
    }

    // ── read results as data, for the L4 lane ──────────────────────────────────────────────
    // The 2026-09-24 documents L4 graded every inspect/extract/find row as `None`: native printed
    // its answer as prose and the lane grades data. These pin the dump to Python's result shapes
    // (skills/documents/python/steps.py), which the documents verifier reads.

    #[test]
    fn an_inspection_dumps_python_field_names() {
        use knaif_skill_documents::run::ReadResult;
        use knaif_skill_documents::text::Inspection;
        let v = read_result_json(&ReadResult::Inspection(Inspection {
            format: "png".into(),
            size_bytes: 1396,
            encrypted: false,
            has_text_layer: false,
            pages: 1,
        }));
        assert_eq!(
            v,
            serde_json::json!({"format": "png", "size_bytes": 1396, "encrypted": false,
                               "has_text_layer": false, "pages": 1})
        );
    }

    #[test]
    fn extracted_text_dumps_pages_and_joined_text() {
        use knaif_skill_documents::run::ReadResult;
        use knaif_skill_documents::text::PageText;
        let v = read_result_json(&ReadResult::Text(vec![
            PageText {
                page: 1,
                text: "Alpha".into(),
            },
            PageText {
                page: 3,
                text: "Gamma".into(),
            },
        ]));
        assert_eq!(v["text"], "Alpha\nGamma");
        assert_eq!(
            v["pages"],
            serde_json::json!([{"page": 1, "text": "Alpha"}, {"page": 3, "text": "Gamma"}])
        );
    }

    #[test]
    fn matches_dump_their_count() {
        use knaif_skill_documents::run::ReadResult;
        use knaif_skill_documents::text::Match;
        let v = read_result_json(&ReadResult::Matches(vec![Match {
            page: 3,
            snippet: "Gamma page three".into(),
            span: (1, 6),
        }]));
        assert_eq!(v["count"], 1);
        assert_eq!(
            v["matches"],
            serde_json::json!([{"page": 3, "snippet": "Gamma page three", "span": [1, 6]}])
        );
    }

    #[test]
    fn a_result_dump_is_one_marked_line_or_nothing() {
        assert!(result_dump(false, "find_in_document", &serde_json::json!({"count": 0})).is_none());
        let line = result_dump(true, "find_in_document", &serde_json::json!({"count": 0})).unwrap();
        assert!(!line.contains('\n'));
        let body: serde_json::Value =
            serde_json::from_str(line.strip_prefix(RESULT_DUMP_MARKER).unwrap()).unwrap();
        assert_eq!(
            body,
            serde_json::json!({"tool": "find_in_document", "result": {"count": 0}})
        );
    }

    #[test]
    fn plan_dump_is_none_when_disabled() {
        assert!(plan_dump(false, &serde_json::json!({"plan": []})).is_none());
    }

    #[test]
    fn plan_dump_is_one_parseable_line() {
        // The consumer (the L4 lane) scans stderr for the marker and parses the rest of that
        // line, so the envelope must survive round-tripping and must not wrap.
        let payload = serde_json::json!({
            "plan": [{"tool": "strip_audio", "args": {"inputs": ["a b.mp4"], "output": "o.mp4"}}]
        });
        let msg = plan_dump(true, &payload).expect("enabled → Some");
        assert!(!msg.contains('\n'), "the dump must be a single line: {msg}");
        let rest = msg.strip_prefix(PLAN_DUMP_MARKER).expect("marker prefix");
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(rest).expect("parses"),
            payload
        );
    }

    #[test]
    fn argv_dump_carries_the_exact_argv_on_one_line() {
        // L3 compares this, not the display line: `shell_join` quotes for a shell, Python quotes
        // nothing, and neither round-trips a space or a filter escape once re-split.
        let argv: Vec<String> = [
            "ffmpeg",
            "-i",
            "silent clip.mp4",
            "-vf",
            r"crop=trunc(min(iw\,ih*1/1)/2)*2",
            "out.mp4",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        assert!(argv_dump(false, &argv).is_none(), "disabled → nothing");
        let msg = argv_dump(true, &argv).expect("enabled → Some");
        assert!(!msg.contains('\n'), "one line: {msg}");
        let rest = msg.strip_prefix(ARGV_DUMP_MARKER).expect("marker prefix");
        assert_eq!(
            serde_json::from_str::<Vec<String>>(rest).expect("parses"),
            argv
        );
    }

    #[test]
    fn prompt_dump_carries_system_and_user_when_enabled() {
        let msg = prompt_dump(true, "SYSTEM_TEXT", "USER_TEXT").expect("enabled → Some");
        assert!(msg.contains("SYSTEM_TEXT"));
        assert!(msg.contains("USER_TEXT"));
    }

    #[test]
    fn prompt_dump_reproduces_both_messages_byte_for_byte() {
        // Deliberately nasty: trailing spaces, a blank line, a lone CR, tabs and a non-ASCII
        // char — all things a "helpful" formatter would trim, join or re-encode.
        let system = "line one  \n\n\tindented\r\nsuffix — ünicode ";
        let user = "  leading and trailing  ";
        let msg = prompt_dump(true, system, user).expect("enabled → Some");

        assert_eq!(
            extract_dump_section(&msg, "system"),
            system,
            "system message must survive the dump unaltered"
        );
        assert_eq!(
            extract_dump_section(&msg, "user"),
            user,
            "user message must survive the dump unaltered"
        );
    }

    /// Pull one framed section back out of a dump, so the tests assert on the payload rather
    /// than on the framing. Mirrors what the P1 capture script does.
    fn extract_dump_section(dump: &str, name: &str) -> String {
        let begin = format!("{PROMPT_DUMP_MARKER}BEGIN {name}\n");
        let end = format!("\n{PROMPT_DUMP_MARKER}END {name}");
        let start = dump.find(&begin).expect("begin marker present") + begin.len();
        let stop = dump[start..].find(&end).expect("end marker present") + start;
        dump[start..stop].to_string()
    }

    // ── F5: a multi-step plan must never be silently truncated ───────────────────────────────
    //
    // `cmd_run` originally took `steps.first()` and discarded the rest without a word, so a valid
    // 2-step plan (e.g. strip_audio -> resize) executed only the first command and still exited 0
    // — reporting full success for partial completion. The first fix made that an explicit
    // refusal; E2 replaced the refusal with an ordered executor, which is what the audit actually
    // recommended. `decide_steps` stays deterministic (no model/GPU/subprocess), so the shape of
    // the decision is tested here and the execution semantics in
    // `apps/cli/tests/executor_semantics.rs`.

    #[test]
    fn decide_steps_empty_plan_is_empty() {
        assert!(matches!(decide_steps(&[]), StepDecision::Empty));
    }

    #[test]
    fn decide_steps_single_step_is_ok() {
        let steps = vec![serde_json::json!({"tool": "strip_audio", "args": {}})];
        assert!(matches!(
            decide_steps(&steps),
            StepDecision::Run { total: 1 }
        ));
    }

    /// L1a: the prompt-parity contract. Fixed utterance x fixed registry x fixed prompt
    /// overrides -> the exact `(system, user)` pair the reference runtime produces, compared
    /// byte for byte with no allow-list.
    ///
    /// It lives here rather than in `knaif-core` because the stage under test is
    /// *normalize -> build*, and `normalize_path_separators` is a binary-crate function: a
    /// core test could only compare half the pipeline, which is the mistake Rule 2 exists to
    /// prevent (an earlier ad-hoc comparison broke that rule and reported 18.2% disagreement,
    /// of which 150/154 were an artifact of comparing different stages).
    ///
    /// **Green since V3 (2026-09-10).** It was authored red and verified red first: native
    /// rewrote every backslash in the utterance where Python rewrites only path-shaped tokens,
    /// so a quoted Windows path and a lone backslash diverged. That failure, then this pass, is
    /// the evidence the contract detects what it was written for — a contract that was never
    /// observed failing proves nothing.
    ///
    /// See docs/plans/2026-09-10-skill-quality-lifecycle.md (L1a, V3).
    #[test]
    fn prompt_parity_cases() {
        let fixtures =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../contracts/parity/prompt_cases.json");
        let doc: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures"))
                .unwrap();
        let registries = doc["registries"].as_object().unwrap();
        let overrides_by_name = doc["prompt_overrides"].as_object().unwrap();

        for case in doc["cases"].as_array().unwrap() {
            let name = case["name"].as_str().unwrap();
            let reg_yaml = registries[case["registry"].as_str().unwrap()]
                .as_str()
                .unwrap();
            let registry = knaif_core::registry::load_registry_str(reg_yaml)
                .unwrap_or_else(|e| panic!("{name}: registry {e}"));

            let ov = &overrides_by_name[case["prompt_overrides"].as_str().unwrap()];
            let overrides = knaif_core::PromptOverrides {
                system_header: Some(ov["system_header"].as_str().unwrap().to_string()),
                examples_block: Some(ov["examples_block"].as_str().unwrap().to_string()),
                // L1a pins the *rendering* of a given block; the per-utterance selection that
                // chooses which examples go into it is L1e's contract.
                examples: Vec::new(),
            };

            let utterance = normalize_path_separators(case["utterance"].as_str().unwrap());
            let (system, user) = knaif_core::build_prompt(&utterance, &registry, &overrides);

            assert_eq!(
                system,
                case["expected_system"].as_str().unwrap(),
                "case {name}: system message differs"
            );
            assert_eq!(
                user,
                case["expected_user"].as_str().unwrap(),
                "case {name}: user message differs"
            );
        }
    }

    #[test]
    fn capability_refusal_is_marked_not_implemented_not_reject() {
        // A capability the runtime has not built and a request it deliberately refuses are
        // both a refusal to the user, but they are opposite facts about the product: one is
        // a coverage gap, the other is the safety model working. Recorded under the same
        // `reject:` prefix they are indistinguishable, and coverage becomes uncomputable.
        //
        // The example moved with E2: multi-step chains used to be the marker's main producer,
        // and are now executed. What still produces it is a skill tool the native runtime has
        // not built (`is_supported` in the documents crate).
        let msg = not_implemented_message("the documents tool \"redact\" is not built");
        assert!(msg.starts_with(knaif_skill_api::capability::NOT_IMPLEMENTED_PREFIX));
        assert!(!msg.starts_with("reject:"));
        assert!(msg.contains("is not built"));
    }

    /// E2: a chain is work to do, not a plan to decline. This asserted `Unsupported { total: 2 }`
    /// until the executor existed; the behavior it used to pin — running one step of two and
    /// exiting 0 — is what `Unsupported` was introduced to stop, and what the executor now
    /// actually handles. The end-to-end proof that both steps run is
    /// `apps/cli/tests/executor_semantics.rs`.
    #[test]
    fn decide_steps_multi_step_runs_every_step() {
        let steps = vec![
            serde_json::json!({
                "tool": "strip_audio",
                "args": {"inputs": "clip.mp4", "output": "silent.mp4"}
            }),
            serde_json::json!({
                "tool": "resize_video",
                "args": {"inputs": "silent.mp4", "height": 720}
            }),
        ];
        match decide_steps(&steps) {
            StepDecision::Run { total } => assert_eq!(total, 2),
            other => panic!("expected Run, got {other:?}"),
        }
    }

    /// E3: the failure report has to name the step and account for the ones around it — a bare
    /// "ffmpeg exited 1" after a chain leaves the user guessing what reached their disk.
    #[test]
    fn chain_failure_context_accounts_for_every_step() {
        assert_eq!(chain_failure_context(0, 1), "the step failed");

        let first_of_three = chain_failure_context(0, 3);
        assert!(first_of_three.contains("step 1 of 3"), "{first_of_three}");
        assert!(
            first_of_three.contains("nothing had run yet"),
            "{first_of_three}"
        );
        assert!(
            first_of_three.contains("steps 2-3 were not run"),
            "{first_of_three}"
        );

        let middle = chain_failure_context(1, 3);
        assert!(middle.contains("step 2 of 3"), "{middle}");
        assert!(middle.contains("step 1 had already completed"), "{middle}");
        assert!(middle.contains("step 3 was not run"), "{middle}");

        // The last step of a chain has nothing after it — "steps 4-3 were not run" would be
        // worse than saying nothing.
        let last = chain_failure_context(2, 3);
        assert!(last.contains("steps 1-2 had already completed"), "{last}");
        assert!(last.contains("it was the last step"), "{last}");
        assert!(!last.contains("4-3"), "{last}");
    }

    // ── the CUDA offer is about THIS build, not just the payload receipt ─────────

    #[test]
    fn a_build_with_cuda_compiled_in_is_never_offered_the_payload() {
        // `just eval-native` and the release `cuda` kind both compile the CUDA backend in
        // (`llama,cuda,pdfium` and `llama,dynamic-backends,cuda`). The receipt in
        // `~/.knaif/backends` is empty in both cases, so `cuda_offer` says NotInstalled and the
        // CLI told a CUDA-capable binary to go install CUDA.
        assert!(!cuda_payload_is_worth_offering(true, true));
        assert!(!cuda_payload_is_worth_offering(true, false));
    }

    #[test]
    fn a_build_that_cannot_dlopen_a_payload_is_never_offered_one() {
        // `load_dynamic_backends` is a no-op without `dynamic-backends`, so `~/.knaif/backends`
        // is never scanned. Following the advice downloads ~668 MB that is never loaded — the
        // same "CUDA didn't work" outcome `DriverTooOld` exists to avoid.
        assert!(!cuda_payload_is_worth_offering(false, false));
    }

    #[test]
    fn the_shipped_cpu_and_vulkan_artifacts_are_still_offered_the_payload() {
        // The whole point of the opt-in payload: `llama,dynamic-backends[,vulkan]` can load it
        // and does not already have it. Suppressing this case would make the feature unreachable.
        assert!(cuda_payload_is_worth_offering(false, true));
    }

    #[test]
    fn an_optional_offer_never_claims_vulkan_works_when_no_gpu_is_active() {
        // Found 2026-10-01 in WSL: Vulkan saw no device, the run went to the CPU, and the same
        // run printed both "No GPU backend is active" and "Vulkan … already works here".
        let offer = CudaOffer::Optional {
            gpu: "NVIDIA GeForce RTX 5080".to_string(),
        };
        let (warning, text) = cuda_offer_text(&offer, false).expect("an offer");
        assert!(
            warning,
            "running on the CPU makes the payload the fix, not an option"
        );
        assert!(!text.contains("already works"), "{text}");
        assert!(text.contains("knaif backend install cuda"), "{text}");

        let (warning, text) = cuda_offer_text(&offer, true).expect("an offer");
        assert!(!warning);
        assert!(text.contains("already works here"), "{text}");
    }

    #[test]
    fn nothing_to_offer_prints_nothing() {
        assert_eq!(cuda_offer_text(&CudaOffer::NotApplicable, false), None);
        assert_eq!(cuda_offer_text(&CudaOffer::AlreadyInstalled, true), None);
    }

    // ── `backend list --json` (workbench T2a) ──────────────────────────────────────────────
    //
    // The workbench parses this to label a build, so the KEY NAMES ARE AN INTERFACE. A build
    // directory named `release-vulkan` holding a CUDA build must not be able to mislead an
    // operator — the binary has the last word, and this is how it speaks.

    #[test]
    fn backend_list_json_always_carries_the_same_keys() {
        // No store: a build with no backend manifest still answers, rather than failing. That is
        // what lets the workbench parse ONE shape from every binary it finds.
        let doc = backend_list_json(None);
        for key in [
            "built_with",
            "dynamic_backends",
            "backends_dir",
            "platform",
            "entries",
        ] {
            assert!(doc.get(key).is_some(), "missing key: {key}");
        }
        assert!(doc["entries"].as_array().is_some_and(|e| e.is_empty()));
        assert!(doc["backends_dir"].is_null());
    }

    #[test]
    fn backend_list_json_reports_the_compiled_feature_set() {
        let doc = backend_list_json(None);
        // `dynamic_backends` is a compile-time fact, never read from the store — a static build
        // has no backend store at all, and would otherwise report nothing.
        assert_eq!(doc["dynamic_backends"], cfg!(feature = "dynamic-backends"));

        let features: Vec<String> = doc["built_with"]
            .as_array()
            .expect("built_with is an array")
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        assert_eq!(
            features.contains(&"llama".to_string()),
            cfg!(feature = "llama")
        );
        assert_eq!(
            features.contains(&"cuda".to_string()),
            cfg!(feature = "cuda")
        );
        assert_eq!(
            features.contains(&"vulkan".to_string()),
            cfg!(feature = "vulkan")
        );
        assert_eq!(
            features.contains(&"pdfium".to_string()),
            cfg!(feature = "pdfium")
        );
    }
    fn control_steps(n: usize) -> Vec<serde_json::Value> {
        (0..n)
            .map(|i| serde_json::json!({"tool": format!("t{i}"), "args": {}}))
            .collect()
    }

    #[test]
    fn a_declined_step_stops_the_chain() {
        let steps = control_steps(3);
        let mut ran = Vec::new();
        let result = execute_steps(&steps, 3, |step| {
            ran.push(step["tool"].as_str().unwrap().to_string());
            Ok(if ran.len() == 1 {
                StepOutcome::Declined
            } else {
                StepOutcome::Continue
            })
        });
        assert!(result.is_ok());
        assert_eq!(ran, vec!["t0"], "no step may run after a declined one");
    }

    #[test]
    fn declining_the_last_step_has_nothing_to_report_beyond_aborted() {
        assert_eq!(declined_note(1, 2), None);
        assert_eq!(declined_note(0, 1), None);
    }

    #[test]
    fn a_declined_step_says_which_steps_did_not_run() {
        assert_eq!(declined_note(0, 2).as_deref(), Some("Step 2 was not run."));
        assert_eq!(
            declined_note(0, 4).as_deref(),
            Some("Steps 2-4 were not run.")
        );
        assert_eq!(
            declined_note(1, 4).as_deref(),
            Some("Steps 3-4 were not run.")
        );
    }

    #[test]
    fn a_confirmed_chain_runs_every_step() {
        let steps = control_steps(3);
        let mut ran = 0;
        execute_steps(&steps, 3, |_| {
            ran += 1;
            Ok(StepOutcome::Continue)
        })
        .unwrap();
        assert_eq!(ran, 3);
    }
    #[test]
    fn reverse_video_warns_about_memory_before_confirming() {
        let w = confirm_warning("reverse_video", 2).expect("a warning");
        assert!(w.contains("2 clip(s)") && w.contains("RAM"), "{w}");
        // A lost `\` line continuation leaves the next line's indent inside the sentence.
        assert!(!w.contains("  "), "{w}");
    }

    #[test]
    fn other_tools_carry_no_confirmation_warning() {
        assert_eq!(confirm_warning("resize_video", 1), None);
    }
}
