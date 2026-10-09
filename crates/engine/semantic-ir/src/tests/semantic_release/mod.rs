use std::{
    cell::RefCell,
    fmt, fs,
    process::Command,
    sync::{
        Arc, Condvar, Mutex, Weak,
        atomic::{AtomicU64, AtomicUsize, Ordering},
    },
    thread::{self, ThreadId},
    time::{Duration, Instant},
};

use rg_ir_model::{CrateId, CrateRef, DefMapRef, PackageSlot};
use rg_package_store::PackageStoreError;
use rg_std::CancellationToken;

use crate::{
    ItemLookupIndex, ItemStore, ItemStoreBuilder, LoadSemanticIr, SemanticIrLoader,
    SemanticIrReadTxn, SemanticPackage, SemanticPackageManifest,
};

#[derive(Debug, Clone, PartialEq, Eq)]
struct ReleaseWorker {
    thread: ThreadId,
    task: String,
    name: String,
}

impl ReleaseWorker {
    fn current() -> Self {
        // procfs names the actual kernel task; Rust's ThreadId is a separate identity.
        let task = fs::read_link("/proc/thread-self")
            .expect("current kernel task should be readable")
            .file_name()
            .expect("thread-self should end in a task ID")
            .to_string_lossy()
            .into_owned();
        Self {
            thread: thread::current().id(),
            task,
            name: fs::read_to_string("/proc/thread-self/comm")
                .expect("current kernel task name should be readable")
                .trim_end()
                .to_owned(),
        }
    }

    fn task_observation(task: &str, name: &str) -> Option<String> {
        let stat = match fs::read_to_string(format!("/proc/self/task/{task}/stat")) {
            Ok(stat) => stat,
            Err(error)
                if error.kind() == std::io::ErrorKind::NotFound
                    || error.raw_os_error() == Some(libc::ESRCH) =>
            {
                return None;
            }
            Err(error) => panic!("read semantic release task stat: {error}"),
        };
        let (_, fields) = stat
            .rsplit_once(')')
            .expect("task stat should contain its comm");
        let state = fields
            .split_whitespace()
            .next()
            .expect("task stat should contain its state");
        Some(format!(
            "task={task} comm={name} state={state} stat={}",
            stat.trim_end()
        ))
    }

    fn remaining(known_workers: &[Self]) -> Vec<String> {
        // pthread_join can return when child_tid clears before Linux unhashes the procfs
        // task. Only disappearance succeeds; every still-visible state fails at the deadline.
        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            let remaining: Vec<_> = fs::read_dir("/proc/self/task")
                .expect("process tasks should be readable")
                .filter_map(|entry| {
                    let entry = match entry {
                        Ok(entry) => entry,
                        Err(error)
                            if error.kind() == std::io::ErrorKind::NotFound
                                || error.raw_os_error() == Some(libc::ESRCH) =>
                        {
                            return None;
                        }
                        Err(error) => panic!("read process task entry: {error}"),
                    };
                    match fs::read_to_string(entry.path().join("comm")) {
                        Ok(name)
                            if name.trim_end() == "release-semanti"
                                || known_workers.iter().any(|worker| {
                                    worker.task.as_str()
                                        == entry.file_name().to_string_lossy().as_ref()
                                }) =>
                        {
                            Self::task_observation(
                                &entry.file_name().to_string_lossy(),
                                name.trim_end(),
                            )
                        }
                        Ok(_) => None,
                        Err(error)
                            if error.kind() == std::io::ErrorKind::NotFound
                                || error.raw_os_error() == Some(libc::ESRCH) =>
                        {
                            None
                        }
                        Err(error) => panic!("read process task name: {error}"),
                    }
                })
                .collect();
            if remaining.is_empty() || Instant::now() >= deadline {
                return remaining;
            }
            thread::sleep(
                Duration::from_millis(1).min(deadline.saturating_duration_since(Instant::now())),
            );
        }
    }

    fn assert_none_remaining() {
        let remaining = Self::remaining(&[]);
        assert!(
            remaining.is_empty(),
            "semantic release tasks remain: {remaining:?}"
        );
    }
}

#[derive(Debug, Default, Clone)]
struct ReleaseGate {
    open: bool,
    done: bool,
    started: Vec<ReleaseWorker>,
    finished: Vec<ReleaseWorker>,
    active: usize,
    peak: usize,
    errors: Vec<String>,
}

#[derive(Debug, Default)]
struct ReleaseState {
    gate: Mutex<ReleaseGate>,
    changed: Condvar,
    items: Mutex<Vec<Weak<ItemStore>>>,
    indexes: Mutex<Vec<Weak<ItemLookupIndex>>>,
    reads: AtomicUsize,
    loader_drops: Mutex<Vec<(ThreadId, usize, usize, usize)>>,
}

impl ReleaseState {
    fn live_payloads(&self) -> (usize, usize) {
        (
            self.items
                .lock()
                .unwrap()
                .iter()
                .filter(|value| value.upgrade().is_some())
                .count(),
            self.indexes
                .lock()
                .unwrap()
                .iter()
                .filter(|value| value.upgrade().is_some())
                .count(),
        )
    }

    fn reset_gate(&self) {
        let mut gate = self.gate.lock().unwrap();
        assert_eq!(gate.active, 0);
        *gate = ReleaseGate::default();
    }

    fn drop_with_gated_workers(self: &Arc<Self>, txn: SemanticIrReadTxn<'static>) -> ThreadId {
        self.reset_gate();
        let state = Arc::clone(self);
        let request = thread::Builder::new()
            .name("semantic-test".to_owned())
            .spawn(move || {
                let parent = thread::current().id();
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| drop(txn)));
                state.gate.lock().unwrap().done = true;
                state.changed.notify_all();
                if let Err(panic) = result {
                    std::panic::resume_unwind(panic);
                }
                parent
            })
            .expect("controlled transaction owner should start");
        let gate = self.gate.lock().unwrap();
        let (mut gate, _) = self
            .changed
            .wait_timeout_while(gate, Duration::from_secs(10), |gate| {
                gate.started.len() < 8 && !gate.done
            })
            .unwrap();
        // The gate is always opened before joining and before any test assertions. Serial
        // baseline teardown signals done with zero starts, so it fails without a timeout.
        gate.open = true;
        self.changed.notify_all();
        drop(gate);
        request.join().expect("transaction release should finish")
    }

    fn assert_joined_workers(&self, parent: ThreadId, expected: usize) {
        // A failed assertion must not poison the gate: retained transactions still need
        // their ordinary teardown while this test unwinds.
        let gate = self.gate.lock().unwrap().clone();
        assert!(
            gate.errors.is_empty(),
            "worker trace errors: {:?}",
            gate.errors
        );
        assert_eq!(
            gate.started.len(),
            expected,
            "decoded transaction must start actual bounded semantic release workers"
        );
        assert_eq!(gate.finished.len(), expected);
        assert_eq!(gate.peak, expected);
        assert_eq!(gate.active, 0);
        for worker in &gate.started {
            assert_ne!(worker.thread, parent);
            assert_eq!(worker.name, "release-semanti");
            assert_eq!(
                gate.started.iter().filter(|value| *value == worker).count(),
                1
            );
            assert_eq!(
                gate.finished
                    .iter()
                    .filter(|value| *value == worker)
                    .count(),
                1
            );
        }
        let remaining = ReleaseWorker::remaining(&gate.started);
        assert!(
            remaining.is_empty(),
            "joined semantic workers remain: {remaining:?}"
        );
    }
}

#[derive(Debug)]
struct ReleaseLoader {
    state: Arc<ReleaseState>,
}

impl ReleaseLoader {
    fn transaction(packages: usize) -> (Arc<ReleaseState>, SemanticIrReadTxn<'static>) {
        let state = Arc::new(ReleaseState::default());
        let txn = SemanticIrReadTxn::from_store_entries(
            (0..packages).map(|_| (true, None)),
            SemanticIrLoader::new(Self {
                state: Arc::clone(&state),
            }),
        );
        (state, txn)
    }

    fn crate_ref(package: usize) -> CrateRef {
        CrateRef {
            package: PackageSlot(package),
            crate_id: CrateId(0),
        }
    }

    fn populate(txn: &SemanticIrReadTxn<'_>, packages: usize) {
        for package in 0..packages {
            let owner = Self::crate_ref(package);
            assert_eq!(
                txn.items(owner).unwrap().unwrap().origin(),
                DefMapRef::Crate(owner)
            );
            assert!(txn.item_lookup_index(owner).unwrap().is_some());
        }
    }

    fn isolated(name: &str) -> bool {
        const CHILD: &str = "RG_SEMANTIC_RELEASE_CHILD";
        if std::env::var_os(CHILD).as_deref() == Some(std::ffi::OsStr::new(name)) {
            return false;
        }
        // Global tracing and kernel task observations belong to one test process.
        let output = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", name, "--nocapture"])
            .env(CHILD, name)
            .output()
            .expect("isolated semantic release test should start");
        assert!(
            output.status.success(),
            "isolated semantic release failed: {} {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        true
    }
}

impl LoadSemanticIr for ReleaseLoader {
    fn load_manifest(
        &self,
        package: PackageSlot,
    ) -> Result<Arc<SemanticPackageManifest>, PackageStoreError> {
        let items = ItemStoreBuilder::new(DefMapRef::Crate(Self::crate_ref(package.0)), 0).build();
        Ok(Arc::new(SemanticPackage::new(vec![items]).manifest()))
    }

    fn load_items(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
    ) -> Result<Arc<ItemStore>, PackageStoreError> {
        self.state.reads.fetch_add(1, Ordering::Relaxed);
        let items = Arc::new(
            ItemStoreBuilder::new(DefMapRef::Crate(CrateRef { package, crate_id }), 0).build(),
        );
        self.state
            .items
            .lock()
            .unwrap()
            .push(Arc::downgrade(&items));
        Ok(items)
    }

    fn load_lookup_index(
        &self,
        _package: PackageSlot,
        _crate_id: CrateId,
    ) -> Result<Arc<ItemLookupIndex>, PackageStoreError> {
        self.state.reads.fetch_add(1, Ordering::Relaxed);
        let index = Arc::new(ItemLookupIndex::default());
        self.state
            .indexes
            .lock()
            .unwrap()
            .push(Arc::downgrade(&index));
        Ok(index)
    }
}

impl Drop for ReleaseLoader {
    fn drop(&mut self) {
        let (items, indexes) = self.state.live_payloads();
        let finished = self.state.gate.lock().unwrap().finished.len();
        self.state.loader_drops.lock().unwrap().push((
            thread::current().id(),
            items,
            indexes,
            finished,
        ));
    }
}

#[derive(Default)]
struct ReleaseEvent {
    message: String,
    thread_id: String,
}

impl tracing::field::Visit for ReleaseEvent {
    fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
        if field.name() == "message" {
            self.message = value.to_owned();
        }
    }

    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn fmt::Debug) {
        match field.name() {
            "message" => self.message = format!("{value:?}"),
            "thread_id" => self.thread_id = format!("{value:?}"),
            _ => {}
        }
    }
}

struct ReleaseLog {
    state: Arc<ReleaseState>,
    next_span: AtomicU64,
    enabled: bool,
    panic: Option<Arc<PanicReleaseState>>,
}

impl ReleaseLog {
    fn install(state: &Arc<ReleaseState>, enabled: bool) {
        tracing::subscriber::set_global_default(Self {
            state: Arc::clone(state),
            next_span: AtomicU64::new(0),
            enabled,
            panic: None,
        })
        .expect("isolated test owns its global subscriber");
    }
}

impl tracing::Subscriber for ReleaseLog {
    fn enabled(&self, metadata: &tracing::Metadata<'_>) -> bool {
        self.enabled && metadata.target().starts_with("rg_semantic_ir::store")
    }

    fn new_span(&self, _span: &tracing::span::Attributes<'_>) -> tracing::span::Id {
        tracing::span::Id::from_u64(self.next_span.fetch_add(1, Ordering::Relaxed) + 1)
    }

    fn record(&self, _span: &tracing::span::Id, _values: &tracing::span::Record<'_>) {}
    fn record_follows_from(&self, _span: &tracing::span::Id, _follows: &tracing::span::Id) {}
    fn enter(&self, _span: &tracing::span::Id) {}
    fn exit(&self, _span: &tracing::span::Id) {}

    fn event(&self, event: &tracing::Event<'_>) {
        let mut fields = ReleaseEvent::default();
        event.record(&mut fields);
        if !matches!(
            fields.message.as_str(),
            "semantic release worker started" | "semantic release worker finished"
        ) {
            return;
        }
        let worker = ReleaseWorker::current();
        if let Some(panic) = &self.panic {
            panic.event(&fields, worker);
            return;
        }
        let mut gate = self.state.gate.lock().unwrap();
        if fields.thread_id != format!("{:?}", worker.thread) {
            gate.errors
                .push("worker event must identify its actual emitter".to_owned());
        }
        if fields.message == "semantic release worker started" {
            gate.started.push(worker);
            gate.active += 1;
            gate.peak = gate.peak.max(gate.active);
            self.state.changed.notify_all();
            while !gate.open {
                gate = self.state.changed.wait(gate).unwrap();
            }
        } else {
            gate.finished.push(worker);
            gate.active = gate
                .active
                .checked_sub(1)
                .expect("finished worker must have started");
        }
    }
}

#[derive(Debug, Default)]
struct PanicReleaseGate {
    started: Vec<ReleaseWorker>,
    first: Option<ThreadId>,
    workers_open: bool,
    tls_started: Vec<ReleaseWorker>,
    tls_finished: Vec<ReleaseWorker>,
    tls_open: bool,
    owner_finished: bool,
    errors: Vec<String>,
}

#[derive(Debug, Default)]
struct PanicReleaseState {
    gate: Mutex<PanicReleaseGate>,
    changed: Condvar,
}

impl PanicReleaseState {
    fn event(self: &Arc<Self>, fields: &ReleaseEvent, worker: ReleaseWorker) {
        if fields.message != "semantic release worker started" {
            return;
        }
        let mut gate = self.gate.lock().unwrap();
        if fields.thread_id != format!("{:?}", worker.thread) {
            gate.errors
                .push("panic worker event must identify its emitter".to_owned());
        }
        gate.started.push(worker.clone());
        self.changed.notify_all();
        while !gate.workers_open {
            gate = self.changed.wait(gate).unwrap();
        }
        let first = gate.first == Some(worker.thread);
        drop(gate);
        if first {
            // Do not poison either gate when intentionally panicking in the worker body.
            std::panic::panic_any("semantic release controlled panic");
        }
        PANIC_RELEASE_TLS.with(|tls| {
            *tls.borrow_mut() = Some(PanicReleaseTls {
                state: Arc::clone(self),
                worker,
            });
        });
    }
}

struct PanicReleaseTls {
    state: Arc<PanicReleaseState>,
    worker: ReleaseWorker,
}

impl Drop for PanicReleaseTls {
    fn drop(&mut self) {
        let mut gate = self.state.gate.lock().unwrap();
        gate.tls_started.push(self.worker.clone());
        self.state.changed.notify_all();
        while !gate.tls_open {
            gate = self.state.changed.wait(gate).unwrap();
        }
        gate.tls_finished.push(self.worker.clone());
        self.state.changed.notify_all();
    }
}

thread_local! {
    static PANIC_RELEASE_TLS: RefCell<Option<PanicReleaseTls>> = const { RefCell::new(None) };
}

#[test]
fn semantic_release_worker_panic_joins_other_workers_through_tls_teardown() {
    const NAME: &str = "tests::semantic_release::semantic_release_worker_panic_joins_other_workers_through_tls_teardown";
    if ReleaseLoader::isolated(NAME) {
        return;
    }
    let (state, txn) = ReleaseLoader::transaction(72);
    let panic = Arc::new(PanicReleaseState::default());
    tracing::subscriber::set_global_default(ReleaseLog {
        state: Arc::clone(&state),
        next_span: AtomicU64::new(0),
        enabled: true,
        panic: Some(Arc::clone(&panic)),
    })
    .expect("isolated panic test owns its subscriber");
    ReleaseLoader::populate(&txn, 72);
    let owner_panic = Arc::clone(&panic);
    let owner = thread::Builder::new()
        .name("semantic-test".to_owned())
        .spawn(move || {
            let parent = thread::current().id();
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| drop(txn)));
            owner_panic.gate.lock().unwrap().owner_finished = true;
            owner_panic.changed.notify_all();
            (parent, result)
        })
        .expect("controlled transaction owner should start");

    let gate = panic.gate.lock().unwrap();
    let (mut gate, _) = panic
        .changed
        .wait_timeout_while(gate, Duration::from_secs(10), |gate| {
            gate.started.len() < 8 && !gate.owner_finished
        })
        .unwrap();
    // The pinned Rust 1.98.1 implementation allocates monotonically increasing ThreadIds
    // in spawn_scoped before OS thread creation. Its smallest worker ID is therefore the
    // first handle joined, regardless of start-event scheduling. This isolated fixture
    // relies on that implementation detail; these numbers are never kernel task IDs.
    let first = gate
        .started
        .iter()
        .map(|worker| {
            let debug = format!("{:?}", worker.thread);
            let number = debug
                .strip_prefix("ThreadId(")
                .and_then(|value| value.strip_suffix(')'))
                .and_then(|value| value.parse::<u64>().ok());
            (number, worker.thread)
        })
        .collect::<Vec<_>>();
    if first.iter().any(|(number, _)| number.is_none()) {
        gate.errors
            .push("pinned ThreadId debug format changed".to_owned());
    }
    gate.first = first
        .iter()
        .filter_map(|(number, id)| number.map(|number| (number, *id)))
        .min_by_key(|(number, _)| *number)
        .map(|(_, id)| id);
    gate.workers_open = true;
    panic.changed.notify_all();
    let (gate, _) = panic
        .changed
        .wait_timeout_while(gate, Duration::from_secs(10), |gate| {
            gate.tls_started.len() < 7
        })
        .unwrap();
    // Automatic scope completion can precede TLS teardown. Keep the seven destructors
    // held while giving an incorrectly early unwind a bounded opportunity to finish.
    // This is a synchronization guard, not a performance requirement.
    let (mut gate, _) = panic
        .changed
        .wait_timeout_while(gate, Duration::from_secs(1), |gate| !gate.owner_finished)
        .unwrap();
    let returned_with_held_tls = gate.owner_finished;
    gate.tls_open = true;
    panic.changed.notify_all();
    drop(gate);
    // Every owned gate opens and the owner is manually joined before any assertion.
    let (parent, outcome) = owner.join().expect("owner must contain the worker panic");
    let gate = panic.gate.lock().unwrap();
    let started = gate.started.clone();
    let tls_started = gate.tls_started.clone();
    let tls_finished = gate.tls_finished.clone();
    let errors = gate.errors.clone();
    drop(gate);
    let remaining = ReleaseWorker::remaining(&started);
    let payload = outcome.expect_err("worker panic must propagate from transaction retirement");
    assert_eq!(
        payload.downcast_ref::<&str>(),
        Some(&"semantic release controlled panic")
    );
    assert_eq!(started.len(), 8);
    assert_eq!(tls_started.len(), 7);
    assert_eq!(tls_finished.len(), 7);
    assert!(errors.is_empty(), "panic trace errors: {errors:?}");
    for worker in &tls_started {
        assert_eq!(worker.name, "release-semanti");
        assert_eq!(started.iter().filter(|value| *value == worker).count(), 1);
        assert_eq!(
            tls_started.iter().filter(|value| *value == worker).count(),
            1
        );
        assert_eq!(
            tls_finished.iter().filter(|value| *value == worker).count(),
            1
        );
    }
    assert!(
        remaining.is_empty(),
        "panic-path workers remain: {remaining:?}"
    );
    assert_eq!(state.live_payloads(), (0, 0));
    let loader_drops = state.loader_drops.lock().unwrap().clone();
    assert_eq!(loader_drops.len(), 1);
    assert_eq!(loader_drops[0].0, parent);
    assert!(
        !returned_with_held_tls,
        "transaction retirement propagated its panic before manually joining other workers' TLS teardown"
    );
}

#[test]
fn live_semantic_release_worker_is_retained_at_observation_deadline() {
    const NAME: &str =
        "tests::semantic_release::live_semantic_release_worker_is_retained_at_observation_deadline";
    if ReleaseLoader::isolated(NAME) {
        return;
    }
    let (started, ready) = std::sync::mpsc::channel();
    let (release, held) = std::sync::mpsc::channel();
    let worker = thread::Builder::new()
        .name("release-semantic".to_owned())
        .spawn(move || {
            started.send(ReleaseWorker::current()).unwrap();
            held.recv().unwrap();
        })
        .expect("violating semantic worker should start");
    let started = ready.recv_timeout(Duration::from_secs(10));
    let observed = std::panic::catch_unwind(|| ReleaseWorker::remaining(&[]));
    let released = release.send(());
    let joined = worker.join();
    let after_join = ReleaseWorker::remaining(&[]);
    // Keep the rejecting observation, but release and manually join before asserting it.
    assert!(released.is_ok());
    assert!(joined.is_ok());
    let started = started.expect("violating semantic worker must reach its hold gate");
    let observed = observed.expect("semantic worker observation should succeed");
    assert_eq!(
        observed.len(),
        1,
        "held worker must remain at deadline: {observed:?}"
    );
    assert!(observed[0].contains(&format!("task={} ", started.task)));
    assert!(
        after_join.is_empty(),
        "joined worker must disappear: {after_join:?}"
    );
}

#[test]
fn decoded_semantic_release_joins_eight_workers_and_preserves_retained_transaction() {
    const NAME: &str = "tests::semantic_release::decoded_semantic_release_joins_eight_workers_and_preserves_retained_transaction";
    if ReleaseLoader::isolated(NAME) {
        return;
    }
    let (state, txn) = ReleaseLoader::transaction(72);
    ReleaseLog::install(&state, true);
    ReleaseLoader::populate(&txn, 72);
    txn.package(PackageSlot(0)).unwrap();
    assert_eq!(state.live_payloads(), (72, 72));
    let retained = txn.clone();
    let first_parent = state.drop_with_gated_workers(txn);
    // Large decoded transactions use bounded workers. Assert after opening the gate and joining.
    state.assert_joined_workers(first_parent, 8);
    assert_eq!(state.live_payloads(), (72, 72));
    let loader_drops = state.loader_drops.lock().unwrap().clone();
    assert!(loader_drops.is_empty());
    ReleaseLoader::populate(&retained, 72);
    assert_eq!(
        retained
            .package(PackageSlot(0))
            .unwrap()
            .crate_items(CrateId(0))
            .unwrap()
            .origin(),
        DefMapRef::Crate(ReleaseLoader::crate_ref(0))
    );
    assert_eq!(
        state.reads.load(Ordering::Relaxed),
        144,
        "retained clone must not reload"
    );
    let final_parent = state.drop_with_gated_workers(retained);
    state.assert_joined_workers(final_parent, 8);
    assert_eq!(state.live_payloads(), (0, 0));
    let loader_drops = state.loader_drops.lock().unwrap().clone();
    assert_eq!(loader_drops, vec![(final_parent, 0, 0, 8)]);
    ReleaseWorker::assert_none_remaining();
}

#[test]
fn sparse_decoded_semantic_release_stays_serial() {
    const NAME: &str = "tests::semantic_release::sparse_decoded_semantic_release_stays_serial";
    if ReleaseLoader::isolated(NAME) {
        return;
    }
    let (state, txn) = ReleaseLoader::transaction(72);
    ReleaseLog::install(&state, true);
    assert_eq!(
        txn.included_crates(&CancellationToken::new())
            .unwrap()
            .len(),
        72
    );
    ReleaseLoader::populate(&txn, 63);
    assert_eq!(state.live_payloads(), (63, 63));
    let parent = state.drop_with_gated_workers(txn);
    // Dense package directories do not justify workers for a sparse decoded subset.
    state.assert_joined_workers(parent, 0);
    assert_eq!(state.live_payloads(), (0, 0));
    let drops = state.loader_drops.lock().unwrap().clone();
    assert_eq!(drops.len(), 1);
    assert_eq!(drops[0].0, parent);
    ReleaseWorker::assert_none_remaining();
}

#[test]
fn manifest_only_semantic_release_stays_serial() {
    const NAME: &str = "tests::semantic_release::manifest_only_semantic_release_stays_serial";
    if ReleaseLoader::isolated(NAME) {
        return;
    }
    let (state, txn) = ReleaseLoader::transaction(72);
    ReleaseLog::install(&state, true);
    assert_eq!(
        txn.included_crates(&CancellationToken::new())
            .unwrap()
            .len(),
        72
    );
    assert_eq!(state.reads.load(Ordering::Relaxed), 0);
    let parent = state.drop_with_gated_workers(txn);
    state.assert_joined_workers(parent, 0);
    assert_eq!(state.live_payloads(), (0, 0));
    let drops = state.loader_drops.lock().unwrap().clone();
    assert_eq!(drops.len(), 1);
    assert_eq!(drops[0].0, parent);
    ReleaseWorker::assert_none_remaining();
}

#[test]
fn semantic_release_without_trace_releases_payloads_and_retains_clones() {
    const NAME: &str = "tests::semantic_release::semantic_release_without_trace_releases_payloads_and_retains_clones";
    if ReleaseLoader::isolated(NAME) {
        return;
    }
    let (state, txn) = ReleaseLoader::transaction(72);
    ReleaseLog::install(&state, false);
    ReleaseLoader::populate(&txn, 72);
    let retained = txn.clone();
    drop(txn);
    assert_eq!(state.live_payloads(), (72, 72));
    ReleaseLoader::populate(&retained, 72);
    assert_eq!(state.reads.load(Ordering::Relaxed), 144);
    let parent = thread::current().id();
    drop(retained);
    assert_eq!(state.live_payloads(), (0, 0));
    let drops = state.loader_drops.lock().unwrap().clone();
    assert_eq!(drops, vec![(parent, 0, 0, 0)]);
    // Disabled tracing cannot count workers. Actual payload/resource ownership still proves
    // synchronous retirement and catches cleanup mistakenly hidden behind a TRACE guard.
    ReleaseWorker::assert_none_remaining();
}

#[test]
fn denied_semantic_release_worker_start_releases_cells_on_parent() {
    const NAME: &str =
        "tests::semantic_release::denied_semantic_release_worker_start_releases_cells_on_parent";
    if ReleaseLoader::isolated(NAME) {
        return;
    }
    let (state, txn) = ReleaseLoader::transaction(72);
    ReleaseLog::install(&state, true);
    ReleaseLoader::populate(&txn, 72);
    assert_eq!(state.live_payloads(), (72, 72));
    // Do not block any unexpectedly permitted worker: restore limits and report a failed
    // denial control after all cleanup, including on privileged systems.
    state.gate.lock().unwrap().open = true;
    let mut original = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    // SAFETY: original is valid writable storage for this isolated process's limit.
    assert_eq!(
        unsafe { libc::getrlimit(libc::RLIMIT_NPROC, &mut original) },
        0
    );
    let restricted = libc::rlimit {
        rlim_cur: 0,
        rlim_max: original.rlim_max,
    };
    // SAFETY: only this isolated child's soft limit changes; its hard limit is preserved.
    assert_eq!(
        unsafe { libc::setrlimit(libc::RLIMIT_NPROC, &restricted) },
        0
    );
    let control = thread::Builder::new().spawn(|| {});
    let failure = match control {
        Ok(worker) => {
            worker.join().unwrap();
            None
        }
        Err(error) => error.raw_os_error(),
    };
    let parent = thread::current().id();
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| drop(txn)));
    // SAFETY: original came from getrlimit. Restore before asserting or resuming unwind.
    assert_eq!(unsafe { libc::setrlimit(libc::RLIMIT_NPROC, &original) }, 0);
    if let Err(panic) = result {
        std::panic::resume_unwind(panic);
    }
    assert_eq!(
        failure,
        Some(libc::EAGAIN),
        "control must actually deny thread creation"
    );
    state.assert_joined_workers(parent, 0);
    assert_eq!(state.live_payloads(), (0, 0));
    let drops = state.loader_drops.lock().unwrap().clone();
    assert_eq!(drops, vec![(parent, 0, 0, 0)]);
    ReleaseWorker::assert_none_remaining();
}
