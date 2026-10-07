use std::{
    sync::{Arc, mpsc},
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
        QueryRunner::new(&mut project, Arc::new(())).respond_to_query(
            context,
            sender,
            CancellationToken::new(),
            |runner, cancellation| runner.hover(input, cancellation),
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
