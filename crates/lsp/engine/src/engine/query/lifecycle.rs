//! Work that every analysis request does before and after its feature-specific query.
//!
//! `respond_to_query` owns the common flow:
//!
//! 1. Skip work if the caller has already dropped the response channel, or if the saved project is
//!    already known to be stale.
//! 2. Run the feature query and let expensive phases check whether the response is still wanted.
//! 3. Tag a successful value with the project and document ids used to compute it.
//! 4. Release data loaded only for this request and repair a recoverable package-cache failure.
//!
//! This layer does not decide whether an editor document is still current. Only server ingress
//! owns live sessions and revisions, so the engine returns the captured ids and the server checks
//! them before publishing the value.
//!
//! A saved-source race follows a different path. If hover discovers that `src/lib.rs` changed on
//! disk, hover returns `QueryError::SavedSourceChanged`, records that the saved generation is stale,
//! and enqueues `src/lib.rs` on the normal path-change stream. Later queries return the same error
//! until that mutation publishes a new saved project; the hover itself never turns into a
//! synchronous reindex.

use std::{
    cell::RefCell,
    sync::Arc,
    time::{Duration, Instant},
};

use rg_lsp_proto::{
    EditorDocumentSnapshot, EngineError, GlobalPositionSnapshot, QueryError, QueryScope, QueryValue,
};
use rg_project::Project;
use rg_std::{CancellationToken, Cancelled};

use super::QueryRunner;
use crate::{engine::command::QueryResponder, memory::MemoryReporter};

/// Error kept by `QueryRunner` until the query lifecycle classifies it for the protocol.
///
/// Most failures keep their rich `anyhow` chain while this layer checks for cancellation, stale
/// source, and recoverable cache failures. Operations that require saved editor text use the
/// separate variant because that is an expected request outcome, not an engine failure.
#[derive(Debug)]
pub(crate) enum QueryRunError {
    SaveRequired(std::path::PathBuf),
    Analysis(anyhow::Error),
}

impl QueryRunError {
    fn as_error(&self) -> Option<&anyhow::Error> {
        match self {
            Self::SaveRequired(_) => None,
            Self::Analysis(error) => Some(error),
        }
    }
}

impl From<anyhow::Error> for QueryRunError {
    fn from(error: anyhow::Error) -> Self {
        Self::Analysis(error)
    }
}

impl From<Cancelled> for QueryRunError {
    fn from(error: Cancelled) -> Self {
        Self::Analysis(error.into())
    }
}

/// Lets expensive query phases check whether anyone still wants the result.
///
/// Dropping the RPC future cancels the request token and closes the engine response channel.
/// Ordinary query phases check both here. The token can also enter bounded semantic loops where
/// polling the response endpoint would cross an engine-layer ownership boundary.
pub(crate) struct QueryCancellation<'a> {
    request: &'a CancellationToken,
    response_is_closed: &'a dyn Fn() -> bool,
}

impl<'a> QueryCancellation<'a> {
    fn new(request: &'a CancellationToken, response_is_closed: &'a dyn Fn() -> bool) -> Self {
        Self {
            request,
            response_is_closed,
        }
    }

    /// Share the request signal with synchronous work that has its own bounded checkpoints.
    pub(crate) fn token(&self) -> CancellationToken {
        self.request.clone()
    }
}

impl rg_std::Cancelable for QueryCancellation<'_> {
    /// Stop at a named query boundary if nobody can receive the result anymore.
    fn check_cancelled(&self, checkpoint: &'static str) -> Result<(), Cancelled> {
        if (self.response_is_closed)() {
            self.request.cancel();
        }
        rg_std::Cancelable::check_cancelled(self.request, checkpoint)
    }
}

/// Common request data recorded before one command starts analysis.
///
/// Queue time is kept separate from execution time. The scope is copied from the command so a
/// successful result can tell the server which editor state needs a final check.
#[derive(Debug)]
pub(crate) struct QueryContext {
    label: &'static str,
    queue_elapsed: Duration,
    scope: QueryScope,
}

impl QueryContext {
    pub(crate) fn saved_project(label: &'static str, queue_elapsed: Duration) -> Self {
        Self {
            label,
            queue_elapsed,
            scope: QueryScope::SavedProject,
        }
    }

    pub(crate) fn global_operation(
        label: &'static str,
        queue_elapsed: Duration,
        snapshot: &GlobalPositionSnapshot,
    ) -> Self {
        Self {
            label,
            queue_elapsed,
            scope: QueryScope::GlobalOperation {
                target: snapshot.target().clone(),
                open_documents_revision: snapshot.open_documents_revision(),
            },
        }
    }

    /// Record a query that depends on its target document but not on open sibling documents.
    pub(crate) fn target_document(
        label: &'static str,
        queue_elapsed: Duration,
        document: &EditorDocumentSnapshot,
    ) -> Self {
        Self {
            label,
            queue_elapsed,
            scope: QueryScope::TargetDocument(document.target().clone()),
        }
    }
}

/// Proof that the query used its response endpoint after its final cancellation check.
pub(crate) struct QueryCompleted(());

/// Consume the right to complete one response once its protocol value is fully owned.
pub(crate) struct QueryCompletion<'a, T> {
    control: &'a QueryCancellation<'a>,
    response: &'a RefCell<Option<(QueryScope, QueryResponder<T>)>>,
}

impl<T> QueryCompletion<'_, T> {
    pub(crate) fn complete(self, value: T) -> Result<QueryCompleted, QueryRunError> {
        // The RPC may disappear as the last analysis unit finishes. Check before taking the
        // endpoint: the cancellation check itself reads whether that endpoint is closed.
        rg_std::check_cancel!(self.control, "before query publication");
        let (scope, sender) = self
            .response
            .borrow_mut()
            .take()
            .expect("query response is pending");
        let _ = sender.send(Ok(QueryValue::new(value, scope)));
        Ok(QueryCompleted(()))
    }
}

impl QueryRunner<'_> {
    /// Run one read-only request through the common query lifecycle.
    ///
    /// The closure contains only the feature query and receives a cancellation check it can use
    /// between expensive phases. If reading saved source proves that the project is stale, this
    /// method schedules the normal path-change recovery instead of rebuilding inside the request.
    ///
    /// A finished response is sent before cleanup and recovery start. Cleanup still completes
    /// before the dispatcher accepts the next command, but it does not add to the latency observed
    /// by this request's caller.
    pub(crate) fn respond_to_query<T>(
        &mut self,
        context: QueryContext,
        respond_to: QueryResponder<T>,
        cancellation: CancellationToken,
        query: impl FnOnce(&mut Self, &QueryCancellation<'_>) -> Result<T, QueryRunError>,
    ) where
        T: Send + 'static,
    {
        self.respond_to_query_with_completion(
            context,
            respond_to,
            cancellation,
            |runner, control, completion| completion.complete(query(runner, control)?),
        );
    }

    /// Let a query publish fully owned protocol data before releasing its temporary analysis.
    ///
    /// The completion consumes the endpoint and checks cancellation immediately before sending.
    /// After completion, the closure must only release data and return its marker. In particular,
    /// receiving a successful RPC response can cancel the token; that must not interrupt cleanup.
    pub(crate) fn respond_to_query_with_completion<T>(
        &mut self,
        context: QueryContext,
        respond_to: QueryResponder<T>,
        cancellation: CancellationToken,
        query: impl FnOnce(
            &mut Self,
            &QueryCancellation<'_>,
            QueryCompletion<'_, T>,
        ) -> Result<QueryCompleted, QueryRunError>,
    ) where
        T: Send + 'static,
    {
        let QueryContext {
            label,
            queue_elapsed,
            scope,
        } = context;

        // LSP cancellation drops the RPC handler waiting on this response. The command may still
        // be in the dispatcher queue, but there is no reason to materialize packages or run
        // analysis once nobody can receive the result.
        if respond_to.is_closed() || cancellation.is_cancelled() {
            tracing::debug!(
                label,
                queued_ms = queue_elapsed.as_millis(),
                "cancelled analysis query skipped"
            );
            return;
        }

        // Once one query proves that the saved generation no longer describes disk, all later
        // queries are known to be unsafe. They return the same cheap error until the queued
        // watcher/recovery mutation publishes a coherent generation.
        if let Some(stale_source) = self.project.stale_source() {
            tracing::debug!(
                label,
                path = %stale_source.display(),
                queued_ms = queue_elapsed.as_millis(),
                "analysis query skipped for stale saved generation"
            );
            let _ = respond_to.send(Err(QueryError::SavedSourceChanged));
            return;
        }

        // Keeping the stale check in the context layer lets timing and cache recovery remain
        // uniform for every analysis query.
        tracing::trace!(
            label,
            queued_ms = queue_elapsed.as_millis(),
            "analysis query started"
        );
        let started = Instant::now();
        let memory_control = Arc::clone(&self.memory_control);
        let memory_before = MemoryReporter::snapshot(memory_control.as_ref());
        let response = RefCell::new(Some((scope, respond_to)));
        let result = {
            let response_is_closed = || {
                response
                    .borrow()
                    .as_ref()
                    .is_some_and(|(_, sender)| sender.is_closed())
            };
            let control = QueryCancellation::new(&cancellation, &response_is_closed);
            let completion = QueryCompletion {
                control: &control,
                response: &response,
            };
            query(self, &control, completion)
        };
        let respond_to = response.into_inner();
        // A completed query has used its sole endpoint. Once success has been sent, its tail may
        // only release owned data; a later fallible operation would hide its error from the caller.
        assert_eq!(
            result.is_ok(),
            respond_to.is_none(),
            "query must complete once or fail before publication"
        );
        let cancelled_checkpoint = result
            .as_ref()
            .err()
            .and_then(QueryRunError::as_error)
            .and_then(|error| {
                error
                    .chain()
                    .find_map(|cause| cause.downcast_ref::<Cancelled>())
            })
            .map(Cancelled::checkpoint);
        let stale_path = if cancelled_checkpoint.is_some() {
            None
        } else {
            result
                .as_ref()
                .err()
                .and_then(QueryRunError::as_error)
                .and_then(Project::stale_source_path)
                .map(std::path::Path::to_path_buf)
        };
        let saved_source_changed = stale_path.is_some();
        if let Some(stale_path) = &stale_path {
            // A source race is a project-lifecycle event, not a feature-query failure. Re-enter the
            // FIFO mutation stream and stop this response while that recovery catches up.
            self.project.record_stale_source(label, stale_path);
        }
        let query_elapsed = started.elapsed();
        let should_recover = cancelled_checkpoint.is_none()
            && result
                .as_ref()
                .err()
                .and_then(QueryRunError::as_error)
                .is_some_and(Project::is_recoverable_cache_load_failure);
        if let Some(checkpoint) = cancelled_checkpoint {
            tracing::debug!(
                query = label,
                queued_ms = queue_elapsed.as_millis(),
                elapsed_ms = query_elapsed.as_millis(),
                checkpoint,
                "cancelled analysis query stopped"
            );
        } else if saved_source_changed {
            tracing::info!(
                query = label,
                queued_ms = queue_elapsed.as_millis(),
                elapsed_ms = query_elapsed.as_millis(),
                status = "saved_source_changed",
                error = ?QueryError::SavedSourceChanged,
                "analysis query completed"
            );
        } else {
            match &result {
                Ok(_) => {
                    tracing::info!(
                        query = label,
                        queued_ms = queue_elapsed.as_millis(),
                        elapsed_ms = query_elapsed.as_millis(),
                        status = "ok",
                        "analysis query completed"
                    );
                }
                Err(QueryRunError::SaveRequired(path)) => {
                    tracing::debug!(
                        query = label,
                        queued_ms = queue_elapsed.as_millis(),
                        elapsed_ms = query_elapsed.as_millis(),
                        status = "save_required",
                        path = %path.display(),
                        "analysis query completed"
                    );
                }
                Err(QueryRunError::Analysis(error)) => {
                    let error = format!("{error:#}");
                    tracing::warn!(
                        query = label,
                        queued_ms = queue_elapsed.as_millis(),
                        elapsed_ms = query_elapsed.as_millis(),
                        status = "error",
                        recoverable_cache_failure = should_recover,
                        error = %error,
                        "analysis query completed"
                    );
                }
            }
        }

        if let Some((_, respond_to)) = respond_to {
            if cancelled_checkpoint.is_some() {
                // Cancellation is an execution detail, not an empty feature result.
            } else if saved_source_changed {
                let _ = respond_to.send(Err(QueryError::SavedSourceChanged));
            } else if should_recover {
                // Keep the failed request explicitly unavailable while the next command sees the
                // repaired cache, rather than pretending the feature found no result.
                let _ = respond_to.send(Err(QueryError::TemporarilyUnavailable));
            } else {
                let error = match result {
                    Err(QueryRunError::SaveRequired(path)) => QueryError::SaveRequired { path },
                    Err(QueryRunError::Analysis(error)) => {
                        QueryError::Internal(EngineError::from(error))
                    }
                    Ok(_) => unreachable!("successful query has completed its response"),
                };
                let _ = respond_to.send(Err(error));
            }
        }

        // Publication wakes the RPC task immediately. Request-owned loads and allocator pages are
        // still released synchronously before this dispatcher accepts another command, but that
        // housekeeping no longer delays a result that is already complete and safe to publish.
        self.project.release_query_memory();
        MemoryReporter::purge_and_report_delta_debug(memory_control.as_ref(), label, memory_before);
        if should_recover {
            self.project.recover_after_package_cache_failure(label);
        }
    }
}

#[cfg(test)]
mod tests {
    use std::{
        cell::Cell,
        path::PathBuf,
        sync::{Arc, mpsc},
        time::Duration,
    };

    use rg_lsp_proto::{
        DocumentRevision, EditorDocumentSnapshot, GlobalPositionSnapshot, OpenDocumentSession,
        OpenDocumentsRevision, QueryError, QueryScope, QueryValue, ServiceNotification,
    };
    use rg_std::CancellationToken;
    use tokio::sync::oneshot;

    use super::QueryContext;
    use crate::{
        engine::{project::ProjectCoordinator, query::QueryRunner},
        memory::MemoryControl,
        service::{ServiceNotificationPublisher, ServiceNotificationsSink},
    };

    #[derive(Debug)]
    struct NoopNotifications;

    impl ServiceNotificationPublisher for NoopNotifications {
        fn send(&self, _notification: ServiceNotification) {}
    }

    struct ReleaseFlag<'a>(&'a Cell<bool>);

    impl Drop for ReleaseFlag<'_> {
        fn drop(&mut self) {
            self.0.set(true);
        }
    }

    #[test]
    fn cancelled_query_does_not_run_analysis() {
        let memory_control: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory_control));
        let mut runner = QueryRunner::new(&mut project, memory_control);
        let (respond_to, response) =
            oneshot::channel::<Result<QueryValue<Vec<usize>>, QueryError>>();
        drop(response);
        let query_ran = Cell::new(false);

        runner.respond_to_query(
            QueryContext::saved_project("workspace_symbol", Duration::ZERO),
            respond_to,
            CancellationToken::new(),
            |_, _| {
                query_ran.set(true);
                Ok(vec![1])
            },
        );

        assert!(!query_ran.get(), "cancelled query should not run analysis");
    }

    #[test]
    fn global_operation_returns_the_exact_open_document_identity_used_by_analysis() {
        let document = EditorDocumentSnapshot::new(
            PathBuf::from("/workspace/src/lib.rs"),
            OpenDocumentSession::new(3),
            DocumentRevision::new(8),
            Some(5),
            "fn editor() {}".to_string(),
        );
        let snapshot = GlobalPositionSnapshot::new(
            document.target().clone(),
            OpenDocumentsRevision::new(13),
            vec![document],
            gen_lsp_types::Position::new(0, 3),
        );
        let memory_control: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory_control));
        let mut runner = QueryRunner::new(&mut project, memory_control);
        let (respond_to, response) = oneshot::channel();
        let context = QueryContext::global_operation("references", Duration::ZERO, &snapshot);

        runner.respond_to_query(context, respond_to, CancellationToken::new(), |_, _| {
            Ok(vec![1_usize])
        });

        let result =
            futures::executor::block_on(response).expect("document query should send a response");
        let response = result.expect("document query should succeed");
        assert_eq!(response.value(), &vec![1]);
        assert_eq!(
            response.scope(),
            &QueryScope::GlobalOperation {
                target: snapshot.target().clone(),
                open_documents_revision: snapshot.open_documents_revision(),
            }
        );
    }

    #[test]
    fn valid_empty_query_remains_successful() {
        let memory_control: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory_control));
        let mut runner = QueryRunner::new(&mut project, memory_control);
        let (respond_to, response) = oneshot::channel();

        runner.respond_to_query(
            QueryContext::saved_project("workspace_symbol", Duration::ZERO),
            respond_to,
            CancellationToken::new(),
            |_, _| Ok(Vec::<usize>::new()),
        );

        let result = futures::executor::block_on(response)
            .expect("valid empty query should send a response");
        let response = result.expect("valid empty query should remain successful");
        assert!(response.value().is_empty());
        assert_eq!(response.scope(), &QueryScope::SavedProject);
    }

    #[test]
    fn query_stops_when_response_closes_during_analysis() {
        let memory_control: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory_control));
        let mut runner = QueryRunner::new(&mut project, memory_control);
        let (respond_to, response) =
            oneshot::channel::<Result<QueryValue<Vec<usize>>, QueryError>>();
        let work_after_checkpoint_ran = Cell::new(false);

        runner.respond_to_query(
            QueryContext::saved_project("completion", Duration::ZERO),
            respond_to,
            CancellationToken::new(),
            |_, cancellation| {
                // Model the RPC task disappearing after the engine has already entered the query.
                drop(response);
                rg_std::check_cancel!(cancellation, "test semantic work");
                work_after_checkpoint_ran.set(true);
                Ok(vec![1])
            },
        );

        assert!(
            !work_after_checkpoint_ran.get(),
            "work after a closed-response checkpoint should not run",
        );
    }

    #[test]
    fn query_stops_when_request_token_is_cancelled_during_analysis() {
        let memory_control: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory_control));
        let mut runner = QueryRunner::new(&mut project, memory_control);
        let (respond_to, _response) =
            oneshot::channel::<Result<QueryValue<Vec<usize>>, QueryError>>();
        let cancellation = CancellationToken::new();
        let request_owner = cancellation.clone();
        let work_after_checkpoint_ran = Cell::new(false);

        runner.respond_to_query(
            QueryContext::saved_project("inlay_hint", Duration::ZERO),
            respond_to,
            cancellation,
            |_, cancellation| {
                request_owner.cancel();
                rg_std::check_cancel!(cancellation, "test semantic work");
                work_after_checkpoint_ran.set(true);
                Ok(vec![1])
            },
        );

        assert!(
            !work_after_checkpoint_ran.get(),
            "work after a cancelled request checkpoint should not run",
        );
    }

    fn test_project(memory_control: Arc<dyn MemoryControl>) -> ProjectCoordinator {
        let (sender, _receiver) = mpsc::channel();
        let notifications = ServiceNotificationsSink::from_publisher(NoopNotifications);
        ProjectCoordinator::new(sender, memory_control, notifications)
    }

    #[test]
    fn cancellation_before_publication_cleans_up_and_allows_the_next_query() {
        #[derive(Debug, Default)]
        struct Purges(std::sync::atomic::AtomicUsize);
        impl MemoryControl for Purges {
            fn try_purge_allocator(&self) -> bool {
                self.0.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                true
            }
        }

        let purges = Arc::new(Purges::default());
        let memory: Arc<dyn MemoryControl> = purges.clone();
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, response) = oneshot::channel();
        runner.respond_to_query(
            QueryContext::saved_project("references", Duration::ZERO),
            sender,
            CancellationToken::new(),
            |_, control| {
                control.token().cancel();
                Ok(vec![1_usize])
            },
        );
        assert!(
            futures::executor::block_on(response).is_err(),
            "obsolete success must not be published"
        );
        assert_eq!(purges.0.load(std::sync::atomic::Ordering::Relaxed), 1);

        let (sender, response) = oneshot::channel();
        runner.respond_to_query(
            QueryContext::saved_project("workspace_symbol", Duration::ZERO),
            sender,
            CancellationToken::new(),
            |_, _| Ok(vec![2_usize]),
        );
        let result = futures::executor::block_on(response)
            .expect("next query responds")
            .expect("next query succeeds");
        assert_eq!(result.value(), &[2]);
        assert_eq!(purges.0.load(std::sync::atomic::Ordering::Relaxed), 2);
    }

    #[test]
    fn hover_completion_publishes_before_local_release_and_drains_cleanup() {
        #[derive(Debug, Default)]
        struct Purges(std::sync::atomic::AtomicUsize);
        impl MemoryControl for Purges {
            fn try_purge_allocator(&self) -> bool {
                self.0.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                true
            }
        }

        struct LocalAnalysisRelease<'a> {
            response: &'a mut oneshot::Receiver<
                Result<QueryValue<Option<gen_lsp_types::Hover>>, QueryError>,
            >,
            cancellation: &'a CancellationToken,
            released: &'a Cell<bool>,
        }

        impl Drop for LocalAnalysisRelease<'_> {
            fn drop(&mut self) {
                let response = self
                    .response
                    .try_recv()
                    .expect("owned typed hover must be published before local analysis release")
                    .expect("completed hover must be a successful typed response");
                assert_eq!(response.scope(), &QueryScope::SavedProject);
                let hover = response.into_value().expect("completed hover has content");
                let gen_lsp_types::Contents::MarkupContent(content) = hover.contents else {
                    panic!("completed hover must keep its owned Markdown");
                };
                assert_eq!(content.value, "let rsp_without: Builder<User>");

                // Receiving the response also drops the RPC cancellation guard. Local release
                // and common housekeeping must still finish after that normal completion.
                self.cancellation.cancel();
                self.released.set(true);
            }
        }

        let purges = Arc::new(Purges::default());
        let memory: Arc<dyn MemoryControl> = purges.clone();
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, mut response) = oneshot::channel();
        let cancellation = CancellationToken::new();
        let released = Cell::new(false);
        let query_returned_after_release = Cell::new(false);

        runner.respond_to_query_with_completion(
            QueryContext::saved_project("hover", Duration::ZERO),
            sender,
            cancellation.clone(),
            |_, _, completion| {
                let local_analysis = LocalAnalysisRelease {
                    response: &mut response,
                    cancellation: &cancellation,
                    released: &released,
                };
                let hover = Some(gen_lsp_types::Hover {
                    contents: gen_lsp_types::Contents::MarkupContent(
                        gen_lsp_types::MarkupContent {
                            kind: gen_lsp_types::MarkupKind::Markdown,
                            value: "let rsp_without: Builder<User>".to_string(),
                        },
                    ),
                    range: None,
                });
                let completed = completion.complete(hover)?;
                drop(local_analysis);
                query_returned_after_release.set(true);
                Ok(completed)
            },
        );

        assert!(cancellation.is_cancelled());
        assert!(released.get());
        assert!(query_returned_after_release.get());
        assert_eq!(purges.0.load(std::sync::atomic::Ordering::Relaxed), 1);

        let (sender, response) = oneshot::channel();
        runner.respond_to_query(
            QueryContext::saved_project("workspace_symbol", Duration::ZERO),
            sender,
            CancellationToken::new(),
            |_, _| {
                assert!(released.get(), "the next query must follow local release");
                assert!(query_returned_after_release.get());
                assert_eq!(purges.0.load(std::sync::atomic::Ordering::Relaxed), 1);
                Ok(vec![2_usize])
            },
        );
        let next = futures::executor::block_on(response)
            .expect("the next query responds after cleanup")
            .expect("the next query succeeds");
        assert_eq!(next.value(), &[2]);
        assert_eq!(purges.0.load(std::sync::atomic::Ordering::Relaxed), 2);
    }

    #[test]
    fn completion_route_cancelled_before_publication_drains_locals() {
        let memory: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, mut response) = oneshot::channel();
        let cancellation = CancellationToken::new();
        let released = Cell::new(false);
        runner.respond_to_query_with_completion(
            QueryContext::saved_project("hover", Duration::ZERO),
            sender,
            cancellation.clone(),
            |_, control, completion| {
                let _local_analysis = ReleaseFlag(&released);
                control.token().cancel();
                completion.complete(Some(7_usize))
            },
        );
        assert!(cancellation.is_cancelled());
        assert!(released.get());
        assert_eq!(
            response.try_recv(),
            Err(oneshot::error::TryRecvError::Closed)
        );
    }

    #[test]
    fn completion_route_preserves_real_error_racing_with_cancellation() {
        let memory: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, response) =
            oneshot::channel::<Result<QueryValue<Option<usize>>, QueryError>>();
        let released = Cell::new(false);
        runner.respond_to_query_with_completion(
            QueryContext::saved_project("hover", Duration::ZERO),
            sender,
            CancellationToken::new(),
            |_, control, _completion| {
                let _local_analysis = ReleaseFlag(&released);
                control.token().cancel();
                let error =
                    rg_std::OperationError::Source(std::io::Error::other("source read failed"));
                Err(anyhow::Error::new(error)
                    .context("render owned hover")
                    .into())
            },
        );
        let response =
            futures::executor::block_on(response).expect("real source error is published");
        let Err(QueryError::Internal(error)) = response else {
            panic!("real source error must not turn into cancellation or empty success");
        };
        assert!(error.to_string().contains("source read failed"));
        assert!(error.to_string().contains("render owned hover"));
        assert!(released.get());
    }

    #[test]
    fn completion_route_preserves_wrapped_cancellation_without_empty_success() {
        let memory: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, mut response) =
            oneshot::channel::<Result<QueryValue<Option<usize>>, QueryError>>();
        let released = Cell::new(false);
        runner.respond_to_query_with_completion(
            QueryContext::saved_project("hover", Duration::ZERO),
            sender,
            CancellationToken::new(),
            |_, control, _completion| {
                let _local_analysis = ReleaseFlag(&released);
                let token = control.token();
                token.cancel();
                let cancelled =
                    rg_std::Cancelable::check_cancelled(&token, "owned hover conversion")
                        .expect_err("request was cancelled");
                let error: rg_std::OperationError<std::io::Error> =
                    rg_std::OperationError::Cancelled(cancelled);
                Err(anyhow::Error::new(error)
                    .context("render owned hover")
                    .into())
            },
        );
        assert!(released.get());
        assert_eq!(
            response.try_recv(),
            Err(oneshot::error::TryRecvError::Closed)
        );
    }

    #[test]
    fn completion_route_preserves_save_required_error() {
        let memory: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, response) =
            oneshot::channel::<Result<QueryValue<Option<usize>>, QueryError>>();
        let path = PathBuf::from("/workspace/src/lib.rs");
        runner.respond_to_query_with_completion(
            QueryContext::saved_project("hover", Duration::ZERO),
            sender,
            CancellationToken::new(),
            |_, _, _completion| Err(super::QueryRunError::SaveRequired(path.clone())),
        );
        assert_eq!(
            futures::executor::block_on(response).expect("save requirement is published"),
            Err(QueryError::SaveRequired { path }),
        );
    }

    #[test]
    fn completion_route_returns_owned_none_with_exact_global_scope() {
        let document = EditorDocumentSnapshot::new(
            PathBuf::from("/workspace/src/lib.rs"),
            OpenDocumentSession::new(3),
            DocumentRevision::new(8),
            Some(5),
            "fn editor() {}".to_string(),
        );
        let snapshot = GlobalPositionSnapshot::new(
            document.target().clone(),
            OpenDocumentsRevision::new(13),
            vec![document],
            gen_lsp_types::Position::new(0, 3),
        );
        let memory: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, mut response) = oneshot::channel();
        runner.respond_to_query_with_completion(
            QueryContext::global_operation("hover", Duration::ZERO, &snapshot),
            sender,
            CancellationToken::new(),
            |_, _, completion| {
                let completed = completion.complete(Option::<gen_lsp_types::Hover>::None)?;
                let response = response
                    .try_recv()
                    .expect("valid None is already published")
                    .expect("valid None remains successful");
                assert!(response.value().is_none());
                assert_eq!(
                    response.scope(),
                    &QueryScope::GlobalOperation {
                        target: snapshot.target().clone(),
                        open_documents_revision: snapshot.open_documents_revision(),
                    }
                );
                Ok(completed)
            },
        );
    }

    #[test]
    fn completion_route_receiver_drop_before_publication_drains_locals() {
        let memory: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, response) = oneshot::channel();
        let cancellation = CancellationToken::new();
        let released = Cell::new(false);
        runner.respond_to_query_with_completion(
            QueryContext::saved_project("hover", Duration::ZERO),
            sender,
            cancellation.clone(),
            |_, _, completion| {
                let _local_analysis = ReleaseFlag(&released);
                drop(response);
                completion.complete(Some(7_usize))
            },
        );
        assert!(cancellation.is_cancelled());
        assert!(released.get());
    }

    #[test]
    fn completion_receiver_drop_between_final_check_and_send_still_drains_locals() {
        let (sender, receiver) =
            oneshot::channel::<Result<QueryValue<Option<usize>>, QueryError>>();
        let response = std::cell::RefCell::new(Some((QueryScope::SavedProject, sender)));
        let receiver = std::cell::RefCell::new(Some(receiver));
        let cancellation = CancellationToken::new();
        let released = Cell::new(false);
        // Read an open endpoint, then close its receiver before returning that observed state.
        // This deterministically represents the close racing with the subsequent send.
        let response_is_closed = || {
            let was_closed = response
                .borrow()
                .as_ref()
                .expect("response is pending")
                .1
                .is_closed();
            drop(receiver.borrow_mut().take());
            was_closed
        };
        let control = super::QueryCancellation::new(&cancellation, &response_is_closed);
        let completion = super::QueryCompletion {
            control: &control,
            response: &response,
        };
        {
            let _local_analysis = ReleaseFlag(&released);
            completion
                .complete(None)
                .expect("racing close does not create a semantic error");
        }
        assert!(
            response.borrow().is_none(),
            "the endpoint is consumed exactly once"
        );
        assert!(receiver.borrow().is_none());
        assert!(released.get());
        assert!(!cancellation.is_cancelled());
    }

    #[test]
    fn completion_route_rejects_error_after_publication() {
        let memory: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        let (sender, mut response) = oneshot::channel();
        let released = Cell::new(false);
        let violated = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            runner.respond_to_query_with_completion(
                QueryContext::saved_project("hover", Duration::ZERO),
                sender,
                CancellationToken::new(),
                |_, _, completion| {
                    let _local_analysis = ReleaseFlag(&released);
                    let _completed = completion.complete(Option::<gen_lsp_types::Hover>::None)?;
                    Err(anyhow::anyhow!("fallible work after completed hover").into())
                },
            );
        }));
        let panic = violated.expect_err("late error must violate the publication invariant");
        let message = panic
            .downcast_ref::<String>()
            .map(String::as_str)
            .or_else(|| panic.downcast_ref::<&str>().copied())
            .expect("invariant panic has a message");
        assert!(message.contains("query must complete once or fail before publication"));
        assert!(released.get());
        let published = response
            .try_recv()
            .expect("original success is still the only response")
            .expect("late error cannot replace the published success");
        assert!(published.value().is_none());
        assert_eq!(published.scope(), &QueryScope::SavedProject);
    }

    #[test]
    fn wrapped_cancellation_is_distinct_from_a_source_failure_racing_with_cancellation() {
        let memory: Arc<dyn MemoryControl> = Arc::new(());
        let mut project = test_project(Arc::clone(&memory));
        let mut runner = QueryRunner::new(&mut project, memory);
        for source_failed in [false, true] {
            let (sender, response) = oneshot::channel::<Result<QueryValue<()>, QueryError>>();
            runner.respond_to_query(
                QueryContext::saved_project("references", Duration::ZERO),
                sender,
                CancellationToken::new(),
                |_, control| {
                    let token = control.token();
                    token.cancel();
                    let error = if source_failed {
                        rg_std::OperationError::Source(std::io::Error::other("source read failed"))
                    } else {
                        rg_std::OperationError::Cancelled(
                            rg_std::Cancelable::check_cancelled(&token, "test scan")
                                .expect_err("request was cancelled"),
                        )
                    };
                    Err(anyhow::Error::new(error).context("scan source").into())
                },
            );
            let result = futures::executor::block_on(response);
            if source_failed {
                assert!(
                    matches!(result, Ok(Err(QueryError::Internal(_)))),
                    "a source failure keeps its error policy"
                );
            } else {
                assert!(
                    result.is_err(),
                    "a wrapped cancellation produces no response"
                );
            }
        }
    }
}
