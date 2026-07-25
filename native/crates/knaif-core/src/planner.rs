//! Plan envelope parsing, validation, and variable binding.
//!
//! Rust port of the deterministic parts of Python `knaif.planner`: `parse_plan`,
//! `validate_arg_by_schema`, `validate_step`, `validate_plan`, and `resolve_args`. Same
//! rules so both runtimes accept/reject the identical `{"plan": [...]}` payloads.
//!
//! Includes the safety-critical `path`/`src`/`dst` sandbox resolution, the
//! `file_type`/`pattern`/`recursive` checks, and the plan-transform trio `normalize_plan`
//! (output promotion, input/inputs coercion, arg-key aliases, scalar + enum coercion),
//! `apply_defaults`, and `optimize_plan`.

use std::collections::HashSet;
use std::path::{Component, Path, PathBuf};

use anyhow::{bail, Result};
use serde_json::{Map, Value};

use crate::registry::{ArgSchema, Registry};

/// Valid `file_type` enum values (mirrors Python `_VALID_FILE_TYPES`).
const VALID_FILE_TYPES: &[&str] = &[
    "executable",
    "text",
    "image",
    "document",
    "script",
    "archive",
    "log",
    "config",
];

/// Collapse `.`/`..` segments lexically (no filesystem access, unlike `canonicalize`), so a
/// path that doesn't exist yet can still be boundary-checked. Symlinks are not resolved.
fn lexical_normalize(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for comp in p.components() {
        match comp {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn to_abs_lexical(p: &Path, base: &Path) -> PathBuf {
    let joined = if p.is_absolute() {
        p.to_path_buf()
    } else {
        base.join(p)
    };
    lexical_normalize(&joined)
}

/// Resolve a path, enforcing the sandbox boundary when one is given. Mirrors Python
/// `_resolve_path`: relative paths resolve against sandbox (or `root` in open mode); in
/// sandbox mode the result must stay inside the sandbox.
fn resolve_path(raw: &str, root: &Path, sandbox: Option<&Path>) -> Result<PathBuf> {
    let p = Path::new(raw);
    match sandbox {
        Some(sb) => {
            let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
            let sb_abs = to_abs_lexical(sb, &cwd);
            let path_abs = if p.is_absolute() {
                lexical_normalize(p)
            } else {
                to_abs_lexical(p, &sb_abs)
            };
            if !path_abs.starts_with(&sb_abs) {
                bail!(
                    "Path '{}' is outside sandbox '{}'. Use a sandbox-relative path.",
                    path_abs.display(),
                    sb.display()
                );
            }
            Ok(path_abs)
        }
        None => Ok(to_abs_lexical(p, root)),
    }
}

/// Parse model output into a plan payload: a JSON object with a `"plan"` array.
pub fn parse_plan(json_text: &str) -> Result<Value> {
    let payload: Value =
        serde_json::from_str(json_text).map_err(|e| anyhow::anyhow!("Invalid JSON: {e}"))?;
    let obj = payload
        .as_object()
        .filter(|o| o.contains_key("plan"))
        .ok_or_else(|| anyhow::anyhow!("Payload must be a JSON object with a 'plan' field."))?;
    if !obj["plan"].is_array() {
        bail!("'plan' must be a list.");
    }
    Ok(payload)
}

fn is_ident(s: &str) -> bool {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => return false,
    }
    chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

/// `^\$[A-Za-z_][A-Za-z0-9_]*$` — a plain output variable (no dotted field).
fn is_output_ref(s: &str) -> bool {
    s.strip_prefix('$').is_some_and(is_ident)
}

/// `^\$id(\.id)?$` — a `$var` or `$var.field` reference.
fn is_var_ref(s: &str) -> bool {
    let Some(rest) = s.strip_prefix('$') else {
        return false;
    };
    match rest.split_once('.') {
        Some((v, f)) => is_ident(v) && is_ident(f),
        None => is_ident(rest),
    }
}

fn json_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_f64() {
                "float"
            } else {
                "int"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Validate a value against an [`ArgSchema`]. `$var` references pass through (resolved and
/// validated at runtime). Mirrors Python `validate_arg_by_schema`.
pub fn validate_arg_by_schema(arg_name: &str, value: &Value, schema: &ArgSchema) -> Result<()> {
    if value.as_str().is_some_and(|s| s.starts_with('$')) {
        return Ok(());
    }
    match schema.arg_type.as_str() {
        "string" => {
            if !value.is_string() {
                bail!(
                    "Arg '{arg_name}' must be a string, got '{}'",
                    json_type_name(value)
                );
            }
        }
        "boolean" => {
            if !value.is_boolean() {
                bail!(
                    "Arg '{arg_name}' must be a boolean, got '{}'",
                    json_type_name(value)
                );
            }
        }
        "integer" => {
            if !(value.is_i64() || value.is_u64()) {
                bail!(
                    "Arg '{arg_name}' must be an integer, got '{}'",
                    json_type_name(value)
                );
            }
            check_bounds(arg_name, value, schema)?;
        }
        "number" => {
            if !value.is_number() {
                bail!(
                    "Arg '{arg_name}' must be a number, got '{}'",
                    json_type_name(value)
                );
            }
            check_bounds(arg_name, value, schema)?;
        }
        "array" => {
            if !value.is_array() {
                bail!(
                    "Arg '{arg_name}' must be an array, got '{}'",
                    json_type_name(value)
                );
            }
        }
        "enum" => {
            if let Some(allowed) = &schema.enum_values {
                let ok = value
                    .as_str()
                    .is_some_and(|s| allowed.iter().any(|a| a == s));
                if !ok {
                    bail!("Arg '{arg_name}' must be one of {allowed:?}, got {value}");
                }
            }
        }
        _ => {}
    }
    Ok(())
}

fn check_bounds(arg_name: &str, value: &Value, schema: &ArgSchema) -> Result<()> {
    let n = value.as_f64().unwrap_or(f64::NAN);
    if let Some(min) = schema.min {
        if n < min {
            bail!("Arg '{arg_name}' must be >= {min}");
        }
    }
    if let Some(max) = schema.max {
        if n > max {
            bail!("Arg '{arg_name}' must be <= {max}");
        }
    }
    Ok(())
}

/// Validate one plan step's structure against the registry. Mirrors Python `validate_step`.
///
/// `root` is the base for resolving relative paths in open mode; `sandbox`, when set,
/// confines `path`/`src`/`dst` args (sandbox-escape → error).
pub fn validate_step(
    step: &Value,
    registry: &Registry,
    root: &Path,
    sandbox: Option<&Path>,
) -> Result<()> {
    let obj = step
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("Each plan step must be an object."))?;

    let tool_name = obj.get("tool").and_then(Value::as_str);
    let tool = match tool_name.and_then(|n| registry.get(n)) {
        Some(t) => t,
        None => match tool_name {
            Some(n) => bail!("Unknown tool: {n:?}"),
            None => bail!("Unknown tool: None"),
        },
    };

    let args = obj
        .get("args")
        .and_then(Value::as_object)
        .ok_or_else(|| anyhow::anyhow!("Each step must contain an 'args' object."))?;

    let missing: Vec<&str> = tool
        .required_args
        .iter()
        .filter(|k| !args.contains_key(k.as_str()))
        .map(String::as_str)
        .collect();
    if !missing.is_empty() {
        bail!("Tool '{}' missing required args: {missing:?}", tool.name);
    }

    if !tool.any_of_args.is_empty() && !tool.any_of_args.iter().any(|k| args.contains_key(k)) {
        bail!(
            "Tool '{}' requires at least one of: {:?}",
            tool.name,
            tool.any_of_args
        );
    }

    let allowed: HashSet<&str> = tool
        .required_args
        .iter()
        .chain(tool.optional_args.iter())
        .map(String::as_str)
        .collect();
    let extra: Vec<&str> = args
        .keys()
        .filter(|k| !allowed.contains(k.as_str()))
        .map(String::as_str)
        .collect();
    if !extra.is_empty() {
        bail!("Tool '{}' has unsupported args: {extra:?}", tool.name);
    }

    for (arg_name, value) in args {
        if let Some(schema) = tool.arg_schemas.get(arg_name) {
            validate_arg_by_schema(arg_name, value, schema)
                .map_err(|e| anyhow::anyhow!("Tool '{}' arg validation: {e}", tool.name))?;
        }
    }

    if let Some(output) = obj.get("output") {
        if !output.is_null() && !output.as_str().is_some_and(is_output_ref) {
            bail!("Step output must be a $identifier (no dots), got: {output}");
        }
    }

    // Path-like args: a `$var` reference is syntax-checked; a literal string is
    // sandbox-resolved (open mode just normalizes and never fails).
    for key in ["path", "src", "dst"] {
        if let Some(val) = args.get(key) {
            match val.as_str() {
                Some(s) if s.starts_with('$') => validate_var_ref_syntax(s)?,
                Some(s) => {
                    resolve_path(s, root, sandbox)?;
                }
                None => bail!("'{key}' must be a string."),
            }
        }
    }

    if let Some(val) = args.get("pattern") {
        match val.as_str() {
            Some(s) if s.starts_with('$') => validate_var_ref_syntax(s)?,
            Some(_) => {}
            None => bail!("'pattern' must be a string."),
        }
    }

    if let Some(val) = args.get("file_type") {
        match val.as_str() {
            Some(s) if s.starts_with('$') => validate_var_ref_syntax(s)?,
            Some(s) if VALID_FILE_TYPES.contains(&s) => {}
            Some(s) => bail!("Unknown file_type: {s:?}. Valid types: {VALID_FILE_TYPES:?}"),
            None => bail!("'file_type' must be a string."),
        }
    }

    if let Some(val) = args.get("recursive") {
        match val.as_str() {
            Some(s) if s.starts_with('$') => validate_var_ref_syntax(s)?,
            _ if val.is_boolean() => {}
            _ => bail!("'recursive' must be a boolean."),
        }
    }

    Ok(())
}

/// Validate every step in `payload["plan"]`. Enforces variable-before-assignment ordering in
/// multi-step plans. Mirrors Python `validate_plan`.
pub fn validate_plan(
    payload: &Value,
    registry: &Registry,
    root: &Path,
    sandbox: Option<&Path>,
) -> Result<()> {
    let plan = payload
        .get("plan")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("'plan' must be a list."))?;
    let multi_step = plan.len() > 1;
    let mut assigned: HashSet<String> = HashSet::new();

    for (i, step) in plan.iter().enumerate() {
        let n = i + 1;
        validate_step(step, registry, root, sandbox)
            .map_err(|e| anyhow::anyhow!("Plan step {n} invalid: {e}"))?;

        if multi_step {
            if let Some(args) = step.get("args").and_then(Value::as_object) {
                for val in args.values() {
                    if let Some(s) = val.as_str() {
                        if let Some(rest) = s.strip_prefix('$') {
                            let varname = rest.split('.').next().unwrap_or("");
                            if !assigned.contains(varname) {
                                bail!("Plan step {n}: variable '{s}' used before it is assigned.");
                            }
                        }
                    }
                }
            }
        }

        if let Some(out) = step.get("output").and_then(Value::as_str) {
            if !out.is_empty() {
                assigned.insert(out.trim_start_matches('$').to_string());
            }
        }
    }
    Ok(())
}

/// Resolve `$var` / `$var.field` references in an args map against a variable context.
/// Non-string values pass through. Mirrors Python `resolve_args`.
pub fn resolve_args(
    args: &Map<String, Value>,
    context: &Map<String, Value>,
) -> Result<Map<String, Value>> {
    let mut resolved = Map::new();
    for (key, val) in args {
        let Some(s) = val.as_str().filter(|s| s.starts_with('$')) else {
            resolved.insert(key.clone(), val.clone());
            continue;
        };
        let refstr = s.trim_start_matches('$');
        let (varname, field) = match refstr.split_once('.') {
            Some((v, f)) => (v, Some(f)),
            None => (refstr, None),
        };
        let value = context
            .get(varname)
            .ok_or_else(|| anyhow::anyhow!("Variable '{s}' used in args was never assigned."))?;
        match field {
            None => {
                resolved.insert(key.clone(), value.clone());
            }
            Some(f) => {
                let obj = value.as_object().ok_or_else(|| {
                    anyhow::anyhow!(
                        "Variable '{s}': '{varname}' is not a dict, cannot access field '{f}'."
                    )
                })?;
                let fv = obj.get(f).ok_or_else(|| {
                    anyhow::anyhow!("Variable '{s}': field '{f}' not found in '{varname}'.")
                })?;
                resolved.insert(key.clone(), fv.clone());
            }
        }
    }
    Ok(resolved)
}

/// Convert a YAML value (from `ToolDef.defaults`) to a JSON value for insertion into a plan.
fn yaml_to_json(v: &serde_yaml::Value) -> Value {
    serde_json::to_value(v).unwrap_or(Value::Null)
}

/// Lowercase and collapse runs of whitespace/underscore to a single `-` (mirrors Python
/// `re.sub(r"[\s_]+", "-", s.lower())`) — for separator-insensitive enum matching.
fn sep_normalize(s: &str) -> String {
    let mut out = String::new();
    let mut prev_sep = false;
    for c in s.to_lowercase().chars() {
        if c.is_whitespace() || c == '_' {
            if !prev_sep {
                out.push('-');
                prev_sep = true;
            }
        } else {
            out.push(c);
            prev_sep = false;
        }
    }
    out
}

/// Fill missing args from `ToolDef.defaults` for each step (in place). Explicit values are
/// never overwritten. Mirrors Python `apply_defaults`.
pub fn apply_defaults(payload: &mut Value, registry: &Registry) {
    let Some(plan) = payload.get_mut("plan").and_then(Value::as_array_mut) else {
        return;
    };
    for step in plan.iter_mut() {
        let Some(tool_name) = step.get("tool").and_then(Value::as_str).map(str::to_string) else {
            continue;
        };
        let Some(tool) = registry.get(&tool_name) else {
            continue;
        };
        if tool.defaults.is_empty() {
            continue;
        }
        let Some(step_obj) = step.as_object_mut() else {
            continue;
        };
        let args = step_obj
            .entry("args")
            .or_insert_with(|| Value::Object(Map::new()));
        let Some(args_obj) = args.as_object_mut() else {
            continue;
        };
        for (key, value) in &tool.defaults {
            args_obj
                .entry(key.clone())
                .or_insert_with(|| yaml_to_json(value));
        }
    }
}

/// Return a new plan with redundant readonly steps removed: a readonly step is dropped only
/// when a later action step exists AND its output (if any) isn't referenced later. Terminal
/// readonly steps are kept. Mirrors Python `optimize_plan`.
pub fn optimize_plan(plan: &[Value], registry: &Registry) -> Vec<Value> {
    let mut referenced_after: HashSet<String> = HashSet::new();
    let mut has_later_action = false;
    let mut to_remove: HashSet<usize> = HashSet::new();

    for i in (0..plan.len()).rev() {
        let step = &plan[i];
        let is_readonly = step
            .get("tool")
            .and_then(Value::as_str)
            .and_then(|n| registry.get(n))
            .map(|t| t.readonly)
            .unwrap_or(false);

        if is_readonly && has_later_action {
            let varname = step
                .get("output")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(|o| o.trim_start_matches('$').to_string());
            match varname {
                None => {
                    to_remove.insert(i);
                }
                Some(v) if !referenced_after.contains(&v) => {
                    to_remove.insert(i);
                }
                _ => {}
            }
        }

        if let Some(args) = step.get("args").and_then(Value::as_object) {
            for val in args.values() {
                if let Some(rest) = val.as_str().and_then(|s| s.strip_prefix('$')) {
                    referenced_after.insert(rest.split('.').next().unwrap_or("").to_string());
                }
            }
        }
        if !is_readonly {
            has_later_action = true;
        }
    }

    plan.iter()
        .enumerate()
        .filter(|(i, _)| !to_remove.contains(i))
        .map(|(_, s)| s.clone())
        .collect()
}

/// Normalize a raw model payload in place before validation. Mirrors Python `normalize_plan`:
/// promote `args.output = $id` to the step-level `output`; then (registry-driven) reconcile
/// `input`/`inputs`, apply `arg_aliases`, coerce scalar types, and coerce enum values.
pub fn normalize_plan(payload: &mut Value, registry: Option<&Registry>) {
    let Some(plan) = payload.get_mut("plan").and_then(Value::as_array_mut) else {
        return;
    };
    for step in plan.iter_mut() {
        let Some(step_obj) = step.as_object_mut() else {
            continue;
        };

        // Pass 1 — $var output promotion.
        let promote = {
            let has_output = step_obj.contains_key("output");
            step_obj
                .get("args")
                .and_then(Value::as_object)
                .and_then(|a| a.get("output"))
                .and_then(Value::as_str)
                .filter(|s| is_output_ref(s) && !has_output)
                .map(str::to_string)
        };
        if let Some(out) = promote {
            step_obj.insert("output".into(), Value::String(out));
            if let Some(a) = step_obj.get_mut("args").and_then(Value::as_object_mut) {
                a.remove("output");
            }
        }

        // Passes 2–5 need the tool schema.
        let Some(reg) = registry else { continue };
        let Some(tool_name) = step_obj
            .get("tool")
            .and_then(Value::as_str)
            .map(str::to_string)
        else {
            continue;
        };
        let Some(tool) = reg.get(&tool_name) else {
            continue;
        };
        let Some(args) = step_obj.get_mut("args").and_then(Value::as_object_mut) else {
            continue;
        };

        // Pass 2 — input/inputs coercion (schema-driven).
        let allowed: HashSet<&str> = tool
            .required_args
            .iter()
            .chain(tool.optional_args.iter())
            .map(String::as_str)
            .collect();
        let wants_scalar = allowed.contains("input") && !allowed.contains("inputs");
        let wants_plural = allowed.contains("inputs") && !allowed.contains("input");
        if wants_scalar && args.contains_key("inputs") && !args.contains_key("input") {
            match args.get("inputs").cloned() {
                Some(Value::Array(a)) if a.len() == 1 => {
                    args.insert("input".into(), a[0].clone());
                    args.remove("inputs");
                }
                Some(Value::Array(_)) => {} // ≥2 elements: leave for validation to reject
                Some(v @ Value::String(_)) => {
                    args.insert("input".into(), v);
                    args.remove("inputs");
                }
                _ => {}
            }
        } else if wants_plural && args.contains_key("input") && !args.contains_key("inputs") {
            match args.get("input").cloned() {
                Some(v @ Value::String(_)) => {
                    args.insert("inputs".into(), Value::Array(vec![v]));
                    args.remove("input");
                }
                Some(v @ Value::Array(_)) => {
                    args.insert("inputs".into(), v);
                    args.remove("input");
                }
                _ => {}
            }
        }

        // Pass 3 — arg-key aliases (rename to canonical, never clobbering).
        for (src, dst) in &tool.arg_aliases {
            if args.contains_key(src) && !args.contains_key(dst) {
                if let Some(v) = args.remove(src) {
                    args.insert(dst.clone(), v);
                }
            }
        }

        // Pass 4 — scalar type coercion.
        let names: Vec<String> = args.keys().cloned().collect();
        for name in &names {
            let Some(schema) = tool.arg_schemas.get(name) else {
                continue;
            };
            let value = args.get(name).cloned().unwrap_or(Value::Null);
            match schema.arg_type.as_str() {
                "string" | "enum" if value.is_number() => {
                    let s = if let Some(i) = value.as_i64() {
                        i.to_string()
                    } else if let Some(u) = value.as_u64() {
                        u.to_string()
                    } else {
                        value.as_f64().map(|f| f.to_string()).unwrap_or_default()
                    };
                    args.insert(name.clone(), Value::String(s));
                }
                "integer" => {
                    if let Some(parsed) = value.as_str().and_then(|s| s.trim().parse::<i64>().ok())
                    {
                        args.insert(name.clone(), Value::from(parsed));
                    }
                }
                "number" => {
                    if let Some(parsed) = value.as_str().and_then(|s| s.trim().parse::<f64>().ok())
                    {
                        args.insert(name.clone(), Value::from(parsed));
                    }
                }
                _ => {}
            }
        }

        // Pass 5 — enum value coercion (schema aliases + case/separator-insensitive match).
        for name in &names {
            let Some(schema) = tool.arg_schemas.get(name) else {
                continue;
            };
            let Some(enum_vals) = &schema.enum_values else {
                continue;
            };
            let Some(value) = args.get(name).and_then(Value::as_str).map(str::to_string) else {
                continue;
            };
            if enum_vals.contains(&value) {
                continue;
            }
            let low = value.to_lowercase();
            if let Some(aliases) = &schema.aliases {
                if let Some((_, canonical)) = aliases.iter().find(|(k, _)| k.to_lowercase() == low)
                {
                    args.insert(name.clone(), Value::String(canonical.clone()));
                    continue;
                }
            }
            let target = sep_normalize(&low);
            if let Some(m) = enum_vals.iter().find(|e| sep_normalize(e) == target) {
                args.insert(name.clone(), Value::String(m.clone()));
            }
        }
    }
}

/// Well-formed `$var` / `$var.field` check (exposed for callers that validate references).
pub fn validate_var_ref_syntax(value: &str) -> Result<()> {
    if !is_var_ref(value) {
        bail!("Malformed variable reference {value:?}. Expected $identifier or $identifier.field");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::load_registry_str;
    use serde_json::json;

    const TOOLS: &str = "\
find_files:
  description: Find files
  required_args: [path]
  optional_args: [file_type, pattern]
compress:
  description: Compress
  required_args: [input]
  optional_args: [quality, output]
  arg_schemas:
    quality:
      type: enum
      enum: [low, high]
    tries:
      type: integer
      min: 1
      max: 5
report:
  description: Report
  any_of_args: [a, b]
  optional_args: [a, b]
";

    fn reg() -> Registry {
        load_registry_str(TOOLS).unwrap()
    }

    // Test helpers: open mode (root = ".", no sandbox).
    fn vs(step: &Value, r: &Registry) -> Result<()> {
        validate_step(step, r, Path::new("."), None)
    }
    fn vp(payload: &Value, r: &Registry) -> Result<()> {
        validate_plan(payload, r, Path::new("."), None)
    }

    #[test]
    fn parse_plan_ok_and_errors() {
        assert!(parse_plan(r#"{"plan": []}"#).is_ok());
        assert!(parse_plan("not json")
            .unwrap_err()
            .to_string()
            .contains("Invalid JSON"));
        assert!(parse_plan(r#"{"x": 1}"#)
            .unwrap_err()
            .to_string()
            .contains("'plan' field"));
        assert!(parse_plan(r#"{"plan": 3}"#)
            .unwrap_err()
            .to_string()
            .contains("must be a list"));
    }

    #[test]
    fn validate_step_tool_and_args() {
        let r = reg();
        // unknown tool
        assert!(vs(&json!({"tool": "nope", "args": {}}), &r)
            .unwrap_err()
            .to_string()
            .contains("Unknown tool"));
        // args not an object
        assert!(vs(&json!({"tool": "find_files", "args": 5}), &r)
            .unwrap_err()
            .to_string()
            .contains("'args' object"));
        // missing required
        assert!(vs(&json!({"tool": "find_files", "args": {}}), &r)
            .unwrap_err()
            .to_string()
            .contains("missing required args"));
        // unsupported arg
        assert!(vs(
            &json!({"tool": "find_files", "args": {"path": ".", "zzz": 1}}),
            &r
        )
        .unwrap_err()
        .to_string()
        .contains("unsupported args"));
        // ok
        assert!(vs(&json!({"tool": "find_files", "args": {"path": "."}}), &r).is_ok());
    }

    #[test]
    fn any_of_and_schema_and_output() {
        let r = reg();
        // any_of: neither present
        assert!(vs(&json!({"tool": "report", "args": {}}), &r)
            .unwrap_err()
            .to_string()
            .contains("at least one of"));
        assert!(vs(&json!({"tool": "report", "args": {"a": 1}}), &r).is_ok());
        // enum reject + accept
        assert!(vs(
            &json!({"tool": "compress", "args": {"input": "x", "quality": "mid"}}),
            &r
        )
        .is_err());
        assert!(vs(
            &json!({"tool": "compress", "args": {"input": "x", "quality": "low"}}),
            &r
        )
        .is_ok());
        // $var passes schema
        assert!(vs(
            &json!({"tool": "compress", "args": {"input": "x", "quality": "$q"}}),
            &r
        )
        .is_ok());
        // bad output syntax
        assert!(vs(
            &json!({"tool": "compress", "args": {"input": "x"}, "output": "$a.b"}),
            &r
        )
        .is_err());
        assert!(vs(
            &json!({"tool": "compress", "args": {"input": "x"}, "output": "$vid"}),
            &r
        )
        .is_ok());
    }

    #[test]
    fn validate_plan_var_before_assignment() {
        let r = reg();
        // step 2 uses $out before it is assigned
        let bad = json!({"plan": [
            {"tool": "compress", "args": {"input": "a"}},
            {"tool": "compress", "args": {"input": "$out"}}
        ]});
        assert!(vp(&bad, &r)
            .unwrap_err()
            .to_string()
            .contains("used before it is assigned"));
        // assign then use
        let good = json!({"plan": [
            {"tool": "compress", "args": {"input": "a"}, "output": "$out"},
            {"tool": "compress", "args": {"input": "$out"}}
        ]});
        assert!(vp(&good, &r).is_ok());
    }

    #[test]
    fn resolve_args_scalar_dotted_and_errors() {
        let mut ctx = Map::new();
        ctx.insert("f".into(), json!("video.mp4"));
        ctx.insert("probe".into(), json!({"duration": 12}));

        let args: Map<String, Value> = json!({"input": "$f", "n": 3, "d": "$probe.duration"})
            .as_object()
            .unwrap()
            .clone();
        let out = resolve_args(&args, &ctx).unwrap();
        assert_eq!(out["input"], json!("video.mp4"));
        assert_eq!(out["n"], json!(3)); // non-string passthrough
        assert_eq!(out["d"], json!(12));

        // missing var
        let a2: Map<String, Value> = json!({"x": "$missing"}).as_object().unwrap().clone();
        assert!(resolve_args(&a2, &ctx)
            .unwrap_err()
            .to_string()
            .contains("never assigned"));
        // field on non-dict
        let a3: Map<String, Value> = json!({"x": "$f.field"}).as_object().unwrap().clone();
        assert!(resolve_args(&a3, &ctx)
            .unwrap_err()
            .to_string()
            .contains("is not a dict"));
        // missing field
        let a4: Map<String, Value> = json!({"x": "$probe.missing"}).as_object().unwrap().clone();
        assert!(resolve_args(&a4, &ctx)
            .unwrap_err()
            .to_string()
            .contains("not found"));
    }

    const TOOLS2: &str = "\
scan:
  description: Scan
  optional_args: [path]
  readonly: true
act:
  description: Act
  required_args: [input]
combine:
  description: Combine
  optional_args: [inputs]
split:
  description: Split
  optional_args: [ranges, count]
  arg_aliases: {pages: ranges}
  arg_schemas:
    count: {type: integer}
    ranges: {type: string}
convert:
  description: Convert
  optional_args: [to_format, output]
  defaults: {output: out.pdf}
  arg_schemas:
    to_format:
      type: enum
      enum: [md, pdf]
      aliases: {markdown: md}
";

    fn reg2() -> Registry {
        load_registry_str(TOOLS2).unwrap()
    }

    #[test]
    fn apply_defaults_fills_missing_only() {
        let r = reg2();
        let mut p = json!({"plan": [
            {"tool": "convert", "args": {"to_format": "pdf"}},
            {"tool": "convert", "args": {"output": "explicit.pdf"}}
        ]});
        apply_defaults(&mut p, &r);
        let plan = p["plan"].as_array().unwrap();
        assert_eq!(plan[0]["args"]["output"], json!("out.pdf")); // filled
        assert_eq!(plan[1]["args"]["output"], json!("explicit.pdf")); // not overwritten
    }

    #[test]
    fn optimize_removes_redundant_readonly() {
        let r = reg2();
        // scan (readonly, unreferenced output) before an action → removed
        let plan = json!([
            {"tool": "scan", "args": {}, "output": "$s"},
            {"tool": "act", "args": {"input": "x"}}
        ]);
        let out = optimize_plan(plan.as_array().unwrap(), &r);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["tool"], json!("act"));

        // scan output referenced later → kept
        let plan2 = json!([
            {"tool": "scan", "args": {}, "output": "$s"},
            {"tool": "act", "args": {"input": "$s"}}
        ]);
        assert_eq!(optimize_plan(plan2.as_array().unwrap(), &r).len(), 2);

        // terminal readonly → kept
        let plan3 = json!([
            {"tool": "act", "args": {"input": "x"}},
            {"tool": "scan", "args": {}}
        ]);
        assert_eq!(optimize_plan(plan3.as_array().unwrap(), &r).len(), 2);
    }

    #[test]
    fn normalize_output_promotion_and_io_coercion() {
        let r = reg2();
        // args.output = $v promoted to step.output
        let mut p = json!({"plan": [{"tool": "act", "args": {"input": "x", "output": "$v"}}]});
        normalize_plan(&mut p, Some(&r));
        let step = &p["plan"][0];
        assert_eq!(step["output"], json!("$v"));
        assert!(step["args"].get("output").is_none());

        // scalar tool given inputs:[one] → input
        let mut p2 = json!({"plan": [{"tool": "act", "args": {"inputs": ["a.mp4"]}}]});
        normalize_plan(&mut p2, Some(&r));
        assert_eq!(p2["plan"][0]["args"]["input"], json!("a.mp4"));
        assert!(p2["plan"][0]["args"].get("inputs").is_none());

        // plural tool given input:"a" → inputs:["a"]
        let mut p3 = json!({"plan": [{"tool": "combine", "args": {"input": "a.mp4"}}]});
        normalize_plan(&mut p3, Some(&r));
        assert_eq!(p3["plan"][0]["args"]["inputs"], json!(["a.mp4"]));
    }

    #[test]
    fn normalize_aliases_and_coercions() {
        let r = reg2();
        // arg-key alias pages→ranges; string arg 5→"5"; integer arg "3"→3
        let mut p = json!({"plan": [{"tool": "split", "args": {"pages": 5, "count": "3"}}]});
        normalize_plan(&mut p, Some(&r));
        let a = &p["plan"][0]["args"];
        assert_eq!(a["ranges"], json!("5")); // renamed + number→string
        assert_eq!(a["count"], json!(3)); // "3"→3
        assert!(a.get("pages").is_none());

        // enum: alias markdown→md, case MD→md, case PDF→pdf
        for (input, want) in [("markdown", "md"), ("MD", "md"), ("PDF", "pdf")] {
            let mut pe = json!({"plan": [{"tool": "convert", "args": {"to_format": input}}]});
            normalize_plan(&mut pe, Some(&r));
            assert_eq!(
                pe["plan"][0]["args"]["to_format"],
                json!(want),
                "input {input}"
            );
        }
    }

    #[test]
    fn file_type_pattern_recursive_checks() {
        let r = reg();
        // valid file_type
        assert!(vs(
            &json!({"tool": "find_files", "args": {"path": ".", "file_type": "image"}}),
            &r
        )
        .is_ok());
        // invalid file_type
        assert!(vs(
            &json!({"tool": "find_files", "args": {"path": ".", "file_type": "bogus"}}),
            &r
        )
        .unwrap_err()
        .to_string()
        .contains("Unknown file_type"));
        // $var file_type passes (syntax only)
        assert!(vs(
            &json!({"tool": "find_files", "args": {"path": ".", "file_type": "$ft"}}),
            &r
        )
        .is_ok());
        // path must be a string
        assert!(vs(&json!({"tool": "find_files", "args": {"path": 5}}), &r)
            .unwrap_err()
            .to_string()
            .contains("'path' must be a string"));
    }

    #[test]
    fn sandbox_boundary_enforced() {
        let r = reg();
        let sb = Path::new("/work/sandbox");
        // in-sandbox relative path is fine
        assert!(validate_step(
            &json!({"tool": "find_files", "args": {"path": "sub/a.txt"}}),
            &r,
            Path::new("."),
            Some(sb),
        )
        .is_ok());
        // ../ escape is rejected
        let err = validate_step(
            &json!({"tool": "find_files", "args": {"path": "../../etc/passwd"}}),
            &r,
            Path::new("."),
            Some(sb),
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("outside sandbox"), "{err}");
    }
}
