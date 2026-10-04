//! Capture supplied declarations once and route them through every source construction path.

use std::{
    collections::{BTreeMap, HashMap, HashSet},
    sync::Arc,
};

use anyhow::{Context as _, ensure};
use rg_ir_model::{CrateId, CrateRef, PackageSlot};
use rg_item_tree::CompilerTypeDeclarations;
use rg_std::{ExpectedUnique, MemorySize};
use rg_workspace::WorkspaceMetadata;

use super::builder::RustdocInput;
use crate::{PackageResidency, PackageResidencyPlan};

#[cfg(test)]
mod tests;

/// Replayable declaration facts for this saved generation. The original JSON and compiler IDs
/// are dropped immediately; rebuilding never rereads a mutable external export.
#[derive(Debug, Clone, Default, MemorySize)]
pub(crate) struct CompilerImports {
    declarations: Arc<Vec<(CrateRef, CompilerTypeDeclarations)>>,
    affected_packages: Vec<PackageSlot>,
}

impl CompilerImports {
    pub(super) fn read(
        workspace: &WorkspaceMetadata,
        inputs: &[RustdocInput],
    ) -> anyhow::Result<Self> {
        let mut declarations = Vec::new();
        let mut selected = HashSet::new();
        let mut affected_ids = HashSet::new();
        for input in inputs {
            let manifest = input.manifest_path.canonicalize().with_context(|| {
                format!("resolve rustdoc manifest {}", input.manifest_path.display())
            })?;
            let (package_slot, package) = workspace
                .packages()
                .iter()
                .enumerate()
                .find(|(_, package)| package.manifest_path == manifest)
                .with_context(|| {
                    format!(
                        "rustdoc manifest {} is absent from the workspace",
                        manifest.display()
                    )
                })?;
            let targets = rg_parse::Package::analyzed_targets(package);
            let mut matches = targets.iter().enumerate().filter(|(_, target)| {
                target.name == input.target_name && target.kind == input.target_kind
            });
            let (target_slot, _) = matches.next().with_context(|| {
                format!(
                    "rustdoc target {} ({}) is absent from {}",
                    input.target_name,
                    input.target_kind,
                    manifest.display()
                )
            })?;
            ensure!(
                matches.next().is_none(),
                "rustdoc target {} is ambiguous",
                input.target_name
            );
            let crate_ref = CrateRef {
                package: PackageSlot(package_slot),
                crate_id: CrateId(target_slot),
            };
            ensure!(
                selected.insert((crate_ref, input.item_path.clone())),
                "duplicate rustdoc input for {}",
                input.item_path
            );
            let file = std::fs::File::open(&input.export_path)
                .with_context(|| format!("open rustdoc export {}", input.export_path.display()))?;
            let export = rg_rustdoc::RustdocExport::read(file)
                .with_context(|| format!("read rustdoc export {}", input.export_path.display()))?;
            let view = export
                .type_api(&input.item_path)
                .with_context(|| format!("select rustdoc owner {}", input.item_path))?;
            ensure!(
                view.path
                    .first()
                    .is_some_and(|name| name == &input.target_name.replace('-', "_")),
                "rustdoc owner {} belongs to another crate target",
                input.item_path
            );
            declarations.extend(
                export
                    .lower_type(
                        &input.item_path,
                        &Self::crate_roots(workspace, package_slot),
                    )
                    .with_context(|| format!("lower rustdoc owner {}", input.item_path))?
                    .into_iter()
                    .map(|declarations| (crate_ref, declarations)),
            );
            affected_ids.insert(package.id.clone());
        }
        // Cached dependent payloads can refer to the imported package's arena IDs. Rebuild and
        // retain that reverse dependency closure too; never write an unkeyed compiler import into
        // an ordinary source-only artifact.
        loop {
            let mut changed = false;
            for package in workspace.packages() {
                if package
                    .dependencies
                    .iter()
                    .any(|dependency| affected_ids.contains(dependency.package_id()))
                {
                    changed |= affected_ids.insert(package.id.clone());
                }
            }
            if !changed {
                break;
            }
        }
        let affected_packages = workspace
            .packages()
            .iter()
            .enumerate()
            .filter_map(|(slot, package)| {
                affected_ids
                    .contains(&package.id)
                    .then_some(PackageSlot(slot))
            })
            .collect();
        Ok(Self {
            declarations: Arc::new(declarations),
            affected_packages,
        })
    }

    /// Rustdoc uses defining crate names, even when source only sees a facade re-export.
    /// Map unique names in the selected package's resolved dependency closure without adding
    /// those transitive dependencies to the source extern prelude.
    fn crate_roots(
        workspace: &WorkspaceMetadata,
        package_slot: usize,
    ) -> BTreeMap<String, Option<CrateRef>> {
        let packages = workspace.packages();
        let slots: HashMap<_, _> = packages
            .iter()
            .enumerate()
            .map(|(slot, package)| (&package.id, slot))
            .collect();
        let mut seen = HashSet::new();
        let mut pending = vec![package_slot];
        let mut roots: BTreeMap<String, ExpectedUnique<CrateRef>> = BTreeMap::new();
        while let Some(slot) = pending.pop() {
            if !seen.insert(slot) {
                continue;
            }
            let package = &packages[slot];
            for dependency in &package.dependencies {
                if dependency.is_normal()
                    && let Some(slot) = slots.get(dependency.package_id())
                {
                    pending.push(*slot);
                }
            }
            for (target_slot, target) in rg_parse::Package::analyzed_targets(package)
                .iter()
                .enumerate()
            {
                if target.kind.is_lib() {
                    roots
                        .entry(target.name.replace('-', "_"))
                        .or_default()
                        .push(CrateRef {
                            package: PackageSlot(slot),
                            crate_id: CrateId(target_slot),
                        });
                }
            }
        }
        roots
            .into_iter()
            .map(|(name, root)| (name, root.into_option()))
            .collect()
    }

    pub(super) fn retain_affected_packages(&self, plan: &mut PackageResidencyPlan) {
        for package in &self.affected_packages {
            plan.packages[package.0] = PackageResidency::Resident;
        }
    }

    pub(super) fn queue(
        &self,
        session: &mut rg_def_map::DefMapBuildSession,
        packages: &[PackageSlot],
    ) -> anyhow::Result<()> {
        for (crate_ref, declarations) in self.declarations.iter() {
            if packages.contains(&crate_ref.package) {
                session.import_declarations(*crate_ref, declarations.clone())?;
            }
        }
        Ok(())
    }
}
