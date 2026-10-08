//! Lazy phase loading from sectioned package cache artifacts.
//!
//! DefMap and Semantic IR load crate shards, while Body IR loads source-file shards.
//!
//! One query or build shares an open reader for each package cache file across all three phases.
//! They keep reading the same file contents even if another operation replaces the file's path.

use std::{
    fmt,
    sync::{Arc, OnceLock},
};

use rg_body_ir::{BodyFileShard, BodyIrLoader, CrateBodies, LoadBodyIr, PackageBodiesManifest};
use rg_def_map::{CrateData, DefMapLoader, LoadDefMap, PackageDefMapsManifest};
use rg_ir_model::{CrateId, FileId, PackageSlot};
use rg_package_store::PackageStoreError;
use rg_semantic_ir::{
    ItemLookupIndex, ItemStore, LoadSemanticIr, SemanticIrLoader, SemanticPackageManifest,
};

use crate::{
    state::ProjectState,
    storage::cache::{Fingerprint, PackageArtifactReader, PackageCacheStore, WorkspaceCachePlan},
};

/// Gives a query or build loaders that share one open reader per package cache file.
///
/// DefMap, Semantic IR, and Body IR all read through the same [`PackageArtifactReader`], so replacing
/// the cache file cannot mix old declarations with new bodies. Decoded analysis belongs to each
/// phase's read transaction; only the reader is shared for the duration of this loader set.
#[derive(Clone)]
pub(crate) struct PackageReadLoaders {
    pub(crate) def_map: DefMapLoader<'static>,
    pub(crate) semantic_ir: SemanticIrLoader<'static>,
    pub(crate) body_ir: BodyIrLoader<'static>,
}

impl fmt::Debug for PackageReadLoaders {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PackageReadLoaders")
            .finish_non_exhaustive()
    }
}

impl PackageReadLoaders {
    pub(crate) fn new(project: &ProjectState) -> Self {
        Self::from_cache(
            project.cache_plan.clone(),
            project.cache_store.clone(),
            project.package_source_fingerprints.clone(),
        )
    }

    /// Creates cache readers for dependencies while excluding every package rebuilt from source.
    ///
    /// A dirty package's final fingerprint is unknown until macro source-file discovery settles, so
    /// allowing its old artifact into the build would mix two source generations.
    pub(crate) fn for_package_rebuild(
        project: &ProjectState,
        source_packages: &[PackageSlot],
    ) -> Self {
        Self::from_cache_excluding(
            project.cache_plan.clone(),
            project.cache_store.clone(),
            project.package_source_fingerprints.clone(),
            source_packages,
        )
    }

    /// Clears selected fingerprints before constructing artifact readers.
    ///
    /// Unselected packages keep their already validated fingerprints and remain available for lazy
    /// dependency reads. A selected slot has no usable artifact identity until its source build has
    /// produced the final file table and fingerprint.
    pub(crate) fn from_cache_excluding(
        cache_plan: WorkspaceCachePlan,
        cache_store: PackageCacheStore,
        mut package_source_fingerprints: Vec<Option<Fingerprint>>,
        source_packages: &[PackageSlot],
    ) -> Self {
        for package in source_packages {
            if let Some(fingerprint) = package_source_fingerprints.get_mut(package.0) {
                *fingerprint = None;
            }
        }
        debug_assert!(source_packages.iter().all(|package| {
            package_source_fingerprints
                .get(package.0)
                .is_some_and(Option::is_none)
        }));

        Self::from_cache(cache_plan, cache_store, package_source_fingerprints)
    }

    pub(crate) fn from_cache(
        cache_plan: WorkspaceCachePlan,
        cache_store: PackageCacheStore,
        package_source_fingerprints: Vec<Option<Fingerprint>>,
    ) -> Self {
        let artifacts = Arc::new(PackageArtifactReaders::new(
            cache_plan,
            cache_store,
            package_source_fingerprints,
        ));
        Self {
            def_map: DefMapLoader::new(DefMapPackageLoader {
                artifacts: Arc::clone(&artifacts),
            }),
            semantic_ir: SemanticIrLoader::new(SemanticIrPackageLoader {
                artifacts: Arc::clone(&artifacts),
            }),
            body_ir: BodyIrLoader::new(BodyIrPackageLoader {
                artifacts: Arc::clone(&artifacts),
            }),
        }
    }
}

/// Shared request cache for package artifact revisions.
///
/// The cache lives behind `PackageReadLoaders`, so several read transactions can share it without
/// making decoded dependencies permanent project state. Operations drop their loader set on
/// return.
#[derive(Debug)]
struct PackageArtifactReaders {
    cache_plan: WorkspaceCachePlan,
    cache_store: PackageCacheStore,
    package_source_fingerprints: Vec<Option<Fingerprint>>,
    packages: Vec<OnceLock<PackageArtifactReader>>,
}

impl Drop for PackageArtifactReaders {
    fn drop(&mut self) {
        // All phase loaders have released their shared readers. Their independent files and name
        // tables can be destroyed together; no reader or project mutation survives this scope.
        let started = std::time::Instant::now();
        let loaded = self
            .packages
            .iter()
            .filter(|cell| cell.get().is_some())
            .count();
        let worker_count = if loaded >= 64 { 8 } else { 0 };
        let chunk_size = self.packages.len().div_ceil(8).max(1);
        std::thread::scope(|scope| {
            let mut workers = Vec::new();
            for chunk in self.packages.chunks_mut(chunk_size).take(worker_count) {
                match std::thread::Builder::new()
                    .name("release-artifacts".to_owned())
                    .spawn_scoped(scope, move || {
                        chunk.iter_mut().map(OnceLock::take).for_each(drop);
                    }) {
                    Ok(worker) => workers.push(worker),
                    Err(error) => tracing::warn!(
                        %error,
                        "could not start an artifact release worker; releasing its readers on the query lane"
                    ),
                }
            }
            for worker in workers {
                if let Err(panic) = worker.join() {
                    std::panic::resume_unwind(panic);
                }
            }
        });
        // A failed spawn leaves its cells untouched. Drop those on this lane only after every
        // successful worker has joined; small and sparse reader sets also follow this path.
        drop(std::mem::take(&mut self.packages));
        tracing::trace!(
            elapsed_us = started.elapsed().as_micros(),
            loaded,
            "query artifact readers released"
        );
    }
}

impl PackageArtifactReaders {
    fn new(
        cache_plan: WorkspaceCachePlan,
        cache_store: PackageCacheStore,
        package_source_fingerprints: Vec<Option<Fingerprint>>,
    ) -> Self {
        let package_count = package_source_fingerprints.len();
        Self {
            cache_plan,
            cache_store,
            package_source_fingerprints,
            packages: (0..package_count).map(|_| OnceLock::new()).collect(),
        }
    }

    /// Open a package cache file once and share the reader across DefMap, Semantic IR and Body IR.
    ///
    /// Failed opens are not cached, so a storage error is returned with its package context rather
    /// than leaving a permanently initialized error sentinel in the request.
    fn reader(&self, package: PackageSlot) -> Result<&PackageArtifactReader, PackageStoreError> {
        let cell = self
            .packages
            .get(package.0)
            .ok_or(PackageStoreError::MissingSlot { slot: package })?;

        if let Some(reader) = cell.get() {
            return Ok(reader);
        }

        let reader = self.open_reader(package)?;
        let _ = cell.set(reader);
        Ok(cell
            .get()
            .expect("package artifact reader cell should be initialized after successful open"))
    }

    /// Reconstructs the expected artifact header from the cache plan before opening the file.
    fn open_reader(
        &self,
        package: PackageSlot,
    ) -> Result<PackageArtifactReader, PackageStoreError> {
        let Some(header) = self
            .cache_plan
            .artifact_header(package, &self.package_source_fingerprints)
        else {
            return Err(PackageStoreError::stale_package(
                package,
                "workspace cache plan has no package header",
            ));
        };

        match self.cache_store.open_artifact(&header) {
            Ok(Some(reader)) => Ok(reader),
            Ok(None) => Err(PackageStoreError::missing_package(package)),
            Err(error) => Err(error.into_package_store_error(package)),
        }
    }
}

#[derive(Debug)]
struct DefMapPackageLoader {
    artifacts: Arc<PackageArtifactReaders>,
}

impl LoadDefMap for DefMapPackageLoader {
    fn load_manifest(
        &self,
        package: PackageSlot,
    ) -> Result<Arc<PackageDefMapsManifest>, PackageStoreError> {
        self.artifacts
            .reader(package)?
            .read_def_map_manifest()
            .map(Arc::new)
            .map_err(|error| error.into_package_store_error(package))
    }

    fn load_crate(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
    ) -> Result<Arc<CrateData>, PackageStoreError> {
        let started = std::time::Instant::now();
        let result = self
            .artifacts
            .reader(package)?
            .read_def_map_crate(crate_id)
            .map(Arc::new)
            .map_err(|error| error.into_package_store_error(package));
        tracing::trace!(
            package = package.0,
            crate_id = crate_id.0,
            elapsed_us = started.elapsed().as_micros(),
            section = "def_map.crate",
            thread_id = ?std::thread::current().id(),
            "query artifact loaded"
        );
        result
    }
}

#[derive(Debug)]
struct SemanticIrPackageLoader {
    artifacts: Arc<PackageArtifactReaders>,
}

impl LoadSemanticIr for SemanticIrPackageLoader {
    fn load_manifest(
        &self,
        package: PackageSlot,
    ) -> Result<Arc<SemanticPackageManifest>, PackageStoreError> {
        self.artifacts
            .reader(package)?
            .read_semantic_ir_manifest()
            .map(Arc::new)
            .map_err(|error| error.into_package_store_error(package))
    }

    fn load_items(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
    ) -> Result<Arc<ItemStore>, PackageStoreError> {
        let started = std::time::Instant::now();
        let result = self
            .artifacts
            .reader(package)?
            .read_semantic_ir_items(crate_id)
            .map(Arc::new)
            .map_err(|error| error.into_package_store_error(package));
        tracing::trace!(
            package = package.0,
            crate_id = crate_id.0,
            elapsed_us = started.elapsed().as_micros(),
            section = "semantic_ir.items",
            thread_id = ?std::thread::current().id(),
            "query artifact loaded"
        );
        result
    }

    fn load_lookup_index(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
    ) -> Result<Arc<ItemLookupIndex>, PackageStoreError> {
        let started = std::time::Instant::now();
        let result = self
            .artifacts
            .reader(package)?
            .read_semantic_ir_lookup_index(crate_id)
            .map(Arc::new)
            .map_err(|error| error.into_package_store_error(package));
        tracing::trace!(
            package = package.0,
            crate_id = crate_id.0,
            elapsed_us = started.elapsed().as_micros(),
            section = "semantic_ir.lookup_index",
            thread_id = ?std::thread::current().id(),
            "query artifact loaded"
        );
        result
    }
}

#[derive(Debug)]
struct BodyIrPackageLoader {
    artifacts: Arc<PackageArtifactReaders>,
}

impl LoadBodyIr for BodyIrPackageLoader {
    fn load_manifest(
        &self,
        package: PackageSlot,
    ) -> Result<Arc<PackageBodiesManifest>, PackageStoreError> {
        self.artifacts
            .reader(package)?
            .read_body_ir_manifest()
            .map(Arc::new)
            .map_err(|error| error.into_package_store_error(package))
    }

    fn load_file_shard(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
        file: FileId,
    ) -> Result<Arc<BodyFileShard>, PackageStoreError> {
        self.artifacts
            .reader(package)?
            .read_body_file_shard(crate_id, file)
            .map(Arc::new)
            .map_err(|error| error.into_package_store_error(package))
    }

    fn load_crate(
        &self,
        package: PackageSlot,
        crate_id: CrateId,
    ) -> Result<Arc<CrateBodies>, PackageStoreError> {
        self.artifacts
            .reader(package)?
            .read_body_crate(crate_id)
            .map(Arc::new)
            .map_err(|error| error.into_package_store_error(package))
    }
}

impl ProjectState {
    /// Create one request-owned loader set for this saved project snapshot.
    pub(crate) fn query_read_loaders(&self) -> PackageReadLoaders {
        PackageReadLoaders::new(self)
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use std::{fs, process::Command};

    use super::PackageArtifactReaders;
    use crate::{PackageResidencyPolicy, testonly::ProjectFixture};

    fn reader_set(count: usize) -> (ProjectFixture, PackageArtifactReaders) {
        let fixture = ProjectFixture::build_with_package_residency_policy(
            r#"
//- /Cargo.toml
[package]
name = "app"
version = "0.1.0"
edition = "2024"

//- /src/lib.rs
pub struct App;
"#,
            PackageResidencyPolicy::AllOffloadable,
        );
        let state = &fixture.project().state;
        let header = state
            .cache_plan
            .artifact_header(
                rg_ir_model::PackageSlot(0),
                &state.package_source_fingerprints,
            )
            .expect("offloaded package should have an artifact header");
        let mut readers = PackageArtifactReaders::new(
            state.cache_plan.clone(),
            state.cache_store.clone(),
            state.package_source_fingerprints.clone(),
        );
        // Independent opens of one genuine artifact exercise handle ownership without needing
        // a large workspace fixture. Teardown does not inspect package identities.
        readers.packages = (0..count)
            .map(|_| {
                std::sync::OnceLock::from(
                    state
                        .cache_store
                        .open_artifact(&header)
                        .expect("fixture artifact should open")
                        .expect("fixture artifact should exist"),
                )
            })
            .collect();
        (fixture, readers)
    }

    fn isolated_release_test(name: &str) -> bool {
        const CHILD: &str = "SUPRNOVA_LSP_READER_RELEASE_CHILD";
        if std::env::var_os(CHILD).as_deref() == Some(std::ffi::OsStr::new(name)) {
            return false;
        }

        // Resource observations belong to this test's process. Other tests can release artifacts
        // concurrently, using the same worker names and their own independent reader handles.
        let output = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", name, "--nocapture"])
            .env(CHILD, name)
            .output()
            .expect("isolated release test should start");
        assert!(
            output.status.success(),
            "isolated release failed: {} {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        true
    }

    fn artifact_release_workers() -> Vec<std::ffi::OsString> {
        fs::read_dir("/proc/self/task")
            .expect("process tasks should be readable")
            .filter_map(|entry| {
                let entry = match entry {
                    Ok(entry) => entry,
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => return None,
                    Err(error) => panic!("read process task entry: {error}"),
                };
                let name = match fs::read_to_string(entry.path().join("comm")) {
                    Ok(name) => name,
                    // A fixture/runtime thread can exit between listing its task and reading it.
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => return None,
                    Err(error) => panic!("read process task name: {error}"),
                };
                // Linux comm retains only the first 15 bytes of the production worker name.
                (name.trim_end() == "release-artifac").then(|| entry.file_name())
            })
            .collect()
    }

    fn artifact_handles(fixture: &ProjectFixture) -> usize {
        let state = &fixture.project().state;
        let header = state
            .cache_plan
            .artifact_header(
                rg_ir_model::PackageSlot(0),
                &state.package_source_fingerprints,
            )
            .unwrap();
        let path = state.cache_store.package_artifact_path(&header.package);
        fs::read_dir("/proc/self/fd")
            .unwrap()
            .filter(
                |entry| match fs::read_link(entry.as_ref().unwrap().path()) {
                    Ok(target) => target == path,
                    // Other fixture/runtime handles can close while procfs is being enumerated.
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
                    Err(error) => panic!("read process handle: {error}"),
                },
            )
            .count()
    }

    #[test]
    fn large_reader_release_closes_handles_and_joins_every_thread() {
        if isolated_release_test(
            "storage::loaders::tests::large_reader_release_closes_handles_and_joins_every_thread",
        ) {
            return;
        }
        for count in [0, 70] {
            let (fixture, readers) = reader_set(count);
            assert_eq!(artifact_handles(&fixture), count);
            drop(readers);
            assert_eq!(artifact_handles(&fixture), 0);
            assert_eq!(artifact_release_workers(), Vec::<std::ffi::OsString>::new());
        }
    }

    #[test]
    fn sparse_reader_release_preserves_a_cloned_revision_until_its_owner_drops() {
        if isolated_release_test(
            "storage::loaders::tests::sparse_reader_release_preserves_a_cloned_revision_until_its_owner_drops",
        ) {
            return;
        }
        for count in [1, 70] {
            let (fixture, mut readers) = reader_set(count);
            readers.packages.resize_with(140, std::sync::OnceLock::new);
            let reader = readers.packages[0].get().unwrap().clone();
            let header = reader.probe().header.clone();
            assert_eq!(artifact_handles(&fixture), count);
            drop(readers);
            assert_eq!(artifact_handles(&fixture), 1);
            assert_eq!(artifact_release_workers(), Vec::<std::ffi::OsString>::new());
            assert_eq!(reader.probe().header, header);
            reader
                .read_def_map_manifest()
                .expect("cloned reader should keep its pinned revision readable");
            drop(reader);
            assert_eq!(artifact_handles(&fixture), 0);
        }
    }

    #[test]
    fn thread_start_failure_still_releases_all_reader_handles() {
        if isolated_release_test(
            "storage::loaders::tests::thread_start_failure_still_releases_all_reader_handles",
        ) {
            return;
        }

        let (fixture, readers) = reader_set(70);
        assert_eq!(artifact_handles(&fixture), 70);
        let mut original = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        // SAFETY: original is valid writable storage for the queried process limit.
        assert_eq!(
            unsafe { libc::getrlimit(libc::RLIMIT_NPROC, &mut original) },
            0
        );
        let restricted = libc::rlimit {
            rlim_cur: 0,
            rlim_max: original.rlim_max,
        };
        // SAFETY: both values describe this isolated child's limit; the hard limit is unchanged.
        assert_eq!(
            unsafe { libc::setrlimit(libc::RLIMIT_NPROC, &restricted) },
            0
        );
        let attempt = std::thread::Builder::new().spawn(|| {});
        let failure = match attempt {
            Ok(thread) => {
                thread.join().unwrap();
                None
            }
            Err(error) => error.raw_os_error(),
        };
        drop(readers);
        // SAFETY: original came from getrlimit; restore the child before checking any assertions.
        assert_eq!(unsafe { libc::setrlimit(libc::RLIMIT_NPROC, &original) }, 0);
        assert_eq!(
            failure,
            Some(libc::EAGAIN),
            "control must actually deny a thread start"
        );
        assert_eq!(artifact_handles(&fixture), 0);
        assert_eq!(artifact_release_workers(), Vec::<std::ffi::OsString>::new());
    }
}
