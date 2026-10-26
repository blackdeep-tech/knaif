//! NL clarify gate — native port of Python `knaif.nl_clarify_gate.nl_clarify_gate`, as both CLIs
//! run it: with no host-supplied file listing (`injected_files=None`).
//!
//! Runs after stem resolution and asks instead of acting when the plan uses something the user
//! never actually said:
//!
//! 1. **Grounded args** (a tool's `grounded_args`, e.g. a PDF password) must appear in the
//!    utterance, case-insensitively; otherwise "What {arg} should I use?". Checked even for a
//!    batch request.
//! 2. Unless the utterance is a batch request ("all", "every", "folder", ...), every **input
//!    token** (keys `inputs input files src dst path base append`, in that order) must be named
//!    concretely: written as a filename in the utterance, or its stem written as an identifier
//!    carrying `_`, `-` or a digit. Globs, `$chain` references, paths with `/` or `\` and the
//!    outputs of earlier steps are exempt. Otherwise "Which {token} did you mean?".
//!
//! Native had no port until 2026-09-28: the R5c L3 run found Python asking "Which mov did you
//! mean?" where native ran and failed with "input not found: mov". The contract both runtimes
//! are held to is `contracts/parity/nl_clarify_gate_cases.json`.

use std::collections::HashSet;
use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Value};

use crate::registry::Registry;

/// Python `_INPUT_ARG_ORDER` (the planner's `_PATH_ARG_KEYS`, in a fixed order).
const INPUT_KEYS: [&str; 8] = [
    "inputs", "input", "files", "src", "dst", "path", "base", "append",
];
const OUTPUT_KEYS: [&str; 2] = ["output", "outputs"];
/// Python `_BATCH_SIGNALS`.
const BATCH_SIGNALS: [&str; 25] = [
    "all",
    "every",
    "each",
    "batch",
    "bulk",
    "folder",
    "directory",
    "alle",
    "todos",
    "toutes",
    "lote",
    "stapel",
    "lot",
    "jede",
    "jeder",
    "jedes",
    "jeden",
    "jedem",
    "ordner",
    "verzeichnis",
    "cada",
    "carpeta",
    "chaque",
    "dossier",
    "tous",
];
/// Python `_BATCH_SIGNALS_UNICODE`: whole Unicode words, so English keeps its `[a-z]+` runs.
const BATCH_SIGNALS_UNICODE: [&str; 10] = [
    "всички",
    "всичките",
    "всеки",
    "всяко",
    "всяка",
    "пакетно",
    "папка",
    "папката",
    "директория",
    "директорията",
];
/// Python `_BATCH_SIGNALS_CJK`: substrings.
const BATCH_SIGNALS_CJK: [&str; 7] = ["所有", "每个", "每個", "批量", "文件夹", "資料夾", "全部"];
/// Python `_PLURAL_FILE_WORDS`: a request for many files without a batch word lets a glob
/// through, and nothing else.
const PLURAL_FILE_WORDS: [&str; 25] = [
    "videos",
    "clips",
    "files",
    "movies",
    "recordings",
    "songs",
    "tracks",
    "images",
    "photos",
    "pictures",
    "documents",
    "pdfs",
    "mp4s",
    "movs",
    "mkvs",
    "mp3s",
    "wavs",
    "gifs",
    "jpgs",
    "pngs",
    "dateien",
    "bilder",
    "dokumente",
    "archivos",
    "fichiers",
];
/// Python `_PLURAL_FILE_WORDS_UNICODE`.
const PLURAL_FILE_WORDS_UNICODE: [&str; 12] = [
    "vídeos",
    "vidéos",
    "видеа",
    "видеата",
    "файлове",
    "файловете",
    "клипове",
    "клиповете",
    "снимки",
    "снимките",
    "документи",
    "документите",
];
/// Python `_PLURAL_FILE_WORDS_CJK`.
const PLURAL_FILE_WORDS_CJK: [&str; 2] = ["这些", "這些"];
/// Python `input_refs.GLOB_CHARS`.
const GLOB_CHARS: [char; 3] = ['*', '?', '['];

/// Python `input_refs.FILENAME_RE` (`re.ASCII`): a stem, a dot, a 2–4 char tail.
fn filename_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?-u)\b[\w\-]+\.[A-Za-z0-9]{2,4}\b").unwrap())
}

/// Python's `\w` for a `str` pattern: alphanumeric per `str.isalnum`, or `_`. Not the `regex`
/// crate's Unicode `\w`, which also admits combining marks and not every numeric: `clip_4k` + a
/// combining accent is a word end to Python and not to the crate, and a superscript `²` the
/// reverse (Codex audit, 2026-09-28).
///
/// By general category, not `char::is_alphanumeric`: that follows the Alphabetic property, which
/// includes the Other_Alphabetic combining marks (U+0345, Indic vowel signs). Python counts a
/// character only if it is a letter or a number, so a mark ends a word there (1.2.1 B7).
fn py_is_word(c: char) -> bool {
    use unicode_properties::{GeneralCategoryGroup, UnicodeGeneralCategory};
    c == '_'
        || matches!(
            c.general_category_group(),
            GeneralCategoryGroup::Letter | GeneralCategoryGroup::Number
        )
}

/// Python's `str.isspace` (and `\s` for a `str` pattern): Unicode whitespace plus the four
/// information separators U+001C–U+001F, which Rust's `char::is_whitespace` leaves out.
fn py_is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python `str.strip()`.
fn py_strip(s: &str) -> &str {
    s.trim_matches(py_is_space)
}

/// The words `re.findall(r"\b[a-zA-Z0-9][a-zA-Z0-9_\-]*\b", utterance)` returns, with Python's
/// word boundary: a match starts at an ASCII alphanumeric not preceded by a word character, runs
/// greedily over `[A-Za-z0-9_-]`, and backs off to the last position that is a boundary.
fn stem_words(utterance: &str) -> Vec<String> {
    let chars: Vec<char> = utterance.chars().collect();
    let is_word_at = |i: usize| i < chars.len() && py_is_word(chars[i]);
    let boundary = |i: usize| (i > 0 && py_is_word(chars[i - 1])) != is_word_at(i);
    let in_class = |c: char| c.is_ascii_alphanumeric() || c == '_' || c == '-';
    let mut words = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if chars[i].is_ascii_alphanumeric() && boundary(i) {
            let mut end = i + 1;
            while end < chars.len() && in_class(chars[end]) {
                end += 1;
            }
            if let Some(e) = (i + 1..=end).rev().find(|&e| boundary(e)) {
                words.push(chars[i..e].iter().collect());
                i = e;
                continue;
            }
        }
        i += 1;
    }
    words
}

/// The gate. Returns *payload* unchanged, or with its plan replaced by one clarify step.
pub fn nl_clarify_gate(mut payload: Value, utterance: &str, registry: &Registry) -> Value {
    let Some(steps) = payload.get("plan").and_then(Value::as_array) else {
        return payload;
    };
    if let Some(question) = question_for(steps, utterance, registry) {
        payload["plan"] = json!([{ "tool": "clarify", "args": { "question": question } }]);
    }
    payload
}

fn question_for(steps: &[Value], utterance: &str, registry: &Registry) -> Option<String> {
    // 1. Grounded args, batch or not: a batch request still must not carry an invented password.
    for step in steps {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let Some(def) = registry.get(tool) else {
            continue;
        };
        let args = step.get("args").and_then(Value::as_object);
        for arg in &def.grounded_args {
            let Some(value) = args.and_then(|a| a.get(arg)) else {
                continue;
            };
            if !is_value_grounded(value, utterance) {
                return Some(format!("What {arg} should I use?"));
            }
        }
    }

    // 2. Input tokens, unless the user asked for a batch.
    if is_batch_utterance(utterance) {
        return None;
    }
    let inline = inline_filenames(utterance);
    let mut plan_outputs: HashSet<String> = HashSet::new();
    for step in steps {
        let tool = step.get("tool").and_then(Value::as_str).unwrap_or("");
        let args = step.get("args").and_then(Value::as_object);
        for key in INPUT_KEYS {
            let Some(value) = args.and_then(|a| a.get(key)) else {
                continue;
            };
            for token in strings(value) {
                if token.contains(GLOB_CHARS) {
                    // A glob is many files: fine when the request asks for many, else ask.
                    if asks_for_many(utterance, token) {
                        continue;
                    }
                    return Some(glob_question(token));
                }
                if token.contains('/') || token.contains('\\') {
                    continue; // a path, not a media-file reference
                }
                if plan_outputs.contains(token) {
                    continue; // produced by an earlier step
                }
                if !is_concretely_specified(token, utterance, &inline) {
                    return Some(clarify_question(token, tool));
                }
            }
        }
        for key in OUTPUT_KEYS {
            if let Some(value) = args.and_then(|a| a.get(key)) {
                plan_outputs.extend(strings(value).into_iter().map(str::to_string));
            }
        }
    }
    None
}

/// A string, or the string items of a list (Python `_input_tokens` / `_output_tokens`).
fn strings(value: &Value) -> Vec<&str> {
    match value {
        Value::String(s) => vec![s.as_str()],
        Value::Array(items) => items.iter().filter_map(Value::as_str).collect(),
        _ => Vec::new(),
    }
}

/// Python `_is_value_grounded`: a non-empty string that appears in the utterance.
fn is_value_grounded(value: &Value, utterance: &str) -> bool {
    let Some(s) = value.as_str() else {
        return false;
    };
    let s = py_strip(s);
    !s.is_empty() && utterance.to_lowercase().contains(&s.to_lowercase())
}

/// Python `_is_batch_utterance`: an ASCII signal as a `[a-z]+` run of the lowercased
/// utterance, a non-ASCII one as a whole Unicode word, a Chinese one as a substring.
fn is_batch_utterance(utterance: &str) -> bool {
    let low = utterance.to_lowercase();
    low.split(|c: char| !c.is_ascii_lowercase())
        .any(|w| BATCH_SIGNALS.contains(&w))
        || unicode_words(&low).any(|w| BATCH_SIGNALS_UNICODE.contains(&w))
        || BATCH_SIGNALS_CJK.iter().any(|w| low.contains(w))
}

/// Python `re.findall(r"\w+", text)`: runs of letters, digits and `_`.
fn unicode_words(text: &str) -> impl Iterator<Item = &str> {
    text.split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .filter(|w| !w.is_empty())
}

/// Python `_asks_for_many`: a plural file word, or the glob written in the request itself.
fn asks_for_many(utterance: &str, glob: &str) -> bool {
    let low = utterance.to_lowercase();
    low.contains(&glob.to_lowercase())
        || low
            .split(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit()))
            .any(|w| PLURAL_FILE_WORDS.contains(&w))
        || unicode_words(&low).any(|w| PLURAL_FILE_WORDS_UNICODE.contains(&w))
        || PLURAL_FILE_WORDS_CJK.iter().any(|w| low.contains(w))
}

fn url_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)https?://\S+").unwrap())
}

fn named_file_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    // No `\b` around it: a CJK character is a word character, so "把report.pdf拆分" has none.
    RE.get_or_init(|| Regex::new(r"(?i)[\w-]+\.([a-z0-9]{2,5})(?:[^a-z0-9]|$)").unwrap())
}

/// A name the request gives an output ("call it preview.gif", "save them as out.mp4", "als",
/// "comme", "като", "保存为"): not a file the request is about (2026-10-23).
/// Python `knaif.nl_clarify_gate._OUTPUT_NAME_RE`.
fn output_name_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(concat!(
            r"(?i)(?:\b(?:call(?:ed)?\s+(?:it|them)|name[ds]?\s+(?:it|them)|named|save(?:d)?\s+(?:it\s+|them\s+)?as",
            r"|as|into|als|nach|namens|como|llamad[oa]s?|comme|vers|appel[ée]e?s?|като)",
            r"|(?:命名为|保存为|另存为|存为))\s*[\w-]+\.[a-z0-9]{2,5}\b"
        ))
        .unwrap()
    })
}

/// A name with an extension holding a letter (`clip.mp4`, `x.3gp`); a codec or a number
/// (`H.264`, `1.25`) names no file. Python `knaif.nl_clarify_gate._names_a_file`.
fn names_a_file(text: &str) -> bool {
    named_file_re()
        .captures_iter(text)
        .any(|c| c[1].chars().any(|ch| ch.is_ascii_alphabetic()))
}

/// Whether `utterance` asks for many files: a batch word, a plural file word, or a glob written
/// in it. The clarify gate's own test, shared with the prompt: the per-file batch rule and its
/// examples join only such a prompt. What names a place is left out first: a URL, and, when the
/// request names a file, a known folder after in/into/to/on/onto/from (where that file goes);
/// with no file named the folder may be the batch. A request that names a file is about that
/// file, so a batch word alone ("every page of sample.pdf") is not enough there; a glob or a
/// plural file word still is. Python `knaif.nl_clarify_gate.asks_for_many_files`;
/// `contracts/parity/many_files_cases.json`.
pub fn asks_for_many_files(utterance: &str) -> bool {
    let text = url_re().replace_all(utterance, " ").into_owned();
    // A URL names a file too; a name given to an output does not.
    if names_a_file(&output_name_re().replace_all(utterance, " ")) {
        return asks_for_many(&crate::known_folders::without_folder_places(&text), "*");
    }
    is_batch_utterance(&text) || asks_for_many(&text, "*")
}

/// Number words of the corpora's languages (Python `_COUNT_WORDS`). "both" and its
/// translations are two; German "ein" is left out, as it is also the article.
fn count_word(word: &str) -> Option<usize> {
    const LISTS: [(&str, usize); 5] = [
        ("one two three four five six seven eight nine ten", 1),
        ("zwei drei vier fünf sechs sieben acht neun zehn", 2),
        ("dos tres cuatro cinco seis siete ocho nueve diez", 2),
        ("deux trois quatre cinq six sept huit neuf dix", 2),
        ("три четири пет шест седем осем девет десет", 3),
    ];
    for (list, first) in LISTS {
        if let Some(i) = list.split(' ').position(|w| w == word) {
            return Some(first + i);
        }
    }
    match word {
        "both" | "beide" | "beiden" | "ambos" | "ambas" | "два" | "две" | "двата" | "двете" => {
            Some(2)
        }
        "трите" => Some(3),
        _ => None,
    }
}

/// A count in Chinese sits right before the file word, without spaces ("两个视频").
fn cjk_count_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"([两兩二三四五六七八九十]|\d+)\s*[个個部支份张張]?\s*(?:视频|視頻|影片|文件|檔案|档案|图片|圖片|片段|剪辑|剪輯)",
        )
        .unwrap()
    })
}

/// The number of files `utterance` asks for. Python `knaif.nl_clarify_gate._requested_count`.
fn requested_count(utterance: &str) -> Option<usize> {
    let low = url_re()
        .replace_all(&utterance.to_lowercase(), " ")
        .into_owned();
    let words: Vec<&str> = word_re().find_iter(&low).map(|m| m.as_str()).collect();
    let is_number = |w: &str| w.chars().all(|c| c.is_ascii_digit()) || count_word(w).is_some();
    for (i, word) in words.iter().enumerate() {
        let n = if word.chars().all(|c| c.is_ascii_digit()) {
            word.parse().ok()
        } else {
            count_word(word)
        };
        let Some(n) = n else { continue };
        for later in words.iter().skip(i + 1).take(3) {
            if PLURAL_FILE_WORDS.contains(later) || PLURAL_FILE_WORDS_UNICODE.contains(later) {
                return Some(n);
            }
            if is_number(later) {
                break;
            }
        }
    }
    // A digit that follows a letter is part of a name ("mp4文件"), not a count; the regex crate
    // has no look-behind, so the character before the match is checked here.
    let caps = cjk_count_re().captures_iter(&low).find(|c| {
        let start = c.get(1).map_or(0, |m| m.start());
        !c[1].starts_with(|ch: char| ch.is_ascii_digit())
            || !low[..start]
                .chars()
                .next_back()
                .is_some_and(|ch| ch.is_ascii_alphanumeric())
    })?;
    let raw = &caps[1];
    raw.parse().ok().or_else(|| {
        "两兩二三四五六七八九十"
            .chars()
            .zip([2, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10])
            .find(|(c, _)| raw.starts_with(*c))
            .map(|(_, n)| n)
    })
}

fn word_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\w+").unwrap())
}

/// The question to ask when `utterance` asks for a number of files the plan does not touch:
/// the plan's folder and glob inputs, listed as the per-file batch lists them, plus any
/// file named beside them; an earlier step's output is not counted again. Only a plan with a
/// folder or a glob is checked. Python `knaif.nl_clarify_gate.count_question`;
/// `contracts/parity/count_cases.json`.
pub fn count_question(
    utterance: &str,
    plan: &[Value],
    base: &std::path::Path,
    file_kinds: &crate::clarify_gate::FileKinds,
) -> Option<String> {
    let wanted = requested_count(utterance)?;
    let mut touched: Vec<(String, String)> = Vec::new();
    let mut outputs: HashSet<String> = HashSet::new();
    let mut listed = false;
    for step in plan {
        let Some(args) = step.get("args").and_then(Value::as_object) else {
            continue;
        };
        for key in crate::batch_chain::INPUT_KEYS {
            let entries: Vec<&str> = match args.get(key) {
                Some(Value::String(s)) => vec![s.as_str()],
                Some(Value::Array(items)) => items.iter().filter_map(Value::as_str).collect(),
                _ => continue,
            };
            for entry in entries {
                if entry.starts_with('$') || entry.contains("://") {
                    continue;
                }
                if outputs.contains(&entry.to_lowercase()) {
                    continue;
                }
                let found =
                    crate::batch_chain::expand_inputs(&[entry.to_string()], base, file_kinds);
                let files = match found.get(entry) {
                    Some(files) => {
                        listed = true;
                        files.clone()
                    }
                    None => vec![entry.to_string()],
                };
                for f in files {
                    let key = f.to_lowercase();
                    if !touched.iter().any(|(k, _)| *k == key) {
                        touched.push((key, f));
                    }
                }
            }
        }
        if let Some(out) = args.get("output").and_then(Value::as_str) {
            outputs.insert(out.to_lowercase());
        }
    }
    let have = touched.len();
    if !listed || have == wanted || have == 0 {
        return None;
    }
    let kinds: HashSet<Option<&String>> = touched
        .iter()
        .map(|(_, f)| {
            let ext = f.rsplit_once('.').map_or("", |(_, e)| e).to_lowercase();
            file_kinds.get(&ext)
        })
        .collect();
    let noun = match kinds.iter().next() {
        Some(Some(kind)) if kinds.len() == 1 => match kind.as_str() {
            "video" => "videos",
            "audio" => "audio files",
            "document" => "documents",
            "image" => "images",
            "text" => "text files",
            _ => "files",
        },
        _ => "files",
    };
    Some(if have > wanted {
        format!("Which {wanted} of the {have} {noun}? Name them.")
    } else {
        format!("I found {have} {noun} here, not {wanted}. Which ones do you mean?")
    })
}

/// Python `_glob_question`: what to ask about a glob the request never asked for.
fn glob_question(glob: &str) -> String {
    let normalized = glob.replace('\\', "/");
    let name = normalized.rsplit('/').next().unwrap_or("");
    // Python `PurePath.suffix`: from the last dot, unless the name starts or ends with it.
    let ext = match name.rfind('.') {
        Some(i) if i > 0 && i + 1 < name.len() => &name[i..],
        _ => "",
    };
    let what = if !ext.is_empty() && !ext.contains(GLOB_CHARS) {
        format!("{} files", ext.to_lowercase())
    } else {
        "files".to_string()
    };
    format!("Did you mean all the {what} here? Name the file, or say \"all\" to use every one.")
}

/// Python `input_refs.parse_inline_filenames`.
fn inline_filenames(utterance: &str) -> HashSet<String> {
    filename_re()
        .find_iter(utterance)
        .map(|m| m.as_str())
        .filter(|name| {
            let ext = name.rsplit('.').next().unwrap_or("");
            (2..=4).contains(&ext.chars().count()) && ext.chars().any(char::is_alphabetic)
        })
        .map(str::to_string)
        .collect()
}

/// Python `_is_concretely_specified` with no injected listing.
fn is_concretely_specified(token: &str, utterance: &str, inline: &HashSet<String>) -> bool {
    if token.starts_with('$') {
        return true; // a chain reference
    }
    if inline.contains(token) {
        return true; // the user wrote it out
    }
    has_matching_stem_in_utterance(token, utterance)
}

/// Python `_has_matching_stem_in_utterance`: an identifier in the utterance that carries a
/// structural marker and equals the token's `Path(...).stem`, ignoring case.
fn has_matching_stem_in_utterance(token: &str, utterance: &str) -> bool {
    let stem = path_stem(token).to_lowercase();
    stem_words(utterance).iter().any(|word| {
        word.chars()
            .any(|c| c == '_' || c == '-' || c.is_ascii_digit())
            && word.to_lowercase() == stem
    })
}

/// Python `pathlib.Path(token).stem` (3.14, the pinned interpreter) for a token with no
/// separator: `.` is empty, `..` stays whole, otherwise everything before the last dot unless
/// that dot leads. On Windows a `WindowsPath` also drops a drive prefix (`C:clip.mp4` names
/// `clip.mp4`), so this does too; a `PosixPath` keeps it.
fn path_stem(token: &str) -> &str {
    let token = if cfg!(windows) {
        strip_drive(token)
    } else {
        token
    };
    match token {
        "." => "",
        ".." => "..",
        _ => match token.rfind('.') {
            Some(i) if i > 0 => &token[..i],
            _ => token,
        },
    }
}

/// `C:` from the front of a Windows drive-relative name.
fn strip_drive(token: &str) -> &str {
    let b = token.as_bytes();
    if b.len() >= 2 && b[0].is_ascii_alphabetic() && b[1] == b':' {
        &token[2..]
    } else {
        token
    }
}

/// Python `_clarify_question`: the token without a leading article, or the tool as a fallback.
fn clarify_question(token: &str, tool: &str) -> String {
    let body = py_strip(strip_article(token));
    if body.is_empty() {
        format!("Which file would you like to {}?", tool.replace('_', " "))
    } else {
        format!("Which {body} did you mean?")
    }
}

/// Python `_ARTICLE_RE = ^\s*(the|a|an)\s+` (case-insensitive), removed once.
fn strip_article(token: &str) -> &str {
    let rest = token.trim_start_matches(py_is_space);
    for article in ["the", "a", "an"] {
        let Some(head) = rest.get(..article.len()) else {
            continue;
        };
        if !head.eq_ignore_ascii_case(article) {
            continue;
        }
        let after = &rest[article.len()..];
        if after.starts_with(py_is_space) {
            return after.trim_start_matches(py_is_space);
        }
    }
    token
}

#[cfg(test)]
mod tests {
    use super::*;

    // B7 (1.2.1): Rust's `is_alphanumeric` follows the Unicode Alphabetic property, which takes in
    // Other_Alphabetic combining marks; Python's `str.isalnum` takes letters and numbers by general
    // category, so a mark is never a word character there.
    #[test]
    fn a_combining_mark_is_never_a_word_character() {
        for mark in ['\u{0345}', '\u{093E}', '\u{05B0}', '\u{0301}'] {
            assert!(!py_is_word(mark), "U+{:04X}", mark as u32);
        }
        for word in ['a', 'é', 'Ж', '中', '٣', '²', '_'] {
            assert!(py_is_word(word), "U+{:04X}", word as u32);
        }
    }

    #[test]
    fn a_stem_before_an_other_alphabetic_mark_is_recognized() {
        assert!(stem_words("trim clip_4k\u{0345} to 5 seconds").contains(&"clip_4k".to_string()));
    }

    #[test]
    fn path_stem_matches_python() {
        for (token, stem) in [
            ("clip.", "clip"),
            (".mp4", ".mp4"),
            ("a..b", "a."),
            ("a.b.c", "a.b"),
            ("silent clip", "silent clip"),
            ("", ""),
            ("4K_clip.mp4", "4K_clip"),
            ("x .mp4", "x "),
            ("..", ".."),
            (".", ""),
        ] {
            assert_eq!(path_stem(token), stem, "{token:?}");
        }
    }

    #[test]
    fn stem_words_follow_python_findall() {
        assert_eq!(stem_words("trim clip_4k now"), ["trim", "clip_4k", "now"]);
        assert_eq!(
            stem_words("trim clip_4k\u{301} now"),
            ["trim", "clip_4k", "now"]
        );
        assert_eq!(stem_words("trim \u{b2}clip_4k"), ["trim"]);
        assert_eq!(stem_words("a-b- c_"), ["a-b", "c_"]);
        assert_eq!(stem_words("\u{e9}clip_4k x"), ["x"]);
    }

    #[test]
    #[cfg(windows)]
    fn a_windows_drive_is_not_part_of_the_stem() {
        assert_eq!(path_stem("C:clip_4k.mp4"), "clip_4k");
    }

    #[test]
    fn article_is_removed_once_and_only_before_whitespace() {
        assert_eq!(strip_article("the silent clip"), "silent clip");
        assert_eq!(strip_article("  An intro"), "intro");
        assert_eq!(strip_article("another clip"), "another clip");
        assert_eq!(strip_article("the the clip"), "the clip");
        assert_eq!(strip_article("thereafter"), "thereafter");
    }

    #[test]
    fn batch_words_are_whole_words() {
        assert!(is_batch_utterance("compress ALL the videos"));
        assert!(!is_batch_utterance("compress the tallest video"));
        assert!(!is_batch_utterance("call me later"));
    }

    #[test]
    fn inline_filenames_need_a_lettered_tail() {
        let found = inline_filenames("convert clip.mp4 and H.264 then v1.2 and song.MP3");
        assert!(found.contains("clip.mp4") && found.contains("song.MP3"));
        assert!(!found.iter().any(|f| f == "H.264"));
    }
}
