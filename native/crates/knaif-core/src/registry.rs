//! Tool registry — `ToolDef` / `ArgSchema` + the YAML loader.
//!
//! Rust port of the Python `knaif.registry` (ToolDef dataclass, `load_registry`). Same
//! shape and same skip/guard behavior so both runtimes accept the identical `tools.yaml` /
//! `core_tools.yaml`. Retrieval (`retrieve_tools`) is ported in a later slice.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;
use serde_yaml::Value;

/// A keyword claimed by more than this many tools is too generic to discriminate — a
/// curation mistake rejected at load time. Mirrors `_MAX_KEYWORD_TOOLS`.
const MAX_KEYWORD_TOOLS: usize = 4;

fn default_type() -> String {
    "string".to_string()
}

fn default_safety() -> String {
    "safe".to_string()
}

/// `batch_output:` as an extension without its leading dot; empty is none (Python's
/// `str(...).lstrip(".")` of a truthy value).
fn batch_output_ext<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    let raw: Option<Value> = Option::deserialize(d)?;
    Ok(match raw {
        None | Some(Value::Null) | Some(Value::Bool(false)) => None,
        Some(Value::String(s)) if s.is_empty() => None,
        Some(Value::String(s)) => Some(s.trim_start_matches('.').to_string()),
        Some(Value::Number(n)) => Some(n.to_string()),
        Some(_) => None,
    })
}

/// Typed metadata for a single tool argument (mirrors Python `ArgSchema`).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct ArgSchema {
    #[serde(rename = "type", default = "default_type")]
    pub arg_type: String,
    #[serde(default)]
    pub items: Option<String>,
    #[serde(rename = "enum", default)]
    pub enum_values: Option<Vec<String>>,
    #[serde(default)]
    pub aliases: Option<BTreeMap<String, String>>,
    #[serde(default)]
    pub min: Option<f64>,
    #[serde(default)]
    pub max: Option<f64>,
    #[serde(default)]
    pub path_role: Option<String>,
    #[serde(default)]
    pub help: Option<String>,
}

impl Default for ArgSchema {
    fn default() -> Self {
        Self {
            arg_type: default_type(),
            items: None,
            enum_values: None,
            aliases: None,
            min: None,
            max: None,
            path_role: None,
            help: None,
        }
    }
}

/// What a tool writes when a step names no output: a kind from the skill's `file_kinds`
/// (`audio`), or `{arg: <name>}` when the kind follows an argument naming an extension
/// (`convert_document`'s `to_format`). Anything else fails to load, as in Python.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, serde::Serialize)]
#[serde(untagged)]
pub enum Produces {
    Kind(String),
    Arg { arg: String },
}

/// One tool's definition (mirrors Python `ToolDef`). `name` comes from the YAML map key.
#[derive(Debug, Clone, Deserialize)]
pub struct ToolDef {
    #[serde(skip)]
    pub name: String,
    /// Position in the source `tools.yaml` mapping, preserved because [`Registry`] is a `BTreeMap`
    /// and would otherwise impose alphabetical order.
    ///
    /// It is **not** the order the fine-tune saw: training prompts are built
    /// `retrieve_tools(utterance)` → `agent.build_prompt(registry_override=…)`
    /// (`python/training/build_dataset.py`), so the model was trained on tools in *relevance*
    /// order. This field is what the whole-registry [`crate::build_prompt`] sorts by, which
    /// reproduces what the reference yields when it iterates a full registry;
    /// [`crate::build_prompt_ordered`] is the in-distribution path and keeps retrieval's ranking.
    #[serde(skip)]
    pub order: usize,
    pub description: String,
    #[serde(default)]
    pub required_args: Vec<String>,
    #[serde(default)]
    pub optional_args: Vec<String>,
    #[serde(default)]
    pub any_of_args: Vec<String>,
    #[serde(default)]
    pub grounded_args: Vec<String>,
    #[serde(default = "default_safety")]
    pub safety_category: String,
    #[serde(default)]
    pub keywords: Vec<String>,
    /// Whole words or phrases that bring this tool into a prompt. A tool with triggers is never
    /// ranked: retrieval adds it only when one is in the request (`retrieve_tools`), so a request
    /// without one gets the same tools as if the tool did not exist (core's `alert_when_done`).
    /// Mirrors Python `ToolDef.triggers`.
    #[serde(default)]
    pub triggers: Vec<String>,
    #[serde(default)]
    pub readonly: bool,
    /// Several inputs become ONE output (concat), not one output each (a batch). Read by chain
    /// routing (`scratch::route_intermediates_to_scratch`). Mirrors Python `ToolDef.joins_inputs`.
    #[serde(default)]
    pub joins_inputs: bool,
    /// The kind of file the tool writes when a step names no output (`tools.yaml` `produces:`).
    /// Read by chain routing's keep rule (`scratch::route_intermediates_to_scratch`). Mirrors
    /// Python `ToolDef.produces`.
    #[serde(default)]
    pub produces: Option<Produces>,
    /// The tool's output is a NEW thing made from its input (a piece, a page range, a join), not
    /// the input improved (`tools.yaml` `derives: true`). Read by the keep rule
    /// (`scratch::route_intermediates_to_scratch`). Mirrors Python `ToolDef.derives`.
    #[serde(default)]
    pub derives: bool,
    /// The extension a tool that only reads (extract_text) saves its result as when it runs over
    /// many files (`tools.yaml` `batch_output:`, a leading dot dropped): a single-file step
    /// prints, a lifted one writes `<file's name>.<ext>`. Validation accepts `output` for it, which
    /// the prompt never lists. Mirrors Python `ToolDef.batch_output`.
    #[serde(default, deserialize_with = "batch_output_ext")]
    pub batch_output: Option<String>,
    /// What the tool may do to the world (`crate::effects`): `None` when `tools.yaml` declares
    /// nothing, which a skill bundle refuses; empty for a tool with none. Mirrors Python
    /// `ToolDef.effects`. Read by hand in [`load_registry_str`] so a malformed value gets the
    /// contract's message.
    #[serde(skip)]
    pub effects: Option<Vec<String>>,
    #[serde(default)]
    pub mock_args: BTreeMap<String, Value>,
    #[serde(default)]
    pub internal: bool,
    #[serde(default)]
    pub defaults: BTreeMap<String, Value>,
    #[serde(default)]
    pub arg_schemas: BTreeMap<String, ArgSchema>,
    #[serde(default)]
    pub arg_aliases: BTreeMap<String, String>,
}

/// A `name → ToolDef` registry.
pub type Registry = BTreeMap<String, ToolDef>;

/// True when one of `triggers` is in `text` as a whole word or phrase, ignoring case and accents:
/// "beep" matches "Beep when done" and not "beeper.mp4". A typographic apostrophe counts as a
/// plain one, so "when it’s done" matches "when it's done". Mirrors Python `triggered`
/// (`(?<!\w)phrase(?!\w)` over the normalized, whitespace-collapsed text).
pub fn triggered(text: &str, triggers: &[String]) -> bool {
    fn squash(s: &str) -> String {
        crate::retrieval::normalize(s)
            .replace('’', "'")
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
    }
    fn word(c: Option<char>) -> bool {
        c.is_some_and(|c| c.is_alphanumeric() || c == '_')
    }
    let norm = squash(text);
    triggers.iter().any(|trigger| {
        let phrase = squash(trigger);
        if phrase.is_empty() {
            return false;
        }
        // A `#` stands for a number ("every # pages"). Chinese, Japanese and Korean write no
        // spaces between words: a trigger in that script matches anywhere
        // (contracts/parity/trigger_cases.json).
        let body = phrase
            .split('#')
            .map(regex::escape)
            .collect::<Vec<_>>()
            .join(r"\d+");
        let Ok(re) = regex::Regex::new(&body) else {
            return false;
        };
        if phrase.chars().any(is_cjk) {
            return re.is_match(&norm);
        }
        let hit = re.find_iter(&norm).any(|m| {
            !word(norm[..m.start()].chars().next_back()) && !word(norm[m.end()..].chars().next())
        });
        hit
    })
}

/// Chinese, Japanese and Korean scripts (Python `registry._CJK`): kana, CJK ideographs and
/// extension A, compatibility ideographs, Hangul syllables.
fn is_cjk(c: char) -> bool {
    matches!(c,
        '\u{3040}'..='\u{30ff}'
            | '\u{3400}'..='\u{4dbf}'
            | '\u{4e00}'..='\u{9fff}'
            | '\u{f900}'..='\u{faff}'
            | '\u{ac00}'..='\u{d7af}')
}

/// Load tool definitions from a YAML file into a `name → ToolDef` map.
pub fn load_registry(path: &Path) -> anyhow::Result<Registry> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| anyhow::anyhow!("reading tools YAML {}: {e}", path.display()))?;
    load_registry_str(&text)
}

/// Parse a `tools.yaml`/`core_tools.yaml` string into a registry.
///
/// Skips top-level entries that are not mappings or lack a `description` (mirrors the Python
/// loader), then guards against an over-generic keyword (> [`MAX_KEYWORD_TOOLS`] tools).
pub fn load_registry_str(text: &str) -> anyhow::Result<Registry> {
    let root: Value = serde_yaml::from_str(text)?;
    let map = root
        .as_mapping()
        .ok_or_else(|| anyhow::anyhow!("tools YAML must be a mapping"))?;

    let mut registry: Registry = BTreeMap::new();
    for (idx, (key, cfg)) in map.iter().enumerate() {
        let Some(name) = key.as_str() else { continue };
        let Some(cfg_map) = cfg.as_mapping() else {
            continue; // skip non-tool entries
        };
        if !cfg_map.contains_key(Value::from("description")) {
            continue; // skip entries without a description
        }
        let mut tool: ToolDef = serde_yaml::from_value(cfg.clone())
            .map_err(|e| anyhow::anyhow!("parsing tool {name:?}: {e}"))?;
        tool.name = name.to_string();
        tool.effects = crate::effects::parse_effects(name, cfg_map.get(Value::from("effects")))?;
        tool.order = idx; // preserve tools.yaml order for the prompt (see ToolDef::order)
        registry.insert(name.to_string(), tool);
    }

    // Keywords MAY be shared (retrieval down-weights by document frequency); reject only an
    // egregiously generic one claimed by more than MAX_KEYWORD_TOOLS tools.
    let mut claims: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for tool in registry.values() {
        for kw in &tool.keywords {
            claims
                .entry(crate::retrieval::normalize(kw))
                .or_default()
                .push(tool.name.clone());
        }
    }
    for (kw, owners) in &claims {
        if owners.len() > MAX_KEYWORD_TOOLS {
            anyhow::bail!(
                "Keyword '{kw}' is claimed by {} tools ({}); at most {MAX_KEYWORD_TOOLS} allowed \
                 — it is too generic to discriminate.",
                owners.len(),
                owners.join(", ")
            );
        }
    }

    Ok(registry)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reg(text: &str) -> Registry {
        load_registry_str(text).unwrap()
    }

    #[test]
    fn batch_output_is_an_extension_without_its_dot() {
        let r = reg("a:\n  description: A\n  effects: []\n  batch_output: .txt\nb:\n  description: B\n  effects: []\n");
        assert_eq!(r["a"].batch_output.as_deref(), Some("txt"));
        assert_eq!(r["b"].batch_output, None);
        let shipped = load_registry(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../skills/documents/tools.yaml"),
        )
        .unwrap();
        assert_eq!(shipped["extract_text"].batch_output.as_deref(), Some("txt"));
    }

    #[test]
    fn parses_core_fields_and_defaults() {
        let r = reg("\
list_files:
  description: List files
  required_args: [path]
  optional_args: [pattern]
  keywords: [list, ls]
  readonly: true
delete_files:
  description: Delete files
  required_args: [path]
  safety_category: destructive
");
        let lt = &r["list_files"];
        assert_eq!(lt.name, "list_files");
        assert_eq!(lt.required_args, ["path"]);
        assert_eq!(lt.optional_args, ["pattern"]);
        assert_eq!(lt.safety_category, "safe"); // default
        assert!(lt.readonly);
        assert!(lt.grounded_args.is_empty());
        assert!(lt.defaults.is_empty());
        assert!(lt.arg_schemas.is_empty());

        assert_eq!(r["delete_files"].safety_category, "destructive");
        assert!(!r["delete_files"].readonly); // default false
    }

    #[test]
    fn parses_arg_schemas_and_defaults_blocks() {
        let r = reg("\
my_tool:
  description: Test tool
  required_args: [priority, count]
  optional_args: [output]
  defaults:
    output: out.mp4
  arg_schemas:
    priority:
      type: enum
      enum: [low, med, high]
      help: Task priority
    count:
      type: integer
      min: 1
      max: 100
");
        let t = &r["my_tool"];
        assert_eq!(t.defaults["output"], Value::from("out.mp4"));
        let p = &t.arg_schemas["priority"];
        assert_eq!(p.arg_type, "enum");
        assert_eq!(p.enum_values.as_deref().unwrap(), ["low", "med", "high"]);
        assert_eq!(p.help.as_deref(), Some("Task priority"));
        let c = &t.arg_schemas["count"];
        assert_eq!(c.arg_type, "integer");
        assert_eq!(c.min, Some(1.0));
        assert_eq!(c.max, Some(100.0));
    }

    #[test]
    fn grounded_args_parsed() {
        let r = reg("foo:\n  description: d\n  required_args: [input, password]\n  grounded_args: [password]\n");
        assert_eq!(r["foo"].grounded_args, ["password"]);
    }

    #[test]
    fn skips_entries_without_description() {
        let r = reg("real:\n  description: d\n  required_args: []\nnot_a_tool: 42\nother_map:\n  no_desc: true\n");
        assert!(r.contains_key("real"));
        assert!(!r.contains_key("not_a_tool"));
        assert!(!r.contains_key("other_map"));
    }

    #[test]
    fn shared_keyword_allowed_generic_rejected() {
        // shared by two tools → fine
        assert!(load_registry_str(
            "a:\n  description: A\n  keywords: [foo, bar]\nb:\n  description: B\n  keywords: [foo, baz]\n"
        )
        .is_ok());
        // claimed by 5 tools → rejected
        let mut y = String::new();
        for i in 0..5 {
            y.push_str(&format!(
                "tool_{i}:\n  description: T{i}\n  keywords: [foo, kw{i}]\n"
            ));
        }
        let err = load_registry_str(&y).unwrap_err().to_string();
        assert!(err.contains("too generic"), "{err}");
    }

    #[test]
    fn non_mapping_root_errors() {
        let err = load_registry_str("- not_a_mapping\n")
            .unwrap_err()
            .to_string();
        assert!(err.contains("mapping"), "{err}");
    }

    #[test]
    fn loads_real_ffmpeg_and_core_tools() {
        let base = Path::new(env!("CARGO_MANIFEST_DIR"));
        let ffmpeg = load_registry(&base.join("../../../skills/ffmpeg/tools.yaml")).unwrap();
        assert!(!ffmpeg.is_empty());
        assert!(ffmpeg
            .values()
            .all(|t| !t.name.is_empty() && !t.description.is_empty()));

        let core = load_registry(&base.join("../../../contracts/runtime/core_tools.yaml")).unwrap();
        for name in ["clarify", "reject", "done", "wait_for_confirmation"] {
            assert!(core.contains_key(name), "core tool {name} missing");
        }
    }
}
