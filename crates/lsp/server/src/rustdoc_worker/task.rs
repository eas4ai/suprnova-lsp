use std::{path::{Path, PathBuf}, sync::Arc, time::Duration};
use anyhow::Context as _;
use rg_lsp_proto::{AnalysisConfig, RustdocGenerationInput};
use rg_workspace::SavedWorkspaceInputs;
use tempfile::TempDir;
use tokio::{sync::{Mutex, Semaphore, watch}, task::JoinHandle};
use tower_lsp_server::{Client};
use crate::engine_client::EngineClient;
use super::{GenerationChanges, RustdocStatus, producer::CompilerPass};

#[derive(Debug)]
pub(super) struct WorkspaceWorker {
    root: PathBuf,
    config: AnalysisConfig,
    engine: EngineClient,
    client: Client,
    artifacts: TempDir,
    changes: watch::Sender<u64>,
    state: Mutex<WorkerState>,
    task: Mutex<Option<JoinHandle<()>>>,
}

#[derive(Debug, Default)]
struct WorkerState {
    generation: u64,
    saved_inputs: Option<[u8; 32]>,
    published: bool,
    compiled: bool,
}

impl WorkspaceWorker {
    pub(super) fn spawn(root: PathBuf, config: AnalysisConfig, engine: EngineClient,
        client: Client, permit: Arc<Semaphore>) -> anyhow::Result<Arc<Self>> {
        let parent = config.rustdoc.automatic.artifact_root.as_ref().map(|path| {
            if path.is_absolute() { path.clone() } else { root.join(path) }
        }).unwrap_or_else(|| root.join("target/rust-glancer/rustdoc"));
        std::fs::create_dir_all(&parent).with_context(|| format!("create rustdoc artifact parent {}", parent.display()))?;
        let artifacts = tempfile::Builder::new().prefix(".rust-glancer-rustdoc-").tempdir_in(parent)
            .context("claim isolated rustdoc artifacts")?;
        let (changes, receiver) = watch::channel(0);
        let worker = Arc::new(Self { root, config, engine, client, artifacts, changes,
            state: Mutex::new(WorkerState::default()), task: Mutex::new(None) });
        // The task uses a weak owner while idle, so dropping the registry also closes its channel.
        let owner = Arc::downgrade(&worker);
        let task = tokio::spawn(async move {
            Self::run(owner, receiver, permit).await;
        });
        *worker.task.try_lock().expect("new worker task slot is uncontended") = Some(task);
        Ok(worker)
    }

    pub(super) fn root(&self) -> &Path { &self.root }
    pub(super) fn engine(&self) -> &EngineClient { &self.engine }

    pub(super) async fn trigger(&self, force: bool) -> anyhow::Result<()> {
        let mut state = self.state.lock().await;
        let root = self.root.clone();
        let artifacts = self.artifacts.path().to_path_buf();
        let inputs = tokio::task::spawn_blocking(move || SavedWorkspaceInputs::read(&root, Some(&artifacts)))
            .await.context("join saved compiler input capture")??.digest();
        if !force && state.saved_inputs == Some(inputs) { return Ok(()); }
        state.generation = state.generation.checked_add(1).context("rustdoc request identity exhausted")?;
        state.saved_inputs = Some(inputs);
        let generation = state.generation;
        if state.published { self.status(generation, "stale", "Saved inputs changed; retaining the previous generated API").await; }
        self.status(generation, "pending", "Waiting to generate library and binary model APIs").await;
        // Fence publication on the engine lane before waking or cancelling compiler preparation.
        let invalidation = self.engine.call_unconditional("rustdoc_requested", move |engine, context| async move {
            engine.rustdoc_requested(context, generation).await
        }).await;
        self.changes.send_replace(generation);
        invalidation
    }

    pub(super) async fn failed(&self, message: String) {
        let generation = self.state.lock().await.generation;
        self.status(generation, "failed", &message).await;
    }

    async fn status(&self, generation: u64, state: &str, message: &str) {
        self.client.send_notification::<RustdocStatus>(serde_json::json!({
            "workspaceRoot": self.root, "generation": generation, "state": state,
            "message": message,
        })).await;
    }

    async fn run(owner: std::sync::Weak<Self>, mut changes: GenerationChanges, permit: Arc<Semaphore>) {
        let mut last_attempt = 0;
        loop {
            let generation = *changes.borrow_and_update();
            if generation == last_attempt {
                if changes.changed().await.is_err() { return; }
                continue;
            }
            if generation == u64::MAX { return; }
            let Some(worker) = owner.upgrade() else { return; };
            if generation != 1 {
                tokio::select! {
                    _ = tokio::time::sleep(Duration::from_millis(worker.config.rustdoc.automatic.debounce_ms)) => {},
                    _ = changes.wait_for(|current| *current != generation) => continue,
                }
            }
            let acquired = tokio::select! {
                acquired = Arc::clone(&permit).acquire_owned() => acquired,
                _ = changes.wait_for(|current| *current != generation) => continue,
            };
            let Ok(_permit) = acquired else { return; };
            if *changes.borrow() != generation { continue; }
            worker.status(generation, "running", "Generating library and binary model APIs").await;
            let result = worker.generate(generation, &mut changes).await;
            last_attempt = generation;
            if *changes.borrow() != generation { continue; }
            match result {
                Ok(GenerationResult::Current { compiled }) => {
                    let mut state = worker.state.lock().await;
                    if state.generation != generation { continue; }
                    state.published = true;
                    state.compiled = compiled;
                    worker.status(generation, "current", "Generated library and binary model APIs are current").await;
                }
                Ok(GenerationResult::Superseded) => {
                    worker.status(generation, "stale", "Saved inputs changed before generated API publication").await;
                    // Only a proven supersession creates replacement work; ordinary failures wait
                    // for a later save or explicit reindex instead of retrying themselves.
                    if let Err(error) = worker.trigger(true).await {
                        worker.failed(format!("refresh superseded compiler inputs: {error:#}")).await;
                    }
                }
                Err(error) => worker.status(generation, "failed", &format!("Generate model APIs for {}: {error:#}", worker.root.display())).await,
            }
        }
    }

    async fn generate(&self, generation: u64, changes: &mut GenerationChanges) -> anyhow::Result<GenerationResult> {
        let inputs = SavedWorkspaceInputs::read(&self.root, Some(self.artifacts.path()))?.digest();
        let captured = self.state.lock().await.saved_inputs;
        if captured != Some(inputs) { return Ok(GenerationResult::Superseded); }
        let exports = CompilerPass::new(&self.root, &self.config, self.artifacts.path(), generation, changes).export().await?;
        if *changes.borrow() != generation
            || SavedWorkspaceInputs::read(&self.root, Some(self.artifacts.path()))?.digest() != inputs {
            return Ok(GenerationResult::Superseded);
        }
        let compiled = !exports.targets.is_empty();
        if !compiled && !self.state.lock().await.compiled {
            return Ok(GenerationResult::Current { compiled: false });
        }
        let input = RustdocGenerationInput { generation, workspace_root: self.root.clone(), saved_inputs: inputs,
            artifact_directory: self.artifacts.path().to_path_buf(), exports: exports.targets };
        // This is a cancellable candidate, not an accepted saved-source mutation. Dropping
        // the RPC closes its publication responder; the engine lane checks that before replacement.
        let update = self.engine.begin_project_update();
        let result = tokio::select! {
            result = self.engine.call_unconditional("publish_rustdoc", move |engine, context| async move {
                engine.publish_rustdoc(context, input).await
            }) => result,
            _ = changes.wait_for(|current| *current != generation) => Ok(false),
        }?;
        // The unique staged directory remains alive until this immutable handoff completes.
        if result {
            update.finish();
            Ok(GenerationResult::Current { compiled })
        } else {
            Ok(GenerationResult::Superseded)
        }
    }

    pub(super) fn cancel(&self) { self.changes.send_replace(u64::MAX); }

    pub(super) async fn join(&self) -> anyhow::Result<()> {
        if let Some(task) = self.task.lock().await.take() {
            tokio::time::timeout(Duration::from_secs(15), task).await.context("timeout draining rustdoc worker")?
                .context("join rustdoc worker")?;
        }
        Ok(())
    }
}

enum GenerationResult {
    Current { compiled: bool },
    Superseded,
}
