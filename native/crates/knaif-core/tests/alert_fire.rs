//! The alert itself, as Python's `knaif.alert` makes it (python/core/tests/test_alert.py): the
//! text, the `KNAIF_OS_SINK` record, and each OS backend, through a fake system so nothing
//! beeps. An alert never writes to stdout, never runs a shell, never puts the text into script
//! source, and never fails the run: any backend error falls back to the terminal bell.

use std::collections::HashMap;

use knaif_core::alert::{alert_for, fire_with, run_outcome, Alert, AlertSystem, Platform};

// ── the outcome ──────────────────────────────────────────────────────────────

#[test]
fn the_outcome_of_a_run() {
    assert_eq!(run_outcome(false, false, false), None, "no work, no alert");
    assert_eq!(
        run_outcome(true, false, false),
        None,
        "failed before any work"
    );
    assert_eq!(run_outcome(false, false, true), Some("done"));
    assert_eq!(run_outcome(true, false, true), Some("failed"));
    assert_eq!(run_outcome(false, true, true), Some("stopped"));
    assert_eq!(run_outcome(true, true, true), Some("failed"));
}

// ── the text ─────────────────────────────────────────────────────────────────

#[test]
fn alert_for_done() {
    let a = alert_for(
        "done",
        Some("convert clip.mov to mp4 and beep when it's done"),
        12.4,
        None,
    );
    assert_eq!(
        a,
        Alert {
            outcome: "done".into(),
            title: "knaif".into(),
            message: "Done (12 s): convert clip.mov to mp4 and beep when it's done".into(),
        }
    );
}

#[test]
fn alert_for_failed_names_the_reason() {
    let a = alert_for("failed", Some("x"), 1.0, Some("ffmpeg exited with 1"));
    assert_eq!(a.message, "Failed: ffmpeg exited with 1");
}

#[test]
fn alert_for_stopped() {
    assert!(alert_for("stopped", Some("x"), 1.0, None)
        .message
        .starts_with("Stopped"));
}

#[test]
fn alert_text_is_short() {
    let a = alert_for("done", Some(&"word ".repeat(100)), 3.0, None);
    assert!(a.message.chars().count() <= 120, "{}", a.message);
    assert!(a.message.ends_with('…'));
}

#[test]
fn no_request_still_says_done() {
    assert_eq!(alert_for("done", None, 2.0, None).message, "Done (2 s)");
}

// ── fire ─────────────────────────────────────────────────────────────────────

fn alert() -> Alert {
    Alert {
        outcome: "done".into(),
        title: "knaif".into(),
        message: r#"Done (1 s): a "quoted" & <odd> one"#.into(),
    }
}

/// One `run`: the argv and the environment added to the child.
type Call = (Vec<String>, Vec<(String, String)>);

#[derive(Default)]
struct Fake {
    calls: Vec<Call>,
    fail: Vec<&'static str>,
    programs: HashMap<&'static str, &'static str>,
    beeps: usize,
    bells: usize,
    app_id: Option<String>,
}

impl AlertSystem for Fake {
    fn which(&self, program: &str) -> Option<String> {
        self.programs.get(program).map(|p| p.to_string())
    }
    fn run(&mut self, argv: &[String], env: &[(String, String)]) -> std::io::Result<i32> {
        self.calls.push((argv.to_vec(), env.to_vec()));
        if self.fail.iter().any(|f| argv[0].contains(f)) {
            return Err(std::io::Error::other("not there"));
        }
        Ok(0)
    }
    fn beep(&mut self) {
        self.beeps += 1;
    }
    fn app_id(&self) -> Option<String> {
        self.app_id.clone()
    }
    fn bell(&mut self) {
        self.bells += 1;
    }
}

fn env(pairs: &[(&str, &str)]) -> HashMap<String, String> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn scratch(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir()
        .join("knaif-alert-fire")
        .join(format!("{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn the_sink_records_instead_of_alerting_and_appends() {
    let sink = scratch("sink").join("sink.jsonl");
    let e = env(&[("KNAIF_OS_SINK", sink.to_str().unwrap())]);
    let mut fake = Fake::default();
    assert_eq!(
        fire_with(&alert(), &e, Platform::Windows, &mut fake),
        "sink"
    );
    assert_eq!(
        fire_with(&alert(), &e, Platform::Windows, &mut fake),
        "sink"
    );
    assert!(fake.calls.is_empty() && fake.beeps == 0 && fake.bells == 0);
    let text = std::fs::read_to_string(&sink).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines.len(), 2);
    let record: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
    assert_eq!(
        record,
        serde_json::json!({
            "kind": "alert",
            "outcome": "done",
            "title": "knaif",
            "message": r#"Done (1 s): a "quoted" & <odd> one"#,
            "sound": true,
        })
    );
}

#[test]
fn windows_toast_and_beep() {
    let mut fake = Fake::default();
    assert_eq!(
        fire_with(&alert(), &env(&[]), Platform::Windows, &mut fake),
        "windows-toast"
    );
    assert_eq!(fake.beeps, 1);
    let (argv, child) = &fake.calls[0];
    assert!(argv[0].to_lowercase().starts_with("powershell"));
    assert!(argv.contains(&"-NonInteractive".to_string()));
    // The message travels in the environment, never in the script text.
    assert!(!argv.join(" ").contains(&alert().message));
    let child: HashMap<_, _> = child.iter().cloned().collect();
    assert_eq!(child["KNAIF_ALERT_MESSAGE"], alert().message);
    assert_eq!(child["KNAIF_ALERT_TITLE"], "knaif");
    assert!(
        !child.contains_key("KNAIF_ALERT_APP_ID"),
        "borrows PowerShell's until registered"
    );
}

#[test]
fn windows_uses_knaifs_app_id_once_registered() {
    let mut fake = Fake {
        app_id: Some("knaif.cli".into()),
        ..Fake::default()
    };
    fire_with(&alert(), &env(&[]), Platform::Windows, &mut fake);
    let child: HashMap<_, _> = fake.calls[0].1.iter().cloned().collect();
    assert_eq!(child["KNAIF_ALERT_APP_ID"], "knaif.cli");
}

#[test]
fn windows_falls_back_to_the_bell_when_powershell_fails() {
    let mut fake = Fake {
        fail: vec!["powershell"],
        ..Fake::default()
    };
    assert_eq!(
        fire_with(&alert(), &env(&[]), Platform::Windows, &mut fake),
        "bell"
    );
    assert_eq!(fake.bells, 1);
}

#[test]
fn macos_osascript_takes_the_text_as_argv() {
    let mut fake = Fake::default();
    assert_eq!(
        fire_with(&alert(), &env(&[]), Platform::MacOs, &mut fake),
        "macos"
    );
    let argv = &fake.calls[0].0;
    assert_eq!(argv[0], "osascript");
    assert_eq!(
        argv[argv.len() - 2..],
        ["knaif".to_string(), alert().message]
    );
    assert!(!argv[..argv.len() - 2].join(" ").contains(&alert().message));
}

#[test]
fn linux_notify_send_with_a_desktop() {
    let mut fake = Fake {
        programs: HashMap::from([("notify-send", "/usr/bin/notify-send")]),
        ..Fake::default()
    };
    assert_eq!(
        fire_with(
            &alert(),
            &env(&[("DISPLAY", ":0")]),
            Platform::Linux,
            &mut fake
        ),
        "notify-send"
    );
    assert_eq!(
        fake.calls[0].0,
        [
            "/usr/bin/notify-send".to_string(),
            "knaif".into(),
            alert().message
        ]
    );
    assert_eq!(fake.bells, 1, "no canberra-gtk-play: the bell is the sound");
}

#[test]
fn linux_without_a_desktop_rings_the_bell_only() {
    for e in [
        env(&[]),
        env(&[("DISPLAY", ":0"), ("SSH_CONNECTION", "1 2 3 4")]),
    ] {
        let mut fake = Fake {
            programs: HashMap::from([("notify-send", "/usr/bin/notify-send")]),
            ..Fake::default()
        };
        assert_eq!(fire_with(&alert(), &e, Platform::Linux, &mut fake), "bell");
        assert!(fake.calls.is_empty());
        assert_eq!(fake.bells, 1);
    }
}

#[test]
fn a_backend_error_never_escapes() {
    let mut fake = Fake {
        fail: vec!["osascript"],
        ..Fake::default()
    };
    assert_eq!(
        fire_with(&alert(), &env(&[]), Platform::MacOs, &mut fake),
        "bell"
    );
}
