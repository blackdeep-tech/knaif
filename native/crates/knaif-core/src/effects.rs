//! Declared effects: what a tool may do to the world. Port of Python `knaif.effects`.
//!
//! Every skill and toolkit tool declares `effects:` in `tools.yaml`: `fs.read`, `fs.write`, `net`
//! and `exec:<program>`; `[]` declares a tool with none. Python holds each step to its declaration
//! at run time; native reads and validates the declarations when a bundle loads, until a native
//! step context exists to enforce them (audit F11). Contract: `contracts/parity/effects_cases.json`.

use serde_yaml::Value;

use crate::registry::Registry;

const FIXED: [&str; 3] = ["fs.read", "fs.write", "net"];

/// True for one word of the vocabulary.
pub fn is_effect(value: &str) -> bool {
    if FIXED.contains(&value) {
        return true;
    }
    match value.strip_prefix("exec:") {
        Some(program) => {
            !program.is_empty()
                && program
                    .chars()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
        }
        None => false,
    }
}

/// A `tools.yaml` `effects:` value, sorted and without duplicates; `None` when it is absent.
pub fn parse_effects(tool: &str, raw: Option<&Value>) -> anyhow::Result<Option<Vec<String>>> {
    let Some(raw) = raw else {
        return Ok(None);
    };
    let Some(items) = raw.as_sequence() else {
        anyhow::bail!("tool {tool:?}: effects must be a list, got {raw:?}");
    };
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        match item.as_str() {
            Some(value) if is_effect(value) => out.push(value.to_string()),
            _ => anyhow::bail!(
                "tool {tool:?}: unknown effect {item:?} (use fs.read, fs.write, net or exec:<program>)"
            ),
        }
    }
    out.sort();
    out.dedup();
    Ok(Some(out))
}

/// Refuse a skill's or toolkit's registry in which a tool declares no effects. A registry that
/// also holds the core control tools must leave them out: they are core's and exempt.
pub fn check_declared(registry: &Registry, source: &str) -> anyhow::Result<()> {
    for (name, tool) in registry {
        if tool.effects.is_none() {
            anyhow::bail!(
                "{source}: tool {name:?} declares no effects \
                 (add `effects: [...]`, or `effects: []` for a tool with none)"
            );
        }
    }
    Ok(())
}

/// A skill bundle's own tools (`<bundle>/tools.yaml`), refused when one declares no effects. The
/// core control tools are loaded separately and are exempt.
pub fn load_skill_registry(bundle: &std::path::Path) -> anyhow::Result<Registry> {
    let path = bundle.join("tools.yaml");
    let registry = crate::registry::load_registry(&path)?;
    check_declared(&registry, &path.display().to_string())?;
    Ok(registry)
}
