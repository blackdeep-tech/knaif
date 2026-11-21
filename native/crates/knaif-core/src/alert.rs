//! Completion alerts: "beep me when it's done" (core tool `alert_when_done`).
//!
//! The alert is a **run option the model asks for, never a step that runs**: the pipeline takes
//! it off the plan ([`split_alert`]) and, once a run that did some work has ended, alerts the user
//! with a desktop notification and a beep. Port of Python `knaif.planner.split_alert` /
//! `alert_last`; the L2 contract is `contracts/parity/alert_cases.json`.

use serde_json::{json, Value};

/// The core tool's name (`contracts/runtime/core_tools.yaml`).
pub const ALERT_TOOL: &str = "alert_when_done";

/// Asked for with nothing to do ("beep"): the alert needs a task to finish.
pub const NO_WORK_TO_ALERT: &str = "I can alert you when a task finishes, but there is no task in \
     this request. What should I do first?";

fn is_alert(step: &Value) -> bool {
    step.get("tool").and_then(Value::as_str) == Some(ALERT_TOOL)
}

/// `payload` without its `alert_when_done` steps, and whether the run alerts.
///
/// - the alert may stand anywhere in the plan, and any args the model gives it are ignored;
/// - two alerts are one;
/// - a plan that only clarifies or rejects gets no alert: nothing ran, and the user is reading
///   the answer already;
/// - an alert with no work beside it (only `done`, or nothing) becomes a clarify.
pub fn split_alert(payload: &Value) -> (Value, bool) {
    let Some(plan) = payload.get("plan").and_then(Value::as_array) else {
        return (payload.clone(), false);
    };
    if !plan.iter().any(is_alert) {
        return (payload.clone(), false);
    }
    let rest: Vec<Value> = plan.iter().filter(|s| !is_alert(s)).cloned().collect();
    let work: Vec<&Value> = rest
        .iter()
        .filter(|s| s.is_object() && s.get("tool").and_then(Value::as_str) != Some("done"))
        .collect();
    let mut out = payload.clone();
    if work.is_empty() {
        out["plan"] = json!([{"tool": "clarify", "args": {"question": NO_WORK_TO_ALERT}}]);
        return (out, false);
    }
    let control_only = work.iter().all(|s| {
        matches!(
            s.get("tool").and_then(Value::as_str),
            Some("clarify") | Some("reject")
        )
    });
    out["plan"] = Value::Array(rest);
    (out, !control_only)
}

/// Move `alert_when_done` steps to the end of the plan, in place, keeping every other step's
/// order: chain linking joins a step's output to the next step's input, and an alert between two
/// steps would break the chain.
pub fn alert_last(payload: &mut Value) {
    let Some(plan) = payload.get_mut("plan").and_then(Value::as_array_mut) else {
        return;
    };
    if plan.iter().any(is_alert) {
        let (alerts, rest): (Vec<Value>, Vec<Value>) = plan.drain(..).partition(is_alert);
        plan.extend(rest);
        plan.extend(alerts);
    }
}

// ── firing it ────────────────────────────────────────────────────────────────

/// `KNAIF_OS_SINK=<file>` records each alert as a JSON line instead of making it, for tests and
/// the eval lanes.
pub const SINK_ENV: &str = "KNAIF_OS_SINK";
const MAX_MESSAGE: usize = 120;

/// knaif's own app id. The installer registers it under HKCU (DisplayName, IconUri), and from
/// then on the banner says knaif; until then the toast borrows Windows PowerShell's.
pub const APP_ID: &str = "knaif.cli";

/// The toast, shown under PowerShell's app id unless `KNAIF_ALERT_APP_ID` names knaif's. The text
/// comes from the environment and is XML-escaped in the script, so nothing the user typed is ever
/// parsed as script or markup. Identical to Python's `_TOAST_PS`.
const TOAST_PS: &str = r#"
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [System.Security.SecurityElement]::Escape($env:KNAIF_ALERT_TITLE)
$m = [System.Security.SecurityElement]::Escape($env:KNAIF_ALERT_MESSAGE)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
$app = $env:KNAIF_ALERT_APP_ID
if (-not $app) { $app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe' }
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"#;

const OSASCRIPT: &[&str] = &[
    "-e",
    "on run argv",
    "-e",
    "display notification (item 2 of argv) with title (item 1 of argv) sound name \"Glass\"",
    "-e",
    "end run",
];

/// One alert: what it says, and the outcome it reports.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Alert {
    /// `done`, `failed` or `stopped`.
    pub outcome: String,
    pub title: String,
    pub message: String,
}

/// `done`, `failed` or `stopped` for a run that did some work (a step other than a control tool
/// ran, or a file was downloaded), else None: a request answered before anything ran is on
/// screen already. Port of Python `run_outcome`.
pub fn run_outcome(failed: bool, declined: bool, work_started: bool) -> Option<&'static str> {
    if !work_started {
        return None;
    }
    Some(if failed {
        "failed"
    } else if declined {
        "stopped"
    } else {
        "done"
    })
}

fn short(text: &str) -> String {
    let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if text.chars().count() <= MAX_MESSAGE {
        return text;
    }
    let head: String = text.chars().take(MAX_MESSAGE - 1).collect();
    format!("{}…", head.trim_end())
}

/// The notification's title and text for `outcome`. Port of Python `alert_for`.
pub fn alert_for(
    outcome: &str,
    request: Option<&str>,
    seconds: f64,
    reason: Option<&str>,
) -> Alert {
    let took = format!("{seconds:.0} s");
    let reason = reason.filter(|r| !r.is_empty());
    let mut message = match outcome {
        "failed" => match reason {
            Some(r) => format!("Failed: {r}"),
            None => format!("Failed ({took})"),
        },
        "stopped" => "Stopped: a confirmation was declined".to_string(),
        _ => format!("Done ({took})"),
    };
    if let Some(req) = request.filter(|r| !r.is_empty()) {
        if !(outcome == "failed" && reason.is_some()) && outcome != "stopped" {
            message.push_str(&format!(": {req}"));
        }
    }
    Alert {
        outcome: outcome.to_string(),
        title: "knaif".to_string(),
        message: short(&message),
    }
}

/// The OS an alert is made on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Platform {
    Windows,
    MacOs,
    Linux,
}

impl Platform {
    pub fn current() -> Self {
        if cfg!(windows) {
            Platform::Windows
        } else if cfg!(target_os = "macos") {
            Platform::MacOs
        } else {
            Platform::Linux
        }
    }
}

/// What an alert needs from the system, so the backends can be tested without one.
pub trait AlertSystem {
    /// A program's path on PATH, or None.
    fn which(&self, program: &str) -> Option<String>;
    /// Run `argv` (never through a shell) with `env` added to the inherited environment, its
    /// output discarded; the exit code.
    fn run(&mut self, argv: &[String], env: &[(String, String)]) -> std::io::Result<i32>;
    /// The Windows system sound.
    fn beep(&mut self);
    /// knaif's app id when the installer registered it.
    fn app_id(&self) -> Option<String>;
    /// The terminal bell, on stderr.
    fn bell(&mut self);
}

fn record(alert: &Alert, sink: &str) -> std::io::Result<()> {
    use std::io::Write;
    let line = json!({
        "kind": "alert",
        "outcome": alert.outcome,
        "title": alert.title,
        "message": alert.message,
        "sound": true,
    });
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(sink)?;
    writeln!(file, "{line}")
}

/// Make `alert` (or record it in the sink) on `platform`. Returns the backend used: `sink`,
/// `windows-toast`, `macos`, `notify-send` or `bell`. Never fails and never writes to stdout.
/// Port of Python `fire`.
pub fn fire_with(
    alert: &Alert,
    env: &std::collections::HashMap<String, String>,
    platform: Platform,
    sys: &mut dyn AlertSystem,
) -> &'static str {
    if let Some(sink) = env.get(SINK_ENV).filter(|s| !s.is_empty()) {
        if record(alert, sink).is_ok() {
            return "sink";
        }
        sys.bell();
        return "bell";
    }
    let ok = |r: std::io::Result<i32>| matches!(r, Ok(0));
    match platform {
        Platform::Windows => {
            sys.beep();
            let mut child = vec![
                ("KNAIF_ALERT_TITLE".to_string(), alert.title.clone()),
                ("KNAIF_ALERT_MESSAGE".to_string(), alert.message.clone()),
            ];
            if !env.contains_key("KNAIF_ALERT_APP_ID") {
                if let Some(id) = sys.app_id() {
                    child.push(("KNAIF_ALERT_APP_ID".to_string(), id));
                }
            }
            let argv: Vec<String> = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
            ]
            .iter()
            .map(|s| s.to_string())
            .chain([TOAST_PS.to_string()])
            .collect();
            if ok(sys.run(&argv, &child)) {
                return "windows-toast";
            }
        }
        Platform::MacOs => {
            let argv: Vec<String> = std::iter::once("osascript".to_string())
                .chain(OSASCRIPT.iter().map(|s| s.to_string()))
                .chain([alert.title.clone(), alert.message.clone()])
                .collect();
            if ok(sys.run(&argv, &[])) {
                return "macos";
            }
        }
        Platform::Linux => {
            let display = env.get("DISPLAY").is_some_and(|v| !v.is_empty())
                || env.get("WAYLAND_DISPLAY").is_some_and(|v| !v.is_empty());
            let ssh = env.get("SSH_CONNECTION").is_some_and(|v| !v.is_empty());
            if display && !ssh {
                if let Some(notify) = sys.which("notify-send") {
                    let argv = vec![notify, alert.title.clone(), alert.message.clone()];
                    if ok(sys.run(&argv, &[])) {
                        match sys.which("canberra-gtk-play") {
                            Some(player) => {
                                let _ = sys.run(&[player, "-i".into(), "complete".into()], &[]);
                            }
                            None => sys.bell(),
                        }
                        return "notify-send";
                    }
                }
            }
        }
    }
    sys.bell();
    "bell"
}

/// The real system: PATH lookup, a child process with no window and a 15 s limit, the Windows
/// system sound and the registry check for knaif's app id.
pub struct RealSystem;

impl AlertSystem for RealSystem {
    fn which(&self, program: &str) -> Option<String> {
        let path = std::env::var_os("PATH")?;
        std::env::split_paths(&path)
            .map(|dir| dir.join(program))
            .find(|p| p.is_file())
            .map(|p| p.to_string_lossy().into_owned())
    }

    fn run(&mut self, argv: &[String], env: &[(String, String)]) -> std::io::Result<i32> {
        use std::process::{Command, Stdio};
        let (program, args) = argv
            .split_first()
            .ok_or_else(|| std::io::Error::other("empty argv"))?;
        let mut cmd = Command::new(program);
        cmd.args(args)
            .envs(env.iter().map(|(k, v)| (k, v)))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = cmd.spawn()?;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
        loop {
            if let Some(status) = child.try_wait()? {
                return Ok(status.code().unwrap_or(1));
            }
            if std::time::Instant::now() >= deadline {
                let _ = child.kill();
                return Err(std::io::Error::other("timed out"));
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
    }

    fn beep(&mut self) {
        #[cfg(windows)]
        // SAFETY: MessageBeep takes a sound type and has no other preconditions.
        unsafe {
            windows_sys::Win32::System::Diagnostics::Debug::MessageBeep(0);
        }
    }

    fn app_id(&self) -> Option<String> {
        #[cfg(windows)]
        {
            use windows_sys::Win32::System::Registry::{
                RegCloseKey, RegOpenKeyExW, HKEY, HKEY_CURRENT_USER, KEY_READ,
            };
            let sub: Vec<u16> = format!(r"Software\Classes\AppUserModelId\{APP_ID}")
                .encode_utf16()
                .chain([0])
                .collect();
            let mut key: HKEY = std::ptr::null_mut();
            // SAFETY: `sub` is NUL-terminated and `key` receives a handle closed right after.
            let rc =
                unsafe { RegOpenKeyExW(HKEY_CURRENT_USER, sub.as_ptr(), 0, KEY_READ, &mut key) };
            if rc == 0 {
                // SAFETY: `key` was opened just above.
                unsafe { RegCloseKey(key) };
                return Some(APP_ID.to_string());
            }
        }
        None
    }

    fn bell(&mut self) {
        use std::io::Write;
        let mut err = std::io::stderr();
        let _ = err.write_all(b"\x07");
        let _ = err.flush();
    }
}

/// Make `alert` on this machine (see [`fire_with`]).
pub fn fire(alert: &Alert) -> &'static str {
    let env: std::collections::HashMap<String, String> = std::env::vars().collect();
    fire_with(alert, &env, Platform::current(), &mut RealSystem)
}
