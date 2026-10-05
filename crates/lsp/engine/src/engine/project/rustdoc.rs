//! Immutable compiler candidates built away from the synchronous analysis lane.

use std::{path::PathBuf, sync::Arc};
use anyhow::Context as _;
use rg_lsp_proto::{RustdocGenerationInput, RustdocTargetKind};
use rg_project::{Project, ProjectMemoryHooks, SplitIndexingMode};
use rg_workspace::{SysrootSources, WorkspaceMetadata};
use super::{ProjectConfiguration};

#[derive(Debug)]
pub(crate) struct RustdocProjectBuildInputs {
    pub(super) root: PathBuf,
    pub(super) configuration: ProjectConfiguration,
    pub(super) saved_generation: u64,
    pub(super) memory_hooks: Arc<dyn ProjectMemoryHooks>,
}

impl RustdocProjectBuildInputs {
    pub(crate) fn build(self, input: RustdocGenerationInput) -> anyhow::Result<RustdocProjectCandidate> {
        anyhow::ensure!(input.workspace_root == self.root, "rustdoc candidate belongs to another workspace");
        let metadata_config = self.configuration.cargo_metadata_config.clone().locked(true);
        let metadata = metadata_config.load_metadata_with_target_cfg(self.root.join("Cargo.toml"))
            .context("load locked Cargo graph for rustdoc candidate")?;
        let workspace = WorkspaceMetadata::lower(metadata.metadata, metadata.target_cfg,
            self.configuration.workspace_lowering_config.clone())?;
        let sysroot = if self.configuration.discover_sysroot {
            SysrootSources::discover(workspace.workspace_root())
        } else { None };
        let workspace = workspace.with_sysroot_sources(sysroot);
        let exports = input.exports.iter().map(|export| rg_project::RustdocTargetExport {
            manifest_path: export.manifest_path.clone(),
            target_name: export.target_name.clone(),
            target_kind: match export.target_kind {
                RustdocTargetKind::Lib => rg_workspace::TargetKind::Lib,
                RustdocTargetKind::Bin => rg_workspace::TargetKind::Bin,
            },
            export_path: export.export_path.clone(),
        }).collect();
        let project = Project::builder(workspace)
            .workspace_lowering_config(self.configuration.workspace_lowering_config)
            .cargo_metadata_config(metadata_config)
            .indexing_preference(self.configuration.indexing_preference)
            .package_batch_size(self.configuration.package_batch_size)
            .split_indexing_mode(SplitIndexingMode::EarlyStart)
            .package_residency_policy(self.configuration.package_residency_policy)
            .rustdoc_targets(exports)
            .memory_hooks(self.memory_hooks)
            .build().context("build fresh lowered rustdoc project")?;
        Ok(RustdocProjectCandidate { project, input, saved_generation: self.saved_generation })
    }
}

#[derive(Debug)]
pub(crate) struct RustdocProjectCandidate {
    pub(super) project: Project,
    pub(super) input: RustdocGenerationInput,
    pub(super) saved_generation: u64,
}

