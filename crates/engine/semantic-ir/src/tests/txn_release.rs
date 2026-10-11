use std::{
    sync::{
        Arc, Mutex, Weak,
        atomic::{AtomicUsize, Ordering},
    },
    thread::ThreadId,
};

use rg_ir_model::{CrateId, CrateRef, DefMapRef, PackageSlot};
use rg_package_store::PackageStoreError;

use crate::{
    ItemLookupIndex, ItemStore, ItemStoreBuilder, LoadSemanticIr, SemanticIrLoader,
    SemanticIrReadTxn, SemanticPackage, SemanticPackageManifest,
};

#[derive(Debug, Default)]
struct ReleaseState {
    items: Mutex<Vec<Weak<ItemStore>>>,
    indexes: Mutex<Vec<Weak<ItemLookupIndex>>>,
    manifests: AtomicUsize,
    loader_releases: Mutex<Vec<(ThreadId, bool)>>,
}

impl ReleaseState {
    fn decoded_released(&self) -> bool {
        self.items
            .lock()
            .unwrap()
            .iter()
            .all(|item| item.upgrade().is_none())
            && self
                .indexes
                .lock()
                .unwrap()
                .iter()
                .all(|index| index.upgrade().is_none())
    }

    fn transaction(&self, packages: usize) -> SemanticIrReadTxn<'_> {
        SemanticIrReadTxn::from_store_entries(
            (0..packages).map(|_| (true, None)),
            SemanticIrLoader::new(ReleaseLoader {
                state: self,
                failed_package: None,
            }),
        )
    }

    fn load(txn: &SemanticIrReadTxn<'_>, packages: usize) -> Result<(), PackageStoreError> {
        for package in 0..packages {
            let crate_ref = CrateRef {
                package: PackageSlot(package),
                crate_id: CrateId(0),
            };
            assert!(txn.items(crate_ref)?.is_some());
            assert!(txn.item_lookup_index(crate_ref)?.is_some());
        }
        Ok(())
    }

    fn assert_released(&self, items: usize, indexes: usize) {
        assert_eq!(self.items.lock().unwrap().len(), items);
        assert_eq!(self.indexes.lock().unwrap().len(), indexes);
        assert!(
            self.decoded_released(),
            "drop must finish every decoded release before returning"
        );
        assert_eq!(
            *self.loader_releases.lock().unwrap(),
            vec![(std::thread::current().id(), true)],
            "last loader cleanup must run on the query lane after every decoded value is released",
        );
    }
}

// The loader borrows its observer: it cannot escape to a detached 'static cleanup thread.
#[derive(Debug)]
struct ReleaseLoader<'a> {
    state: &'a ReleaseState,
    failed_package: Option<PackageSlot>,
}

impl Drop for ReleaseLoader<'_> {
    fn drop(&mut self) {
        self.state
            .loader_releases
            .lock()
            .unwrap()
            .push((std::thread::current().id(), self.state.decoded_released()));
    }
}

impl LoadSemanticIr for ReleaseLoader<'_> {
    fn load_manifest(
        &self,
        package: PackageSlot,
    ) -> Result<Arc<SemanticPackageManifest>, PackageStoreError> {
        self.state.manifests.fetch_add(1, Ordering::SeqCst);
        let origin = DefMapRef::Crate(CrateRef {
            package,
            crate_id: CrateId(0),
        });
        let items = ItemStoreBuilder::new(origin, 0).build();
        Ok(Arc::new(SemanticPackage::new(vec![items]).manifest()))
    }

    fn load_items(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
    ) -> Result<Arc<ItemStore>, PackageStoreError> {
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
        package: PackageSlot,
        _crate_id: CrateId,
    ) -> Result<Arc<ItemLookupIndex>, PackageStoreError> {
        if self.failed_package == Some(package) {
            return Err(PackageStoreError::stale_package(
                package,
                "release fixture failure",
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
fn semantic_txn_release_finishes_decoded_entries_before_loader_cleanup() {
    // Cover the serial/parallel boundary with actual loaded cells. Weak references observe data
    // ownership, while the final loader observes the boundary that would otherwise nest cleanup.
    for packages in [1, 63, 64, 128] {
        let state = ReleaseState::default();
        let txn = state.transaction(packages);
        ReleaseState::load(&txn, packages).unwrap();
        assert!(!state.decoded_released());
        drop(txn);
        state.assert_released(packages, packages);
    }
}

#[test]
fn semantic_txn_release_does_not_load_unloaded_or_manifest_only_entries() {
    for load_manifests in [false, true] {
        let state = ReleaseState::default();
        let txn = state.transaction(128);
        if load_manifests {
            assert_eq!(
                txn.included_crates(&rg_std::CancellationToken::new())
                    .unwrap()
                    .len(),
                128
            );
        }
        let before = state.manifests.load(Ordering::SeqCst);
        drop(txn);
        assert_eq!(state.manifests.load(Ordering::SeqCst), before);
        assert_eq!(before, if load_manifests { 128 } else { 0 });
        state.assert_released(0, 0);
    }
}

#[test]
fn semantic_txn_release_preserves_cloned_transaction_ownership() {
    let state = ReleaseState::default();
    let txn = state.transaction(128);
    ReleaseState::load(&txn, 128).unwrap();
    let clone = txn.clone();
    let crate_ref = CrateRef {
        package: PackageSlot(0),
        crate_id: CrateId(0),
    };
    assert!(std::ptr::eq(
        txn.items(crate_ref).unwrap().unwrap(),
        clone.items(crate_ref).unwrap().unwrap()
    ));
    drop(txn);
    assert!(state.loader_releases.lock().unwrap().is_empty());
    assert!(
        state
            .items
            .lock()
            .unwrap()
            .iter()
            .all(|item| item.upgrade().is_some())
    );
    assert!(
        state
            .indexes
            .lock()
            .unwrap()
            .iter()
            .all(|index| index.upgrade().is_some())
    );
    ReleaseState::load(&clone, 128).unwrap();
    assert_eq!(state.manifests.load(Ordering::SeqCst), 128);
    drop(clone);
    state.assert_released(128, 128);
}

#[test]
fn semantic_txn_release_preserves_project_owned_resident_data() {
    let state = ReleaseState::default();
    let resident = Arc::new(SemanticPackage::default());
    let resident_observer = Arc::downgrade(&resident);
    let txn = SemanticIrReadTxn::from_store_entries(
        (0..129).map(|slot| (true, (slot == 128).then(|| Arc::clone(&resident)))),
        SemanticIrLoader::new(ReleaseLoader {
            state: &state,
            failed_package: None,
        }),
    );
    ReleaseState::load(&txn, 128).unwrap();
    drop(txn);
    state.assert_released(128, 128);
    assert_eq!(Arc::strong_count(&resident), 1);
    assert!(resident_observer.upgrade().is_some());
    drop(resident);
    assert!(resident_observer.upgrade().is_none());
}

#[test]
fn semantic_txn_release_on_storage_error_finishes_partial_reads() {
    let state = ReleaseState::default();
    let result = {
        let txn = SemanticIrReadTxn::from_store_entries(
            (0..128).map(|_| (true, None)),
            SemanticIrLoader::new(ReleaseLoader {
                state: &state,
                failed_package: Some(PackageSlot(127)),
            }),
        );
        ReleaseState::load(&txn, 128)
    };
    let error = result.expect_err("fixture must fail its last index read");
    assert!(format!("{error:#}").contains("release fixture failure"));
    state.assert_released(128, 127);
}

#[test]
fn semantic_txn_release_on_unwind_finishes_before_loader_cleanup() {
    let state = ReleaseState::default();
    let result = std::panic::catch_unwind(|| {
        let txn = state.transaction(128);
        ReleaseState::load(&txn, 128).unwrap();
        panic!("request fixture unwind");
    });
    assert!(result.is_err());
    state.assert_released(128, 128);
}
