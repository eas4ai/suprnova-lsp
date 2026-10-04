//! Acceptance queries use the original macro source and genuine compiler exports.

use std::{fs, path::Path};

use rg_ir_view::ty::IndexedType;
use rg_std::CancellationToken;

use crate::{
    IndexingPerformancePreference, PackageResidencyPolicy, Project, RustdocInput, StartupCacheLoad,
    testonly::{ProjectFixture, ProjectSourceFixture},
};

const MODEL: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../rustdoc/fixtures/model");
const DEFAULT_MODEL: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../rustdoc/fixtures/model-default"
);

const CHILD_MODEL: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../rustdoc/fixtures/child-model"
);

const PROBES: &str = r#"
pub fn acceptance(post: Post) {
    let query$query$ = Post::query();
    let expected_query$expected_query$: Builder<Post> = loop {};
    let generic$generic$ = post.generated_method(7u64);
    let expected_generic$expected_generic$: Builder<u64> = loop {};
    let key$key$: <Post as Model>::Key = loop {};
    let expected_key$expected_key$: u64 = 7u64;
    let source$source$ = post.source_method();
    let expected_source$expected_source$: u64 = 7u64;
    let other$other$ = other::Post.generated_method(7u64);
}
"#;

struct Fixture {
    source: ProjectSourceFixture,
    export_path: std::path::PathBuf,
}

impl Fixture {
    fn new(root: &str, source_method: Option<&str>) -> Self {
        let child_model = root == CHILD_MODEL;
        let root = Path::new(root);
        let mut spec = String::new();
        for path in [
            "Cargo.toml",
            "src/lib.rs",
            "macros/Cargo.toml",
            "macros/src/lib.rs",
        ] {
            spec.push_str(&format!("\n//- /{path}\n"));
            let mut text = fs::read_to_string(root.join(path)).expect("compiler fixture exists");
            if path == "src/lib.rs"
                && let Some(source_method) = source_method
            {
                text = text.replace(
                    "impl Post {\n    pub fn source_method(&self) -> u64 {\n        self.id\n    }\n}",
                    source_method,
                );
            }
            spec.push_str(&text);
            if path == "src/lib.rs" {
                spec.push_str(PROBES);
                if child_model {
                    spec.push_str(
                        r#"
pub fn child_acceptance() {
    let entity$child_entity$: <Post as Model>::Entity = loop {};
    let expected_entity$expected_child_entity$: Entity = loop {};
    let column$child_column$: <Post as Model>::Column = loop {};
    let expected_column$expected_child_column$: Column = loop {};
    let key$child_key$: <Post as Model>::Key = loop {};
    let expected_key$expected_child_key$: i64 = 0;
    let storage$child_storage$: generated::Storage = loop {};
    let field$child_field$ = storage.id;
    let expected_field$expected_child_field$: i64 = 0;
}
"#,
                    );
                }
            }
        }
        // Cargo targets and a second package exercise identities that share only a short name.
        spec.push_str(
            r#"
//- /src/main.rs
pub struct Post;
impl Post { pub fn source_method(&self) -> u64 { 0 } }
fn main() {
    let value$target$ = Post.generated_method(7u64);
}
//- /sibling/Cargo.toml
[package]
name = "sibling"
version = "0.1.0"
edition = "2024"
//- /sibling/src/lib.rs
pub struct Post;
pub fn use_it() { let value$package$ = Post.generated_method(7u64); }
"#,
        );
        spec = spec.replace(
            "members = [\"macros\"]",
            "members = [\"macros\", \"sibling\"]",
        );
        let source = ProjectSourceFixture::build(&spec);
        let export_path = source.path("export.json");
        fs::copy(root.join("export.json"), &export_path).expect("copy genuine compiler export");
        Self {
            source,
            export_path,
        }
    }

    fn input(&self) -> RustdocInput {
        RustdocInput {
            manifest_path: self.source.path("Cargo.toml"),
            target_name: "rustdoc_macro_support".into(),
            target_kind: rg_workspace::TargetKind::Lib,
            export_path: self.export_path.clone(),
            item_path: "rustdoc_macro_support::Post".into(),
        }
    }

    fn build(
        &self,
        input: Option<RustdocInput>,
        preference: IndexingPerformancePreference,
    ) -> anyhow::Result<Project> {
        Project::builder(self.source.workspace_metadata())
            .rustdoc_inputs(input.into_iter().collect())
            .indexing_preference(preference)
            .package_residency_policy(PackageResidencyPolicy::AllOffloadable)
            .startup_cache_load(StartupCacheLoad::default())
            .build()
    }

    fn ty(&self, project: &Project, marker: &str) -> Option<IndexedType> {
        let position = self.source.markers().position(marker);
        let snapshot = project.snapshot();
        let contexts = snapshot
            .file_contexts_for_path(self.source.path(&position.path))
            .expect("acceptance source has a file context");
        assert_eq!(contexts.len(), 1, "fixture path has one package owner");
        let context = &contexts[0];
        assert_eq!(context.crates.len(), 1, "fixture path has one crate target");
        let target = context.crates[0];
        snapshot
            .analysis_for_crates(&[target], CancellationToken::new())
            .expect("acceptance analysis materializes")
            .type_at(target, context.file, position.offset.saturating_sub(1))
            .expect("acceptance type query succeeds")
    }

    fn assert_type(&self, project: &Project, actual: &str, expected: &str) {
        let expected_ty = self
            .ty(project, expected)
            .expect("written expected type resolves");
        assert!(
            expected_ty.primitive().is_some() || expected_ty.nominal_type_defs().next().is_some(),
            "expected type must be concrete: {expected_ty:?}"
        );
        assert_eq!(
            self.ty(project, actual),
            Some(expected_ty),
            "wrong imported type at {actual}"
        );
    }

    fn assert_unknown(&self, project: &Project, marker: &str) {
        let ty = self.ty(project, marker);
        assert!(
            ty.as_ref().is_none_or(
                |ty| ty.primitive().is_none() && ty.nominal_type_defs().next().is_none()
            ),
            "generated API leaked at {marker}: {ty:?}"
        );
    }
}

#[test]
fn mac_001_initial_query() {
    let fixture = Fixture::new(MODEL, None);
    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("valid candidate builds");
    fixture.assert_type(&project, "query", "expected_query");
}

#[test]
fn mac_001_batched_query() {
    let fixture = Fixture::new(MODEL, None);
    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::LowerPeakMemory,
        )
        .expect("valid batched candidate builds");
    fixture.assert_type(&project, "query", "expected_query");
}

#[test]
fn mac_001_trait_default() {
    let fixture = Fixture::new(DEFAULT_MODEL, None);
    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("impl-only candidate builds");
    fixture.assert_type(&project, "query", "expected_query");
}

#[test]
fn mac_001_batched_trait_default() {
    let fixture = Fixture::new(DEFAULT_MODEL, None);
    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::LowerPeakMemory,
        )
        .expect("impl-only batched candidate builds");
    fixture.assert_type(&project, "query", "expected_query");
}

#[test]
fn mac_002_generic_and_associated_type() {
    let fixture = Fixture::new(MODEL, None);
    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("valid candidate builds");
    fixture.assert_type(&project, "generic", "expected_generic");
    fixture.assert_type(&project, "key", "expected_key");
}

#[test]
fn mac_003_owner_namespaces_and_overlap() {
    let fixture = Fixture::new(MODEL, None);
    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("valid candidate builds");
    // A value named Post shares its spelling with the selected record type.
    fixture.assert_type(&project, "generic", "expected_generic");
    fixture.assert_type(&project, "source", "expected_source");
    for marker in ["other", "target", "package"] {
        fixture.assert_unknown(&project, marker);
    }
}

#[test]
fn imported_methods_do_not_duplicate_associated_macro_output() {
    let fixture = Fixture::new(
        MODEL,
        Some(
            r#"
macro_rules! leaf_methods {
    () => { pub fn source_method(&self) -> u64 { self.id } };
}

macro_rules! methods { () => { leaf_methods!(); }; }
impl Post { methods!(); }
"#,
        ),
    );
    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("source macro overlap builds");
    fixture.assert_type(&project, "source", "expected_source");
    fixture.assert_type(&project, "query", "expected_query");
}

#[test]
fn imported_methods_keep_conditional_impl_bounds() {
    let root = Path::new(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../rustdoc/fixtures/model-bounded"
    ));
    let mut spec = String::new();
    for path in [
        "Cargo.toml",
        "src/lib.rs",
        "macros/Cargo.toml",
        "macros/src/lib.rs",
    ] {
        spec.push_str(&format!("\n//- /{path}\n"));
        spec.push_str(&fs::read_to_string(root.join(path)).unwrap());
        if path == "src/lib.rs" {
            spec.push_str(
                r#"
pub fn acceptance(allowed: Post<Allowed>, denied: Post<Denied>) {
    let good$good$ = allowed.gated();
    let bad$bad$ = denied.gated();
    let source$source$ = denied.source_method();
    let expected$expected$: u64 = 7u64;
}
"#,
            );
        }
    }
    let source = ProjectSourceFixture::build(&spec);
    let export_path = source.path("export.json");
    fs::copy(root.join("export.json"), &export_path).unwrap();
    let input = RustdocInput {
        manifest_path: source.path("Cargo.toml"),
        target_name: "rustdoc_bounded".into(),
        target_kind: rg_workspace::TargetKind::Lib,
        export_path: export_path.clone(),
        item_path: "rustdoc_bounded::Post".into(),
    };
    let fixture = Fixture {
        source,
        export_path,
    };
    let project = fixture
        .build(Some(input), IndexingPerformancePreference::FasterBuilds)
        .expect("bounded generated impl builds");
    fixture.assert_type(&project, "good", "expected");
    fixture.assert_type(&project, "source", "expected");
    fixture.assert_unknown(&project, "bad");
}

#[test]
fn mac_004_source_artifact_before_import() {
    let fixture = Fixture::new(MODEL, None);
    let baseline = fixture
        .build(None, IndexingPerformancePreference::FasterBuilds)
        .expect("source-only candidate builds");
    fixture.assert_unknown(&baseline, "query");
    // AllOffloadable produces package backing before a new project probes the same cache.
    let package = ProjectFixture::package_slot_by_name_in(
        baseline.snapshot().parse_db(),
        "rustdoc_macro_support",
    );
    let header = baseline
        .state
        .cache_plan
        .artifact_header(package, &baseline.state.package_source_fingerprints)
        .expect("baseline has an artifact header");
    assert!(
        baseline
            .state
            .cache_store
            .package_artifact_path(&header.package)
            .exists(),
        "source artifact must exist before the import"
    );
    drop(baseline);
    let imported = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("import candidate builds");
    fixture.assert_type(&imported, "query", "expected_query");
}

#[test]
fn mac_004_import_does_not_leak_into_source_artifact() {
    let fixture = Fixture::new(MODEL, None);
    let imported = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("import candidate builds");
    fixture.assert_type(&imported, "query", "expected_query");
    drop(imported);
    let source_only = fixture
        .build(None, IndexingPerformancePreference::FasterBuilds)
        .expect("source-only candidate builds");
    fixture.assert_unknown(&source_only, "query");
}

#[test]
fn mac_005_invalid_candidate_preserves_previous_project() {
    let fixture = Fixture::new(MODEL, None);
    let previous = fixture
        .build(None, IndexingPerformancePreference::FasterBuilds)
        .expect("previous source generation builds");
    fixture.assert_type(&previous, "source", "expected_source");
    let pristine = fs::read(&fixture.export_path).expect("export reads");
    let original: serde_json::Value = serde_json::from_slice(&pristine).expect("export parses");
    let mut wrong_format = original.clone();
    wrong_format["format_version"] = 0.into();
    fs::write(
        &fixture.export_path,
        serde_json::to_vec(&wrong_format).unwrap(),
    )
    .unwrap();
    let error = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect_err("unsupported schema must reject a candidate");
    assert!(format!("{error:#}").contains("unsupported rustdoc JSON format"));
    fixture.assert_type(&previous, "source", "expected_source");

    let mut missing_signature = original;
    let builder_id = missing_signature["paths"]
        .as_object()
        .unwrap()
        .iter()
        .find(|(_, summary)| {
            summary["path"] == serde_json::json!(["rustdoc_macro_support", "Builder"])
        })
        .map(|(id, _)| id.clone())
        .expect("export contains Builder");
    missing_signature["paths"]
        .as_object_mut()
        .unwrap()
        .remove(&builder_id);
    fs::write(
        &fixture.export_path,
        serde_json::to_vec(&missing_signature).unwrap(),
    )
    .unwrap();
    let error = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect_err("unresolved signature must reject a candidate");
    assert!(format!("{error:#}").contains("rustdoc"));
    fixture.assert_type(&previous, "source", "expected_source");

    fs::write(&fixture.export_path, pristine).unwrap();
    let mut wrong_owner = fixture.input();
    wrong_owner.target_name = "absent_target".into();
    let error = match fixture.build(
        Some(wrong_owner),
        IndexingPerformancePreference::FasterBuilds,
    ) {
        Ok(_) => panic!("unmappable owner must reject a candidate"),
        Err(error) => error,
    };
    assert!(format!("{error:#}").contains("absent_target"));
    fixture.assert_type(&previous, "source", "expected_source");
}

#[test]
fn mac_005_previous_import_survives_failed_candidate() {
    let fixture = Fixture::new(MODEL, None);
    let previous = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("previous imported generation builds");
    fixture.assert_type(&previous, "query", "expected_query");
    let mut invalid: serde_json::Value =
        serde_json::from_slice(&fs::read(&fixture.export_path).unwrap()).unwrap();
    invalid["format_version"] = 0.into();
    fs::write(&fixture.export_path, serde_json::to_vec(&invalid).unwrap()).unwrap();
    assert!(
        fixture
            .build(
                Some(fixture.input()),
                IndexingPerformancePreference::FasterBuilds
            )
            .is_err()
    );
    fixture.assert_type(&previous, "query", "expected_query");
}

#[test]
fn mac_006_query_without_compiler_servers() {
    let fixture = Fixture::new(MODEL, None);
    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("valid candidate builds without compiler servers");
    fixture.assert_type(&project, "query", "expected_query");
}

#[test]
fn imported_declarations_survive_cache_recovery_without_rereading_export() {
    let fixture = Fixture::new(MODEL, None);
    let mut project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .unwrap();
    fs::remove_file(&fixture.export_path).unwrap();
    project
        .recover_after_cache_load_failure()
        .expect("captured declarations survive recovery");
    fixture.assert_type(&project, "query", "expected_query");
    fixture.assert_type(&project, "generic", "expected_generic");
    fixture.assert_type(&project, "key", "expected_key");
}

#[test]
fn rejects_signature_paths_missing_from_source() {
    let fixture = Fixture::new(MODEL, None);
    let mut export: serde_json::Value =
        serde_json::from_slice(&fs::read(&fixture.export_path).unwrap()).unwrap();
    for summary in export["paths"].as_object_mut().unwrap().values_mut() {
        if summary["path"] == serde_json::json!(["rustdoc_macro_support", "Builder"]) {
            summary["path"] = serde_json::json!(["rustdoc_macro_support", "MissingBuilder"]);
        }
    }
    fs::write(&fixture.export_path, serde_json::to_vec(&export).unwrap()).unwrap();
    let error = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect_err("unmappable signature rejects candidate");
    assert!(format!("{error:#}").contains("MissingBuilder"));
}

#[test]
fn rejects_empty_signature_path_without_changing_previous_generation() {
    let fixture = Fixture::new(MODEL, None);
    let previous = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .unwrap();
    let mut export: serde_json::Value =
        serde_json::from_slice(&fs::read(&fixture.export_path).unwrap()).unwrap();
    for summary in export["paths"].as_object_mut().unwrap().values_mut() {
        if summary["path"] == serde_json::json!(["rustdoc_macro_support", "Builder"]) {
            summary["path"] = serde_json::json!([]);
        }
    }
    fs::write(&fixture.export_path, serde_json::to_vec(&export).unwrap()).unwrap();
    let error = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect_err("empty signature paths reject a candidate without panicking");
    assert!(format!("{error:#}").contains("empty rustdoc signature path"));
    fixture.assert_type(&previous, "query", "expected_query");
}

#[test]
fn imported_declarations_survive_source_rebuild_without_rereading_export() {
    let fixture = Fixture::new(MODEL, None);
    let mut project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .unwrap();
    fs::remove_file(&fixture.export_path).unwrap();
    let path = fixture.source.path("src/lib.rs");
    let mut source = fs::read_to_string(&path).unwrap();
    source.push_str("\npub fn extra() {}\n");
    fs::write(&path, source).unwrap();
    project
        .apply_change(crate::SavedFileChange::fs_path(&path))
        .expect("captured declarations survive source rebuild");
    fixture.assert_type(&project, "query", "expected_query");
    fixture.assert_type(&project, "generic", "expected_generic");
    fixture.assert_type(&project, "key", "expected_key");
}

#[test]
fn compiler_child_nominals_supply_default_queries_reexports_and_fields() {
    for preference in [
        IndexingPerformancePreference::FasterBuilds,
        IndexingPerformancePreference::LowerPeakMemory,
    ] {
        let fixture = Fixture::new(CHILD_MODEL, None);
        let project = fixture
            .build(Some(fixture.input()), preference)
            .expect("child declarations reconcile");
        for (actual, expected) in [
            ("query", "expected_query"),
            ("source", "expected_source"),
            ("child_entity", "expected_child_entity"),
            ("child_column", "expected_child_column"),
            ("child_key", "expected_child_key"),
            ("child_field", "expected_child_field"),
        ] {
            fixture.assert_type(&project, actual, expected);
        }
        fixture.assert_unknown(&project, "other");
    }
}

#[test]
fn compiler_child_nominals_cannot_replace_missing_items_in_a_source_module() {
    let fixture = Fixture::new(
        CHILD_MODEL,
        Some("impl Post { pub fn source_method(&self) -> u64 { self.id } }\npub mod generated {}"),
    );
    let previous = fixture
        .build(None, IndexingPerformancePreference::FasterBuilds)
        .unwrap();
    let error = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .err()
        .unwrap();
    assert!(
        format!("{error:#}").contains("cannot replace a missing source declaration"),
        "{error:#}"
    );
    fixture.assert_type(&previous, "source", "expected_source");
}

#[test]
fn compiler_child_nominals_require_the_actual_parent_module_membership() {
    let fixture = Fixture::new(CHILD_MODEL, None);
    let previous = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .unwrap();
    let mut value: serde_json::Value =
        serde_json::from_slice(&fs::read(&fixture.export_path).unwrap()).unwrap();
    let id = value["paths"]
        .as_object()
        .unwrap()
        .iter()
        .find(|(_, summary)| {
            summary["kind"] == "struct"
                && summary["path"]
                    == serde_json::json!(["rustdoc_macro_support", "generated", "Entity"])
        })
        .unwrap()
        .0
        .parse::<u64>()
        .unwrap();
    let module = value["index"]
        .as_object_mut()
        .unwrap()
        .values_mut()
        .find(|item| item["name"] == "generated" && item["inner"].get("module").is_some())
        .unwrap();
    module["inner"]["module"]["items"]
        .as_array_mut()
        .unwrap()
        .retain(|item| item.as_u64() != Some(id));
    fs::write(&fixture.export_path, serde_json::to_vec(&value).unwrap()).unwrap();
    let error = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .err()
        .unwrap();
    assert!(
        format!("{error:#}").contains("not a child of its module"),
        "{error:#}"
    );
    fixture.assert_type(&previous, "query", "expected_query");
    fixture.assert_type(&previous, "child_entity", "expected_child_entity");
}
