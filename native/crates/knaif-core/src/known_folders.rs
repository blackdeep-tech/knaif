//! Known folders: Downloads, Desktop, Documents, … named in a request instead of a path.
//!
//! "save it in Downloads" means the user's real Downloads folder, not a new `Downloads` folder
//! next to the input. An output path whose first segment is a known-folder name (or `~`) names
//! that folder. The model never writes an absolute path; code resolves the folder. Port of Python
//! `knaif.known_folders`; the L2 contract is `contracts/parity/known_folder_cases.json`.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use regex::Regex;
use serde_json::Value;

use crate::registry::Registry;
use crate::sandbox::resolve_real;

/// The names a request may use, lower case. `home` is also written `~`.
pub const KNOWN_FOLDERS: &[&str] = &[
    "downloads",
    "desktop",
    "documents",
    "pictures",
    "music",
    "videos",
    "home",
];

/// Tests and the eval lanes map every known folder under one directory.
pub const ROOT_ENV: &str = "KNAIF_KNOWN_FOLDERS_ROOT";

/// The folder's own name on disk, where the OS does not say otherwise.
pub fn display_name(name: &str) -> &'static str {
    match name {
        "downloads" => "Downloads",
        "desktop" => "Desktop",
        "documents" => "Documents",
        "pictures" => "Pictures",
        "music" => "Music",
        "videos" => "Videos",
        _ => "~",
    }
}

fn xdg_key(name: &str) -> Option<&'static str> {
    Some(match name {
        "downloads" => "XDG_DOWNLOAD_DIR",
        "desktop" => "XDG_DESKTOP_DIR",
        "documents" => "XDG_DOCUMENTS_DIR",
        "pictures" => "XDG_PICTURES_DIR",
        "music" => "XDG_MUSIC_DIR",
        "videos" => "XDG_VIDEOS_DIR",
        _ => return None,
    })
}

/// Arg keys that name where a step writes. A schema's `path_role: output` counts too.
const OUTPUT_KEYS: &[&str] = &["output", "output_dir"];
const INPUT_KEYS: &[&str] = &["inputs", "input", "files", "src", "path", "base", "append"];

/// The process environment as a map, for [`resolve`] and [`rewrite_outputs`].
pub fn process_env() -> HashMap<String, String> {
    std::env::vars().collect()
}

fn home_dir(env: &HashMap<String, String>) -> PathBuf {
    let key = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    env.get(key)
        .cloned()
        .or_else(|| std::env::var(key).ok())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("~"))
}

fn xdg_dir(name: &str, env: &HashMap<String, String>, home: &Path) -> Option<PathBuf> {
    let config = env
        .get("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".config"));
    let text = std::fs::read_to_string(config.join("user-dirs.dirs")).ok()?;
    let key = xdg_key(name)?;
    for line in text.lines() {
        let line = line.trim();
        if let Some(value) = line.strip_prefix(&format!("{key}=")) {
            let value = value
                .trim()
                .trim_matches('"')
                .replace("$HOME", &home.to_string_lossy());
            return (!value.is_empty()).then(|| PathBuf::from(value));
        }
    }
    None
}

/// The Windows shell's answer (`SHGetKnownFolderPath`), so a moved or OneDrive folder is found.
#[cfg(windows)]
pub fn windows_known_folder(name: &str) -> Option<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::System::Com::CoTaskMemFree;
    use windows_sys::Win32::UI::Shell::{
        FOLDERID_Desktop, FOLDERID_Documents, FOLDERID_Downloads, FOLDERID_Music,
        FOLDERID_Pictures, FOLDERID_Videos, SHGetKnownFolderPath,
    };

    let id = match name {
        "downloads" => &FOLDERID_Downloads,
        "desktop" => &FOLDERID_Desktop,
        "documents" => &FOLDERID_Documents,
        "pictures" => &FOLDERID_Pictures,
        "music" => &FOLDERID_Music,
        "videos" => &FOLDERID_Videos,
        _ => return None,
    };
    let mut out: *mut u16 = std::ptr::null_mut();
    // SAFETY: `id` is a valid GUID, a null token means the current user, and `out` receives a
    // buffer the shell allocates; it is freed below whatever the result.
    let hr = unsafe { SHGetKnownFolderPath(id, 0, std::ptr::null_mut(), &mut out) };
    let path = if hr == 0 && !out.is_null() {
        // SAFETY: on success `out` is a NUL-terminated UTF-16 string.
        let len = (0..).take_while(|&i| unsafe { *out.add(i) } != 0).count();
        let wide = unsafe { std::slice::from_raw_parts(out, len) };
        Some(PathBuf::from(std::ffi::OsString::from_wide(wide)))
    } else {
        None
    };
    if !out.is_null() {
        // SAFETY: allocated by the shell with CoTaskMemAlloc.
        unsafe { CoTaskMemFree(out as *const std::ffi::c_void) };
    }
    path
}

#[cfg(not(windows))]
pub fn windows_known_folder(_name: &str) -> Option<PathBuf> {
    None
}

/// A stand-in for the Windows shell lookup ([`windows_known_folder`]), for tests.
pub type ShellLookup<'a> = &'a dyn Fn(&str) -> Option<PathBuf>;

/// Where the known folder `name` is on this machine. `shell` stands in for the Windows shell
/// lookup in tests; `None` uses the real one on Windows.
pub fn resolve(
    name: &str,
    env: &HashMap<String, String>,
    shell: Option<ShellLookup<'_>>,
) -> PathBuf {
    if let Some(root) = env.get(ROOT_ENV).filter(|r| !r.is_empty()) {
        // Absolute here, against the working folder: a handler joins a relative output to its
        // own root, which would nest the "known folder" inside the user's folder.
        let root = PathBuf::from(root);
        let base = if root.is_absolute() {
            root
        } else {
            std::env::current_dir()
                .map(|cwd| cwd.join(&root))
                .unwrap_or(root)
        };
        return if name == "home" {
            base
        } else {
            base.join(display_name(name))
        };
    }
    let home = home_dir(env);
    if name == "home" {
        return home;
    }
    if cfg!(windows) {
        let found = match shell {
            Some(f) => f(name),
            None => windows_known_folder(name),
        };
        return found.unwrap_or_else(|| home.join(display_name(name)));
    }
    if !cfg!(target_os = "macos") {
        if let Some(found) = xdg_dir(name, env, &home) {
            return found;
        }
    }
    home.join(display_name(name))
}

fn split_parts(value: &str) -> Vec<&str> {
    let mut parts: Vec<&str> = value
        .trim()
        .split(['/', '\\'])
        .filter(|p| !p.is_empty())
        .collect();
    while parts.first() == Some(&".") {
        parts.remove(0);
    }
    parts
}

/// The known folder `value` starts with, or None.
///
/// `Downloads`, `downloads/clip.mp4` and `~/Downloads/clip.mp4` name `downloads`; `~/clip.mp4`
/// and a bare `~` name `home`; `out/Downloads/clip.mp4` names none. A leading `./` is the
/// model's habit, not the user's words, so `./Desktop/x.jpg` names `desktop` too.
pub fn named_folder(value: &str) -> Option<&'static str> {
    let parts = split_parts(value);
    let first = *parts.first()?;
    let known = |s: &str| {
        let lower = s.to_lowercase();
        KNOWN_FOLDERS.iter().copied().find(|k| *k == lower)
    };
    if first == "~" {
        return match parts.get(1).and_then(|p| known(p)) {
            Some(k) if k != "home" => Some(k),
            _ => Some("home"),
        };
    }
    known(first)
}

/// The path parts of `value` after the known-folder segment it starts with.
fn rest<'a>(value: &'a str, folder: &str) -> Vec<&'a str> {
    let mut parts = split_parts(value);
    if parts.first() == Some(&"~") {
        parts.remove(0);
        if folder == "home" {
            return parts;
        }
    }
    if parts.first().is_some_and(|p| p.to_lowercase() == folder) {
        parts.remove(0);
    }
    parts
}

const FOLDER: &str = r"(?:my\s+|the\s+)?(?:downloads|desktop|documents|pictures|music|videos)";

fn into_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(&format!(r"(?i)\b(?:into|onto)\s+{FOLDER}")).unwrap())
}

fn put_verb_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)\b(?:save|saved|put|store|place|output|export|write|copy|move|send|drop|keep|leave)\b",
        )
        .unwrap()
    })
}

fn prep_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(&format!(r"(?i)\b(?:in|to|on)\s+{FOLDER}")).unwrap())
}

/// Python's `(?![\w.])` after a match: the folder name ends a word and is not a file name.
fn ends_cleanly(text: &str, end: usize) -> bool {
    !text[end..]
        .chars()
        .next()
        .is_some_and(|c| c.is_alphanumeric() || c == '_' || c == '.')
}

/// Whether `re` matches `text` at or after `from`, with a clean end. Every start is tried, as
/// Python's backtracking search would, so a failed candidate cannot hide a later one.
fn found_from(re: &Regex, text: &str, from: usize) -> bool {
    let mut start = from;
    while start <= text.len() {
        let Some(m) = re.find_at(text, start) else {
            return false;
        };
        if ends_cleanly(text, m.end()) {
            return true;
        }
        start = m.start() + text[m.start()..].chars().next().map_or(1, char::len_utf8);
    }
    false
}

/// True when `text` names a known folder as where something goes ("save it in Downloads").
///
/// "into"/"onto" always say so ("into my Music folder"); "in"/"to"/"on" only after a verb of
/// putting ("save it in Downloads", "put it on the Desktop"), because "everything in my Videos
/// folder" says where files are, not where they go. Gates the prompt examples that write into a
/// known folder (`prompt::select_examples`).
pub fn names_known_folder(text: &str) -> bool {
    if found_from(into_re(), text, 0) {
        return true;
    }
    // Python's `.*?` stops at a line end, so the verb and the folder share a line.
    text.split('\n').any(|line| {
        put_verb_re()
            .find(line)
            .is_some_and(|verb| found_from(prep_re(), line, verb.end()))
    })
}

/// What [`rewrite_outputs`] made of a plan.
#[derive(Debug, Clone, PartialEq)]
pub enum Rewrite {
    /// The plan, with every known-folder output pointed into its folder.
    Plan(Vec<Value>),
    /// A folder of that name also exists next to the input: the question to ask instead.
    Ask(String),
    /// The folder is outside `--sandbox`.
    Refused(String),
}

fn posix(p: &Path) -> String {
    p.to_string_lossy().replace('\\', "/")
}

fn within(path: &Path, base: &Path, cwd: &Path) -> bool {
    resolve_real(path, cwd).starts_with(resolve_real(base, cwd))
}

fn output_keys(registry: &Registry, tool: &str) -> Vec<String> {
    let mut keys: Vec<String> = OUTPUT_KEYS.iter().map(|k| (*k).to_string()).collect();
    if let Some(def) = registry.get(tool) {
        for (arg, schema) in &def.arg_schemas {
            if schema.path_role.as_deref() == Some("output") && !keys.contains(arg) {
                keys.push(arg.clone());
            }
        }
    }
    keys
}

fn input_dirs(args: &serde_json::Map<String, Value>, cwd: &Path) -> Vec<PathBuf> {
    let mut dirs = vec![cwd.to_path_buf()];
    for key in INPUT_KEYS {
        let items: Vec<&Value> = match args.get(*key) {
            Some(Value::Array(a)) => a.iter().collect(),
            Some(v) => vec![v],
            None => vec![],
        };
        for item in items.into_iter().filter_map(Value::as_str) {
            if item.is_empty() || item.starts_with('$') || item.contains("://") {
                continue;
            }
            let parent = Path::new(item).parent().unwrap_or(Path::new(""));
            dirs.push(if parent.is_absolute() {
                parent.to_path_buf()
            } else {
                cwd.join(parent)
            });
        }
    }
    dirs
}

/// `plan` with every output arg that names a known folder pointed into that folder.
///
/// `Downloads/clip.mp4` becomes `<the user's Downloads>/clip.mp4`; inputs, `$var` references
/// and paths that only contain the name further in (`out/Downloads/x`) stay. A folder of that
/// name also in `cwd` or next to an input makes it a question ([`Rewrite::Ask`]); with a
/// `sandbox`, a folder outside it is refused ([`Rewrite::Refused`]).
pub fn rewrite_outputs(
    plan: &[Value],
    registry: &Registry,
    cwd: &Path,
    sandbox: Option<&Path>,
    env: &HashMap<String, String>,
) -> Rewrite {
    let mut out: Vec<Value> = plan.to_vec();
    for step in out.iter_mut() {
        let tool = step
            .get("tool")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let Some(args) = step.get_mut("args").and_then(Value::as_object_mut) else {
            continue;
        };
        for key in output_keys(registry, &tool) {
            let Some(value) = args.get(&key).and_then(Value::as_str).map(str::to_string) else {
                continue;
            };
            if value.starts_with('$') {
                continue;
            }
            let Some(folder) = named_folder(&value) else {
                continue;
            };
            let base = resolve(folder, env, None);
            let target = rest(&value, folder)
                .into_iter()
                .fold(base.clone(), |p, part| p.join(part));
            let explicit_home = value.trim().starts_with('~');
            if folder != "home" && !explicit_home {
                let spelled = split_parts(&value)[0].to_string();
                let mut names = vec![spelled.clone()];
                if display_name(folder) != spelled {
                    names.push(display_name(folder).to_string());
                }
                let mut seen: HashSet<PathBuf> = HashSet::new();
                for dir in input_dirs(args, cwd) {
                    for name in &names {
                        let local = dir.join(name);
                        if !seen.insert(local.clone()) {
                            continue;
                        }
                        if local.is_dir() && !within(&local, &base, cwd) {
                            return Rewrite::Ask(format!(
                                "There is a {name} folder at {}, and your {} folder is {}. \
                                 Which one should I save into?",
                                posix(&local),
                                display_name(folder),
                                posix(&base)
                            ));
                        }
                    }
                }
            }
            if let Some(sb) = sandbox {
                if !within(&target, sb, cwd) {
                    return Rewrite::Refused(format!(
                        "Path '{}' is outside the sandbox '{}'.",
                        posix(&target),
                        posix(sb)
                    ));
                }
            }
            args.insert(key, Value::String(posix(&target)));
        }
    }
    Rewrite::Plan(out)
}
