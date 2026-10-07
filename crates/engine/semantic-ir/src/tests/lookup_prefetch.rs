use std::{
    sync::{
        Arc, Condvar, Mutex, Weak,
        atomic::{AtomicUsize, Ordering},
        mpsc::{self, Receiver, Sender},
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
        while !*released {
            released = self.state.release.wait(released).unwrap();
        }
        self.state.active.fetch_sub(1, Ordering::SeqCst);
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
fn lookup_prefetch_overlaps_bounded_reads_and_releases_them_with_the_transaction() {
    let (loader, started) = LookupLoader::new(false, None);
    let txn = loader.transaction(12);
    let crates = LookupLoader::crates(12);
    let cancellation = CancellationToken::new();
    std::thread::scope(|scope| {
        let request = scope.spawn(|| txn.prefetch_lookup_indexes(&crates, &cancellation));
        // Each reader blocks inside its first load. Observe the overlap before releasing any read;
        // the deadline only bounds a broken implementation, not a performance assertion.
        let overlap = (0..4)
            .map(|_| started.recv_timeout(Duration::from_secs(10)))
            .collect::<Result<Vec<_>, _>>();
        loader.release();
        request.join().unwrap().unwrap();
        assert_eq!(
            overlap.expect("four artifact readers should overlap").len(),
            4
        );
    });
    assert_eq!(loader.state.peak.load(Ordering::SeqCst), 4);
    assert_eq!(loader.state.active.load(Ordering::SeqCst), 0);
    assert_eq!(loader.state.reads.lock().unwrap().len(), 12);
    for crate_ref in crates {
        assert!(txn.item_lookup_index(crate_ref).unwrap().is_some());
    }
    assert_eq!(loader.state.reads.lock().unwrap().len(), 12);
    assert!(
        loader
            .state
            .indexes
            .lock()
            .unwrap()
            .iter()
            .all(|index| index.upgrade().is_some())
    );
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

#[test]
fn lookup_prefetch_cancellation_stops_each_readers_next_load() {
    let (loader, started) = LookupLoader::new(false, None);
    let txn = loader.transaction(12);
    let crates = LookupLoader::crates(12);
    let cancellation = CancellationToken::new();
    std::thread::scope(|scope| {
        let request = scope.spawn(|| txn.prefetch_lookup_indexes(&crates, &cancellation));
        let overlap = (0..4)
            .map(|_| started.recv_timeout(Duration::from_secs(10)))
            .collect::<Result<Vec<_>, _>>();
        cancellation.cancel();
        loader.release();
        let error = request
            .join()
            .unwrap()
            .expect_err("cancelled prefetch must fail");
        assert!(error.downcast_ref::<rg_std::Cancelled>().is_some());
        overlap.expect("each reader reaches the controlled first load");
    });
    assert_eq!(loader.state.reads.lock().unwrap().len(), 4);
    assert_eq!(loader.state.active.load(Ordering::SeqCst), 0);
}

#[test]
fn lookup_prefetch_preserves_storage_errors_and_does_not_cache_failure() {
    let (loader, _started) = LookupLoader::new(true, Some(PackageSlot(0)));
    let txn = loader.transaction(2);
    let crates = LookupLoader::crates(2);
    let error = txn
        .prefetch_lookup_indexes(&crates, &CancellationToken::new())
        .expect_err("failed lookup artifact must remain an error");
    assert!(error.downcast_ref::<PackageStoreError>().is_some());
    assert!(format!("{error:#}").contains("lookup fixture failure"));
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

#[test]
fn lookup_prefetch_cancelled_before_start_reads_nothing() {
    let (loader, _started) = LookupLoader::new(false, None);
    let txn = loader.transaction(8);
    let cancellation = CancellationToken::new();
    cancellation.cancel();
    let error = txn
        .prefetch_lookup_indexes(&LookupLoader::crates(8), &cancellation)
        .expect_err("already cancelled prefetch must fail");
    assert!(error.downcast_ref::<rg_std::Cancelled>().is_some());
    assert!(loader.state.reads.lock().unwrap().is_empty());
}
