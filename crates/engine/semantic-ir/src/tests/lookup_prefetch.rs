use std::{
    num::NonZeroUsize,
    sync::{
        Arc, Condvar, Mutex, Weak,
        atomic::{AtomicUsize, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, Sender},
    },
    time::Duration,
};

use rg_ir_model::{CrateId, CrateRef, DefMapRef, PackageSlot};
use rg_package_store::PackageStoreError;
use rg_std::CancellationToken;

use crate::{
    ItemLookupIndex, ItemStore, ItemStoreBuilder, LoadSemanticIr, SemanticIrLoader,
    SemanticIrReadTxn, SemanticPackage, SemanticPackageManifest,
};

#[derive(Debug)]
struct LookupReads {
    released: Mutex<bool>,
    release: Condvar,
    started: Sender<CrateRef>,
    active: AtomicUsize,
    peak: AtomicUsize,
    reads: Mutex<Vec<CrateRef>>,
    indexes: Mutex<Vec<Weak<ItemLookupIndex>>>,
}

#[derive(Debug, Clone)]
struct LookupLoader {
    state: Arc<LookupReads>,
    failed_package: Option<PackageSlot>,
    blocked_package: Option<PackageSlot>,
    panic_package: Option<PackageSlot>,
}

impl LookupLoader {
    fn new(released: bool, failed_package: Option<PackageSlot>) -> (Self, Receiver<CrateRef>) {
        let (started, receiver) = mpsc::channel();
        (
            Self {
                state: Arc::new(LookupReads {
                    released: Mutex::new(released),
                    release: Condvar::new(),
                    started,
                    active: AtomicUsize::new(0),
                    peak: AtomicUsize::new(0),
                    reads: Mutex::new(Vec::new()),
                    indexes: Mutex::new(Vec::new()),
                }),
                failed_package,
                blocked_package: None,
                panic_package: None,
            },
            receiver,
        )
    }

    fn transaction(&self, packages: usize) -> SemanticIrReadTxn<'static> {
        SemanticIrReadTxn::from_store_entries(
            (0..packages).map(|_| (true, None)),
            SemanticIrLoader::new(self.clone()),
        )
    }

    fn release(&self) {
        *self.state.released.lock().unwrap() = true;
        self.state.release.notify_all();
    }

    fn started_reads(
        receiver: &Receiver<CrateRef>,
        count: usize,
    ) -> Result<Vec<CrateRef>, RecvTimeoutError> {
        (0..count)
            .map(|_| receiver.recv_timeout(Duration::from_secs(10)))
            .collect()
    }

    fn crates(packages: usize) -> Vec<CrateRef> {
        (0..packages)
            .map(|slot| CrateRef {
                package: PackageSlot(slot),
                crate_id: CrateId(0),
            })
            .collect()
    }
}

impl LoadSemanticIr for LookupLoader {
    fn load_manifest(
        &self,
        package: PackageSlot,
    ) -> Result<Arc<SemanticPackageManifest>, PackageStoreError> {
        let owner = CrateRef {
            package,
            crate_id: CrateId(0),
        };
        let items = ItemStoreBuilder::new(DefMapRef::Crate(owner), 0).build();
        Ok(Arc::new(SemanticPackage::new(vec![items]).manifest()))
    }

    fn load_items(
        &self,
        _package: PackageSlot,
        _crate_id: CrateId,
    ) -> Result<Arc<ItemStore>, PackageStoreError> {
        panic!("lookup prefetch must not load declarations");
    }

    fn load_lookup_index(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
    ) -> Result<Arc<ItemLookupIndex>, PackageStoreError> {
        let crate_ref = CrateRef { package, crate_id };
        let active = self.state.active.fetch_add(1, Ordering::SeqCst) + 1;
        self.state.peak.fetch_max(active, Ordering::SeqCst);
        self.state.reads.lock().unwrap().push(crate_ref);
        self.state.started.send(crate_ref).unwrap();
        let mut released = self.state.released.lock().unwrap();
        while !*released
            && self
                .blocked_package
                .is_none_or(|blocked| blocked == package)
        {
            released = self.state.release.wait(released).unwrap();
        }
        drop(released);
        self.state.active.fetch_sub(1, Ordering::SeqCst);
        if self.panic_package == Some(package) {
            panic!("lookup fixture panic");
        }
        if self.failed_package == Some(package) {
            return Err(PackageStoreError::stale_package(
                package,
                "lookup fixture failure",
            ));
        }
        let index = Arc::new(ItemLookupIndex::default());
        self.state
            .indexes
            .lock()
            .unwrap()
            .push(Arc::downgrade(&index));
        Ok(index)
    }
}

#[test]
fn lookup_prefetch_keeps_reading_when_one_artifact_is_slow() {
    for reader_count in [8, 16] {
        let (mut loader, started) = LookupLoader::new(false, None);
        loader.blocked_package = Some(PackageSlot(0));
        let txn = loader.transaction(20);
        let request = std::thread::spawn(move || {
            txn.prefetch_lookup_indexes(
                &LookupLoader::crates(20),
                NonZeroUsize::new(reader_count).unwrap(),
                &CancellationToken::new(),
            )
        });
        // Keep the first artifact blocked while the other readers take all remaining work.
        // Fixed chunks leave later artifacts behind that blocked read.
        let progress = LookupLoader::started_reads(&started, 20);
        loader.release();
        request.join().unwrap().unwrap();
        assert_eq!(
            progress
                .expect("a slow artifact must not prevent other readers from taking work")
                .len(),
            20
        );
        assert_eq!(loader.state.active.load(Ordering::SeqCst), 0);
        assert_eq!(loader.state.reads.lock().unwrap().len(), 20);
    }
}

#[test]
fn lookup_prefetch_overlaps_bounded_reads_and_releases_them_with_the_transaction() {
    for (reader_count, crate_count) in [(1, 20), (8, 20), (16, 3), (16, 0), (16, 20)] {
        let (loader, started) = LookupLoader::new(false, None);
        let txn = loader.transaction(crate_count);
        let crates = LookupLoader::crates(crate_count);
        let cancellation = CancellationToken::new();
        let reader_limit = NonZeroUsize::new(reader_count).unwrap();
        let expected_readers = crate_count.min(reader_count);
        std::thread::scope(|scope| {
            let request =
                scope.spawn(|| txn.prefetch_lookup_indexes(&crates, reader_limit, &cancellation));
            // Loads block until released. The timeout bounds a broken test, not performance.
            let overlap = LookupLoader::started_reads(&started, expected_readers);
            loader.release();
            request.join().unwrap().unwrap();
            assert_eq!(
                overlap
                    .expect("the caller-selected reader bound must overlap")
                    .len(),
                expected_readers
            );
        });
        assert_eq!(loader.state.peak.load(Ordering::SeqCst), expected_readers);
        assert_eq!(loader.state.active.load(Ordering::SeqCst), 0);
        assert_eq!(loader.state.reads.lock().unwrap().len(), crate_count);
        for crate_ref in &crates {
            assert!(txn.item_lookup_index(*crate_ref).unwrap().is_some());
        }
        assert_eq!(loader.state.reads.lock().unwrap().len(), crate_count);
        let cloned = txn.clone();
        drop(txn);
        assert!(
            loader
                .state
                .indexes
                .lock()
                .unwrap()
                .iter()
                .all(|index| index.upgrade().is_some())
        );
        for crate_ref in crates {
            assert!(cloned.item_lookup_index(crate_ref).unwrap().is_some());
        }
        assert_eq!(loader.state.reads.lock().unwrap().len(), crate_count);
        drop(cloned);
        assert!(
            loader
                .state
                .indexes
                .lock()
                .unwrap()
                .iter()
                .all(|index| index.upgrade().is_none())
        );
    }
}

#[test]
fn lookup_prefetch_cancellation_stops_each_readers_next_load() {
    for (reader_count, crate_count) in [(1, 20), (8, 20), (16, 3), (16, 20)] {
        let (loader, started) = LookupLoader::new(false, None);
        let txn = loader.transaction(crate_count);
        let crates = LookupLoader::crates(crate_count);
        let cancellation = CancellationToken::new();
        let reader_limit = NonZeroUsize::new(reader_count).unwrap();
        let expected_readers = crate_count.min(reader_count);
        std::thread::scope(|scope| {
            let request =
                scope.spawn(|| txn.prefetch_lookup_indexes(&crates, reader_limit, &cancellation));
            let overlap = LookupLoader::started_reads(&started, expected_readers);
            cancellation.cancel();
            loader.release();
            let error = request
                .join()
                .unwrap()
                .expect_err("cancelled prefetch must fail");
            assert!(error.downcast_ref::<rg_std::Cancelled>().is_some());
            overlap.expect("each selected reader reaches its controlled first load");
        });
        assert_eq!(loader.state.reads.lock().unwrap().len(), expected_readers);
        assert_eq!(loader.state.active.load(Ordering::SeqCst), 0);
    }
}

#[test]
fn lookup_prefetch_preserves_storage_errors_and_does_not_cache_failure() {
    for reader_count in [1, 8, 16] {
        let (loader, started) = LookupLoader::new(false, Some(PackageSlot(0)));
        let txn = loader.transaction(2);
        let crates = LookupLoader::crates(2);
        let reader_limit = NonZeroUsize::new(reader_count).unwrap();
        std::thread::scope(|scope| {
            let request = scope.spawn(|| {
                let result =
                    txn.prefetch_lookup_indexes(&crates, reader_limit, &CancellationToken::new());
                assert_eq!(loader.state.active.load(Ordering::SeqCst), 0);
                result
            });
            let overlap = LookupLoader::started_reads(&started, reader_count.min(2));
            loader.release();
            let error = request
                .join()
                .unwrap()
                .expect_err("failed lookup artifact must remain an error");
            assert!(error.downcast_ref::<PackageStoreError>().is_some());
            assert!(format!("{error:#}").contains("lookup fixture failure"));
            overlap.expect("all selected reads entered before the storage error was released");
        });
        assert_eq!(loader.state.active.load(Ordering::SeqCst), 0);
        assert!(txn.item_lookup_index(crates[0]).is_err());
        assert_eq!(
            loader
                .state
                .reads
                .lock()
                .unwrap()
                .iter()
                .filter(|&&krate| krate == crates[0])
                .count(),
            2
        );
    }
}

#[test]
fn lookup_prefetch_cancelled_before_start_reads_nothing() {
    for (reader_count, crate_count) in [(1, 20), (8, 20), (16, 20), (16, 0)] {
        let (loader, _started) = LookupLoader::new(false, None);
        let txn = loader.transaction(crate_count);
        let cancellation = CancellationToken::new();
        cancellation.cancel();
        let error = txn
            .prefetch_lookup_indexes(
                &LookupLoader::crates(crate_count),
                NonZeroUsize::new(reader_count).unwrap(),
                &cancellation,
            )
            .expect_err("already cancelled prefetch must fail");
        assert!(error.downcast_ref::<rg_std::Cancelled>().is_some());
        assert!(loader.state.reads.lock().unwrap().is_empty());
    }
}

#[test]
fn lookup_prefetch_worker_panic_joins_other_reads_before_unwinding() {
    let (mut loader, started) = LookupLoader::new(false, None);
    loader.panic_package = Some(PackageSlot(0));
    let txn = loader.transaction(2);
    let crates = LookupLoader::crates(2);
    std::thread::scope(|scope| {
        let request = scope.spawn(|| {
            txn.prefetch_lookup_indexes(
                &crates,
                NonZeroUsize::new(16).unwrap(),
                &CancellationToken::new(),
            )
        });
        let overlap = LookupLoader::started_reads(&started, 2);
        loader.release();
        assert!(
            request.join().is_err(),
            "worker panic must propagate to the request"
        );
        overlap.expect("both reads entered before allowing one to panic");
    });
    assert_eq!(loader.state.active.load(Ordering::SeqCst), 0);
    assert_eq!(loader.state.reads.lock().unwrap().len(), 2);
    // The successful sibling finished and its value stayed in the transaction before unwinding.
    assert!(txn.item_lookup_index(crates[1]).unwrap().is_some());
    assert_eq!(loader.state.reads.lock().unwrap().len(), 2);
    drop(txn);
    assert!(
        loader
            .state
            .indexes
            .lock()
            .unwrap()
            .iter()
            .all(|index| index.upgrade().is_none())
    );
}
