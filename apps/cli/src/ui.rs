//! The terminal view of `knaif run`: a flowchart of what happens, with colors and timings.
//!
//! **Two views, one rule.** When stdout *and* stderr are terminals the run is drawn as a tree
//! (this module). Anything else — a pipe, a redirect, the L4 eval lane's subprocess — gets the
//! plain lines pinned by `tests/plain_output_golden.rs`, byte for byte as in 1.2.0. That is why
//! nothing here may be reached from the plain path except through [`view`], and why the renderers
//! are pure functions: they are tested without a terminal.
//!
//! Plan: `docs/plans/2026-10-01-cli-terminal-output.md`.

use std::io::{IsTerminal, Write};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

// ── Style ────────────────────────────────────────────────────────────────────────────────────

/// What a piece of text means; the style decides how that looks.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tone {
    Ok,
    Fail,
    Warn,
    Dim,
    Accent,
    Bold,
    /// The wordmark's coral.
    Brand,
    /// The wordmark's dark letters: the terminal's own foreground, in bold.
    Dark,
}

/// Whether to emit ANSI colors. The tree and its symbols are drawn either way.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Style {
    pub color: bool,
}

impl Style {
    pub fn paint(&self, tone: Tone, text: &str) -> String {
        if !self.color {
            return text.to_string();
        }
        let code = match tone {
            Tone::Ok => "32",
            Tone::Fail => "31",
            Tone::Warn => "33",
            Tone::Dim => "90",
            Tone::Accent => "36",
            Tone::Bold | Tone::Dark => "1",
            Tone::Brand => "38;2;255;90;95",
        };
        format!("\x1b[{code}m{text}\x1b[0m")
    }
}

// ── Pure renderers ───────────────────────────────────────────────────────────────────────────

/// `1.842 s` — three decimals, always seconds.
pub fn fmt_secs(d: Duration) -> String {
    format!("{:.3} s", d.as_secs_f64())
}

/// Width of the label column before the leader dots reach the timing.
const LEADER_WIDTH: usize = 40;

/// `label ········ value`, the dots filling to a fixed column so timings line up.
pub fn leader(style: &Style, label: &str, value: &str) -> String {
    leader_painted(style, label, label.chars().count(), value)
}

/// [`leader`] for a label that already carries color codes: `shown_len` is its visible width.
fn leader_painted(style: &Style, label: &str, shown_len: usize, value: &str) -> String {
    let dots = "·".repeat(LEADER_WIDTH.saturating_sub(shown_len).max(2));
    format!("{label} {} {value}", style.paint(Tone::Dim, &dots))
}

/// A model file's name without its quantization suffix: `knaif-qwen3-4b-v2-q4_k_m` ->
/// `knaif-qwen3-4b-v2`. What a person recognizes, not what is on disk.
pub fn model_label(stem: &str) -> String {
    let lower = stem.to_lowercase();
    let cut = lower
        .match_indices("-q")
        .filter(|(i, _)| lower[i + 2..].starts_with(|c: char| c.is_ascii_digit()))
        .map(|(i, _)| i)
        .last()
        .or_else(|| lower.rfind("-f16"));
    match cut {
        Some(i) => stem[..i].to_string(),
        None => stem.to_string(),
    }
}

pub fn render_header(style: &Style, skill: &str, model: Option<&str>) -> String {
    let mut parts = vec![style.paint(Tone::Bold, "knaif"), skill.to_string()];
    if let Some(m) = model {
        parts.push(m.to_string());
    }
    let sep = style.paint(Tone::Dim, " · ");
    format!(" {}\n {}", parts.join(&sep), style.paint(Tone::Dim, "│"))
}

pub fn render_planning(style: &Style, load: Option<Duration>, infer: Option<Duration>) -> String {
    let total = load.unwrap_or_default() + infer.unwrap_or_default();
    let mut out = format!(
        " {} {}",
        style.paint(Tone::Dim, "├─"),
        leader(style, "Planning", &fmt_secs(total))
    );
    if let (Some(l), Some(i)) = (load, infer) {
        out.push_str(&format!(
            "\n {}    {}",
            style.paint(Tone::Dim, "│"),
            style.paint(
                Tone::Dim,
                &format!("model load {} · inference {}", fmt_secs(l), fmt_secs(i))
            )
        ));
    }
    out.push_str(&format!("\n {}", style.paint(Tone::Dim, "│")));
    out
}

/// One row of the plan summary: the tool and what it works on.
pub struct PlanRow {
    pub tool: String,
    pub detail: String,
}

pub fn render_plan(style: &Style, rows: &[PlanRow]) -> String {
    let bar = style.paint(Tone::Dim, "│");
    let noun = if rows.len() == 1 { "step" } else { "steps" };
    let mut out = format!(
        " {} {}",
        style.paint(Tone::Dim, "├─"),
        style.paint(Tone::Bold, &format!("Plan · {} {noun}", rows.len()))
    );
    let width = rows
        .iter()
        .map(|r| r.tool.chars().count())
        .max()
        .unwrap_or(0);
    for (i, row) in rows.iter().enumerate() {
        let branch = if i + 1 == rows.len() {
            "└─"
        } else {
            "├─"
        };
        out.push_str(&format!(
            "\n {bar}   {} {}  {:<width$}  {}",
            style.paint(Tone::Dim, branch),
            i + 1,
            row.tool,
            style.paint(Tone::Dim, &row.detail),
        ));
    }
    out.push_str(&format!("\n {bar}"));
    out
}

pub fn render_step_head(style: &Style, idx: usize, total: usize, tool: &str) -> String {
    format!(
        " {} {}",
        style.paint(Tone::Dim, "├─"),
        style.paint(Tone::Bold, &format!("Step {idx}/{total} · {tool}"))
    )
}

/// A line inside a step, under the vertical rail: the command, a summary, a note.
pub fn render_detail(style: &Style, text: &str) -> String {
    format!(" {}   {text}", style.paint(Tone::Dim, "│"))
}

/// The command a step runs, as a shell line.
pub fn render_command(style: &Style, command: &str) -> String {
    render_detail(style, &format!("{} {command}", style.paint(Tone::Dim, "$")))
}

pub fn render_ok(style: &Style, what: &str, took: Option<Duration>) -> String {
    let body = format!("{} {what}", style.paint(Tone::Ok, "✓"));
    let line = match took {
        Some(d) => leader_painted(style, &body, 2 + what.chars().count(), &fmt_secs(d)),
        None => body,
    };
    format!(
        " {}   {} {line}",
        style.paint(Tone::Dim, "│"),
        style.paint(Tone::Dim, "└─")
    )
}

pub fn render_fail(style: &Style, what: &str, cause: Option<&str>) -> String {
    let mut out = format!(
        " {}   {} {} {what}",
        style.paint(Tone::Dim, "│"),
        style.paint(Tone::Dim, "└─"),
        style.paint(Tone::Fail, "✗"),
    );
    if let Some(c) = cause {
        out.push_str(&format!("\n {}      {}", style.paint(Tone::Dim, "│"), c));
    }
    out
}

/// How a run that stopped at a question or a refusal ends.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Closing {
    Clarify,
    Reject,
    Nothing,
}

pub fn render_closing(
    style: &Style,
    kind: Closing,
    message: &str,
    total: Option<Duration>,
) -> String {
    let (mark, label) = match kind {
        Closing::Clarify => (style.paint(Tone::Warn, "?"), "I need more detail:"),
        Closing::Reject => (style.paint(Tone::Fail, "⊘"), "I won't do that:"),
        Closing::Nothing => (style.paint(Tone::Ok, "✓"), "Nothing to do."),
    };
    let text = match kind {
        Closing::Nothing => label.to_string(),
        _ => format!("{label} {message}"),
    };
    let tail = total
        .map(|t| {
            format!(
                "  {}",
                style.paint(Tone::Dim, &format!("({})", fmt_secs(t)))
            )
        })
        .unwrap_or_default();
    format!(" {} {mark} {text}{tail}", style.paint(Tone::Dim, "└─"))
}

/// The last line of a run that completed: what it did and how long it took, waits excluded.
pub fn render_done(style: &Style, summary: &str, total: Duration) -> String {
    format!(
        " {}\n {} {} · {} {}",
        style.paint(Tone::Dim, "│"),
        style.paint(Tone::Dim, "└─"),
        style.paint(Tone::Ok, "Done"),
        summary,
        style.paint(Tone::Dim, &format!("· total {}", fmt_secs(total)))
    )
}

/// A step whose confirmation the user answered no: the step's own closing line.
pub fn render_declined(style: &Style) -> String {
    format!(
        " {}   {} {} Declined · no changes made",
        style.paint(Tone::Dim, "│"),
        style.paint(Tone::Dim, "└─"),
        style.paint(Tone::Warn, "⊘"),
    )
}

/// The last line of a run the user stopped at a confirmation. Not `Done`: the request was not
/// carried out, and a chain says how far it got.
pub fn render_declined_close(
    style: &Style,
    step: usize,
    steps: usize,
    summary: &str,
    total: Duration,
) -> String {
    let at = if steps > 1 {
        format!("Stopped at step {step} of {steps}")
    } else {
        "Stopped".to_string()
    };
    format!(
        " {}\n {} {} · you declined · {} {}",
        style.paint(Tone::Dim, "│"),
        style.paint(Tone::Dim, "└─"),
        style.paint(Tone::Warn, &at),
        summary,
        style.paint(Tone::Dim, &format!("· total {}", fmt_secs(total)))
    )
}

/// The last line of a run that stopped on an error.
pub fn render_stopped(style: &Style, message: &str, detail: &[String], total: Duration) -> String {
    let mut out = format!(
        " {}\n {} {} {message} {}",
        style.paint(Tone::Dim, "│"),
        style.paint(Tone::Dim, "└─"),
        style.paint(Tone::Fail, "✗"),
        style.paint(Tone::Dim, &format!("· total {}", fmt_secs(total)))
    );
    for line in detail {
        out.push_str(&format!("\n      {}", style.paint(Tone::Dim, line)));
    }
    out
}

/// The wordmark, `kn[AI]f`, as figlet-style letters in plain ASCII: dark letters in the
/// terminal's foreground, `[AI]` in the brand coral. Plain ASCII on purpose: block-character art
/// depends on the font and on the shape of the terminal's cells, and came out stretched or
/// striped in some of them. Shown by `knaif` with no arguments and by `--help`, in the terminal
/// view only.
const LOGO: [&[(Tone, &str)]; 6] = [
    &[
        (Tone::Dark, " _             "),
        (Tone::Brand, " ___      _      ___   ___  "),
        (Tone::Dark, "  __ "),
    ],
    &[
        (Tone::Dark, "| | __  _ __   "),
        (Tone::Brand, "|  _|    / \\    |_ _| |_  | "),
        (Tone::Dark, " / _|"),
    ],
    &[
        (Tone::Dark, "| |/ / | '_ \\  "),
        (Tone::Brand, "| |     / _ \\    | |    | | "),
        (Tone::Dark, "| |_ "),
    ],
    &[
        (Tone::Dark, "|   <  | | | | "),
        (Tone::Brand, "| |    / ___ \\   | |    | | "),
        (Tone::Dark, "|  _|"),
    ],
    &[
        (Tone::Dark, "|_|\\_\\ |_| |_| "),
        (Tone::Brand, "| |_  /_/   \\_\\ |___|  _| | "),
        (Tone::Dark, "|_|  "),
    ],
    &[
        (Tone::Dark, "               "),
        (Tone::Brand, "|___|                 |___| "),
        (Tone::Dark, "     "),
    ],
];

pub fn render_logo(style: &Style) -> String {
    LOGO.iter()
        .map(|row| {
            let body: String = row.iter().map(|(t, text)| style.paint(*t, text)).collect();
            format!(" {body}")
        })
        .collect::<Vec<_>>()
        .join(
            "
",
        )
}

/// Word-wrap `text` to `width` columns.
fn wrap(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        if !line.is_empty() && line.chars().count() + 1 + word.chars().count() > width {
            lines.push(std::mem::take(&mut line));
        }
        if !line.is_empty() {
            line.push(' ');
        }
        line.push_str(word);
    }
    if !line.is_empty() {
        lines.push(line);
    }
    lines
}

/// A framed notice: a warning or a tip that is about the machine, not the request.
pub fn render_box(style: &Style, tone: Tone, title: &str, text: &str) -> String {
    const INNER: usize = 74;
    let edge = |s: &str| style.paint(tone, s);
    let head_fill = "─".repeat(INNER.saturating_sub(title.chars().count() + 3));
    let mut out = format!(
        " {}",
        edge(&format!(
            "╭─ {} {head_fill}╮",
            style.paint(Tone::Bold, title)
        ))
    );
    // The bold title carries color codes inside the edge color, so repaint the edge after it.
    if style.color {
        out = format!(
            " {} {} {}",
            edge("╭─"),
            style.paint(Tone::Bold, title),
            edge(&format!("{head_fill}╮"))
        );
    }
    for line in wrap(text, INNER - 2) {
        let pad = " ".repeat(INNER - 2 - line.chars().count());
        out.push_str(&format!(
            "
 {} {line}{pad} {}",
            edge("│"),
            edge("│")
        ));
    }
    out.push_str(&format!(
        "
 {}",
        edge(&format!("╰{}╯", "─".repeat(INNER)))
    ));
    out
}

/// `0:05`, `12:34`, `1:02:03`: a clock reading for media time.
pub fn fmt_clock(seconds: f64) -> String {
    let total = seconds.max(0.0).round() as u64;
    let (h, m, s) = (total / 3600, (total % 3600) / 60, total % 60);
    if h > 0 {
        format!("{h}:{m:02}:{s:02}")
    } else {
        format!("{m}:{s:02}")
    }
}

/// The live line while ffmpeg runs: a spinner, media time done (of the total when known, with a
/// bar and percentage), the encoding speed and the wall time. Redrawn in place by the caller.
pub fn render_progress(
    style: &Style,
    frame: usize,
    elapsed: Duration,
    done_seconds: f64,
    total_seconds: Option<f64>,
    speed: Option<f64>,
) -> String {
    let mut parts = vec![format!(
        "{} {}",
        style.paint(Tone::Accent, FRAMES[frame % FRAMES.len()]),
        fmt_clock(done_seconds)
    )];
    if let Some(total) = total_seconds.filter(|t| *t > 0.0) {
        let ratio = (done_seconds / total).clamp(0.0, 1.0);
        let filled = (ratio * 20.0).round() as usize;
        let bar = format!("{}{}", "█".repeat(filled), "░".repeat(20 - filled));
        parts[0] = format!(
            "{} {} / {}  {}  {:.0}%",
            style.paint(Tone::Accent, FRAMES[frame % FRAMES.len()]),
            fmt_clock(done_seconds),
            fmt_clock(total),
            style.paint(Tone::Accent, &bar),
            ratio * 100.0
        );
    }
    if let Some(x) = speed {
        parts.push(format!("{x:.1}x"));
    }
    parts.push(fmt_secs(elapsed));
    let sep = style.paint(Tone::Dim, " · ");
    format!(
        " {}   {} {}",
        style.paint(Tone::Dim, "│"),
        style.paint(Tone::Dim, "└─"),
        parts.join(&sep)
    )
}

/// `path` relative to `base` when it lies under it, else as given.
pub fn display_rel(path: &std::path::Path, base: &std::path::Path) -> String {
    path.strip_prefix(base)
        .unwrap_or(path)
        .display()
        .to_string()
}

/// `812 B`, `3.2 KB`, `12.5 MB`: one decimal above a kilobyte.
pub fn fmt_bytes(n: u64) -> String {
    const KB: f64 = 1024.0;
    let f = n as f64;
    if f < KB {
        format!("{n} B")
    } else if f < KB * KB {
        format!("{:.1} KB", f / KB)
    } else if f < KB * KB * KB {
        format!("{:.1} MB", f / (KB * KB))
    } else {
        format!("{:.1} GB", f / (KB * KB * KB))
    }
}

pub fn files_written(n: usize) -> String {
    match n {
        0 => "no files written".to_string(),
        1 => "1 file written".to_string(),
        n => format!("{n} files written"),
    }
}

// ── Process state: view choice, clock, counters ──────────────────────────────────────────────

static VIEW: OnceLock<Option<Style>> = OnceLock::new();
static VERBOSE: AtomicBool = AtomicBool::new(false);
static HEADER: AtomicBool = AtomicBool::new(false);
static CLOSED: AtomicBool = AtomicBool::new(false);
static WRITTEN: AtomicUsize = AtomicUsize::new(0);
static DECLINED: Mutex<Option<(usize, usize)>> = Mutex::new(None);
static CLOCK: Mutex<Option<Clock>> = Mutex::new(None);

struct Clock {
    start: Instant,
    waited: Duration,
    load: Option<Duration>,
    infer: Option<Duration>,
}

/// Decide the view once. Rich only when a person is looking at both streams.
pub fn init(verbose: bool) {
    VERBOSE.store(verbose, Ordering::Relaxed);
    let _ = VIEW.get_or_init(|| {
        // `KNAIF_VIEW=rich|plain` overrides detection: `plain` for a terminal where the tree is
        // unwanted, `rich` so the tree can be tested and demonstrated through a pipe.
        let forced = std::env::var("KNAIF_VIEW").ok();
        match forced.as_deref() {
            Some("plain") => return None,
            Some("rich") => {}
            _ => {
                let tty = std::io::stdout().is_terminal() && std::io::stderr().is_terminal();
                let dumb = std::env::var("TERM").is_ok_and(|t| t == "dumb");
                if !tty || dumb {
                    return None;
                }
            }
        }
        let vt = enable_vt();
        let no_color = std::env::var_os("NO_COLOR").is_some_and(|v| !v.is_empty());
        Some(Style {
            color: vt && !no_color,
        })
    });
}

/// `Some(style)` when this run draws the tree.
pub fn view() -> Option<Style> {
    VIEW.get().copied().flatten()
}

pub fn verbose() -> bool {
    VERBOSE.load(Ordering::Relaxed)
}

#[cfg(windows)]
fn enable_vt() -> bool {
    use windows_sys::Win32::System::Console::{
        GetConsoleMode, GetStdHandle, SetConsoleMode, ENABLE_VIRTUAL_TERMINAL_PROCESSING,
        STD_ERROR_HANDLE, STD_OUTPUT_HANDLE,
    };
    let mut all = true;
    for which in [STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
        // SAFETY: Win32 console calls on the process's own standard handles; `mode` is a local.
        unsafe {
            let h = GetStdHandle(which);
            let mut mode = 0u32;
            if GetConsoleMode(h, &mut mode) == 0 {
                all = false;
                continue;
            }
            if mode & ENABLE_VIRTUAL_TERMINAL_PROCESSING == 0
                && SetConsoleMode(h, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING) == 0
            {
                all = false;
            }
        }
    }
    all
}

#[cfg(not(windows))]
fn enable_vt() -> bool {
    true
}

/// Start the clock for this run. Idempotent.
pub fn start_clock() {
    let mut c = CLOCK.lock().unwrap_or_else(|e| e.into_inner());
    if c.is_none() {
        *c = Some(Clock {
            start: Instant::now(),
            waited: Duration::ZERO,
            load: None,
            infer: None,
        });
    }
}

fn with_clock(f: impl FnOnce(&mut Clock)) {
    let mut c = CLOCK.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(clock) = c.as_mut() {
        f(clock);
    }
}

/// Time spent waiting for the user (a prompt). Excluded from the total.
pub fn add_wait(d: Duration) {
    with_clock(|c| c.waited += d);
}

pub fn set_load(d: Duration) {
    with_clock(|c| c.load = Some(d));
}

/// Inference can run twice (a repair retry); the times add up.
pub fn add_infer(d: Duration) {
    with_clock(|c| c.infer = Some(c.infer.unwrap_or_default() + d));
}

pub fn timings() -> (Option<Duration>, Option<Duration>) {
    let c = CLOCK.lock().unwrap_or_else(|e| e.into_inner());
    c.as_ref()
        .map(|c| (c.load, c.infer))
        .unwrap_or((None, None))
}

/// Wall time since the clock started, minus the time spent at prompts.
pub fn total() -> Duration {
    let c = CLOCK.lock().unwrap_or_else(|e| e.into_inner());
    c.as_ref()
        .map(|c| c.start.elapsed().saturating_sub(c.waited))
        .unwrap_or_default()
}

pub fn note_written(n: usize) {
    WRITTEN.fetch_add(n, Ordering::Relaxed);
}

pub fn written() -> usize {
    WRITTEN.load(Ordering::Relaxed)
}

/// Print the header once, the first time anything in the run speaks.
pub fn header_once(skill: &str, model: Option<&str>) {
    if let Some(style) = view() {
        if !HEADER.swap(true, Ordering::Relaxed) {
            println!("{}", render_header(&style, skill, model));
        }
    }
}

pub fn header_shown() -> bool {
    HEADER.load(Ordering::Relaxed)
}

/// Record that the user declined step `step` of `steps`, so the run closes as stopped, not done.
pub fn mark_declined(step: usize, steps: usize) {
    *DECLINED.lock().unwrap_or_else(|e| e.into_inner()) = Some((step, steps));
}

pub fn declined() -> Option<(usize, usize)> {
    *DECLINED.lock().unwrap_or_else(|e| e.into_inner())
}

/// Mark the run as closed by an outcome line (a question, a refusal), so [`finish`] adds nothing.
pub fn mark_closed() {
    CLOSED.store(true, Ordering::Relaxed);
}

pub fn closed() -> bool {
    CLOSED.load(Ordering::Relaxed)
}

/// Print a closing outcome line (clarify / reject / nothing to do) in the tree.
pub fn closing(style: &Style, kind: Closing, message: &str) {
    println!("{}", render_closing(style, kind, message, Some(total())));
    mark_closed();
}

/// Ask a `[y/N]` question without counting the wait.
pub fn timed<T>(f: impl FnOnce() -> T) -> T {
    let t = Instant::now();
    let out = f();
    add_wait(t.elapsed());
    out
}

// ── Spinner on a private copy of stderr ──────────────────────────────────────────────────────

/// A copy of the real stderr, taken before it can be silenced, so the spinner and knaif's own
/// messages still reach the terminal while llama.cpp's output is thrown away.
pub fn real_stderr() -> Option<std::fs::File> {
    #[cfg(unix)]
    {
        use std::os::fd::AsFd;
        std::io::stderr()
            .as_fd()
            .try_clone_to_owned()
            .ok()
            .map(std::fs::File::from)
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsHandle;
        std::io::stderr()
            .as_handle()
            .try_clone_to_owned()
            .ok()
            .map(std::fs::File::from)
    }
}

const FRAMES: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/// A live `⠋ label 1.234 s` line. It draws to the stderr copy it is given and erases itself when
/// stopped, so the run's own lines start on a clean row.
pub struct Spinner {
    stop: Arc<AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl Spinner {
    pub fn start(style: Style, label: &str, mut out: std::fs::File) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let flag = Arc::clone(&stop);
        let label = label.to_string();
        let handle = std::thread::spawn(move || {
            let started = Instant::now();
            let mut frame = 0usize;
            while !flag.load(Ordering::Relaxed) {
                let line = format!(
                    "\r {} {label} {}",
                    style.paint(Tone::Accent, FRAMES[frame % FRAMES.len()]),
                    style.paint(Tone::Dim, &fmt_secs(started.elapsed()))
                );
                let _ = out.write_all(line.as_bytes());
                let _ = out.flush();
                frame += 1;
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = out.write_all(b"\r\x1b[2K");
            let _ = out.flush();
        });
        Self {
            stop,
            handle: Some(handle),
        }
    }

    pub fn stop(mut self) {
        self.halt();
    }

    fn halt(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

impl Drop for Spinner {
    fn drop(&mut self) {
        self.halt();
    }
}

// ── Silencing the process's stderr while llama.cpp runs ──────────────────────────────────────

/// Points the process's stderr (fd 2) at the null device until dropped.
///
/// llama.cpp and the GPU backend libraries can write to stderr directly, bypassing the log
/// hooks, so a hook alone cannot keep a terminal clean. Held only for the planning window, only
/// in the terminal view, and never under `--verbose`.
pub struct StderrGuard {
    saved: i32,
}

#[cfg(unix)]
impl StderrGuard {
    pub fn silence() -> Option<Self> {
        use std::os::fd::AsRawFd;
        let null = std::fs::OpenOptions::new()
            .write(true)
            .open("/dev/null")
            .ok()?;
        // SAFETY: dup/dup2 on fd 2 and a descriptor we own; the original is restored on drop.
        unsafe {
            let saved = libc::dup(2);
            if saved < 0 {
                return None;
            }
            if libc::dup2(null.as_raw_fd(), 2) < 0 {
                libc::close(saved);
                return None;
            }
            Some(Self { saved })
        }
    }
}

#[cfg(unix)]
impl Drop for StderrGuard {
    fn drop(&mut self) {
        // SAFETY: restores the descriptor saved in `silence`.
        unsafe {
            libc::dup2(self.saved, 2);
            libc::close(self.saved);
        }
    }
}

#[cfg(windows)]
extern "C" {
    fn _dup(fd: i32) -> i32;
    fn _dup2(from: i32, to: i32) -> i32;
    fn _close(fd: i32) -> i32;
    fn _open_osfhandle(handle: isize, flags: i32) -> i32;
}

#[cfg(windows)]
impl StderrGuard {
    pub fn silence() -> Option<Self> {
        use std::os::windows::io::IntoRawHandle;
        let null = std::fs::OpenOptions::new().write(true).open("NUL").ok()?;
        // SAFETY: CRT descriptor calls on fd 2 and one we create. `_open_osfhandle` takes
        // ownership of the handle, so the file is released into it. The CRT's `_dup2` onto fd 2
        // also repoints the process's STD_ERROR_HANDLE, which is what native libraries write to.
        unsafe {
            let nul_fd = _open_osfhandle(null.into_raw_handle() as isize, 0);
            if nul_fd < 0 {
                return None;
            }
            let saved = _dup(2);
            if saved < 0 {
                _close(nul_fd);
                return None;
            }
            let ok = _dup2(nul_fd, 2) == 0;
            _close(nul_fd);
            if !ok {
                _close(saved);
                return None;
            }
            Some(Self { saved })
        }
    }
}

#[cfg(windows)]
impl Drop for StderrGuard {
    fn drop(&mut self) {
        // SAFETY: restores the descriptor saved in `silence`.
        unsafe {
            _dup2(self.saved, 2);
            _close(self.saved);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PLAIN: Style = Style { color: false };
    const COLOR: Style = Style { color: true };

    fn secs(ms: u64) -> Duration {
        Duration::from_millis(ms)
    }

    #[test]
    fn seconds_have_three_decimals() {
        assert_eq!(fmt_secs(secs(1842)), "1.842 s");
        assert_eq!(fmt_secs(secs(0)), "0.000 s");
        assert_eq!(fmt_secs(secs(61_005)), "61.005 s");
    }

    #[test]
    fn color_is_only_added_when_asked_for() {
        assert_eq!(PLAIN.paint(Tone::Ok, "x"), "x");
        assert_eq!(COLOR.paint(Tone::Ok, "x"), "\x1b[32mx\x1b[0m");
        assert_eq!(COLOR.paint(Tone::Fail, "x"), "\x1b[31mx\x1b[0m");
    }

    #[test]
    fn the_leader_pads_to_one_column() {
        let short = leader(&PLAIN, "Planning", "1.000 s");
        let long = leader(&PLAIN, "Planning more", "1.000 s");
        assert!(short.ends_with(" 1.000 s") && long.ends_with(" 1.000 s"));
        assert_eq!(short.chars().count(), long.chars().count());
    }

    #[test]
    fn a_long_label_still_gets_dots() {
        let line = leader(&PLAIN, &"x".repeat(60), "1.000 s");
        assert!(line.contains("··"), "{line}");
    }

    #[test]
    fn the_header_names_skill_and_model() {
        let h = render_header(&PLAIN, "ffmpeg", Some("knaif-qwen3-4b-v2"));
        assert!(
            h.starts_with(" knaif · ffmpeg · knaif-qwen3-4b-v2\n"),
            "{h}"
        );
        assert_eq!(render_header(&PLAIN, "ffmpeg", None), " knaif · ffmpeg\n │");
    }

    #[test]
    fn planning_splits_load_and_inference() {
        let s = render_planning(&PLAIN, Some(secs(913)), Some(secs(929)));
        assert!(s.contains("Planning"), "{s}");
        assert!(s.contains("1.842 s"), "{s}");
        assert!(s.contains("model load 0.913 s · inference 0.929 s"), "{s}");
        // No split to show when nothing was measured (the mock backend).
        let mock = render_planning(&PLAIN, None, None);
        assert!(!mock.contains("model load"), "{mock}");
    }

    #[test]
    fn the_plan_is_a_branching_list() {
        let rows = vec![
            PlanRow {
                tool: "strip_audio".into(),
                detail: "clip.mp4 → silent.mp4".into(),
            },
            PlanRow {
                tool: "resize_video".into(),
                detail: "silent.mp4 → small.mp4".into(),
            },
        ];
        let s = render_plan(&PLAIN, &rows);
        assert!(s.contains("Plan · 2 steps"), "{s}");
        assert!(s.contains("├─ 1  strip_audio   "), "{s}");
        assert!(s.contains("└─ 2  resize_video  "), "{s}");
        assert!(render_plan(&PLAIN, &rows[..1]).contains("Plan · 1 step\n"));
    }

    #[test]
    fn a_step_shows_its_command_then_its_result_with_time() {
        assert_eq!(
            render_step_head(&PLAIN, 1, 2, "strip_audio"),
            " ├─ Step 1/2 · strip_audio"
        );
        assert_eq!(
            render_command(&PLAIN, "ffmpeg -i a.mp4 b.mp4"),
            " │   $ ffmpeg -i a.mp4 b.mp4"
        );
        let ok = render_ok(&PLAIN, "b.mp4", Some(secs(412)));
        assert!(ok.starts_with(" │   └─ ✓ b.mp4 "), "{ok}");
        assert!(ok.ends_with(" 0.412 s"), "{ok}");
        assert_eq!(render_ok(&PLAIN, "b.mp4", None), " │   └─ ✓ b.mp4");
    }

    #[test]
    fn a_failure_carries_its_cause() {
        let f = render_fail(&PLAIN, "b.mp4", Some("the output folder is read-only."));
        assert_eq!(
            f,
            " │   └─ ✗ b.mp4\n │      the output folder is read-only."
        );
    }

    #[test]
    fn dots_line_up_whatever_the_colors_add() {
        let plain = render_ok(&PLAIN, "b.mp4", Some(secs(5)));
        let colored = render_ok(&COLOR, "b.mp4", Some(secs(5)));
        let strip = |s: &str| {
            let mut out = String::new();
            let mut skip = false;
            for c in s.chars() {
                match (skip, c) {
                    (false, '\x1b') => skip = true,
                    (true, 'm') => skip = false,
                    (false, c) => out.push(c),
                    _ => {}
                }
            }
            out
        };
        assert_eq!(strip(&colored), plain);
    }

    #[test]
    fn questions_and_refusals_close_the_tree() {
        let q = render_closing(&PLAIN, Closing::Clarify, "Which file?", Some(secs(2100)));
        assert_eq!(q, " └─ ? I need more detail: Which file?  (2.100 s)");
        let r = render_closing(&PLAIN, Closing::Reject, "Not allowed.", None);
        assert_eq!(r, " └─ ⊘ I won't do that: Not allowed.");
        assert_eq!(
            render_closing(&PLAIN, Closing::Nothing, "", None),
            " └─ ✓ Nothing to do."
        );
    }

    #[test]
    fn a_finished_run_reports_a_total() {
        assert_eq!(
            render_done(&PLAIN, "2 files written", secs(3360)),
            " │\n └─ Done · 2 files written · total 3.360 s"
        );
    }

    #[test]
    fn a_declined_step_closes_inside_the_tree() {
        assert_eq!(
            render_declined(&PLAIN),
            " │   └─ ⊘ Declined · no changes made"
        );
    }

    #[test]
    fn a_declined_run_says_where_it_stopped_not_done() {
        assert_eq!(
            render_declined_close(&PLAIN, 1, 2, "no files written", secs(2134)),
            " │\n └─ Stopped at step 1 of 2 · you declined · no files written · total 2.134 s"
        );
        // A one-step plan has no position worth naming.
        assert_eq!(
            render_declined_close(&PLAIN, 1, 1, "no files written", secs(900)),
            " │\n └─ Stopped · you declined · no files written · total 0.900 s"
        );
    }

    #[test]
    fn a_stopped_run_names_the_error_and_keeps_detail_aligned() {
        let s = render_stopped(
            &PLAIN,
            "step 2 of 2 failed",
            &["the folder is read-only.".to_string()],
            secs(1731),
        );
        assert!(s.contains("└─ ✗ step 2 of 2 failed · total 1.731 s"), "{s}");
        assert!(s.ends_with("\n      the folder is read-only."), "{s}");
    }

    #[test]
    fn a_model_is_named_without_its_quantization() {
        assert_eq!(model_label("knaif-qwen3-4b-v2-q4_k_m"), "knaif-qwen3-4b-v2");
        assert_eq!(
            model_label("knaif-qwen3-1.7b-v2-q6_k"),
            "knaif-qwen3-1.7b-v2"
        );
        assert_eq!(model_label("Qwen3-4B-Q4_K_M"), "Qwen3-4B");
        assert_eq!(model_label("qwen3-1.7b-base-f16"), "qwen3-1.7b-base");
        assert_eq!(model_label("custom"), "custom");
    }

    #[test]
    fn sizes_are_human() {
        assert_eq!(fmt_bytes(812), "812 B");
        assert_eq!(fmt_bytes(3238), "3.2 KB");
        assert_eq!(fmt_bytes(13_107_200), "12.5 MB");
    }

    #[test]
    fn counts_are_worded() {
        assert_eq!(files_written(0), "no files written");
        assert_eq!(files_written(1), "1 file written");
        assert_eq!(files_written(3), "3 files written");
    }

    #[test]
    fn paths_are_shown_relative_when_they_can_be() {
        let base = std::path::Path::new("/work/proj");
        assert_eq!(
            display_rel(&base.join("out/clip.mp4"), base),
            std::path::Path::new("out/clip.mp4").display().to_string()
        );
        assert_eq!(
            display_rel(std::path::Path::new("/elsewhere/a.pdf"), base),
            std::path::Path::new("/elsewhere/a.pdf")
                .display()
                .to_string()
        );
    }

    #[test]
    fn the_logo_is_the_wordmark_in_two_colors() {
        let plain = render_logo(&PLAIN);
        assert_eq!(plain.lines().count(), 6);
        assert!(plain.is_ascii(), "the logo must survive any font");
        assert!(plain.lines().all(|l| l.chars().count() <= 56), "{plain}");
        let colored = render_logo(&COLOR);
        assert!(
            colored.contains("\x1b[38;2;255;90;95m"),
            "no coral in the logo"
        );
        assert!(colored.contains("\x1b[1m"), "no dark letters in the logo");
    }

    #[test]
    fn a_box_frames_wrapped_text_at_one_width() {
        let b = render_box(
            &PLAIN,
            Tone::Warn,
            "Running on the CPU",
            &"word ".repeat(40),
        );
        let widths: std::collections::HashSet<usize> =
            b.lines().map(|l| l.chars().count()).collect();
        assert_eq!(
            widths.len(),
            1,
            "ragged frame:
{b}"
        );
        assert!(b.starts_with(" ╭─ Running on the CPU "), "{b}");
        assert!(b.lines().last().unwrap().starts_with(" ╰"), "{b}");
    }

    #[test]
    fn media_time_reads_as_a_clock() {
        assert_eq!(fmt_clock(5.0), "0:05");
        assert_eq!(fmt_clock(754.4), "12:34");
        assert_eq!(fmt_clock(3723.0), "1:02:03");
        assert_eq!(fmt_clock(-1.0), "0:00");
    }

    #[test]
    fn the_live_line_shows_percent_when_the_length_is_known() {
        let l = render_progress(&PLAIN, 0, secs(1200), 15.0, Some(60.0), Some(2.4));
        assert!(l.contains("0:15 / 1:00"), "{l}");
        assert!(l.contains("25%"), "{l}");
        assert!(l.contains("2.4x"), "{l}");
        assert!(l.ends_with("1.200 s"), "{l}");
    }

    #[test]
    fn the_live_line_still_counts_when_the_length_is_unknown() {
        let l = render_progress(&PLAIN, 1, secs(3000), 7.0, None, None);
        assert!(l.contains("0:07"), "{l}");
        assert!(!l.contains('%') && !l.contains("/ "), "{l}");
        assert!(l.ends_with("3.000 s"), "{l}");
    }

    #[test]
    fn waiting_for_the_user_is_not_counted() {
        start_clock();
        let before = total();
        timed(|| std::thread::sleep(Duration::from_millis(80)));
        let after = total();
        assert!(
            after.saturating_sub(before) < Duration::from_millis(40),
            "a prompt wait leaked into the total: {before:?} -> {after:?}"
        );
    }
}
