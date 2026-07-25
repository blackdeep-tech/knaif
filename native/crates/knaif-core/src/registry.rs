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

/// One tool's definition (mirrors Python `ToolDef`). `name` comes from the YAML map key.
#[derive(Debug, Clone, Deserialize)]
pub struct ToolDef {
    #[serde(skip)]
    pub name: String,
    /// Position in the source `tools.yaml` mapping. The model was fine-tuned on prompts that list
    /// tools in this insertion order, so the prompt builder sorts by it (not the alphabetical
    /// `Registry` key order) to stay in-distribution.
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
    #[serde(default)]
    pub readonly: bool,
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
