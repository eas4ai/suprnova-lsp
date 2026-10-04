//! Application acceptance executable. The Python driver owns provenance and process supervision.

#[path = "../src/memory/mod.rs"]
mod memory;

use std::{collections::BTreeMap, fs, path::PathBuf, thread, time::Duration};

use anyhow::{Context as _, ensure};
use rg_cfg_eval::CfgOptions;
use rg_ir_view::ty::IndexedType;
use rg_item_tree::{ItemKind, TypeBound, TypeRef};
use rg_project::{
    CurrentSourceSelection, IndexingPerformancePreference, PackageResidencyPolicy, Project,
    RustdocInput, SplitIndexingMode, StartupCacheLoad,
};
use rg_std::CancellationToken;
use rg_workspace::{SysrootSources, WorkspaceLoweringConfig, WorkspaceMetadata};
use serde::Deserialize;
use serde_json::{Value, json};

const OWNER: &str = "directory::models::user::User";
const PROBES: &str = r#"
        use suprnova::eloquent::Model as _;
        let sup_query = User::query();
        let sup_without = User::without_global_scopes();
        let sup_filter = User::filter("email", "member@example.test");
        let sup_expected_builder: suprnova::eloquent::Builder<User> = loop {};
        let sup_key: <User as suprnova::eloquent::EloquentModel>::Key = loop {};
        let sup_expected_key: i64 = 1i64;
        let sup_entity: <User as suprnova::eloquent::EloquentModel>::Entity = loop {};
        let sup_expected_entity: Entity = loop {};
        let sup_column: <User as suprnova::eloquent::EloquentModel>::Column = loop {};
        let sup_expected_column: Column = loop {};
        let sup_source = self.verify_password(password);
        let sup_expected_source: Result<bool, FrameworkError> = loop {};
        {
            struct User;
            let sup_other = User::without_global_scopes();
        }
"#;

#[derive(Deserialize)]
struct Acceptance {
    metadata: PathBuf,
    export: PathBuf,
    manifest: PathBuf,
    model_source: PathBuf,
    sysroot: PathBuf,
    target_cfg: String,
}

impl Acceptance {
    fn workspace(&self) -> anyhow::Result<WorkspaceMetadata> {
        let metadata: cargo_metadata::Metadata =
            serde_json::from_slice(&fs::read(&self.metadata)?)?;
        let sysroot = SysrootSources::from_library_root(&self.sysroot)
            .context("acceptance requires the recorded rust-src tree")?;
        Ok(WorkspaceMetadata::lower(
            metadata,
            CfgOptions::from_rustc_cfg_output(&self.target_cfg),
            WorkspaceLoweringConfig::default(),
        )?
        .with_sysroot_sources(Some(sysroot)))
    }

    fn input(&self) -> RustdocInput {
        RustdocInput {
            manifest_path: self.manifest.clone(),
            target_name: "directory".into(),
            target_kind: rg_workspace::TargetKind::Lib,
            export_path: self.export.clone(),
            item_path: OWNER.into(),
        }
    }

    fn build(&self, imported: bool, batched: bool) -> anyhow::Result<Project> {
        Project::builder(self.workspace()?)
            .rustdoc_inputs(if imported { vec![self.input()] } else { vec![] })
            .indexing_preference(if batched {
                IndexingPerformancePreference::LowerPeakMemory
            } else {
                IndexingPerformancePreference::FasterBuilds
            })
            .split_indexing_mode(SplitIndexingMode::EarlyStart)
            .package_residency_policy(PackageResidencyPolicy::WorkspaceResident)
            .memory_hooks(memory::project_memory_hooks())
            .startup_cache_load(StartupCacheLoad::Disabled)
            .build()
    }

    fn lowered(view: &rg_rustdoc::TypeApiView<'_>) -> anyhow::Result<Value> {
        let lowered = view.lower()?;
        let mut filters = Vec::new();
        for node in lowered.items.iter() {
            ensure!(
                node.name
                    .as_ref()
                    .is_none_or(|name| name.as_str() != "query"),
                "query must use the framework trait default"
            );
            if node
                .name
                .as_ref()
                .is_some_and(|name| name.as_str() == "filter")
                && let ItemKind::Function(function) = &node.kind
            {
                filters.push(function);
            }
        }
        ensure!(
            filters.len() == 1,
            "one generated filter signature required"
        );
        let parameters = &filters[0].params;
        let mut bounds = Vec::new();
        for (parameter, expected) in parameters.iter().zip([
            "suprnova::eloquent::builder::IntoColumn",
            "suprnova::eloquent::builder::IntoVal",
        ]) {
            let Some(TypeRef::ImplTrait(actual)) = &parameter.ty else {
                anyhow::bail!("filter lost its argument-position impl Trait");
            };
            let paths = actual
                .iter()
                .filter_map(TypeBound::trait_ty)
                .map(ToString::to_string)
                .collect::<Vec<_>>();
            ensure!(
                paths.iter().any(|path| path == expected),
                "filter lost {expected}"
            );
            bounds.push(expected);
        }
        ensure!(
            parameters.len() == 2,
            "filter must have two bounded arguments"
        );
        // An attached reverse conversion must never be installed with User as its self type.
        for impl_id in &lowered.impls {
            let ItemKind::Impl(header) = &lowered.items[*impl_id].kind else {
                anyhow::bail!("lowered impl kind changed");
            };
            let name = header.self_ty.to_string();
            ensure!(
                name == OWNER
                    || name.starts_with(&format!("{OWNER}<"))
                    || name == "directory::models::user::user::Model",
                "unexpected actual impl owner: {name}"
            );
        }
        Ok(json!({"filterBounds": bounds, "directOwnership": true}))
    }

    fn queries(&self, project: &Project) -> anyhow::Result<BTreeMap<String, bool>> {
        let snapshot = project.snapshot();
        let contexts = snapshot.file_contexts_for_path(&self.model_source)?;
        ensure!(
            contexts.len() == 1,
            "User source must have one package owner"
        );
        let context = &contexts[0];
        ensure!(
            context.crates.len() == 1,
            "User source must have one crate target"
        );
        let target = context.crates[0];
        let targets = [(target, context.file)];
        // Reuse the existing source method's body association. The application file is never edited.
        let source = fs::read_to_string(&self.model_source)?;
        let signature =
            "pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {";
        ensure!(
            source.matches(signature).count() == 1,
            "recorded source method changed"
        );
        let text = source.replacen(signature, &format!("{signature}\n{PROBES}"), 1);
        let start = text.find("let sup_query").context("probe missing")? as u32;
        let cancellation = CancellationToken::new();
        let current = snapshot.prepare_current_source(&targets, &text, &cancellation)?;
        let (analysis, _) = snapshot.analysis_for_current_source(
            &targets,
            current,
            CurrentSourceSelection::AtOffset(start),
            cancellation,
            |_| Ok(()),
        )?;
        let mut types: BTreeMap<&str, Option<IndexedType>> = BTreeMap::new();
        for name in [
            "query",
            "without",
            "filter",
            "expected_builder",
            "key",
            "expected_key",
            "entity",
            "expected_entity",
            "column",
            "expected_column",
            "source",
            "expected_source",
            "other",
        ] {
            let token = format!("sup_{name}");
            let offset = text.find(&token).context("typed probe missing")? + token.len() - 1;
            types.insert(name, analysis.type_at(target, context.file, offset as u32)?);
        }
        let mut result = BTreeMap::new();
        for (actual, expected) in [
            ("query", "expected_builder"),
            ("without", "expected_builder"),
            ("filter", "expected_builder"),
            ("key", "expected_key"),
            ("entity", "expected_entity"),
            ("column", "expected_column"),
            ("source", "expected_source"),
        ] {
            let concrete = types[expected].as_ref().is_some_and(|ty| {
                ty.primitive().is_some() || ty.nominal_type_defs().next().is_some()
            });
            result.insert(actual.into(), concrete && types[actual] == types[expected]);
        }
        result.insert(
            "ownerIsolation".into(),
            types["other"].as_ref().is_none_or(|ty| {
                ty.primitive().is_none() && ty.nominal_type_defs().next().is_none()
            }),
        );
        Ok(result)
    }

    fn failed_candidates(&self, previous: &Project) -> anyhow::Result<Value> {
        let generation = previous.generation_id();
        let before = self.queries(previous)?;
        let mut wrong = self.input();
        wrong.target_name = "absent_target".into();
        let target_rejected = Project::builder(self.workspace()?)
            .rustdoc_inputs(vec![wrong])
            .build()
            .is_err();
        // Only this run's decompressed copy is changed. The frozen compiler capture stays intact.
        let original = fs::read(&self.export)?;
        let mut json: Value = serde_json::from_slice(&original)?;
        let paths = json["paths"]
            .as_object_mut()
            .context("export paths missing")?;
        let builder = paths
            .iter()
            .find(|(_, item)| item["path"] == json!(["suprnova", "eloquent", "builder", "Builder"]))
            .map(|(id, _)| id.clone())
            .context("genuine Builder path missing")?;
        paths.remove(&builder);
        fs::write(&self.export, serde_json::to_vec(&json)?)?;
        let attempted = self.build(true, false);
        fs::write(&self.export, original)?;
        let reference_rejected = attempted.is_err();
        let preserved =
            generation == previous.generation_id() && before == self.queries(previous)?;
        Ok(
            json!({"targetRejected": target_rejected, "referenceRejected": reference_rejected, "previousPreserved": preserved}),
        )
    }

    fn run(&self, mode: &str) -> anyhow::Result<Value> {
        if mode == "lower" {
            let export = rg_rustdoc::RustdocExport::read(fs::File::open(&self.export)?)?;
            return Ok(match export.type_api(OWNER) {
                Ok(view) => match Self::lowered(&view) {
                    Ok(value) => json!({"mode": mode, "selected": true, "lowered": value}),
                    Err(error) => {
                        json!({"mode": mode, "selected": true, "loweringError": format!("{error:#}")})
                    }
                },
                Err(error) => {
                    json!({"mode": mode, "selected": false, "selectionError": format!("{error:#}")})
                }
            });
        }
        ensure!(
            matches!(mode, "initial" | "batched" | "source"),
            "unknown acceptance mode"
        );
        let imported = mode != "source";
        let mut project = match self.build(imported, mode == "batched") {
            Ok(project) => project,
            Err(error) => return Ok(json!({"mode": mode, "buildError": format!("{error:#}")})),
        };
        project.split_indexing().finish()?;
        ensure!(
            !project.has_unfinished_split_indexing(),
            "deferred indexing incomplete"
        );
        let queries = self.queries(&project)?;
        memory::ProcessMemoryControl::try_purge_allocator();
        let mut idle = Vec::new();
        for _ in 0..5 {
            thread::sleep(Duration::from_millis(100));
            // Linux statm measures this engine process, not its compiler or build children.
            let statm = fs::read_to_string("/proc/self/statm")?;
            let pages: u64 = statm
                .split_whitespace()
                .nth(1)
                .context("RSS missing")?
                .parse()?;
            idle.push(pages);
        }
        let comparison = json!({
            "allocator": memory::ProcessMemoryControl::allocator_name(),
            "sysroot": self.sysroot,
            "targetCfg": self.target_cfg,
            "residency": project.package_residency_plan().policy().config_name(),
            "residentPackages": project.workspace().packages().iter().zip(project.package_residency_plan().packages())
                .filter(|(_, residency)| **residency == rg_project::PackageResidency::Resident)
                .map(|(package, _)| package.id.to_string()).collect::<Vec<_>>(),
            "queryWorkload": queries.keys().collect::<Vec<_>>()
        });
        // Candidate controls allocate extra temporary graphs. Run them after the comparable idle sample.
        let candidates = if mode == "initial" {
            Some(self.failed_candidates(&project)?)
        } else {
            None
        };
        Ok(
            json!({"mode": mode, "queries": queries, "candidates": candidates, "comparison": comparison,
            "idleResidentPages": idle, "indexingComplete": true,
            "retainedBytes": project.snapshot().retained_memory_bytes()}),
        )
    }
}

fn main() -> anyhow::Result<()> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    ensure!(arguments.len() == 2, "usage: suprnova_user PLAN MODE");
    if arguments[1] == "initial" {
        let control = PathBuf::from(&arguments[0]).parent().context("control run missing")?.join("rustdoc");
        fs::copy("/usr/bin/true", &control)?;
        ensure!(std::process::Command::new(control).status()?.success(), "control launch failed");
    }
    let acceptance: Acceptance = serde_json::from_slice(&fs::read(&arguments[0])?)?;
    let report = acceptance.run(&arguments[1])?;
    println!("suprnova-observation: {}", serde_json::to_string(&report)?);
    Ok(())
}
