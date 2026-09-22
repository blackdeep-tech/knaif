//! Golden parity: run `contracts/parity/planner_cases.json` through the Rust deterministic
//! pipeline (parse → normalize → apply_defaults → validate) and assert the recorded
//! valid/invalid outcome + error substring. The Python side runs the identical fixtures
//! (python/core/tests/test_planner_parity.py); both must agree.

use std::path::Path;

use knaif_core::registry::load_registry_str;
use knaif_core::{apply_defaults, normalize_plan, parse_plan, validate_plan};
use serde_json::Value;

#[test]
fn planner_parity_cases() {
    let fixtures =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/planner_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();
    let registries = doc["registries"].as_object().unwrap();
    let cwd = std::env::current_dir().unwrap();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = registries[case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry =
            load_registry_str(reg_yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));

        let plan_str = serde_json::to_string(&case["plan"]).unwrap();
        let mut payload = parse_plan(&plan_str).unwrap_or_else(|e| panic!("{name}: parse {e}"));
        normalize_plan(&mut payload, Some(&registry));
        apply_defaults(&mut payload, &registry);
        let result = validate_plan(&payload, &registry, &cwd, None);

        let expect_valid = case["valid"].as_bool().unwrap();
        assert_eq!(result.is_ok(), expect_valid, "case {name}: got {result:?}");
        if !expect_valid {
            if let Some(sub) = case.get("error_contains").and_then(Value::as_str) {
                let err = result.unwrap_err().to_string();
                assert!(
                    err.contains(sub),
                    "case {name}: error {err:?} missing {sub:?}"
                );
            }
        }
    }
}

/// L1b: the retrieval-parity contract. Same utterance + registry -> the same tools in the
/// same order as the reference runtime produced them.
///
/// **Green since V1 (2026-09-10).** Authored red and verified red first: `retrieve_tools`
/// returned a `BTreeMap<String, &ToolDef>`, sorted by name, so it could not represent a
/// relevance ranking at all — the failure read `["clarify", "compress_video", "convert_video",
/// "done", ...]` against the reference's relevance order. The scoring itself was already a
/// faithful port; only the return type threw the answer away.
///
/// See docs/plans/2026-09-10-skill-quality-lifecycle.md (L1b, V1).
#[test]
fn retrieval_parity_cases() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/retrieval_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();
    let registries = doc["registries"].as_object().unwrap();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = registries[case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry =
            load_registry_str(reg_yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));

        let top_k = case["top_k"].as_u64().unwrap() as usize;
        let min_score = case["min_score"].as_f64().unwrap();
        let selected = knaif_core::retrieve_tools(
            case["query"].as_str().unwrap(),
            &registry,
            top_k,
            min_score,
        );
        let got: Vec<&str> = selected.iter().map(|(n, _)| n.as_str()).collect();
        let want: Vec<&str> = case["expected_order"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(got, want, "case {name}: retrieval order differs");
    }
}

/// L2: the clarify-gate parity contract. Given the same `(utterance, plan, registry)` both
/// runtimes must produce the same post-gate payload — the plan with chain intermediates bound
/// to their producer, or a single clarify step.
///
/// Deterministic, so this layer is either 100% or broken; and it matters more than its size
/// suggests, firing on roughly 17% of the ffmpeg corpus. Not `#[ignore]`d: unlike L1 this is
/// expected to pass, and any failure is a live divergence rather than pending work.
#[test]
fn clarify_gate_parity_cases() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/clarify_gate_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();
    let registries = doc["registries"].as_object().unwrap();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = registries[case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry =
            load_registry_str(reg_yaml).unwrap_or_else(|e| panic!("{name}: registry {e}"));
        let output_capable = knaif_core::clarify_gate::output_capable_tools(&registry);

        // The stem exemption is filesystem-dependent, so the case states the listing both
        // runtimes must be given; absent means an empty sandbox and the strict rule.
        let known_files: std::collections::HashSet<String> = case
            .get("sandbox_files")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(|s| s.to_lowercase())
                    .collect()
            })
            .unwrap_or_default();

        let got = knaif_core::clarify_gate::apply_clarify_gate(
            case["plan"].clone(),
            case["utterance"].as_str().unwrap(),
            &output_capable,
            &known_files,
        );
        assert_eq!(
            got, case["expected_payload"],
            "case {name}: post-gate payload differs"
        );
    }
}

/// L1e: the example-selection parity contract. Given the same `(examples, retrieved tools,
/// query)` both runtimes must select the same examples, in the same order, and render the
/// same block.
///
/// Authored red: native had no example selection at all — `load_prompt_yaml` rendered
/// `prompt.yaml`'s whole block once and every utterance got all of it, where the reference
/// sends five of ffmpeg's twenty-eight. This is the second half of the prompt divergence V1
/// closed for the tool listing, and the S3g factorial settled the direction: Python keeps
/// `select_examples`, Rust gains it.
///
/// See docs/plans/2026-09-10-skill-quality-lifecycle.md (V2, L1e).
#[test]
fn example_selection_parity_cases() {
    let fixtures =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/example_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();
    let sets = doc["example_sets"].as_object().unwrap();
    let max_tool_examples = doc["max_tool_examples"].as_u64().unwrap() as usize;

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let why = case["why"].as_str().unwrap_or("");
        let examples: Vec<knaif_core::PromptExample> =
            serde_json::from_value(sets[case["example_set"].as_str().unwrap()].clone()).unwrap();
        let retrieved: std::collections::HashSet<String> = case["retrieved"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();

        let selected = knaif_core::select_examples(
            &examples,
            &retrieved,
            case["query"].as_str().unwrap(),
            max_tool_examples,
        );
        let got: Vec<&str> = selected.iter().map(|e| e.request.as_str()).collect();
        let want: Vec<&str> = case["expected_requests"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(got, want, "case {name}: {why}");

        // Rule 2: the stage is selection *and* rendering — the block is what reaches the model.
        let block = (!selected.is_empty()).then(|| knaif_core::render_examples_block(&selected));
        let want_block = case["expected_block"].as_str().map(str::to_string);
        assert_eq!(block, want_block, "case {name}: rendered block differs");
    }
}

/// The same contract's golden over the real skill bundles: real `prompt.yaml` examples, real
/// retrieval, real utterance — the block the shipped binary actually sends.
#[test]
fn example_selection_matches_the_shipped_bundles() {
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let fixtures = repo.join("contracts/parity/example_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();

    for row in doc["shipped"].as_array().unwrap() {
        let skill = row["skill"].as_str().unwrap();
        let utterance = row["utterance"].as_str().unwrap();
        let bundle = repo.join("skills").join(skill);

        let overrides = knaif_core::load_prompt_yaml(&bundle.join("prompt.yaml"));
        assert_eq!(
            overrides.examples.len(),
            row["corpus_size"].as_u64().unwrap() as usize,
            "{skill}: prompt.yaml example count moved; regenerate the contract"
        );

        let mut registry = knaif_core::load_registry(&bundle.join("tools.yaml")).unwrap();
        registry.extend(
            knaif_core::load_registry(&repo.join("contracts/runtime/core_tools.yaml")).unwrap(),
        );
        let retrieved =
            knaif_core::retrieve_tools(utterance, &registry, knaif_core::DEFAULT_TOP_K, 0.0);
        let mut names: Vec<String> = retrieved
            .iter()
            .filter(|(_, d)| !d.internal)
            .map(|(n, _)| n.clone())
            .collect();
        names.sort();
        let want_names: Vec<String> = row["retrieved"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        assert_eq!(
            names, want_names,
            "{skill}/{utterance}: retrieved set differs"
        );

        let set: std::collections::HashSet<String> = names.into_iter().collect();
        let selected = knaif_core::select_examples(
            &overrides.examples,
            &set,
            utterance,
            knaif_core::MAX_TOOL_EXAMPLES,
        );
        let got: Vec<&str> = selected.iter().map(|e| e.request.as_str()).collect();
        let want: Vec<&str> = row["expected_requests"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(got, want, "{skill}/{utterance}: selection differs");
        assert_eq!(
            knaif_core::render_examples_block(&selected),
            row["expected_block"].as_str().unwrap(),
            "{skill}/{utterance}: rendered block differs"
        );
    }
}

/// L2: the arg-shape-gate parity contract. Given the same `(plan, registry)` both runtimes
/// must produce the same clarify payload, or neither must.
///
/// Both gates were Python-only until 2026-09-15, and the L4 lane priced the gap: ffmpeg's
/// `extract_audio` slice measured 0.872 on the native binary against 0.949 in Python, one row
/// of which was exactly `Tool 'adjust_volume' has unsupported args: [target_sample_rate]`
/// erroring here where Python clarified. Deterministic, so this is either 100% or broken.
///
/// The two gates are asserted separately: they are not composed, they sit on opposite sides of
/// validation, and they carry different retry semantics (see the contract's `_stage`).
#[test]
fn arg_gate_parity_cases() {
    let fixtures =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts/parity/arg_gate_cases.json");
    let doc: Value =
        serde_json::from_str(&std::fs::read_to_string(&fixtures).expect("read fixtures")).unwrap();
    let registries = doc["registries"].as_object().unwrap();

    for case in doc["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let reg_yaml = registries[case["registry"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let registry = load_registry_str(reg_yaml).expect("registry");
        let plan = case["plan"].clone();

        let actual = match case["gate"].as_str().unwrap() {
            "required" => knaif_core::required_args_clarify(&plan, &registry),
            "unsupported" => knaif_core::unsupported_args_clarify(&plan, &registry),
            other => panic!("case {name}: unknown gate {other:?}"),
        };
        let expected = &case["expected"];
        match (actual, expected) {
            (None, Value::Null) => {}
            (Some(got), want) => assert_eq!(&got, want, "case {name}: gate output differs"),
            (None, want) => panic!("case {name}: gate did not fire; contract expects {want}"),
        }
    }
}

/// The shipped ffmpeg bundle's `quality` vocabulary, read by the runtime that ships it.
///
/// Two things are being proven, and neither is covered by the fixtures above — those build
/// their registries from inline YAML strings and so never touch `skills/ffmpeg/tools.yaml`.
///
/// 1. **The shared anchor resolves in serde_yaml.** `quality` is accepted by ten tools, and
///    the schema is written once under a `_shared_arg_schemas:` key and referenced with a
///    YAML anchor. Both loaders skip a top-level entry with no `description:`, so the block
///    declares no tool — but if serde_yaml handled the anchor differently from PyYAML, the
///    native side would silently see *no* schema and go back to accepting anything.
/// 2. **Both runtimes read the same vocabulary**, pinned to `profiles/quality/`. A profile is
///    loaded by filename, so an enum value with no file restores the original bug: a name
///    that passes validation and then fails deep in execution as `Unknown quality profile`.
///
/// Mirrors `test_the_vocabulary_is_exactly_the_profiles_on_disk` on the Python side.
#[test]
fn shipped_ffmpeg_quality_vocabulary_matches_the_profiles_on_disk() {
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let bundle = repo.join("skills/ffmpeg");
    let registry = knaif_core::registry::load_registry(&bundle.join("tools.yaml")).unwrap();

    let on_disk: std::collections::BTreeSet<String> =
        std::fs::read_dir(bundle.join("profiles/quality"))
            .expect("profiles/quality")
            .filter_map(|e| {
                let p = e.ok()?.path();
                (p.extension()? == "yaml").then(|| p.file_stem()?.to_str().map(str::to_string))?
            })
            .collect();
    assert!(!on_disk.is_empty(), "no quality profiles found");

    let mut checked = 0;
    for (name, tool) in &registry {
        if !tool.optional_args.iter().any(|a| a == "quality") || tool.internal {
            continue;
        }
        let schema = tool
            .arg_schemas
            .get("quality")
            .unwrap_or_else(|| panic!("{name}: no quality schema — did the anchor resolve?"));
        let declared: std::collections::BTreeSet<String> = schema
            .enum_values
            .as_ref()
            .unwrap_or_else(|| panic!("{name}: quality schema carries no enum"))
            .iter()
            .cloned()
            .collect();
        assert_eq!(
            declared, on_disk,
            "{name}: enum drifted from profiles/quality"
        );
        checked += 1;
    }
    assert_eq!(
        checked, 10,
        "expected ten model-facing tools to accept a quality"
    );

    // `load_quality_profile` stays open on purpose: expansion routes a CRF spelling
    // ("crf 20") through its quality slot, and a closed enum would reject knaif's own output.
    assert!(!registry["load_quality_profile"]
        .arg_schemas
        .contains_key("quality"));
}
