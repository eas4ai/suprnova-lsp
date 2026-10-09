//! Request-local state for exact reads from one offloaded Semantic IR package.
//!
//! The package manifest supplies the dense crate slots. Each slot then has one cell for declarations
//! and another for its visibility lookup index: resolving a known item needs only declarations,
//! while name lookup can request the index independently. Broad callers can still reconstruct a
//! [`SemanticPackage`], but doing so fills both cells for every crate.
//!
//! The cells live only for the read transaction. Dropping the transaction releases a hover's
//! decoded dependencies instead of making them part of retained project memory.

use std::sync::{Arc, OnceLock};

use rg_ir_model::{CrateId, CrateRef, PackageSlot};
use rg_package_store::PackageStoreError;

use super::SemanticIrLoader;
use crate::{ItemLookupIndex, ItemStore, SemanticCrate, SemanticPackage, SemanticPackageManifest};

/// How one package slot participates in a Semantic IR read transaction.
#[derive(Debug, Clone)]
pub(super) enum PackageReadEntry<'db> {
    /// The full package is already present in the retained project snapshot.
    Resident(Arc<SemanticPackage>),
    /// The package is offloaded and its crate parts are read when requested.
    Lazy(LazyPackage<'db>),
    /// The transaction subset deliberately leaves this package inaccessible.
    Excluded,
}

impl PackageReadEntry<'_> {
    /// Retire large sets of decoded values before releasing their loader handles.
    pub(super) fn release_loaded(packages: &mut [Self]) {
        let loaded = packages
            .iter()
            .filter(|entry| match entry {
                Self::Lazy(package) => package.loaded.get().is_some_and(|loaded| {
                    loaded.package.get().is_some()
                        || loaded.items.iter().any(|cell| cell.get().is_some())
                        || loaded
                            .lookup_indexes
                            .iter()
                            .any(|cell| cell.get().is_some())
                }),
                Self::Resident(_) | Self::Excluded => false,
            })
            .count();
        if loaded < 64 {
            return;
        }

        // Each worker takes only decoded cells. The parent keeps every loader handle until all
        // workers join, so a final loader cannot start its own reader cleanup inside this pool.
        let chunk_size = packages.len().div_ceil(8);
        std::thread::scope(|scope| {
            let mut workers = Vec::new();
            for chunk in packages.chunks_mut(chunk_size) {
                match std::thread::Builder::new()
                    .name("release-semantic".to_owned())
                    .spawn_scoped(scope, move || {
                        tracing::trace!(
                            thread_id = ?std::thread::current().id(),
                            "semantic release worker started"
                        );
                        for entry in chunk {
                            if let Self::Lazy(package) = entry {
                                drop(package.loaded.take());
                            }
                        }
                        tracing::trace!(
                            thread_id = ?std::thread::current().id(),
                            "semantic release worker finished"
                        );
                    }) {
                    Ok(worker) => workers.push(worker),
                    Err(error) => tracing::warn!(
                        %error,
                        "could not start a semantic release worker; releasing its values on the query lane"
                    ),
                }
            }
            let mut panics = Vec::new();
            for worker in workers {
                if let Err(panic) = worker.join() {
                    // Keep secondary payloads too: dropping one can itself unwind.
                    panics.push(panic);
                }
            }
            // Automatic scope completion can precede thread-local destructors. Manually join every
            // worker through full thread termination before propagating the first worker's panic.
            if !panics.is_empty() {
                std::panic::resume_unwind(panics.remove(0));
            }
        });
        // A failed start leaves that chunk untouched. Release its values synchronously after the
        // successful workers finish and before the transaction drops any remaining loader handles.
        for entry in packages {
            if let Self::Lazy(package) = entry {
                drop(package.loaded.take());
            }
        }
    }
}

/// A request-local view of one offloaded Semantic IR package.
///
/// Separate item and lookup-index cells let exact queries share only the data they actually used.
#[derive(Debug, Clone)]
pub(super) struct LazyPackage<'db> {
    loader: SemanticIrLoader<'db>,
    loaded: OnceLock<LoadedPackage>,
}

impl<'db> LazyPackage<'db> {
    pub(super) fn new(loader: SemanticIrLoader<'db>) -> Self {
        Self {
            loader,
            loaded: OnceLock::new(),
        }
    }

    /// Returns the package's dense crate directory, loading it on first access.
    pub(super) fn manifest(
        &self,
        package: PackageSlot,
    ) -> Result<SemanticPackageManifest, PackageStoreError> {
        Ok(*self.loaded(package)?.manifest)
    }

    /// Returns one crate's declaration store and leaves its lookup-index cell untouched.
    pub(super) fn items(
        &self,
        crate_ref: CrateRef,
    ) -> Result<Option<&ItemStore>, PackageStoreError> {
        // A reconstructed package already owns the same items. Until then, keep declaration reads
        // independent from the visibility lookup index stored beside them.
        let loaded = self.loaded(crate_ref.package)?;
        if let Some(package) = loaded.package.get() {
            return Ok(package.crate_items(crate_ref.crate_id));
        }
        let Some(cell) = loaded.items.get(crate_ref.crate_id.0) else {
            return Ok(None);
        };
        if cell.get().is_none() {
            let items = self
                .loader
                .load_items(crate_ref.package, crate_ref.crate_id)?;
            let _ = cell.set(items);
        }
        Ok(cell.get().map(Arc::as_ref))
    }

    /// Returns one crate's lookup index and leaves its declaration cell untouched.
    pub(super) fn lookup_index(
        &self,
        crate_ref: CrateRef,
    ) -> Result<Option<&ItemLookupIndex>, PackageStoreError> {
        // Lookup indexes are used by visibility-wide name search. Known-item queries can therefore
        // read declarations without paying for this cell.
        let loaded = self.loaded(crate_ref.package)?;
        if let Some(package) = loaded.package.get() {
            return Ok(package.crate_lookup_index(crate_ref.crate_id));
        }
        let Some(cell) = loaded.lookup_indexes.get(crate_ref.crate_id.0) else {
            return Ok(None);
        };
        if cell.get().is_none() {
            let index = self
                .loader
                .load_lookup_index(crate_ref.package, crate_ref.crate_id)?;
            let _ = cell.set(index);
        }
        Ok(cell.get().map(Arc::as_ref))
    }

    /// Reconstructs the broad package representation for callers that need every crate part.
    ///
    /// Exact query paths should prefer [`Self::items`] or [`Self::lookup_index`]. This package-wide
    /// path deliberately reads both parts of every crate and validates the result as one package.
    pub(super) fn package(
        &self,
        package: PackageSlot,
    ) -> Result<&SemanticPackage, PackageStoreError> {
        let loaded = self.loaded(package)?;
        if loaded.package.get().is_none() {
            let mut crates = Vec::with_capacity(loaded.items.len());
            for crate_idx in 0..loaded.items.len() {
                let crate_ref = CrateRef {
                    package,
                    crate_id: CrateId(crate_idx),
                };
                let items = self.items(crate_ref)?.ok_or_else(|| {
                    PackageStoreError::stale_package(
                        package,
                        format!(
                            "Semantic IR items for crate {crate_idx} are absent from its manifest"
                        ),
                    )
                })?;
                let lookup_index = self.lookup_index(crate_ref)?.ok_or_else(|| {
                    PackageStoreError::stale_package(
                        package,
                        format!("Semantic IR lookup index for crate {crate_idx} is absent from its manifest"),
                    )
                })?;
                crates.push(SemanticCrate::from_storage_parts(
                    items.clone(),
                    lookup_index.clone(),
                ));
            }
            let semantic_package = SemanticPackage::from_storage_parts(*loaded.manifest, crates)
                .map(Arc::new)
                .map_err(|error| PackageStoreError::stale_package(package, format!("{error:#}")))?;
            let _ = loaded.package.set(semantic_package);
        }
        Ok(loaded
            .package
            .get()
            .expect("Semantic IR package cell should be initialized after successful load"))
    }

    fn loaded(&self, package: PackageSlot) -> Result<&LoadedPackage, PackageStoreError> {
        // The manifest is read first because its crate count defines both exact-read cell arrays.
        if self.loaded.get().is_none() {
            let manifest = self.loader.load_manifest(package)?;
            let loaded = LoadedPackage::new(manifest);
            let _ = self.loaded.set(loaded);
        }
        Ok(self
            .loaded
            .get()
            .expect("Semantic IR package directory should be initialized after successful load"))
    }
}

/// The manifest and decoded values shared by exact reads in one transaction.
#[derive(Debug, Clone)]
struct LoadedPackage {
    manifest: Arc<SemanticPackageManifest>,
    items: Vec<OnceLock<Arc<ItemStore>>>,
    lookup_indexes: Vec<OnceLock<Arc<ItemLookupIndex>>>,
    package: OnceLock<Arc<SemanticPackage>>,
}

impl LoadedPackage {
    fn new(manifest: Arc<SemanticPackageManifest>) -> Self {
        Self {
            items: (0..manifest.crate_count())
                .map(|_| OnceLock::new())
                .collect(),
            lookup_indexes: (0..manifest.crate_count())
                .map(|_| OnceLock::new())
                .collect(),
            manifest,
            package: OnceLock::new(),
        }
    }
}
