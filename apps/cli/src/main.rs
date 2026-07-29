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

use clap::{Args, Parser, Subcommand};
use indicatif::{ProgressBar, ProgressStyle};
use knaif_models::{BackendState, BackendStore, HttpFetcher, ModelStore, VerifyOutcome};

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
    List,
    /// Download a backend payload and install it where the runtime scans for it.
    Install { name: String },
    /// Verify an installed payload's files against the manifest checksums.
    Verify { name: String },
    /// Remove an installed backend payload (falls back to CPU/Vulkan).
    Remove { name: String },
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
    /// Skill to run (native `run` currently supports `ffmpeg`).
    skill: String,
    /// Preview the command(s) without executing (the only supported mode so far).
    #[arg(long)]
    dry_run: bool,
    /// Restrict input/output paths to this directory (open/CLI mode when omitted).
    #[arg(long)]
    sandbox: Option<PathBuf>,
    /// Don't ask, proceed: auto-confirm destructive/overwrite steps, and download the
    /// recommended model without prompting when none is given or installed.
    #[arg(long)]
    yes: bool,
    /// Model for real inference: an installed/manifest NAME (e.g. `knaif-qwen3-4b-v1`) or a GGUF file
    /// PATH. Needs a build with `--features llama`. Without it, the recommended model is
    /// auto-selected — installed ones silently, a missing one after a download prompt — falling
    /// back to the mock (drive it offline with `KNAIF_LLM_MOCK_RESPONSE`). Last one wins.
    #[arg(long, value_name = "NAME|PATH", overrides_with = "model")]
    model: Option<String>,
    /// Show llama.cpp's native trace (model metadata, tensor load/repack, context construction)
    /// on stderr. Quiet by default.
    #[arg(long)]
    verbose: bool,
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

fn main() -> anyhow::Result<()> {
    enable_utf8_console();
    hold_app_mutex();
    let cli = Cli::parse();
    match cli.command {
        Command::Skills { action } => match action {
            SkillsAction::List { include_stale } => cmd_skills_list(include_stale),
            SkillsAction::Deps {
                name,
                include_stale,
            } => cmd_skills_deps(name.as_deref(), include_stale),
        },
        Command::Models { action } => cmd_models(action),
        Command::Backend { action } => cmd_backend(action),
        Command::Plan(args) => cmd_plan(args),
        Command::Run(args) => cmd_run(args),
    }
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
            let detail = if s.satisfied {
                let paths: Vec<_> = s
                    .found
                    .iter()
                    .map(|(_, p)| p.display().to_string())
                    .collect();
                paths.join(", ")
            } else {
                match &s.install_hint {
                    Some(hint) => format!("install: {hint}"),
                    None => "not found".to_string(),
                }
            };
            println!("  [{mark}] {:<14} ({kind}) {detail}", s.name);
        }
        // Call out the blocking set explicitly so it's actionable, not just tabular.
        if let Some(msg) = knaif_core::missing_required_message(&skill.name, &statuses) {
            println!("{msg}");
        }
    }
    Ok(())
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
fn thinking_spinner() -> ProgressBar {
    let bar = ProgressBar::new_spinner();
    if let Ok(style) = ProgressStyle::with_template("{spinner:.green} {msg} [{elapsed_precise}]") {
        bar.set_style(style);
    }
    bar.set_message("Loading model and planning (first run on CPU can take a minute)…");
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
/// the position in the set (`[2/4] libcudart.so.13`), because a CUDA payload is ~618 MB across
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

fn cmd_backend(action: BackendAction) -> anyhow::Result<()> {
    let store = BackendStore::open(&resolve_backend_manifest_path()?)?;
    match action {
        BackendAction::List => {
            let entries = store.list();
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

fn cmd_run(args: RunArgs) -> anyhow::Result<()> {
    if !matches!(args.skill.as_str(), "ffmpeg" | "documents") {
        anyhow::bail!(
            "native `run` supports the ffmpeg and documents skills; got {:?}",
            args.skill
        );
    }
    let root = resolve_known_skill(&args.skill)?;
    let bundle = root.join(&args.skill);

    let request = args.request.join(" ");
    if request.trim().is_empty() {
        anyhow::bail!(
            "no request given. Usage: just native {0} \"<what to do>\" [--model <name|path>]\n  \
             e.g. just native {0} \"compress clip.mp4 for email\" --model knaif-qwen3-4b-v1",
            args.skill
        );
    }

    // Pre-inference safety gate: an unsafe phrase never reaches the model.
    let unsafe_phrases = knaif_core::load_unsafe_phrases(&bundle.join("skill.yaml"));
    if knaif_core::is_unsafe_request(&request, &unsafe_phrases) {
        println!(
            "reject: this request is blocked by the {} skill's safety policy.",
            args.skill
        );
        return Ok(());
    }

    // Dependency preflight: fail fast (before spending inference) when a REQUIRED external tool is
    // missing and we intend to execute. Dry-run still previews the command without the binary.
    if !args.dry_run {
        let statuses = knaif_core::detect_skill_deps(&bundle);
        if let Some(msg) = knaif_core::missing_required_message(&args.skill, &statuses) {
            println!("{msg}");
            return Ok(());
        }
    }

    // Only now that the request has survived every cheap rejection may we resolve (and possibly
    // download) a model — see `select_model`.
    let model = select_model(args.model.as_deref(), args.yes, DownloadPolicy::Prompt)?;

    // Resolve paths against the sandbox when given (and enforce its boundary), else cwd/open mode.
    let base = match &args.sandbox {
        Some(s) => s.clone(),
        None => std::env::current_dir()?,
    };
    let sandbox = args.sandbox.as_deref();
    // Before the (silent) model load + inference, tell the user if it will run entirely on the CPU:
    // no GPU device means a minute-plus wait for a 4B model, and saying so up front turns a
    // baffling "nothing happened" into an expected slow run. Only meaningful for real inference.
    if model.is_some() && knaif_llm::gpu_present(args.verbose) == Some(false) {
        eprintln!(
            "⚠  No GPU detected — running on CPU. Inference will be slow \
             (the first request can take a minute or more)."
        );
    }
    // Model load + inference is the one silent stretch of a real run; show a live spinner so a
    // slow CPU-only run is not mistaken for a hang. Skip it for the mock (instant) and in verbose
    // mode (llama.cpp prints its own trace, which the spinner would fight).
    let show_spinner = model.is_some() && !args.verbose;
    let spinner = show_spinner.then(thinking_spinner);
    let built = build_plan(
        &root,
        &args.skill,
        &request,
        &base,
        sandbox,
        model.as_deref(),
        args.verbose,
    );
    if let Some(s) = spinner {
        s.finish_and_clear();
    }
    // Anything the user typed during that long silent wait is still queued in the terminal's input
    // buffer; discard it so those stray keystrokes can't answer a pending confirm prompt or spill
    // onto the shell after we exit. Gated to the slow path (a spinner was shown) so a fast run
    // never drops a deliberately typed-ahead command.
    if show_spinner {
        flush_terminal_input();
    }
    let payload = built?;

    let steps = payload
        .get("plan")
        .and_then(serde_json::Value::as_array)
        .cloned()
        .unwrap_or_default();
    let Some(step) = steps.first() else {
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
    };

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
            println!("clarify: {q}");
            return Ok(());
        }
        "reject" => {
            let r = step_args
                .get("reason")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("(no reason)");
            println!("reject: {r}");
            return Ok(());
        }
        _ => {}
    }

    match args.skill.as_str() {
        "ffmpeg" => run_ffmpeg_step(&bundle, tool, step_args, sandbox, args.dry_run, args.yes),
        "documents" => run_documents_step(
            &bundle,
            tool,
            step_args,
            &base,
            sandbox,
            args.dry_run,
            args.yes,
        ),
        _ => unreachable!("skill guarded above"),
    }
}

/// ffmpeg dispatch: expand the intent → dry-run preview or confirmed subprocess execution.
fn run_ffmpeg_step(
    bundle: &Path,
    tool: &str,
    step_args: &serde_json::Map<String, serde_json::Value>,
    sandbox: Option<&Path>,
    dry_run: bool,
    yes: bool,
) -> anyhow::Result<()> {
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
            println!("clarify: {q}");
            return Ok(());
        }
    };
    if commands.is_empty() {
        println!("Nothing to do.");
        return Ok(());
    }

    // Dry-run: print the copy-pasteable command line(s) and stop — no side effects.
    if dry_run {
        for cmd in &commands {
            println!("{}", shell_join(cmd));
        }
        return Ok(());
    }

    // Execution: every ffmpeg intent is `safety_category: destructive`, so it needs explicit
    // consent (the native equivalent of `ctx.confirmed`). `--yes`, an interactive yes, or nothing.
    let previews: Vec<String> = commands.iter().map(|c| shell_join(c)).collect();
    if !confirm_action(yes, &previews, "ffmpeg command")? {
        println!("Aborted (no changes made).");
        return Ok(());
    }

    let mut failures = 0;
    for cmd in &commands {
        let output = cmd.last().cloned().unwrap_or_default();
        eprintln!("running: {}", shell_join(cmd));
        let result = knaif_skill_ffmpeg::exec::run_ffmpeg(cmd)?;
        if result.status.success() {
            println!("✓ {output}");
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
            println!("✗ {output} (ffmpeg exited {})\n{tail}", result.status);
        }
    }
    if failures > 0 {
        anyhow::bail!("{failures} of {} command(s) failed", commands.len());
    }
    Ok(())
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
) -> anyhow::Result<()> {
    use knaif_skill_documents::run::{commit, is_supported, preview, Preview, ReadResult};

    if !is_supported(tool) {
        anyhow::bail!(
            "documents tool {tool:?} is not implemented natively yet (image watermark is deferred)"
        );
    }

    match preview(tool, step_args, base, sandbox, bundle)? {
        Preview::Read(result) => {
            match result {
                ReadResult::Inspection(i) => println!(
                    "{}: {} page(s), {} bytes, encrypted={}, text_layer={}",
                    i.format, i.pages, i.size_bytes, i.encrypted, i.has_text_layer
                ),
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
            Ok(())
        }
        Preview::Write { outputs, summary } => {
            if dry_run {
                println!("would {summary} →");
                for o in &outputs {
                    println!("  {}", o.display());
                }
                return Ok(());
            }
            // Surface the operation (incl. the compress method / text-loss warning) before acting.
            println!("{summary}");
            let previews: Vec<String> = outputs.iter().map(|p| p.display().to_string()).collect();
            if !confirm_action(yes, &previews, "output file")? {
                println!("Aborted (no changes made).");
                return Ok(());
            }
            for w in commit(tool, step_args, base, sandbox, bundle)? {
                println!("✓ wrote {}", w.display());
            }
            Ok(())
        }
    }
}

/// Ask a `[y/N]` question on the terminal. `Ok(None)` when stdin is not a tty — no answer can be
/// obtained, and each caller decides what that means (a destructive action errors; an optional
/// download silently declines). Prompt goes to stderr so stdout stays machine-readable.
fn ask_yes_no(question: &str) -> anyhow::Result<Option<bool>> {
    use std::io::{IsTerminal, Write};
    if !std::io::stdin().is_terminal() {
        return Ok(None);
    }
    eprint!("{question} [y/N] ");
    std::io::stderr().flush()?;
    // Drop anything typed *before* the prompt appeared (e.g. during a long inference wait) so a
    // stray keystroke can't silently answer this gate — the user must respond to the prompt itself.
    flush_terminal_input();
    let mut line = String::new();
    std::io::stdin().read_line(&mut line)?;
    Ok(Some(matches!(
        line.trim().to_lowercase().as_str(),
        "y" | "yes"
    )))
}

/// Discard any pending terminal input. During the long, silent model-load + inference wait a user
/// may type (assuming nothing is happening); those keystrokes linger in the tty input buffer and
/// would otherwise be consumed by the next confirm prompt or handed to the shell on exit. Flushing
/// afterwards drops them. No-op when stdin is not a terminal, and (for now) on non-Unix platforms.
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

#[cfg(not(unix))]
fn flush_terminal_input() {}

/// Gate a destructive action on explicit consent: `--yes`, or an interactive `y` when stdin is a
/// terminal. Non-interactive without `--yes` errors with the preview + how to proceed (never acts
/// silently). `noun` names the previewed items (e.g. "ffmpeg command", "output file").
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
    eprintln!("About to act on {} {noun}(s):", previews.len());
    for line in previews {
        eprintln!("  {line}");
    }
    // Tty confirmed above, so a `None` (non-interactive) answer is unreachable; decline defensively.
    Ok(ask_yes_no("Proceed?")?.unwrap_or(false))
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
    PlanSession::new(root, skill, model, verbose)?.plan(utterance, base, sandbox)
}

/// A loaded planning session: the expensive per-run setup (skill registry, prompt overrides, and
/// the model backend) built once and reused across utterances. The model is loaded exactly once in
/// [`knaif_llm::backend_for`]; `plan` creates a fresh inference context per utterance (see
/// `LlamaCppBackend::generate_plan`), so batching cannot leak state between utterances.
struct PlanSession {
    registry: knaif_core::Registry,
    overrides: knaif_core::PromptOverrides,
    output_capable: std::collections::HashSet<String>,
    backend: Box<dyn knaif_llm::LlmBackend>,
    /// Repair only for a real model — the mock repeats its canned response, so a retry is pointless.
    repair: bool,
}

impl PlanSession {
    fn new(root: &Path, skill: &str, model: Option<&Path>, verbose: bool) -> anyhow::Result<Self> {
        let bundle = root.join(skill);
        let mut registry = knaif_core::load_registry(&bundle.join("tools.yaml"))?;
        if let Some(core) = resolve_repo_file("contracts/runtime/core_tools.yaml") {
            registry.extend(knaif_core::load_registry(&core)?);
        }
        let overrides = knaif_core::load_prompt_yaml(&bundle.join("prompt.yaml"));
        let output_capable = knaif_core::output_capable_tools(&registry);
        let backend = knaif_llm::backend_for(model, verbose)?;
        Ok(Self {
            registry,
            overrides,
            output_capable,
            backend,
            repair: model.is_some(),
        })
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
        let (system, user) = knaif_core::build_prompt(&utterance, &self.registry, &self.overrides);
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
        Ok(knaif_core::apply_clarify_gate(
            payload,
            &utterance,
            &self.output_capable,
        ))
    }
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
    let raw = backend.generate_plan(system, user)?;
    match try_build_payload(&raw, registry, base, sandbox) {
        Ok(payload) => Ok(payload),
        Err(first_err) if repair => {
            let previous = knaif_core::extract_json(&raw).json;
            emit_debug(debug, &raw, &previous);
            let feedback =
                knaif_core::validator_feedback_prompt(user, &previous, &first_err.to_string());
            let retry_raw = backend.generate_plan(system, &feedback)?;
            // If the corrected plan is still bad, report the original error (as Python does).
            match try_build_payload(&retry_raw, registry, base, sandbox) {
                Ok(payload) => Ok(payload),
                Err(_) => {
                    emit_debug(
                        debug,
                        &retry_raw,
                        &knaif_core::extract_json(&retry_raw).json,
                    );
                    Err(first_err)
                }
            }
        }
        Err(e) => {
            emit_debug(debug, &raw, &knaif_core::extract_json(&raw).json);
            Err(e)
        }
    }
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

/// Normalize Windows-style backslash path separators in an utterance to forward slashes. A model
/// that echoes a path verbatim (`.\clip.mov`) would otherwise emit an illegal `\c` JSON escape;
/// forward slashes are accepted by ffmpeg and `std::path` on Windows, so this is lossless for the
/// file-path domain these skills operate in.
fn normalize_path_separators(utterance: &str) -> String {
    utterance.replace('\\', "/")
}

/// Extract JSON → parse → normalize → apply defaults → validate. Errors describe the first failure.
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
    knaif_core::validate_plan(&payload, registry, base, sandbox)?;
    Ok(payload)
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::VecDeque;

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
}
