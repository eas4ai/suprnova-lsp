//! Exercise configured compiler imports through the existing LSP service and saved lifecycle.

use std::{fs, path::Path, time::Duration};

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

    fn config(&self, preference: &str, imported: bool) -> EngineConfig {
        let inputs = if imported {
            json!([{"workspaceRoot": self.lsp.fixture.path(""),
                "manifestPath": self.lsp.fixture.path("Cargo.toml"),
                "targetName": "rustdoc_macro_support", "targetKind": "lib",
                "exportPath": self.lsp.fixture.path("api.json"),
                "itemPath": "rustdoc_macro_support::Post"}])
        } else {
            json!([])
        };
        EngineConfig::from_initialization_options(Some(&json!({
            "sysroot": {"discovery": "disabled"}, "cache": {"packageResidency": "workspace"},
            "indexing": {"performancePreference": preference}, "rustdoc": {"inputs": inputs}
        })))
        .unwrap()
    }

    async fn initialize(&self, preference: &str, imported: bool) {
        self.lsp.notifications.clear();
        let initialized = self
            .lsp
            .service
            .clone()
            .initialize(
                context::current(),
                self.lsp.fixture.path(""),
                self.config(preference, imported),
            )
            .await
            .expect("configured fixture initializes");
        if initialized.has_deferred_indexing {
            tokio::time::timeout(
                Duration::from_secs(10),
                self.lsp.notifications.wait_for_deferred_indexing(),
            )
            .await
            .unwrap();
        }
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
        let fixture = EditorFixture::new();
        fixture.initialize(preference, true).await;
        fs::write(
            fixture.lsp.fixture.path("api.json"),
            b"{\"format_version\":0,\"includes_private\":true}",
        )
        .unwrap();
        let outcome = fixture
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
            outcome.is_err(),
            "invalid configured export was silently ignored"
        );
        let error = outcome.unwrap_err().to_string();
        assert!(
            error.contains("api.json") && error.contains("format"),
            "input error lacks context: {error}"
        );
        fixture.assert_imported().await;
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
