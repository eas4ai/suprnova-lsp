//! Exercise configured compiler imports through the existing LSP service and saved lifecycle.

use std::{fs, path::Path};

use rg_lsp_proto::{EngineConfig, EngineService};
use serde_json::json;
use tarpc::context;

use super::{LspEngineFixture, QueryMarkers};

const MODEL: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../engine/rustdoc/fixtures/model-default"
);

struct EditorFixture {
    lsp: LspEngineFixture,
}

impl EditorFixture {
    fn new() -> Self {
        let mut spec = String::new();
        for path in [
            "Cargo.toml",
            "src/lib.rs",
            "macros/Cargo.toml",
            "macros/src/lib.rs",
        ] {
            let text = fs::read_to_string(Path::new(MODEL).join(path)).unwrap();
            spec.push_str(&format!("\n//- /{path}\n"));
            if path == "src/lib.rs" {
                spec.push_str(&text.replace("        self.id", "        let imported$query$ = Post::query();\n        let source$source$ = self.source_method();\n        let other$other$ = other::Post::query();\n        self.id"));
            } else {
                spec.push_str(&text);
            }
        }
        let lsp = LspEngineFixture::new(&spec);
        fs::copy(
            Path::new(MODEL).join("export.json"),
            lsp.fixture.path("api.json"),
        )
        .unwrap();
        Self { lsp }
    }

    fn options(&self, preference: &str, imported: bool) -> serde_json::Value {
        let inputs = if imported {
            json!([{"workspaceRoot": self.lsp.fixture.path(""),
                "manifestPath": self.lsp.fixture.path("Cargo.toml"),
                "targetName": "rustdoc_macro_support", "targetKind": "lib",
                "exportPath": self.lsp.fixture.path("api.json"),
                "itemPath": "rustdoc_macro_support::Post"}])
        } else {
            json!([])
        };
        json!({
            "sysroot": {"discovery": "disabled"}, "cache": {"packageResidency": "workspace"},
            "indexing": {"performancePreference": preference}, "rustdoc": {"inputs": inputs}
        })
    }

    fn config(&self, preference: &str, imported: bool) -> EngineConfig {
        EngineConfig::from_initialization_options(Some(&self.options(preference, imported)))
            .unwrap()
    }

    async fn initialize(&self, preference: &str, imported: bool) {
        self.lsp
            .initialize_with_engine_config(self.config(preference, imported))
            .await;
    }

    async fn hover(&self, marker: &str) -> String {
        let path = self.lsp.marker_path(QueryMarkers::Saved, marker);
        let position = self.lsp.marker_position(QueryMarkers::Saved, marker);
        let input = self.lsp.global_position_snapshot(path, position);
        let value = self
            .lsp
            .service
            .clone()
            .hover(context::current(), input)
            .await
            .unwrap()
            .into_value();
        serde_json::to_string(&value).unwrap()
    }

    async fn assert_imported(&self) {
        let query = self.hover("query").await;
        assert!(
            query.contains("Builder<Post>"),
            "trait default query not exposed by LSP: {query}"
        );
        let source = self.hover("source").await;
        assert!(source.contains("u64"), "source method lost: {source}");
        let other = self.hover("other").await;
        assert!(
            !other.contains("Builder<Post>"),
            "import leaked to other owner: {other}"
        );
    }
}

#[tokio::test]
async fn edt_004_rejects_invalid_startup_without_replacing_the_previous_project() {
    for preference in ["faster-builds", "lower-peak-memory"] {
        for violation in [
            "missing",
            "format",
            "duplicate",
            "target",
            "kind",
            "reference",
        ] {
            let fixture = EditorFixture::new();
            fixture.initialize(preference, true).await;
            let path = fixture.lsp.fixture.path("api.json");
            let mut options = fixture.options(preference, true);
            match violation {
                "missing" => fs::remove_file(&path).unwrap(),
                "format" => {
                    fs::write(&path, b"{\"format_version\":0,\"includes_private\":true}").unwrap()
                }
                "duplicate" => {
                    let input = options["rustdoc"]["inputs"][0].clone();
                    options["rustdoc"]["inputs"]
                        .as_array_mut()
                        .unwrap()
                        .push(input);
                }
                "target" => options["rustdoc"]["inputs"][0]["targetName"] = json!("absent_target"),
                "kind" => options["rustdoc"]["inputs"][0]["targetKind"] = json!("bin"),
                "reference" => {
                    // Remove the declaration still referenced by Model::query's signature.
                    let mut export: serde_json::Value =
                        serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
                    let index = export["index"].as_object_mut().unwrap();
                    let builder = index
                        .iter()
                        .find(|(_, item)| item["name"] == "Builder")
                        .unwrap()
                        .0
                        .clone();
                    index.remove(&builder);
                    export["paths"].as_object_mut().unwrap().remove(&builder);
                    fs::write(&path, serde_json::to_vec(&export).unwrap()).unwrap();
                }
                _ => unreachable!(),
            }
            let invalid = EngineConfig::from_initialization_options(Some(&options)).unwrap();
            let outcome = fixture
                .lsp
                .service
                .clone()
                .initialize(context::current(), fixture.lsp.fixture.path(""), invalid)
                .await;
            assert!(
                outcome.is_err(),
                "{violation}: invalid configured export was silently ignored"
            );
            let error = outcome.unwrap_err().to_string();
            assert!(
                error.contains("api.json")
                    || error.contains(&fixture.lsp.fixture.path("").display().to_string()),
                "input error lacks context: {error}"
            );
            fixture.assert_imported().await;
        }
    }
}

#[tokio::test]
async fn edt_004_saved_body_and_reindex_keep_the_imported_api() {
    for preference in ["faster-builds", "lower-peak-memory"] {
        let fixture = EditorFixture::new();
        fixture.initialize(preference, true).await;
        fixture.assert_imported().await;
        let path = fixture.lsp.fixture.path("src/lib.rs");
        let text = fs::read_to_string(&path)
            .unwrap()
            .replace("        self.id", "        self.id + 1");
        fixture.lsp.external_file_changed("src/lib.rs", &text).await;
        fixture
            .lsp
            .service
            .clone()
            .reindex_workspace(context::current())
            .await
            .unwrap();
        fixture.assert_imported().await;
    }
}

#[tokio::test]
async fn edt_006_keeps_captured_facts_after_the_external_file_changes() {
    for preference in ["faster-builds", "lower-peak-memory"] {
        let fixture = EditorFixture::new();
        fixture.initialize(preference, true).await;
        fs::remove_file(fixture.lsp.fixture.path("api.json")).unwrap();
        let text = fs::read_to_string(fixture.lsp.fixture.path("src/lib.rs"))
            .unwrap()
            .replace("        self.id", "        self.id + 1");
        fixture.lsp.external_file_changed("src/lib.rs", &text).await;
        fixture
            .lsp
            .service
            .clone()
            .reindex_workspace(context::current())
            .await
            .unwrap();
        fixture.assert_imported().await;
        let fresh = fixture
            .lsp
            .service
            .clone()
            .initialize(
                context::current(),
                fixture.lsp.fixture.path(""),
                fixture.config(preference, true),
            )
            .await;
        assert!(
            fresh.is_err(),
            "fresh startup accepted a missing replacement export"
        );
        fixture.assert_imported().await;
    }
}

#[tokio::test]
async fn edt_006_unconfigured_startup_does_not_reuse_an_imported_generation() {
    for preference in ["faster-builds", "lower-peak-memory"] {
        let fixture = EditorFixture::new();
        fixture.initialize(preference, true).await;
        fixture.assert_imported().await;
        fixture.initialize(preference, false).await;
        let query = fixture.hover("query").await;
        assert!(
            !query.contains("Builder<Post>"),
            "unconfigured engine retained import: {query}"
        );
    }
}
