use serde_json::json;

use super::EngineConfig;

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
