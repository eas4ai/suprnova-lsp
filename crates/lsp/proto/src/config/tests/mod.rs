use serde_json::json;

use super::EngineConfig;

#[test]
fn aut_001_defaults_to_automatic_discovery_without_explicit_inputs() {
    let config = EngineConfig::from_initialization_options(None).unwrap();
    let encoded = serde_json::to_value(config.analysis.rustdoc).unwrap();
    assert_eq!(encoded["inputs"], json!([]));
    assert_eq!(encoded["automatic"]["enabled"], json!(true));
    assert_eq!(encoded["automatic"]["debounceMs"], json!(2000));
    assert_eq!(
        encoded["automatic"]["toolchain"],
        json!("nightly-2026-08-19")
    );
    assert_eq!(encoded["automatic"]["timeoutMs"], json!(900000));
    assert_eq!(encoded["automatic"]["jobs"], json!(2));
}

#[test]
fn aut_002_preserves_worker_policy_and_explicit_disable() {
    let policy = json!({"enabled": false, "debounceMs": 3500,
        "toolchain": "nightly-2026-08-19", "timeoutMs": 45000,
        "jobs": 1, "artifactRoot": "/worker/owned artifacts"});
    let options = json!({"rustdoc": {"automatic": policy}});
    let config = EngineConfig::from_initialization_options(Some(&options)).unwrap();
    let encoded = serde_json::to_value(config.analysis.rustdoc).unwrap();
    assert_eq!(encoded["automatic"], policy);
}

#[test]
fn aut_003_rejects_malformed_worker_policy_instead_of_ignoring_it() {
    for invalid in [json!(null), json!([]), json!("enabled")] {
        let options = json!({"rustdoc": {"automatic": invalid}});
        assert!(
            EngineConfig::from_initialization_options(Some(&options)).is_err(),
            "malformed worker section silently accepted: {options}"
        );
    }
    for (field, invalids) in [
        ("enabled", vec![json!(null), json!("true"), json!(1)]),
        (
            "debounceMs",
            vec![
                json!(null),
                json!("2000"),
                json!(0),
                json!(-1),
                json!(1.5),
                json!(u64::MAX),
            ],
        ),
        (
            "timeoutMs",
            vec![
                json!(null),
                json!("900000"),
                json!(0),
                json!(-1),
                json!(1.5),
                json!(u64::MAX),
            ],
        ),
        (
            "jobs",
            vec![
                json!(null),
                json!("2"),
                json!(0),
                json!(-1),
                json!(1.5),
                json!(u64::MAX),
            ],
        ),
        (
            "toolchain",
            vec![json!(null), json!(3), json!(""), json!("  ")],
        ),
        ("artifactRoot", vec![json!(3), json!(""), json!("  ")]),
    ] {
        for invalid in invalids {
            let options = json!({"rustdoc": {"automatic": {field: invalid}}});
            let error = EngineConfig::from_initialization_options(Some(&options))
                .expect_err("malformed worker field silently accepted");
            assert!(format!("{error:#}").contains(field), "{error:#}");
        }
    }
}

#[test]
fn edt_001_preserves_explicit_input_identity() {
    for kind in ["lib", "bin"] {
        let options = json!({"rustdoc": {"inputs": [{
            "workspaceRoot": "/workspace/application", "manifestPath": "Cargo.toml",
            "targetName": "directory", "targetKind": kind,
            "exportPath": "/captures/user.json", "itemPath": "directory::models::user::User"
        }]}});
        let config = EngineConfig::from_initialization_options(Some(&options)).unwrap();
        let encoded = serde_json::to_string(&config).unwrap();
        assert!(
            encoded.to_lowercase().contains(&format!("\"{kind}\"")),
            "target kind lost: {encoded}"
        );
        for identity in [
            "/workspace/application",
            "Cargo.toml",
            "directory",
            "/captures/user.json",
            "directory::models::user::User",
        ] {
            assert!(
                encoded.contains(identity),
                "input identity lost: {identity}: {encoded}"
            );
        }
    }
}

#[test]
fn edt_001_rejects_malformed_entries_instead_of_dropping_them() {
    for section in [
        json!(null),
        json!([]),
        json!({"inputs": null}),
        json!({"inputs": "wrong"}),
        json!({"inputs": [null]}),
        json!({"inputs": [{}]}),
        json!({"inputs": [{"workspaceRoot": "/app", "manifestPath": "Cargo.toml", "targetName": "app", "targetKind": "proc-macro", "exportPath": "export.json", "itemPath": "app::Post"}]}),
    ] {
        let options = json!({"rustdoc": section});
        assert!(
            EngineConfig::from_initialization_options(Some(&options)).is_err(),
            "malformed section silently accepted: {options}"
        );
    }
    let valid = json!({"workspaceRoot": "/app", "manifestPath": "Cargo.toml", "targetName": "app",
        "targetKind": "lib", "exportPath": "export.json", "itemPath": "app::Post"});
    for field in [
        "workspaceRoot",
        "manifestPath",
        "targetName",
        "targetKind",
        "exportPath",
        "itemPath",
    ] {
        for malformed in [None, Some(json!(42)), Some(json!(""))] {
            let mut input = valid.clone();
            if let Some(value) = malformed {
                input[field] = value;
            } else {
                input.as_object_mut().unwrap().remove(field);
            }
            let options = json!({"rustdoc": {"inputs": [input]}});
            assert!(
                EngineConfig::from_initialization_options(Some(&options)).is_err(),
                "malformed identity silently accepted: {options}"
            );
        }
    }
}

#[test]
fn parses_engine_configuration() {
    let options = json!({
        "cfg": {
            "test": false,
        },
        "diagnostics": {
            "onSave": true,
        },
    });

    let config = EngineConfig::from_initialization_options(Some(&options))
        .expect("engine config should parse");

    assert!(!config.analysis.cfg.test);
    assert!(config.diagnostics.on_save);
}
