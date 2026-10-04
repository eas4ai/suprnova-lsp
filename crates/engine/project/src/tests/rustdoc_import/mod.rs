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
    fn new(default_method: bool) -> Self {
        let root = Path::new(if default_method { DEFAULT_MODEL } else { MODEL });
        let mut spec = String::new();
        for path in [
            "Cargo.toml",
            "src/lib.rs",
            "macros/Cargo.toml",
            "macros/src/lib.rs",
        ] {
            spec.push_str(&format!("\n//- /{path}\n"));
            spec.push_str(&fs::read_to_string(root.join(path)).expect("compiler fixture exists"));
            if path == "src/lib.rs" {
                spec.push_str(PROBES);
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
    let fixture = Fixture::new(false);
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
    let fixture = Fixture::new(false);
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
    let fixture = Fixture::new(true);
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
    let fixture = Fixture::new(true);
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
    let fixture = Fixture::new(false);
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
    let fixture = Fixture::new(false);
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
fn mac_004_source_artifact_before_import() {
    let fixture = Fixture::new(false);
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
    let fixture = Fixture::new(false);
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
    let fixture = Fixture::new(false);
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
    let fixture = Fixture::new(false);
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
    let fixture = Fixture::new(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let marker = fixture.source.path("rustdoc");
        fs::write(&marker, "#!/bin/sh\nexit 0\n").unwrap();
        fs::set_permissions(&marker, fs::Permissions::from_mode(0o700)).unwrap();
        assert!(std::process::Command::new(&marker).status().unwrap().success());
    }

    let project = fixture
        .build(
            Some(fixture.input()),
            IndexingPerformancePreference::FasterBuilds,
        )
        .expect("valid candidate builds without compiler servers");
    fixture.assert_type(&project, "query", "expected_query");
}
