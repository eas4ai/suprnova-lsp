use std::{
    fmt,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
        mpsc,
    },
    time::Duration,
};

use rg_lsp_proto::{
    DocumentRevision, EditorDocumentSnapshot, GlobalPositionSnapshot, OpenDocumentSession,
    OpenDocumentsRevision,
};
use rg_project::{Project, SplitIndexingMode};
use rg_std::CancellationToken;
use test_fixture::fixture_crate;

use super::NoopNotifications;
use crate::{
    engine::{
        project::ProjectCoordinator,
        query::{QueryContext, QueryRunner},
    },
    service::ServiceNotificationsSink,
};

#[test]
fn declaration_hover_preserves_pending_saved_bodies_and_current_header_coordinates() {
    let fixture = fixture_crate(
        r#"
        //- /Cargo.toml
        [package]
        name = "declaration_hover"
        version = "0.1.0"
        edition = "2024"

        //- /src/lib.rs
        pub fn value() -> u64 { let local = 7; local }
        "#,
    );
    let metadata = rg_workspace::CargoMetadataConfig::default()
        .load_metadata_with_target_cfg(fixture.path("Cargo.toml"))
        .unwrap();
    let workspace = rg_workspace::WorkspaceMetadata::lower(
        metadata.metadata,
        metadata.target_cfg,
        rg_workspace::WorkspaceLoweringConfig::default(),
    )
    .unwrap();
    let saved = Project::builder(workspace)
        .split_indexing_mode(SplitIndexingMode::EarlyStart)
        .build()
        .unwrap();
    let (sender, _receiver) = mpsc::channel();
    let mut project = ProjectCoordinator::new(
        sender,
        Arc::new(()),
        ServiceNotificationsSink::from_publisher(NoopNotifications),
    );
    project.project.replace_saved(saved);
    let generation = project.project.generation();
    let before = project.saved_snapshot().unwrap().stats().body_ir.body_count;
    assert_eq!(before, 0, "the test starts before deferred bodies publish");
    let saved_text = std::fs::read_to_string(fixture.path("src/lib.rs")).unwrap();

    // The second request moves the unchanged declaration into editor coordinates.
    // Neither request may finish saved bodies just to display the published header.
    for (line, text) in [(0, saved_text.clone()), (1, format!("\n{saved_text}"))] {
        let document = EditorDocumentSnapshot::new(
            fixture.path("src/lib.rs"),
            OpenDocumentSession::new(1),
            DocumentRevision::new(line + 1),
            Some(line as i32 + 1),
            text,
        );
        let input = GlobalPositionSnapshot::new(
            document.target().clone(),
            OpenDocumentsRevision::new(line + 1),
            vec![document],
            gen_lsp_types::Position::new(line as u32, 8),
        );
        let context = QueryContext::global_operation("hover", Duration::ZERO, &input);
        let (sender, response) = tokio::sync::oneshot::channel();
        QueryRunner::new(&mut project, Arc::new(())).respond_to_query_with_completion(
            context,
            sender,
            CancellationToken::new(),
            |runner, cancellation, completion| runner.hover(input, cancellation, completion),
        );
        let response = futures::executor::block_on(response).unwrap().unwrap();
        let hover = response
            .value()
            .as_ref()
            .expect("the declaration has a genuine hover");
        let range = hover.range.expect("the declaration has a token range");
        assert_eq!(range.start.line, line as u32);
        assert_eq!(range.start.character, 7);
        assert_eq!(project.project.generation(), generation);
        assert_eq!(
            project.saved_snapshot().unwrap().stats().body_ir.body_count,
            before,
            "declaration hover must leave deferred bodies unfinished",
        );
    }
}

#[derive(Default)]
struct HoverEventFields {
    query: String,
    phase: String,
    message: String,
}

impl tracing::field::Visit for HoverEventFields {
    fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
        match field.name() {
            "query" => self.query = value.to_owned(),
            "phase" => self.phase = value.to_owned(),
            "message" => self.message = value.to_owned(),
            _ => {}
        }
    }

    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn fmt::Debug) {
        // The message field contains tracing's format arguments rather than a string value.
        if field.name() == "message" {
            self.message = format!("{value:?}");
        }
    }
}

/// Observe the real source-preparation boundary on this test's query thread. Artifact reader
/// threads need no subscriber: the source/association decision runs on the dispatcher itself.
#[derive(Default)]
struct HoverPreparationLog {
    phases: Mutex<Vec<String>>,
    releases: Mutex<usize>,
    next_span: AtomicU64,
}

impl tracing::Subscriber for HoverPreparationLog {
    fn enabled(&self, metadata: &tracing::Metadata<'_>) -> bool {
        metadata.target() == "rg_lsp_engine::engine::query"
    }

    fn new_span(&self, _span: &tracing::span::Attributes<'_>) -> tracing::span::Id {
        tracing::span::Id::from_u64(self.next_span.fetch_add(1, Ordering::Relaxed) + 1)
    }

    fn record(&self, _span: &tracing::span::Id, _values: &tracing::span::Record<'_>) {}

    fn record_follows_from(&self, _span: &tracing::span::Id, _follows: &tracing::span::Id) {}

    fn event(&self, event: &tracing::Event<'_>) {
        let mut fields = HoverEventFields::default();
        event.record(&mut fields);
        if fields.query == "hover" && fields.message == "document analysis phase" {
            self.phases.lock().unwrap().push(fields.phase);
        } else if fields.message == "hover request-owned analysis released" {
            *self.releases.lock().unwrap() += 1;
        }
    }

    fn enter(&self, _span: &tracing::span::Id) {}

    fn exit(&self, _span: &tracing::span::Id) {}
}

#[test]
fn binding_hover_prepares_source_once_across_declaration_probe_and_body_fallback() {
    let fixture = fixture_crate(
        r#"
        //- /Cargo.toml
        [package]
        name = "hover_source_preparation"
        version = "0.1.0"
        edition = "2024"

        //- /src/lib.rs
        pub fn value() { let local = 7_u64; local; }
        "#,
    );
    let metadata = rg_workspace::CargoMetadataConfig::default()
        .load_metadata_with_target_cfg(fixture.path("Cargo.toml"))
        .unwrap();
    let workspace = rg_workspace::WorkspaceMetadata::lower(
        metadata.metadata,
        metadata.target_cfg,
        rg_workspace::WorkspaceLoweringConfig::default(),
    )
    .unwrap();
    let saved = Project::builder(workspace)
        .split_indexing_mode(SplitIndexingMode::EarlyStart)
        .build()
        .unwrap();
    let (sender, _receiver) = mpsc::channel();
    let mut project = ProjectCoordinator::new(
        sender,
        Arc::new(()),
        ServiceNotificationsSink::from_publisher(NoopNotifications),
    );
    project.project.replace_saved(saved);
    let generation = project.project.generation();
    assert_eq!(
        project.saved_snapshot().unwrap().stats().body_ir.body_count,
        0
    );
    let saved_text = std::fs::read_to_string(fixture.path("src/lib.rs")).unwrap();

    // Each revision needs its own captured text and source coordinates. The emoji precedes
    // the hovered token on the same line so its UTF-16 column differs from its byte offset.
    let cases = [
        (
            "\npub fn value() {\n    /*😀*/ let local = 7_u32; local;\n}\n".to_owned(),
            "u32",
            false,
        ),
        (
            "\n\npub fn value() {\n    /*😀*/ let local = true; local;\n}\n".to_owned(),
            "bool",
            false,
        ),
        (saved_text, "u64", true),
    ];
    for (index, (text, ty, exact)) in cases.into_iter().enumerate() {
        let revision = index as u64 + 1;
        let offset = text.rfind("local").unwrap();
        let preceding = &text[..offset];
        let position = gen_lsp_types::Position::new(
            preceding.bytes().filter(|byte| *byte == b'\n').count() as u32,
            preceding
                .rsplit('\n')
                .next()
                .unwrap()
                .encode_utf16()
                .count() as u32,
        );
        let document = EditorDocumentSnapshot::new(
            fixture.path("src/lib.rs"),
            OpenDocumentSession::new(1),
            DocumentRevision::new(revision),
            Some(revision as i32),
            text,
        );
        let input = GlobalPositionSnapshot::new(
            document.target().clone(),
            OpenDocumentsRevision::new(revision),
            vec![document],
            position,
        );
        let expected_scope = rg_lsp_proto::QueryScope::GlobalOperation {
            target: input.target().clone(),
            open_documents_revision: input.open_documents_revision(),
        };
        let context = QueryContext::global_operation("hover", Duration::ZERO, &input);
        let (sender, response) = tokio::sync::oneshot::channel();
        let observation = Arc::new(HoverPreparationLog::default());
        tracing::subscriber::with_default(Arc::clone(&observation), || {
            QueryRunner::new(&mut project, Arc::new(())).respond_to_query_with_completion(
                context,
                sender,
                CancellationToken::new(),
                |runner, cancellation, completion| runner.hover(input, cancellation, completion),
            );
        });
        let response = futures::executor::block_on(response).unwrap().unwrap();
        assert_eq!(response.scope(), &expected_scope);
        let hover = response
            .value()
            .as_ref()
            .expect("the binding has a genuine typed hover");
        let gen_lsp_types::Contents::MarkupContent(contents) = &hover.contents else {
            panic!("binding hover must render markdown");
        };
        assert_eq!(contents.value, format!("```rust\nlet local: {ty}\n```"));
        let range = hover
            .range
            .expect("the binding use has a current token range");
        assert_eq!(range.start, position);
        assert_eq!(
            range.end,
            gen_lsp_types::Position::new(position.line, position.character + 5)
        );
        assert_eq!(project.project.generation(), generation);
        assert_eq!(
            project.saved_snapshot().unwrap().stats().body_ir.body_count,
            usize::from(exact),
            "current bodies are request-owned; only exact saved source materializes the saved body",
        );
        assert_eq!(*observation.releases.lock().unwrap(), 1);
        let phases = observation.phases.lock().unwrap();
        assert_eq!(
            phases
                .iter()
                .filter(|phase| phase.as_str() == "source and declaration associations")
                .count(),
            1,
            "revision {revision}: one hover must not parse and associate its source twice",
        );
    }
}
