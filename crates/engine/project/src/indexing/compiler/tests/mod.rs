//! The dedicated driver supplies a genuine application plan; ordinary workspace tests skip it.

use std::{fs, path::PathBuf};

use crate::{RustdocInput, indexing::compiler::CompilerImports};

#[test]
#[ignore = "requires the provenance-checked Devlist application plan"]
fn sup_004_rejects_wrong_application_target() {
    let path = std::env::var_os("RG_SUPRNOVA_PLAN").expect("acceptance plan must be supplied");
    let plan: serde_json::Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    let metadata: cargo_metadata::Metadata =
        serde_json::from_slice(&fs::read(plan["metadata"].as_str().unwrap()).unwrap()).unwrap();
    let workspace = rg_workspace::WorkspaceMetadata::lower(
        metadata,
        rg_cfg_eval::CfgOptions::from_rustc_cfg_output(plan["target_cfg"].as_str().unwrap()),
        rg_workspace::WorkspaceLoweringConfig::default(),
    )
    .unwrap();
    let wrong_target = RustdocInput {
        manifest_path: PathBuf::from(plan["manifest"].as_str().unwrap()),
        export_path: PathBuf::from(plan["export"].as_str().unwrap()),
        target_name: "absent_target".into(),
        target_kind: rg_workspace::TargetKind::Lib,
        item_path: "directory::models::user::User".into(),
    };
    let error = CompilerImports::read(&workspace, &[wrong_target])
        .expect_err("wrong application target must be rejected before indexing");
    assert!(format!("{error:#}").contains("absent_target"));
}
