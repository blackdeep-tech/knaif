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
///
/// The sandboxed branch resolves filesystem-real (`crate::sandbox::resolve_real`) rather
/// than lexically: a purely lexical check accepts a path that reads as "inside" the sandbox
/// while actually being a symlink/junction pointing outside it — Python's `Path.resolve()`
/// already rejects that case, so this mirrors it (audit F4). The open-mode branch stays
/// lexical: no boundary is enforced there, so there is nothing security-relevant to gain
/// from touching the filesystem.
fn resolve_path(raw: &str, root: &Path, sandbox: Option<&Path>) -> Result<PathBuf> {
    let p = Path::new(raw);
    match sandbox {
        Some(sb) => {
            let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
            let sb_real = crate::sandbox::resolve_real(sb, &cwd);
            // A relative `raw` resolves against the sandbox itself, not cwd; an absolute
            // `raw` ignores the base — resolve_real handles both from a single call.
            let path_real = crate::sandbox::resolve_real(p, sb);
            if !path_real.starts_with(&sb_real) {
                bail!(
                    "Path '{}' is outside sandbox '{}'. Use a sandbox-relative path.",
                    path_real.display(),
                    sb.display()
                );
            }
            Ok(path_real)
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
    validate_step_with(step, registry, root, sandbox, false)
}

/// `validate_step`, with the internal-tool gate made explicit.
///
/// `allow_internal` must be **false** for anything the model produced, and is true only for
/// an already-expanded sub-plan, which the deterministic expander built rather than the
/// model. Port of Python `validate_step(..., allow_internal=...)`.
pub fn validate_step_with(
    step: &Value,
    registry: &Registry,
    root: &Path,
    sandbox: Option<&Path>,
    allow_internal: bool,
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

    // Internal tools are reachable only through an intent's expansion. The prompt never
    // lists them, but "the model was not shown it" is obscurity, not validation: an internal
    // tool is typically the one that runs a command with the args it is handed, so accepting
    // a model-proposed one would skip intent expansion entirely. This check was missing
    // here while Python had it — found by the L2 verdict contract.
    if tool.internal && !allow_internal {
        // Single quotes and this exact wording mirror Python's message: the parity contract
        // compares the error *class* by substring, so the two must agree on the phrasing.
        bail!(
            "Tool '{}' is internal and cannot be proposed directly; it is only reachable through an intent's expansion.",
            tool.name
        );
    }

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
    validate_plan_with(payload, registry, root, sandbox, false)
}

/// `validate_plan`, with the internal-tool gate made explicit. See [`validate_step_with`].
pub fn validate_plan_with(
    payload: &Value,
    registry: &Registry,
    root: &Path,
    sandbox: Option<&Path>,
    allow_internal: bool,
) -> Result<()> {
    let plan = payload
        .get("plan")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("'plan' must be a list."))?;
    let multi_step = plan.len() > 1;
    let mut assigned: HashSet<String> = HashSet::new();

    for (i, step) in plan.iter().enumerate() {
        let n = i + 1;
        validate_step_with(step, registry, root, sandbox, allow_internal)
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
            // Alias keys are normalized the same way the enum values below are, and
            // deliberately so: matching them exactly while enum values tolerated spacing was
            // an asymmetry with no reason behind it, invisible only because every alias in the
            // tree was a single word (`markdown`, `jpeg`). A multi-word one exposes it.
            let target = sep_normalize(&value);
            if let Some(aliases) = &schema.aliases {
                if let Some((_, canonical)) =
                    aliases.iter().find(|(k, _)| sep_normalize(k) == target)
                {
                    args.insert(name.clone(), Value::String(canonical.clone()));
                    continue;
                }
            }
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

    /// An alias KEY is matched on the same normalized form as an enum value.
    ///
    /// Pass 5's two halves used to disagree — enum values tolerated spacing and case while
    /// alias keys were compared exactly after lowercasing. Both runtimes carried the same
    /// asymmetry, so L2 parity never caught it: they agreed on being wrong. It stayed
    /// invisible because every alias in the tree was a single word (`markdown`, `jpeg`),
    /// which has no separator to get wrong; ffmpeg's `visually lossless` is the first
    /// multi-word one. Mirrors `test_normalize_plan_alias_keys_are_separator_insensitive_too`.
    #[test]
    fn enum_alias_keys_are_separator_insensitive() {
        const TOOLS3: &str = "\
encode:
  description: Encode
  optional_args: [quality]
  arg_schemas:
    quality:
      type: enum
      enum: [best_possible, balanced]
      aliases: {visually_lossless: best_possible}
";
        let r = load_registry_str(TOOLS3).unwrap();
        for (input, want) in [
            ("visually_lossless", "best_possible"), // exact, as before
            ("visually lossless", "best_possible"), // space
            ("visually-lossless", "best_possible"), // hyphen
            ("Visually Lossless", "best_possible"), // case + separator
            ("balanced", "balanced"),               // already valid, untouched
            ("nonsense", "nonsense"),               // unknown left for validation
        ] {
            let mut p = json!({"plan": [{"tool": "encode", "args": {"quality": input}}]});
            normalize_plan(&mut p, Some(&r));
            assert_eq!(
                p["plan"][0]["args"]["quality"],
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

    #[cfg(windows)]
    #[test]
    fn sandbox_boundary_rejects_a_junction_escape() {
        // The audit's literal F4 repro, exercised through the real entry point
        // (validate_step -> resolve_path), not just the sandbox module directly: a junction
        // placed INSIDE the sandbox, pointing to a directory OUTSIDE it, must be rejected —
        // the previous lexical-only check accepted it because the junction's own path reads
        // as "inside" without ever touching the filesystem.
        let r = reg();
        let tmp = std::env::temp_dir().join(format!(
            "knaif-core-planner-test-{}-junction",
            std::process::id()
        ));
        let sandbox = tmp.join("sandbox");
        let outside = tmp.join("outside");
        std::fs::create_dir_all(&sandbox).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(outside.join("secret.txt"), b"secret").unwrap();

        let link = sandbox.join("escape_link");
        let status = std::process::Command::new("cmd")
            .args([
                "/C",
                "mklink",
                "/J",
                &link.display().to_string(),
                &outside.display().to_string(),
            ])
            .status()
            .expect("mklink must run on Windows");
        assert!(
            status.success(),
            "junction creation must succeed (no admin needed)"
        );

        let err = validate_step(
            &json!({"tool": "find_files", "args": {"path": "escape_link/secret.txt"}}),
            &r,
            Path::new("."),
            Some(&sandbox),
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("outside sandbox"), "{err}");

        std::fs::remove_dir_all(&tmp).ok();
    }
}

// ── stem resolution (port of `planner.resolve_stems`) ────────────────────────

/// Arg keys whose values are file paths. Mirrors Python's `_PATH_ARG_KEYS`.
const STEM_PATH_ARG_KEYS: &[&str] = &[
    "inputs", "input", "files", "src", "dst", "path", "base", "append",
];

/// What resolving a stem produced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StemOutcome {
    /// Nothing to do, or resolved in place.
    Resolved(serde_json::Value),
    /// The model named something the sandbox cannot pin down. Carries the question to ask.
    Clarify(String),
}

/// True if *value* looks like an extension-less filename stem worth resolving.
///
/// The **structural marker** (`_`, `-`, or a digit) is the load-bearing half and is easy to drop
/// when re-deriving this: without it, ordinary words like `video` or `audio` become stems, and a
/// plan naming one gets downgraded to a clarify instead of running. Ported from Python's
/// `_is_stem_candidate`.
fn is_stem_candidate(value: &str) -> bool {
    if value.starts_with('$') || value.contains('.') {
        return false;
    }
    if value.contains('*') || value.contains('?') || value.contains('[') {
        return false;
    }
    let mut chars = value.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !first.is_ascii_alphanumeric() {
        return false;
    }
    if !value
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return false;
    }
    value
        .chars()
        .any(|c| c == '_' || c == '-' || c.is_ascii_digit())
}

/// Resolve one stem against the sandbox: 0 matches → clarify, 1 → the filename, >1 → clarify.
///
/// The two questions are Python's, word for word, because they reach the user.
fn resolve_one_stem(value: &str, sandbox: &Path) -> Result<String, String> {
    if !is_stem_candidate(value) {
        return Ok(value.to_string());
    }
    let Ok(entries) = std::fs::read_dir(sandbox) else {
        return Err(format!(
            "No file matching '{value}.*' found — please specify the filename."
        ));
    };
    let mut matches: Vec<String> = entries
        .flatten()
        .filter(|e| e.path().is_file())
        .filter_map(|e| e.file_name().to_str().map(String::from))
        .filter(|n| !n.starts_with('.'))
        .filter(|n| {
            n.strip_prefix(value)
                .is_some_and(|rest| rest.starts_with('.') && rest.len() > 1)
        })
        .collect();
    match matches.len() {
        0 => Err(format!(
            "No file matching '{value}.*' found — please specify the filename."
        )),
        1 => Ok(matches.remove(0)),
        _ => {
            matches.sort();
            Err(format!(
                "'{value}' matches multiple files: {} — please specify which one.",
                matches.join(", ")
            ))
        }
    }
}

/// Substitute extension-less filename stems in a step's path-bearing args.
///
/// Native rendered `-i clip_4k` verbatim and let ffmpeg fail on a missing file, where Python
/// resolves the stem to `clip_4k.mp4` — or asks which file was meant when the sandbox cannot
/// decide. Measured on the 2026-09-11 L4 re-run: the second largest native-only failure class
/// after globs, and **the more serious of the two**, because native was acting on an ambiguous
/// reference where Python asked (N2).
pub fn resolve_stems(args: &serde_json::Value, sandbox: &Path) -> StemOutcome {
    let Some(obj) = args.as_object() else {
        return StemOutcome::Resolved(args.clone());
    };
    let mut out = obj.clone();
    for key in STEM_PATH_ARG_KEYS {
        let Some(val) = obj.get(*key) else { continue };
        match val {
            serde_json::Value::String(s) => match resolve_one_stem(s, sandbox) {
                Ok(r) => {
                    out.insert((*key).to_string(), serde_json::Value::String(r));
                }
                Err(q) => return StemOutcome::Clarify(q),
            },
            serde_json::Value::Array(items) => {
                let mut resolved = Vec::with_capacity(items.len());
                for item in items {
                    match item {
                        serde_json::Value::String(s) => match resolve_one_stem(s, sandbox) {
                            Ok(r) => resolved.push(serde_json::Value::String(r)),
                            Err(q) => return StemOutcome::Clarify(q),
                        },
                        other => resolved.push(other.clone()),
                    }
                }
                out.insert((*key).to_string(), serde_json::Value::Array(resolved));
            }
            _ => {}
        }
    }
    StemOutcome::Resolved(serde_json::Value::Object(out))
}

#[cfg(test)]
mod stem_tests {
    use super::*;

    /// Ground truth captured by running Python's `planner.resolve_stems` over the same sandbox.
    fn sandbox() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("knaif-stems-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        for n in [
            "clip_4k.mp4",
            "clip.mp4",
            "clip.mov",
            "audio.mp3",
            ".hidden.mp4",
        ] {
            std::fs::write(dir.join(n), b"x").unwrap();
        }
        dir
    }

    fn run(args: serde_json::Value) -> StemOutcome {
        resolve_stems(&args, &sandbox())
    }

    #[test]
    fn a_unique_stem_resolves_to_the_filename() {
        assert_eq!(
            run(serde_json::json!({"input": "clip_4k"})),
            StemOutcome::Resolved(serde_json::json!({"input": "clip_4k.mp4"}))
        );
    }

    #[test]
    fn a_stem_with_no_match_asks_which_file() {
        match run(serde_json::json!({"input": "silent_clip"})) {
            StemOutcome::Clarify(q) => {
                assert_eq!(
                    q,
                    "No file matching 'silent_clip.*' found — please specify the filename."
                )
            }
            other => panic!("expected clarify, got {other:?}"),
        }
    }

    #[test]
    fn a_bare_word_is_not_a_stem() {
        // THE EASY ONE TO GET WRONG. `clip`, `mov`, `video` have no `_`, `-` or digit, so Python
        // does not treat them as stems at all — they pass through untouched. Resolving them would
        // turn `clip` into `clip.mp4` (or a clarify) where Python leaves it alone, which is a
        // divergence in the *opposite* direction from the bug being fixed.
        for word in ["clip", "mov", "video", "audio"] {
            assert_eq!(
                run(serde_json::json!({ "input": word })),
                StemOutcome::Resolved(serde_json::json!({ "input": word })),
                "{word} must pass through"
            );
        }
    }

    #[test]
    fn refs_globs_and_real_filenames_pass_through() {
        for value in ["$prev", "*.mp4", "clip.mp4"] {
            assert_eq!(
                run(serde_json::json!({ "input": value })),
                StemOutcome::Resolved(serde_json::json!({ "input": value })),
                "{value} must pass through"
            );
        }
    }

    #[test]
    fn a_list_resolves_each_entry() {
        assert_eq!(
            run(serde_json::json!({"inputs": ["clip_4k", "audio"]})),
            StemOutcome::Resolved(serde_json::json!({"inputs": ["clip_4k.mp4", "audio"]}))
        );
    }

    #[test]
    fn an_ambiguous_stem_lists_the_candidates() {
        let dir = std::env::temp_dir().join(format!("knaif-stems-amb-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        for n in ["take_1.mp4", "take_1.mov"] {
            std::fs::write(dir.join(n), b"x").unwrap();
        }
        match resolve_stems(&serde_json::json!({"input": "take_1"}), &dir) {
            StemOutcome::Clarify(q) => {
                assert!(q.contains("take_1.mov, take_1.mp4"), "{q}");
                assert!(q.contains("please specify which one"), "{q}");
            }
            other => panic!("expected clarify, got {other:?}"),
        }
        std::fs::remove_dir_all(&dir).ok();
    }
}
