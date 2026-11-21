//! Golden parity: `contracts/parity/known_folder_cases.json` through `knaif_core::known_folders`.
//! The Python side runs the identical fixtures (python/core/tests/test_known_folder_parity.py):
//! which known folder a path names, which requests name one as a destination (the gate of the
//! known-folder prompt examples), and how a plan's outputs are rewritten into them.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use knaif_core::known_folders::{named_folder, names_known_folder, rewrite_outputs, Rewrite};
use knaif_core::registry::load_registry_str;
use serde_json::Value;

fn fixtures() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../contracts/parity/known_folder_cases.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read fixtures")).unwrap()
}

#[test]
fn named_folders_match_the_contract() {
    for case in fixtures()["named"].as_array().unwrap() {
        let value = case["value"].as_str().unwrap();
        assert_eq!(named_folder(value), case["expected"].as_str(), "{value}");
    }
}

#[test]
fn destinations_match_the_contract() {
    for case in fixtures()["destination"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        assert_eq!(
            names_known_folder(text),
            case["expected"].as_bool().unwrap(),
            "{text}"
        );
    }
}

fn posix(p: &Path) -> String {
    p.to_string_lossy().replace('\\', "/")
}

fn fill(value: &Value, work: &Path, known: &Path) -> Value {
    match value {
        Value::String(s) => Value::String(
            s.replace("<work>", &posix(work))
                .replace("<known>", &posix(known)),
        ),
        Value::Array(a) => Value::Array(a.iter().map(|v| fill(v, work, known)).collect()),
        Value::Object(o) => Value::Object(
            o.iter()
                .map(|(k, v)| (k.clone(), fill(v, work, known)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn scratch_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir()
        .join("knaif-known-folder-parity")
        .join(format!("{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    dir
}

#[test]
fn rewrites_match_the_contract() {
    let registry = load_registry_str(
        "convert_video:\n  description: ''\n  required_args: [inputs]\n  optional_args: [container, output]\n  effects: []\n",
    )
    .unwrap();
    for case in fixtures()["rewrite"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let tmp = scratch_dir(name);
        let (work, known) = (tmp.join("work"), tmp.join("known"));
        std::fs::create_dir_all(&work).unwrap();
        for folder in case["folders"].as_array().unwrap() {
            std::fs::create_dir_all(work.join(folder.as_str().unwrap())).unwrap();
        }
        let sandbox = case.get("sandbox").and_then(Value::as_str).map(|s| {
            PathBuf::from(
                fill(&Value::String(s.into()), &work, &known)
                    .as_str()
                    .unwrap(),
            )
        });
        let env: HashMap<String, String> = HashMap::from([(
            "KNAIF_KNOWN_FOLDERS_ROOT".to_string(),
            known.to_string_lossy().into_owned(),
        )]);
        let plan: Vec<Value> = serde_json::from_value(case["plan"].clone()).unwrap();
        let got = rewrite_outputs(&plan, &registry, &work, sandbox.as_deref(), &env);
        if let Some(want) = case.get("expected_clarify") {
            let want = fill(want, &work, &known);
            match got {
                Rewrite::Ask(q) => assert_eq!(q, want.as_str().unwrap(), "{name}"),
                other => panic!("{name}: wanted a clarify, got {other:?}"),
            }
        } else if let Some(want) = case.get("expected_error") {
            match got {
                Rewrite::Refused(e) => {
                    assert!(e.contains(want.as_str().unwrap()), "{name}: {e:?}")
                }
                other => panic!("{name}: wanted a refusal, got {other:?}"),
            }
        } else {
            match got {
                Rewrite::Plan(p) => assert_eq!(
                    Value::Array(p),
                    fill(&case["expected_plan"], &work, &known),
                    "{name}"
                ),
                other => panic!("{name}: wanted a plan, got {other:?}"),
            }
        }
        let _ = std::fs::remove_dir_all(&tmp);
    }
}

#[test]
fn a_relative_root_is_made_absolute_where_it_is_read() {
    let env: HashMap<String, String> = HashMap::from([(
        "KNAIF_KNOWN_FOLDERS_ROOT".to_string(),
        "lane/r.known".to_string(),
    )]);
    let got = knaif_core::known_folders::resolve("downloads", &env, None);
    assert!(got.is_absolute(), "{got:?}");
    assert_eq!(
        got,
        std::env::current_dir()
            .unwrap()
            .join("lane")
            .join("r.known")
            .join("Downloads")
    );
}
