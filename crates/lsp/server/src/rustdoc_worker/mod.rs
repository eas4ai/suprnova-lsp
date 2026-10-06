//! Saved-input compiler preparation, with one export slot shared by every workspace engine.

mod command;
mod process_tree;
mod producer;
mod task;

use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::{Arc, RwLock},
};

use rg_lsp_proto::AnalysisConfig;
use rg_workspace::SavedWorkspaceInputs;
use tokio::sync::{Mutex, Semaphore, watch};
use tower_lsp_server::{
    Client,
    gen_lsp_types::{LspAny, LspNotificationMethod, MessageDirection, Notification},
};

use self::task::WorkspaceWorker;
use crate::engine_client::EngineClient;

/// Server-owned scheduler; an engine-local permit would allow separate roots to compile together.
#[derive(Clone, Debug)]
pub(crate) struct RustdocWorkers {
    client: Client,
    permit: Arc<Semaphore>,
    roots: Arc<Mutex<BTreeMap<PathBuf, Arc<WorkspaceWorker>>>>,
    artifact_directories: Arc<RwLock<Vec<PathBuf>>>,
}

impl RustdocWorkers {
    pub(crate) fn new(client: Client) -> Self {
        Self {
            client,
            permit: Arc::new(Semaphore::new(1)),
            roots: Arc::new(Mutex::new(BTreeMap::new())),
            artifact_directories: Arc::new(RwLock::new(Vec::new())),
        }
    }

    pub(crate) async fn register(
        &self,
        root: PathBuf,
        config: AnalysisConfig,
        engine: EngineClient,
    ) -> anyhow::Result<()> {
        if !config.rustdoc.automatic.enabled || !config.rustdoc.inputs.is_empty() {
            return Ok(());
        }
        let mut roots = self.roots.lock().await;
        if roots.contains_key(&root) {
            return Ok(());
        }
        anyhow::ensure!(
            !self.permit.is_closed(),
            "compiler scheduling has stopped; restart the language server"
        );
        let worker = WorkspaceWorker::spawn(
            root.clone(),
            config,
            engine,
            self.client.clone(),
            Arc::clone(&self.permit),
            Arc::clone(&self.artifact_directories),
        );
        roots.insert(root, Arc::clone(&worker));
        drop(roots);
        worker.trigger(true).await
    }

    pub(crate) async fn saved_path(&self, path: &Path) {
        if self.is_artifact(path) {
            return;
        }
        let roots = self
            .roots
            .lock()
            .await
            .values()
            .filter(|worker| SavedWorkspaceInputs::is_input(worker.root(), path))
            .cloned()
            .collect::<Vec<_>>();
        for worker in roots {
            if let Err(error) = worker.trigger(false).await {
                worker
                    .failed(format!("read saved compiler inputs: {error:#}"))
                    .await;
            }
        }
    }

    pub(crate) fn is_artifact(&self, path: &Path) -> bool {
        self.artifact_directories
            .read()
            .expect("artifact ownership lock is not poisoned")
            .iter()
            .any(|owned| path.starts_with(owned))
    }

    pub(crate) fn artifact_directories(&self) -> Arc<RwLock<Vec<PathBuf>>> {
        Arc::clone(&self.artifact_directories)
    }

    pub(crate) async fn changed_root(&self, root: &Path) {
        let roots = self
            .roots
            .lock()
            .await
            .values()
            .filter(|worker| worker.root().starts_with(root) || root.starts_with(worker.root()))
            .cloned()
            .collect::<Vec<_>>();
        for worker in roots {
            if let Err(error) = worker.trigger(false).await {
                worker
                    .failed(format!("read changed compiler inputs: {error:#}"))
                    .await;
            }
        }
    }

    pub(crate) async fn reindex(&self, engine: &EngineClient) {
        let roots = self
            .roots
            .lock()
            .await
            .values()
            .filter(|worker| worker.engine().same_engine(engine))
            .cloned()
            .collect::<Vec<_>>();
        for worker in roots {
            if let Err(error) = worker.trigger(true).await {
                worker
                    .failed(format!("prepare compiler reindex: {error:#}"))
                    .await;
            }
        }
    }

    pub(crate) async fn shutdown(&self) -> anyhow::Result<()> {
        // Also fence registration racing a source engine's startup completion.
        self.permit.close();
        let roots = std::mem::take(&mut *self.roots.lock().await);
        for worker in roots.values() {
            worker.cancel();
        }
        let mut result = Ok(());
        for worker in roots.into_values() {
            if let Err(error) = worker.join().await {
                tracing::error!(error = %format!("{error:#}"), "failed to join automatic compiler worker");
                result = Err(error.context("drain automatic rustdoc worker"));
            }
        }
        result
    }
}

pub(super) struct RustdocStatus;

impl Notification for RustdocStatus {
    type Params = LspAny;
    const METHOD: LspNotificationMethod<'static> =
        LspNotificationMethod::Custom("rust-glancer/rustdocStatus");
    const MESSAGE_DIRECTION: MessageDirection = MessageDirection::ServerToClient;
}

pub(super) type GenerationChanges = watch::Receiver<u64>;
