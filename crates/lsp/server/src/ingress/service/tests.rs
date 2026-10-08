use std::{
    convert::Infallible,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
    task::{Context, Poll},
};

use futures::future::BoxFuture;
use rg_std::NormalizedPathBuf;
use serde_json::json;
use test_fixture::fixture_crate;
use tower::Service;
use tower_lsp_server::{
    LanguageServer, LspService,
    gen_lsp_types::{Hover, HoverParams, InitializeParams, InitializeResult, Uri},
    jsonrpc::{Request, Response},
};

use super::{EditorIngress, completion_request, document_request};
use crate::{
    completion_scheduler::CompletionScheduler, ingress::EditorStateHandle,
    inlay_refresher::InlayRefresher, recent_editor_saves::RecentEditorSaves,
    tests::synthetic_test_path,
};

#[test]
fn folding_range_is_a_document_request() {
    assert!(EditorIngress::<()>::is_document_request(
        "textDocument/foldingRange"
    ));
}

#[test]
fn semantic_tokens_capture_the_requested_document() {
    for method in [
        "textDocument/semanticTokens/full",
        "textDocument/semanticTokens/range",
    ] {
        assert!(EditorIngress::<()>::is_document_request(method), "{method}");
    }
}

#[tokio::test(flavor = "current_thread")]
async fn admitted_cancellation_drops_pending_hover_without_polling_its_notification_future() {
    let started = Arc::new(AtomicUsize::new(0));
    let dropped = Arc::new(AtomicUsize::new(0));
    let (inner, _socket) = LspService::new({
        let started = Arc::clone(&started);
        let dropped = Arc::clone(&dropped);
        move |_| PendingHoverBackend { started, dropped }
    });
    let mut service = EditorIngress::new(
        inner,
        EditorStateHandle::default(),
        InlayRefresher::default(),
        RecentEditorSaves::default(),
        CompletionScheduler::default(),
    );
    let initialized = service
        .call(
            Request::build("initialize")
                .id(0_i64)
                .params(json!({ "capabilities": {} }))
                .finish(),
        )
        .await
        .expect("initialization should complete")
        .expect("initialization should return a response");
    assert!(initialized.is_ok());

    let uri = rg_lsp_proto::path_to_file_uri(synthetic_test_path("workspace/src/lib.rs"))
        .expect("test path should convert to URI");
    let mut requests = (1_i64..=4)
        .map(|id| {
            service.call(
                Request::build("textDocument/hover")
                    .id(id)
                    .params(json!({
                        "textDocument": { "uri": uri.as_str() },
                        "position": { "line": 0, "character": 0 }
                    }))
                    .finish(),
            )
        })
        .collect::<Vec<_>>();
    for request in &mut requests {
        assert!(futures::poll!(request.as_mut()).is_pending());
    }
    assert_eq!(started.load(Ordering::SeqCst), 4);
    assert_eq!(dropped.load(Ordering::SeqCst), 0);

    // All four ordinary handler slots are occupied. Admit the cancellation in wire order,
    // but do not poll its future: it must not need an ordinary async slot to abort its target.
    let cancellation = service.call(
        Request::build("$/cancelRequest")
            .params(json!({ "id": 3 }))
            .finish(),
    );
    let Poll::Ready(result) = futures::poll!(requests[2].as_mut()) else {
        panic!("admitted cancellation still waits for its notification future to be polled");
    };
    let response = result
        .expect("cancelled request should complete through the real LSP service")
        .expect("cancelled request must retain its response");
    let response = serde_json::to_value(response).expect("response should serialize");
    assert_eq!(response["id"], 3);
    assert_eq!(response["error"]["code"], -32800);
    assert!(response.get("result").is_none());
    assert_eq!(dropped.load(Ordering::SeqCst), 1);
    for index in [0, 1, 3] {
        assert!(futures::poll!(requests[index].as_mut()).is_pending());
    }
    assert!(cancellation.await.expect("cancel notification should finish").is_none());
}

struct PendingHoverBackend {
    started: Arc<AtomicUsize>,
    dropped: Arc<AtomicUsize>,
}

impl LanguageServer for PendingHoverBackend {
    async fn initialize(
        &self,
        _: InitializeParams,
    ) -> tower_lsp_server::jsonrpc::Result<InitializeResult> {
        Ok(InitializeResult::default())
    }

    async fn shutdown(&self) -> tower_lsp_server::jsonrpc::Result<()> {
        Ok(())
    }

    async fn hover(&self, _: HoverParams) -> tower_lsp_server::jsonrpc::Result<Option<Hover>> {
        let _drop = PendingHoverDrop(Arc::clone(&self.dropped));
        self.started.fetch_add(1, Ordering::SeqCst);
        futures::future::pending().await
    }
}

struct PendingHoverDrop(Arc<AtomicUsize>);

impl Drop for PendingHoverDrop {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}

#[tokio::test(flavor = "current_thread")]
async fn pending_cancellation_future_survives_ingress_poll_and_completes_once() {
    let calls = Arc::new(AtomicUsize::new(0));
    let polls = Arc::new(AtomicUsize::new(0));
    let mut service = EditorIngress::new(
        PendingCancellationService {
            calls: Arc::clone(&calls),
            polls: Arc::clone(&polls),
        },
        EditorStateHandle::default(),
        InlayRefresher::default(),
        RecentEditorSaves::default(),
        CompletionScheduler::default(),
    );
    let cancellation = service.call(
        Request::build("$/cancelRequest")
            .params(json!({ "id": 3 }))
            .finish(),
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(polls.load(Ordering::SeqCst), 1, "ingress should try the first poll");
    assert!(cancellation.await.expect("pending notification should survive").is_none());
    assert_eq!(calls.load(Ordering::SeqCst), 1, "the inner service must not be called again");
    assert_eq!(polls.load(Ordering::SeqCst), 2, "the same future should finish on its next poll");
}

struct PendingCancellationService {
    calls: Arc<AtomicUsize>,
    polls: Arc<AtomicUsize>,
}

impl Service<Request> for PendingCancellationService {
    type Response = Option<Response>;
    type Error = Infallible;
    type Future = BoxFuture<'static, Result<Self::Response, Self::Error>>;

    fn poll_ready(&mut self, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }

    fn call(&mut self, request: Request) -> Self::Future {
        assert_eq!(request.method(), "$/cancelRequest");
        self.calls.fetch_add(1, Ordering::SeqCst);
        let polls = Arc::clone(&self.polls);
        Box::pin(futures::future::poll_fn(move |_| {
            if polls.fetch_add(1, Ordering::SeqCst) == 0 {
                Poll::Pending
            } else {
                Poll::Ready(Ok(None))
            }
        }))
    }
}

#[tokio::test]
async fn later_request_keeps_incrementally_changed_text_when_futures_finish_in_reverse() {
    let captured = Arc::new(Mutex::new(Vec::new()));
    let inner = CapturingService {
        captured: Arc::clone(&captured),
    };
    let mut service = EditorIngress::new(
        inner,
        EditorStateHandle::default(),
        InlayRefresher::default(),
        RecentEditorSaves::default(),
        CompletionScheduler::default(),
    );
    let uri = rg_lsp_proto::path_to_file_uri(synthetic_test_path("workspace/src/lib.rs"))
        .expect("test path should convert to URI");

    let open = service.call(
        Request::build("textDocument/didOpen")
            .params(json!({
                "textDocument": {
                    "uri": uri.as_str(),
                    "languageId": "rust",
                    "version": 1,
                    "text": "fn opened() {}"
                }
            }))
            .finish(),
    );
    let change = service.call(
        Request::build("textDocument/didChange")
            .params(json!({
                "textDocument": { "uri": uri.as_str(), "version": 2 },
                "contentChanges": [{
                    "range": {
                        "start": { "line": 0, "character": 3 },
                        "end": { "line": 0, "character": 9 }
                    },
                    "text": "changed"
                }]
            }))
            .finish(),
    );
    let query = service.call(
        Request::build("textDocument/hover")
            .id(1_i64)
            .params(json!({
                "textDocument": { "uri": uri.as_str() },
                "position": { "line": 0, "character": 3 }
            }))
            .finish(),
    );

    // The later query completes first. Each query must still read the document snapshot chosen
    // for it in `Service::call`, including the text from changes that arrived before it.
    query.await.expect("query future should complete");
    change.await.expect("change future should complete");
    open.await.expect("open future should complete");

    assert_eq!(
        *captured
            .lock()
            .expect("captured text mutex should be usable"),
        [("fn changed() {}".to_string(), (0, 3))]
    );
}

#[tokio::test(flavor = "current_thread")]
async fn completion_ownership_is_decided_in_wire_order_before_handlers_are_polled() {
    let observations = Arc::new(Mutex::new(Vec::new()));
    let inner = CompletionOwnershipService {
        observations: Arc::clone(&observations),
    };
    let mut service = EditorIngress::new(
        inner,
        EditorStateHandle::default(),
        InlayRefresher::default(),
        RecentEditorSaves::default(),
        CompletionScheduler::default(),
    );
    let uri = rg_lsp_proto::path_to_file_uri(synthetic_test_path("workspace/src/lib.rs"))
        .expect("test path should convert to URI");

    service
        .call(
            Request::build("textDocument/didOpen")
                .params(json!({
                    "textDocument": {
                        "uri": uri.as_str(),
                        "languageId": "rust",
                        "version": 1,
                        "text": "impl Source"
                    }
                }))
                .finish(),
        )
        .await
        .expect("open handler should finish");
    let older = service.call(completion_request_message(&uri, 1, 9));
    let newer = service.call(completion_request_message(&uri, 2, 11));

    // Poll in reverse to prove each snapshot was chosen in `Service::call`, not according to
    // the order in which handler futures happened to run.
    newer.await.expect("newer completion should finish");
    older.await.expect("older completion should finish");

    let mut observations = observations
        .lock()
        .expect("completion observation mutex should be usable")
        .clone();
    observations.sort_unstable();
    assert_eq!(observations, [(9, true), (11, false)]);
}

#[test]
fn save_echo_is_recorded_before_any_handler_future_is_polled() {
    let fixture = fixture_crate(
        r#"
        //- /src/lib.rs
        pub fn saved() {}
        "#,
    );
    let path = fixture.path("src/lib.rs");
    let uri = rg_lsp_proto::path_to_file_uri(&path).expect("fixture path should convert to URI");
    let recent_editor_saves = RecentEditorSaves::default();
    let inner = CapturingService {
        captured: Arc::new(Mutex::new(Vec::new())),
    };
    let mut service = EditorIngress::new(
        inner,
        EditorStateHandle::default(),
        InlayRefresher::default(),
        recent_editor_saves.clone(),
        CompletionScheduler::default(),
    );

    let _open = service.call(
        Request::build("textDocument/didOpen")
            .params(json!({
                "textDocument": {
                    "uri": uri.as_str(),
                    "languageId": "rust",
                    "version": 1,
                    "text": "pub fn saved() {}\n"
                }
            }))
            .finish(),
    );
    let _save = service.call(
        Request::build("textDocument/didSave")
            .params(json!({
                "textDocument": { "uri": uri.as_str() },
                "text": "pub fn saved() {}\n"
            }))
            .finish(),
    );

    let path = NormalizedPathBuf::from_absolute(path).expect("fixture path should normalize");
    assert!(
        recent_editor_saves.saves_to_process(vec![path]).is_empty(),
        "watcher filtering must observe the save before async lifecycle routing starts",
    );
}

#[derive(Debug)]
struct CapturingService {
    captured: Arc<Mutex<Vec<CapturedQuery>>>,
}

impl Service<Request> for CapturingService {
    type Response = Option<Response>;
    type Error = Infallible;
    type Future = BoxFuture<'static, Result<Self::Response, Self::Error>>;

    fn poll_ready(&mut self, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }

    fn call(&mut self, request: Request) -> Self::Future {
        let is_query = request.method() == "textDocument/hover";
        let position = request.params().and_then(|params| {
            Some((
                params.get("position")?.get("line")?.as_u64()?,
                params.get("position")?.get("character")?.as_u64()?,
            ))
        });
        let captured = Arc::clone(&self.captured);
        Box::pin(async move {
            if is_query {
                let document = document_request()
                    .expect("query should run inside an ingress envelope")
                    .expect("changed document should remain available");
                captured
                    .lock()
                    .expect("captured text mutex should be usable")
                    .push((
                        document.document().text().to_string(),
                        position.expect("query should capture its position before polling"),
                    ));
            }
            Ok(None)
        })
    }
}

#[derive(Debug)]
struct CompletionOwnershipService {
    observations: Arc<Mutex<Vec<(u64, bool)>>>,
}

impl Service<Request> for CompletionOwnershipService {
    type Response = Option<Response>;
    type Error = Infallible;
    type Future = BoxFuture<'static, Result<Self::Response, Self::Error>>;

    fn poll_ready(&mut self, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }

    fn call(&mut self, request: Request) -> Self::Future {
        let character = request
            .params()
            .and_then(|params| params.get("position")?.get("character")?.as_u64());
        let observations = Arc::clone(&self.observations);
        Box::pin(async move {
            if let Some(character) = character {
                let request = completion_request()
                    .expect("completion should carry an ingress ownership token");
                observations
                    .lock()
                    .expect("completion observation mutex should be usable")
                    .push((character, request.is_replaced()));
            }
            Ok(None)
        })
    }
}

type CapturedQuery = (String, (u64, u64));

fn completion_request_message(uri: &Uri, id: i64, character: u32) -> Request {
    Request::build("textDocument/completion")
        .id(id)
        .params(json!({
            "textDocument": { "uri": uri.as_str() },
            "position": { "line": 0, "character": character }
        }))
        .finish()
}
