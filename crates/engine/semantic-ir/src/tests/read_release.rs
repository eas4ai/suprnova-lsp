use std::sync::{Arc, Mutex, Weak};

use rg_ir_model::{CrateId, CrateRef, DefMapRef, PackageSlot};
use rg_package_store::PackageStoreError;

use crate::{
    ItemLookupIndex, ItemStore, ItemStoreBuilder, LoadSemanticIr, SemanticIrLoader,
    SemanticIrReadTxn, SemanticPackage, SemanticPackageManifest,
};

#[derive(Debug, Default)]
struct Reads {
    manifests: Mutex<usize>,
    items: Mutex<Vec<Weak<ItemStore>>>,
    indexes: Mutex<Vec<Weak<ItemLookupIndex>>>,
    loader_release: Mutex<Option<(std::thread::ThreadId, bool)>>,
}

impl Reads {
    fn transaction(&self, count: usize) -> SemanticIrReadTxn<'_> {
        SemanticIrReadTxn::from_store_entries(
            (0..count).map(|_| (true, None)),
            SemanticIrLoader::new(Loader { reads: self }),
        )
    }

    fn payloads_released(&self) -> bool {
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

    fn assert_released_on_caller(&self) {
        assert_eq!(
            *self.loader_release.lock().unwrap(),
            Some((std::thread::current().id(), true)),
            "all request-owned payloads must be released before the last loader on the caller",
        );
        assert!(self.payloads_released());
    }
}

// Borrow the fixture rather than requiring a 'static loader. Scoped release must keep the
// project's reader references alive while decoded values are being released.
#[derive(Debug)]
struct Loader<'a> {
    reads: &'a Reads,
}

impl Drop for Loader<'_> {
    fn drop(&mut self) {
        *self.reads.loader_release.lock().unwrap() =
            Some((std::thread::current().id(), self.reads.payloads_released()));
    }
}

impl LoadSemanticIr for Loader<'_> {
    fn load_manifest(
        &self,
        package: PackageSlot,
    ) -> Result<Arc<SemanticPackageManifest>, PackageStoreError> {
        *self.reads.manifests.lock().unwrap() += 1;
        let owner = CrateRef {
            package,
            crate_id: CrateId(0),
        };
        let items = ItemStoreBuilder::new(DefMapRef::Crate(owner), 0).build();
        Ok(Arc::new(SemanticPackage::new(vec![items]).manifest()))
    }

    fn load_items(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
    ) -> Result<Arc<ItemStore>, PackageStoreError> {
        let owner = CrateRef { package, crate_id };
        let items = Arc::new(ItemStoreBuilder::new(DefMapRef::Crate(owner), 0).build());
        self.reads
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
        let index = Arc::new(ItemLookupIndex::default());
        self.reads
            .indexes
            .lock()
            .unwrap()
            .push(Arc::downgrade(&index));
        Ok(index)
    }
}

#[test]
fn decoded_values_finish_releasing_before_the_last_borrowed_loader() {
    for count in [1, 70] {
        let reads = Reads::default();
        let txn = reads.transaction(count);
        for package in 0..count {
            let owner = CrateRef {
                package: PackageSlot(package),
                crate_id: CrateId(0),
            };
            txn.items(owner).unwrap().unwrap();
            txn.item_lookup_index(owner).unwrap().unwrap();
        }
        assert!(!reads.payloads_released());
        drop(txn);
        reads.assert_released_on_caller();
    }
}

#[test]
fn cloned_exact_and_reconstructed_values_remain_readable_until_their_owner_drops() {
    for reconstruct in [false, true] {
        let reads = Reads::default();
        let txn = reads.transaction(70);
        for package in 0..70 {
            let owner = CrateRef {
                package: PackageSlot(package),
                crate_id: CrateId(0),
            };
            if reconstruct {
                assert_eq!(txn.package(owner.package).unwrap().crates().len(), 1);
            } else {
                txn.items(owner).unwrap().unwrap();
                txn.item_lookup_index(owner).unwrap().unwrap();
            }
        }
        let clone = txn.clone();
        drop(txn);
        assert!(!reads.payloads_released());
        assert!(reads.loader_release.lock().unwrap().is_none());
        for package in 0..70 {
            let owner = CrateRef {
                package: PackageSlot(package),
                crate_id: CrateId(0),
            };
            assert_eq!(clone.items(owner).unwrap().unwrap().crate_ref(), owner);
            clone.item_lookup_index(owner).unwrap().unwrap();
            if reconstruct {
                assert_eq!(clone.package(owner.package).unwrap().crates().len(), 1);
            }
        }
        assert_eq!(reads.items.lock().unwrap().len(), 70);
        assert_eq!(reads.indexes.lock().unwrap().len(), 70);
        drop(clone);
        reads.assert_released_on_caller();
    }
}

#[test]
fn release_does_not_load_unread_or_excluded_slots_or_take_a_resident_owner() {
    let reads = Reads::default();
    let resident = Arc::new(SemanticPackage::default());
    let weak = Arc::downgrade(&resident);
    let txn = SemanticIrReadTxn::from_store_entries(
        [(true, Some(resident.clone())), (false, None), (true, None)],
        SemanticIrLoader::new(Loader { reads: &reads }),
    );
    assert!(txn.package(PackageSlot(1)).is_err());
    drop(txn);
    assert_eq!(*reads.manifests.lock().unwrap(), 0);
    assert!(reads.items.lock().unwrap().is_empty());
    assert!(reads.indexes.lock().unwrap().is_empty());
    reads.assert_released_on_caller();
    assert!(weak.upgrade().is_some());
    drop(resident);
    assert!(weak.upgrade().is_none());
}

#[test]
fn unwinding_still_releases_loaded_values_before_the_loader() {
    let reads = Reads::default();
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let txn = reads.transaction(70);
        for package in 0..70 {
            let owner = CrateRef {
                package: PackageSlot(package),
                crate_id: CrateId(0),
            };
            txn.item_lookup_index(owner).unwrap().unwrap();
        }
        panic!("caller unwinds with a loaded transaction");
    }));
    assert!(result.is_err());
    reads.assert_released_on_caller();
}

#[cfg(target_os = "linux")]
#[test]
fn denied_worker_starts_release_all_payloads_before_the_loader() {
    const NAME: &str =
        "tests::read_release::denied_worker_starts_release_all_payloads_before_the_loader";
    const CHILD: &str = "SUPRNOVA_LSP_SEMANTIC_RELEASE_CHILD";
    if std::env::var_os(CHILD).as_deref() != Some(std::ffi::OsStr::new(NAME)) {
        // Only this isolated child changes its process limit. Ordinary parallel tests and the
        // editor keep their own limits; the bounded test runner owns the child process tree.
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", NAME, "--nocapture"])
            .env(CHILD, NAME)
            .output()
            .expect("isolated semantic release test should start");
        assert!(
            output.status.success(),
            "isolated release failed: {} {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        return;
    }

    let reads = Reads::default();
    let txn = reads.transaction(70);
    for package in 0..70 {
        txn.item_lookup_index(CrateRef {
            package: PackageSlot(package),
            crate_id: CrateId(0),
        })
        .unwrap()
        .unwrap();
    }
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
    // SAFETY: change only the isolated child's soft limit and preserve its hard limit.
    assert_eq!(
        unsafe { libc::setrlimit(libc::RLIMIT_NPROC, &restricted) },
        0
    );
    let failure = match std::thread::Builder::new().spawn(|| {}) {
        Ok(worker) => {
            worker.join().unwrap();
            None
        }
        Err(error) => error.raw_os_error(),
    };
    drop(txn);
    // SAFETY: restore the value from getrlimit before checking the experiment's result.
    assert_eq!(unsafe { libc::setrlimit(libc::RLIMIT_NPROC, &original) }, 0);
    assert_eq!(
        failure,
        Some(libc::EAGAIN),
        "the control must actually deny thread creation"
    );
    reads.assert_released_on_caller();
}
