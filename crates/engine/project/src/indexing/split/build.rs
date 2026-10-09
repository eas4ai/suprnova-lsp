//! Captures saved source and declarations so body analysis can run outside the live project.

use std::{
    num::NonZeroUsize,
    path::Path,
    sync::{Arc, Mutex},
};

use anyhow::Context as _;
use rg_body_ir::{BodyIrBuilder, BodyIrFile, CrateBodies, CrateBodiesCoverage};
use rg_def_map::{DefMapDb, DefMapLoader};
use rg_ir_model::{CrateRef, PackageSlot};
use rg_package_store::PackageSubset;
use rg_parse::ParseDb;
use rg_semantic_ir::{SemanticIrDb, SemanticIrLoader};
use rg_std::{MemorySize, UniqueVec};
use rg_text::PackageNameInterners;

use super::{AnalysisSurface, SplitIndexingProgress};
use crate::{
    selection::PhasePackageSet,
    state::{ProjectGenerationId, ProjectState},
    storage::loaders::PackageReadLoaders,
};

/// Owned inputs for analyzing selected bodies from one saved version of [`Project`](crate::Project).
///
/// Created by [`Project::deferred_body_build`](crate::Project::deferred_body_build) for background
/// work, or [`SplitIndexing::prepare`](crate::SplitIndexing::prepare) for a query. The source and
/// declarations are shared with that project version, so these inputs can move to a worker while
/// the live project accepts newer changes. Building returns [`SavedBodyProducts`]; installing
/// them is a separate [`SplitIndexing::publish`](crate::SplitIndexing::publish) step.
#[derive(Debug)]
pub struct SavedBodyBuildInputs {
    generation: ProjectGenerationId,
    parse: Arc<ParseDb>,
    def_map: DefMapDb,
    semantic_ir: SemanticIrDb,
    def_map_loader: DefMapLoader<'static>,
    semantic_ir_loader: SemanticIrLoader<'static>,
    files: Vec<BodyIrFile>,
    crates: UniqueVec<CrateRef>,
    packages: Vec<PackageSlot>,
    subset: PackageSubset,
    packages_to_reallocate: Vec<PackageSlot>,
    worker_limit: Option<NonZeroUsize>,
}

impl SavedBodyBuildInputs {
    pub(crate) fn deferred(state: &ProjectState) -> Self {
        let crates = state.unfinished_crates().collect::<Vec<_>>();
        let mut inputs = Self::for_surface(state, AnalysisSurface::Crates(&crates));
        // Deferred bodies share the engine with interactive analysis. Leave CPU capacity for those
        // queries, while preserving explicit Rayon settings and the lower-memory worker limit.
        if inputs.worker_limit.is_none()
            && std::env::var_os("RAYON_NUM_THREADS").is_none()
            && std::env::var_os("RAYON_RS_NUM_CPUS").is_none()
            && let Ok(workers) = std::thread::available_parallelism()
        {
            inputs.worker_limit = NonZeroUsize::new((workers.get() / 2).max(1));
        }
        inputs
    }

    /// Choose missing body work and include previously analyzed files in each requested crate.
    pub(super) fn for_surface(state: &ProjectState, surface: AnalysisSurface<'_>) -> Self {
        let (requested_files, requested_crates) = surface.parts();
        let mut crates = requested_crates
            .iter()
            .copied()
            .filter(|&crate_ref| {
                !state
                    .body_ir
                    .crate_coverage(crate_ref)
                    .is_some_and(CrateBodiesCoverage::is_complete)
            })
            .collect::<UniqueVec<_>>();
        let mut files = UniqueVec::new();
        for &(crate_ref, file) in requested_files {
            let coverage = state.body_ir.crate_coverage(crate_ref);
            if coverage.is_some_and(|coverage| coverage.contains_file(file)) {
                continue;
            }
            // Partial coverage is transient. An offloaded target is completed before rewriting its
            // package artifact, leaving unrelated targets encoded in that artifact.
            if state.body_ir.package_is_offloaded(crate_ref.package) {
                crates.push(crate_ref);
                continue;
            }
            files.push(BodyIrFile::new(crate_ref, file));
            // If first.rs was prepared before a query asks for second.rs, rebuild both together.
            // Their body ids must come from one crate revision. Coverage also keeps empty files
            // ready, even though scanning the old body arenas would not find them.
            if let Some(CrateBodiesCoverage::Files(processed)) = coverage {
                for &file in processed {
                    files.push(BodyIrFile::new(crate_ref, file));
                }
            }
        }
        let files = files
            .into_vec()
            .into_iter()
            .filter(|file| !crates.contains(&file.crate_ref))
            .collect::<Vec<_>>();
        let selected = files
            .iter()
            .map(|file| file.crate_ref)
            .chain(crates.iter().copied())
            .collect::<Vec<_>>();
        let packages = PhasePackageSet::from_crates(&selected);
        let subset = packages.visible_dependency_subset(&state.workspace);
        // File work may leave an offloadable package incomplete and still in memory, so reallocate
        // its crate payloads too. Complete products headed straight to disk do not need the copy.
        let packages_to_reallocate = if files.is_empty() {
            state
                .package_residency
                .resident_packages(packages.as_slice())
        } else {
            packages.as_slice().to_vec()
        };
        let loaders = PackageReadLoaders::new(state);
        Self {
            generation: state.generation_id,
            parse: Arc::clone(&state.parse),
            def_map: state.def_map.clone(),
            semantic_ir: state.semantic_ir.clone(),
            def_map_loader: loaders.def_map,
            semantic_ir_loader: loaders.semantic_ir,
            files,
            crates,
            packages: packages.as_slice().to_vec(),
            subset,
            packages_to_reallocate,
            worker_limit: state.indexing_preference.body_ir_worker_limit(),
        }
    }

    pub fn generation_id(&self) -> ProjectGenerationId {
        self.generation
    }
    pub fn packages(&self) -> &[PackageSlot] {
        &self.packages
    }

    pub fn package_slots_for_path(&self, path: &Path) -> anyhow::Result<Vec<PackageSlot>> {
        PhasePackageSet::from_path(&self.parse, path).map(PhasePackageSet::into_vec)
    }

    /// Analyze the selected bodies and return all results as [`SavedBodyProducts`].
    /// Submit them through [`SplitIndexing::publish`](crate::SplitIndexing::publish) after success.
    /// If construction fails, completed results are dropped and the live project is unchanged.
    pub fn build(
        self,
        cancellation: &rg_std::CancellationToken,
    ) -> anyhow::Result<SavedBodyProducts> {
        let generation = self.generation;
        let products = Mutex::new(Vec::new());
        self.build_with_package_priority(
            &|| Vec::new(),
            &|batch| {
                products
                    .lock()
                    .expect("body products should not be poisoned")
                    .extend(batch.crates);
            },
            &|_| {},
            cancellation,
        )
        .context("build saved bodies")?;
        Ok(SavedBodyProducts {
            generation,
            crates: products
                .into_inner()
                .expect("body products should not be poisoned"),
        })
    }

    /// Analyze the selected bodies, passing each package's results to `publish` when ready.
    /// The callback owns each [`SavedBodyProducts`] batch and can send it to the thread that
    /// owns the project for [`SplitIndexing::publish`](crate::SplitIndexing::publish).
    ///
    /// Callbacks may run concurrently. `priority` supplies preferred packages between jobs, so
    /// changes to it affect only work that has not started. This call returns completion status
    /// after all workers stop; an error does not take back batches already sent to `publish`.
    pub fn build_with_package_priority(
        self,
        priority: &(dyn Fn() -> Vec<PackageSlot> + Sync),
        publish: &(dyn Fn(SavedBodyProducts) + Sync),
        report: &(dyn Fn(SplitIndexingProgress) + Sync),
        cancellation: &rg_std::CancellationToken,
    ) -> anyhow::Result<()> {
        let mut names = PackageNameInterners::new(self.parse.package_count());
        let result = BodyIrBuilder::new(
            &self.parse,
            &self.def_map,
            &self.semantic_ir,
            &self.packages,
            &self.packages_to_reallocate,
            &mut names,
            self.def_map_loader,
            self.semantic_ir_loader,
            &self.subset,
        )
        .worker_limit(self.worker_limit)
        .cancellation(cancellation.clone())
        .selected_bodies(self.files, self.crates)
        .build_with_package_priority(
            priority,
            &|crates| {
                publish(SavedBodyProducts {
                    generation: self.generation,
                    crates,
                })
            },
            &|progress| report(SplitIndexingProgress::from_body_ir(progress)),
        );
        // Reloadable text and weak interner tables are build-scoped on both success and failure.
        // Names in a completed payload own their strings and need no interner publication.
        self.parse.evict_saved_source_text();
        result.context("construct saved body products")
    }
}

/// Body analysis results ready to be installed in a [`Project`](crate::Project).
///
/// Each result pairs a [`CrateRef`] with its new [`CrateBodies`]. The batch also records the
/// [`ProjectGenerationId`] of the source and declarations used by the build. Pass it to
/// [`SplitIndexing::publish`](crate::SplitIndexing::publish), which rejects results from an older
/// project version and checks for work completed by other requests since the build started.
#[derive(Debug, MemorySize)]
pub struct SavedBodyProducts {
    pub(super) generation: ProjectGenerationId,
    pub(super) crates: Vec<(CrateRef, CrateBodies)>,
}

impl SavedBodyProducts {
    pub fn generation_id(&self) -> ProjectGenerationId {
        self.generation
    }
}

#[cfg(test)]
mod tests {
    use std::{num::NonZeroUsize, process::Command};

    use rg_std::CancellationToken;

    use super::SavedBodyBuildInputs;
    use crate::{
        AnalysisSurface, IndexingPerformancePreference, Project, SplitIndexingMode,
        testonly::ProjectSourceFixture,
    };

    struct DeferredFixture {
        _source: ProjectSourceFixture,
        project: Project,
    }

    impl DeferredFixture {
        const CHILD: &str = "RG_DEFERRED_WIDTH_CHILD";

        fn build(preference: IndexingPerformancePreference) -> Self {
            let source = ProjectSourceFixture::build(
                r#"
//- /Cargo.toml
[workspace]
members = ["first", "second"]
resolver = "3"

//- /first/Cargo.toml
[package]
name = "deferred_first"
version = "0.1.0"
edition = "2024"

//- /first/src/lib.rs
pub fn value() -> u32 { 1 }

//- /second/Cargo.toml
[package]
name = "deferred_second"
version = "0.1.0"
edition = "2024"

//- /second/src/lib.rs
pub fn value() -> u32 { 2 }
"#,
            );
            let project = Project::builder(source.workspace_metadata())
                .split_indexing_mode(SplitIndexingMode::EarlyStart)
                .indexing_preference(preference)
                .build()
                .expect("real early-start project should prepare its declarations");
            assert_eq!(
                project.has_unfinished_split_indexing(),
                preference == IndexingPerformancePreference::FasterBuilds,
            );
            Self {
                _source: source,
                project,
            }
        }

        fn is_child(name: &str) -> bool {
            std::env::var_os(Self::CHILD).as_deref() == Some(std::ffi::OsStr::new(name))
        }

        fn isolated(name: &str, environment: &[(&str, &str)]) {
            // Rayon reads process-global environment. Give each configuration its own process;
            // never mutate the environment of concurrently running Cargo tests.
            let mut command = Command::new(std::env::current_exe().unwrap());
            command
                .args(["--exact", name, "--nocapture"])
                .env(Self::CHILD, name)
                .env_remove("RAYON_NUM_THREADS")
                .env_remove("RAYON_RS_NUM_CPUS");
            for &(key, value) in environment {
                command.env(key, value);
            }
            let output = command
                .output()
                .expect("isolated deferred-width control should start");
            assert!(
                output.status.success(),
                "isolated deferred-width control {environment:?} failed: {} {}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr),
            );
        }
    }

    #[test]
    fn deferred_faster_builds_reserve_parallelism_for_queries() {
        const NAME: &str =
            "indexing::split::build::tests::deferred_faster_builds_reserve_parallelism_for_queries";
        if !DeferredFixture::is_child(NAME) {
            DeferredFixture::isolated(NAME, &[]);
            return;
        }
        assert!(std::env::var_os("RAYON_NUM_THREADS").is_none());
        assert!(std::env::var_os("RAYON_RS_NUM_CPUS").is_none());
        let fixture = DeferredFixture::build(IndexingPerformancePreference::FasterBuilds);
        let inputs = fixture.project.deferred_body_build();
        let expected = std::thread::available_parallelism()
            .ok()
            .map(|available| NonZeroUsize::new((available.get() / 2).max(1)).unwrap());
        assert_eq!(
            inputs.worker_limit, expected,
            "automatic deferred inputs must reserve half the host parallelism for queries",
        );
        assert_eq!(inputs.packages.len(), 2);
        let generation = inputs.generation_id();
        let products = inputs
            .build(&CancellationToken::new())
            .expect("limited deferred work should produce both real packages");
        assert_eq!(products.generation_id(), generation);
        assert_eq!(products.crates.len(), 2);
        assert_ne!(products.crates[0].0.package, products.crates[1].0.package);
        assert!(fixture.project.has_unfinished_split_indexing());
    }

    #[test]
    fn deferred_width_preserves_explicit_rayon_environment() {
        const NAME: &str =
            "indexing::split::build::tests::deferred_width_preserves_explicit_rayon_environment";
        if !DeferredFixture::is_child(NAME) {
            for environment in [
                vec![("RAYON_NUM_THREADS", "2")],
                vec![("RAYON_NUM_THREADS", "0")],
                vec![("RAYON_NUM_THREADS", "invalid")],
                vec![("RAYON_NUM_THREADS", "")],
                vec![("RAYON_RS_NUM_CPUS", "2")],
                vec![("RAYON_RS_NUM_CPUS", "0")],
                vec![("RAYON_RS_NUM_CPUS", "invalid")],
                vec![("RAYON_NUM_THREADS", "0"), ("RAYON_RS_NUM_CPUS", "2")],
            ] {
                DeferredFixture::isolated(NAME, &environment);
            }
            return;
        }
        let current = std::env::var_os("RAYON_NUM_THREADS");
        let legacy = std::env::var_os("RAYON_RS_NUM_CPUS");
        assert!(current.is_some() || legacy.is_some());
        let fixture = DeferredFixture::build(IndexingPerformancePreference::FasterBuilds);
        assert_eq!(
            fixture.project.deferred_body_build().worker_limit,
            None,
            "explicit Rayon configuration must remain authoritative: current={current:?}, legacy={legacy:?}",
        );
    }

    #[test]
    fn deferred_width_preserves_lower_memory_and_synchronous_limits() {
        const NAME: &str = "indexing::split::build::tests::deferred_width_preserves_lower_memory_and_synchronous_limits";
        if !DeferredFixture::is_child(NAME) {
            DeferredFixture::isolated(NAME, &[]);
            return;
        }
        for preference in [
            IndexingPerformancePreference::FasterBuilds,
            IndexingPerformancePreference::LowerPeakMemory,
        ] {
            let fixture = DeferredFixture::build(preference);
            let crates = fixture
                .project
                .state
                .unfinished_crates()
                .collect::<Vec<_>>();
            let synchronous = SavedBodyBuildInputs::for_surface(
                &fixture.project.state,
                AnalysisSurface::Crates(&crates),
            );
            let expected = match preference {
                IndexingPerformancePreference::FasterBuilds => None,
                IndexingPerformancePreference::LowerPeakMemory => NonZeroUsize::new(4),
            };
            assert_eq!(synchronous.worker_limit, expected);
            let expected_packages = match preference {
                IndexingPerformancePreference::FasterBuilds => 2,
                IndexingPerformancePreference::LowerPeakMemory => 0,
            };
            assert_eq!(synchronous.packages.len(), expected_packages);
            if preference == IndexingPerformancePreference::LowerPeakMemory {
                let deferred = fixture.project.deferred_body_build();
                assert_eq!(deferred.worker_limit, expected);
                assert!(deferred.packages.is_empty());
            }
        }
    }
}
