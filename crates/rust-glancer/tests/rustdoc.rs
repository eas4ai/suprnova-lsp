use std::{path::Path, process::Command};

const EXPORT: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../engine/rustdoc/fixtures/model/export.json"
);

#[test]
fn inspects_real_macro_api_without_starting_a_language_server() {
    let output = Command::new(env!("CARGO_BIN_EXE_suprnova-lsp"))
        .arg("inspect-rustdoc")
        .arg(EXPORT)
        .args(["--item", "rustdoc_macro_support::Post"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        report["path"],
        serde_json::json!(["rustdoc_macro_support", "Post"])
    );
    assert_eq!(report["format_version"], 61);
    assert_eq!(report["impls"].as_array().unwrap().len(), 6);
    assert!(report["target"].as_str().unwrap().contains("linux"));
    assert!(
        report["limitations"][0]
            .as_str()
            .unwrap()
            .contains("Hidden-item coverage is unknown")
    );
    assert!(
        report["limitations"][1]
            .as_str()
            .unwrap()
            .contains("Blanket")
    );
    assert!(report["excluded_blanket_impls"].as_u64().unwrap() > 0);
    assert!(
        report["type_paths"]
            .as_object()
            .unwrap()
            .values()
            .any(|summary| {
                summary["path"] == serde_json::json!(["rustdoc_macro_support", "Builder"])
            })
    );
    let names = report["impls"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|implementation| implementation["associated_items"].as_array().unwrap())
        .filter_map(|item| item["name"].as_str())
        .collect::<Vec<_>>();
    assert!(names.contains(&"query"));
    assert!(names.contains(&"generated_method"));
    assert!(output.stderr.is_empty());
}

#[test]
fn reports_unknown_type_without_emitting_a_partial_report() {
    let output = Command::new(env!("CARGO_BIN_EXE_suprnova-lsp"))
        .arg("inspect-rustdoc")
        .arg(EXPORT)
        .args(["--item", "rustdoc_macro_support::Missing"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("fully qualified path"));
}

#[test]
fn reports_missing_export_with_its_path() {
    let missing = Path::new(EXPORT).with_file_name("missing-export.json");
    let output = Command::new(env!("CARGO_BIN_EXE_suprnova-lsp"))
        .arg("inspect-rustdoc")
        .arg(&missing)
        .args(["--item", "rustdoc_macro_support::Post"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("missing-export.json"));
}
