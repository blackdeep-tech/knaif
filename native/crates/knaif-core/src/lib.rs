//! knaif-core — the deterministic runtime contract.
//!
//! Target responsibility (mirrors the Python `knaif` package): plan-envelope parsing,
//! registry loading, prompt construction, validation, variable binding, safety gates,
//! optimizer, and the shared step library. This Phase 4 skeleton implements only skill
//! discovery/metadata (`skills` module); the rest is ported in Phase 6.

pub mod clarify_gate;
pub mod deps;
pub mod extract;
pub mod planner;
pub mod prompt;
pub mod registry;
pub mod retrieval;
pub mod safety;
pub mod skills;

pub use clarify_gate::{apply_clarify_gate, hallucinated_filename, output_capable_tools};
pub use deps::{
    detect_skill_deps, load_external_tools, missing_required_message, parse_external_tools,
    unmet_required, ExternalTool, InstallHints, ToolStatus,
};
pub use extract::{extract_json, ExtractedJson};
pub use planner::{
    apply_defaults, normalize_plan, optimize_plan, parse_plan, resolve_args, validate_plan,
    validate_step,
};
pub use prompt::{build_prompt, load_prompt_yaml, validator_feedback_prompt, PromptOverrides};
pub use registry::{load_registry, ArgSchema, Registry, ToolDef};
pub use retrieval::retrieve_tools;
pub use safety::{is_unsafe_request, load_unsafe_phrases};
pub use skills::{list_skills, resolve_skills_root, SkillMeta};
