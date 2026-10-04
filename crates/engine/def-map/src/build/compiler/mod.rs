//! Reconcile supplied declarations with one exact crate's collected source identities.

use anyhow::{Context as _, ensure};
use rg_ir_model::{CrateRef, DefId, ModuleRef};
use rg_item_tree::{CompilerTypeDeclarations, GenericArg, ItemKind, ItemNode, ItemTreeDb, TypeRef};
use rg_std::ExpectedUnique;

use super::{collect::CrateState, finalize::FinalizeCrateStates};
use crate::{
    GeneratedItemRef, ItemSource, ItemSourceKind, LocalDefKind, LocalImplData, NamespaceSet,
    ScopeResolver, query::CrateResolutionEnv, source::GeneratedSourceData,
};

/// Prepared imports allocate no persistent identities until every owner and reference is valid.
pub(super) struct CompilerImport {
    crate_ref: CrateRef,
    module: rg_ir_model::ModuleId,
    source: GeneratedSourceData,
    new_impls: Vec<rg_item_tree::ItemTreeId>,
    extensions: Vec<(ItemSource, Vec<rg_item_tree::ItemTreeId>)>,
}

impl CompilerImport {
    pub(super) fn prepare<E>(
        env: &E,
        state: &CrateState,
        item_tree: &ItemTreeDb,
        mut declarations: CompilerTypeDeclarations,
    ) -> anyhow::Result<Self>
    where
        E: CrateResolutionEnv<Error = rg_package_store::PackageStoreError>,
    {
        ensure!(
            declarations
                .path
                .first()
                .is_some_and(|name| name == &state.crate_name.replace('-', "_")),
            "rustdoc crate identity does not match {}",
            state.crate_name
        );
        let (name, modules) = declarations.path[1..]
            .split_last()
            .context("rustdoc owner path is empty")?;
        let map = state.def_map_builder.partial();
        let mut module = state.root_module;
        for name in modules {
            let mut children = ExpectedUnique::new();
            for (child_name, child) in &map
                .module(module)
                .context("rustdoc parent module missing")?
                .children
            {
                if child_name.as_str() == name {
                    children.push(*child);
                }
            }
            let ExpectedUnique::One(child) = children else {
                anyhow::bail!(
                    "rustdoc module {} cannot be mapped to source",
                    declarations.path.join("::")
                );
            };
            module = child;
        }
        let kind = LocalDefKind::from_item_tag(declarations.kind)
            .context("invalid rustdoc nominal kind")?;
        let mut owners = ExpectedUnique::new();
        for owner in map.local_defs() {
            if owner.module == module && owner.kind == kind && owner.name.as_str() == name {
                owners.push(owner);
            }
        }
        let ExpectedUnique::One(owner) = owners else {
            anyhow::bail!(
                "rustdoc owner {} cannot be mapped uniquely to source",
                declarations.path.join("::")
            );
        };
        let (owner_item, origin_source) = Self::source_item(state, item_tree, owner.source)?;
        let origin_source = origin_source.context("rustdoc owner has no source provenance")?;
        let context = ModuleRef::krate(state.crate_ref, module);
        let resolver = ScopeResolver::new(env);
        let mut new_impls = Vec::new();
        let mut extensions = Vec::new();
        for impl_id in &declarations.impls {
            let ItemKind::Impl(imported) = &declarations.items[*impl_id].kind else {
                anyhow::bail!("compiler import contains a non-impl item");
            };
            let params = imported
                .generics
                .type_param_names()
                .map(|name| name.as_str())
                .collect::<Vec<_>>();
            let imported_self = Self::type_key(env, context, &imported.self_ty, &params)?;
            let imported_trait = imported
                .trait_ref
                .as_ref()
                .map(|ty| Self::type_key(env, context, ty, &params))
                .transpose()?;
            let mut matching = Vec::new();
            for existing in map.local_impls() {
                let (node, _) = Self::source_item(state, item_tree, existing.source)?;
                let ItemKind::Impl(written) = &node.kind else {
                    continue;
                };
                let written_context = ModuleRef::krate(state.crate_ref, existing.module);
                let written_params = written
                    .generics
                    .type_param_names()
                    .map(|name| name.as_str())
                    .collect::<Vec<_>>();
                let written_self =
                    Self::type_key(env, written_context, &written.self_ty, &written_params)?;
                let written_trait = written
                    .trait_ref
                    .as_ref()
                    .map(|ty| Self::type_key(env, written_context, ty, &written_params))
                    .transpose()?;
                if imported_self.is_some()
                    && imported_self == written_self
                    && imported_trait == written_trait
                {
                    matching.push((existing.source, written));
                }
            }
            if imported.trait_ref.is_some() {
                ensure!(
                    matching.len() <= 1,
                    "rustdoc trait impl matches multiple source impls"
                );
            }
            // `methods!()` may already supply a member the compiler also reports. Walk retained
            // replacements, including nested calls, before deciding that a declaration is missing.
            let mut existing_items = Vec::new();
            for (source, written) in &matching {
                let mut pending = written
                    .items
                    .iter()
                    .map(|child| source.with_item(*child))
                    .collect::<Vec<_>>();
                let mut visited = std::collections::HashSet::new();
                while let Some(source) = pending.pop() {
                    if !visited.insert(source) {
                        continue;
                    }
                    let (node, _) = Self::source_item(state, item_tree, source)?;
                    if matches!(node.kind, ItemKind::MacroCall(_)) {
                        pending.extend(map.associated_macro_expansion(source).unwrap_or_default());
                    } else {
                        existing_items.push((node.name.clone(), node.kind.tag()));
                    }
                }
            }
            let missing = imported
                .items
                .iter()
                .copied()
                .filter(|child| {
                    let incoming = &declarations.items[*child];
                    !existing_items.contains(&(incoming.name.clone(), incoming.kind.tag()))
                })
                .collect::<Vec<_>>();
            if let Some((source, _)) = matching.first() {
                if !missing.is_empty() {
                    extensions.push((*source, missing));
                }
            } else {
                new_impls.push(*impl_id);
            }
        }
        // Referenced declarations must exist in this candidate's graph with the compiler's item
        // kind. An export-local ID or a coincidentally matching value name is insufficient.
        let mut needed = std::collections::HashSet::new();
        for impl_id in &new_impls {
            needed.insert(*impl_id);
            if let ItemKind::Impl(header) = &declarations.items[*impl_id].kind {
                needed.extend(&header.items);
            }
        }
        for (_, children) in &extensions {
            needed.extend(children);
        }
        for (item, path, kind) in &declarations.references {
            if !needed.contains(item) {
                continue;
            }
            let resolved = resolver.resolve_path(
                context,
                &path
                    .as_def_map_path()
                    .context("invalid rustdoc signature path")?,
                NamespaceSet::TYPES,
            )?;
            let [DefId::Local(def)] = resolved.resolved.as_slice() else {
                anyhow::bail!("rustdoc signature path {path} cannot be mapped uniquely to source");
            };
            ensure!(
                env.local_def_kind(*def)? == LocalDefKind::from_item_tag(*kind),
                "rustdoc signature path {path} has the wrong source kind"
            );
        }

        // Imported nodes have no editable source syntax. Use the nominal owner only as provenance;
        // signatures retain synthetic zero spans rather than pretending their paths were written.
        for item in declarations.items.iter_mut() {
            item.file_id = owner_item.file_id;
        }
        Ok(Self {
            crate_ref: state.crate_ref,
            module,
            source: GeneratedSourceData {
                origin_file_id: owner_item.file_id,
                origin_span: owner_item.span,
                origin_source,
                top_level: declarations.impls,
                items: declarations.items,
            },
            new_impls,
            extensions,
        })
    }

    pub(super) fn apply(self, states: &mut FinalizeCrateStates) {
        let state = states
            .crate_state_mut(self.crate_ref)
            .expect("prepared import crate exists");
        let file_id = self.source.origin_file_id;
        let span = self.source.origin_span;
        let source = state.def_map_builder.alloc_generated_source(self.source);
        for item in self.new_impls {
            let local_impl = state.def_map_builder.alloc_local_impl(LocalImplData {
                module: self.module,
                source: ItemSource::synthetic(file_id, GeneratedItemRef { source, item }),
                file_id,
                span,
            });
            state
                .def_map_builder
                .module_mut(self.module)
                .expect("prepared import module exists")
                .impls
                .push(local_impl);
        }
        for (owner, children) in self.extensions {
            state.def_map_builder.extend_imported_associated_items(
                owner,
                children
                    .into_iter()
                    .map(|item| ItemSource::synthetic(file_id, GeneratedItemRef { source, item }))
                    .collect(),
            );
        }
    }

    fn source_item<'a>(
        state: &'a CrateState,
        item_tree: &'a ItemTreeDb,
        source: ItemSource,
    ) -> anyhow::Result<(&'a ItemNode, Option<rg_item_tree::ItemTreeRef>)> {
        match source.kind {
            ItemSourceKind::ItemTree(reference) => Ok((
                item_tree
                    .package(state.crate_ref.package.0)
                    .and_then(|package| package.item(reference))
                    .context("source declaration missing")?,
                Some(reference),
            )),
            ItemSourceKind::Generated(reference) | ItemSourceKind::Synthetic(reference) => {
                let data = state
                    .def_map_builder
                    .partial()
                    .generated_source(reference.source)
                    .context("generated declaration missing")?;
                Ok((
                    data.item(reference.item)
                        .context("generated item missing")?,
                    Some(data.origin_source),
                ))
            }
            ItemSourceKind::Body(_) => {
                anyhow::bail!("rustdoc owner cannot be a body-local declaration")
            }
        }
    }

    /// Compare receiver and trait identities across different path spellings and module contexts.
    fn type_key<E>(
        env: &E,
        module: ModuleRef,
        ty: &TypeRef,
        params: &[&str],
    ) -> anyhow::Result<Option<String>>
    where
        E: CrateResolutionEnv<Error = rg_package_store::PackageStoreError>,
    {
        Ok(match ty {
            TypeRef::Path(path) => {
                let Some(plain) = path.as_def_map_path() else {
                    return Ok(None);
                };
                let resolved =
                    ScopeResolver::new(env).resolve_path(module, &plain, NamespaceSet::TYPES)?;
                let base = match resolved.resolved.as_slice() {
                    [def] => format!("{def:?}"),
                    [] if path
                        .single_name()
                        .is_some_and(|name| params.contains(&name.as_str())) =>
                    {
                        path.to_string()
                    }
                    _ => return Ok(None),
                };
                let mut args = Vec::new();
                for arg in path.segments.iter().flat_map(|segment| &segment.args) {
                    args.push(match arg {
                        GenericArg::Type(ty) => {
                            let Some(key) = Self::type_key(env, module, ty, params)? else {
                                return Ok(None);
                            };
                            key
                        }
                        GenericArg::Lifetime(name) => name.to_string(),
                        GenericArg::Const(value) => value.to_string(),
                        _ => return Ok(None),
                    });
                }
                Some(format!("{base}<{args:?}>"))
            }
            TypeRef::Reference {
                lifetime,
                mutability,
                inner,
            } => Self::type_key(env, module, inner, params)?
                .map(|key| format!("&{lifetime:?}{mutability:?}{key}")),
            _ => None,
        })
    }
}
