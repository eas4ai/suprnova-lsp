//! Reconcile supplied declarations with one exact crate's collected source identities.

use anyhow::{Context as _, ensure};
use rg_ir_model::{CrateRef, DefId, DefMapRef, LocalDefRef, ModuleId, ModuleRef};
use rg_item_tree::{CompilerTypeDeclarations, GenericArg, ItemKind, ItemNode, ItemTreeDb, TypeRef};
use rg_std::ExpectedUnique;

use super::{
    collect::CrateState,
    finalize::{FinalizeCrateStates, ScopeMatrix},
};
use crate::{
    GeneratedItemRef, ItemSource, ItemSourceKind, LocalDefData, LocalDefKind, LocalImplData,
    ModuleData, ModuleOrigin, ModuleScope, Namespace, NamespaceSet, ScopeBinding,
    ScopeBindingProvenance, ScopeResolver, query::CrateResolutionEnv, source::GeneratedSourceData,
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
    /// Add only supporting declarations beneath new compiler-established child modules. Existing
    /// source namespaces still reject a missing nominal instead of silently inventing its owner.
    pub(super) fn install_nominals(
        states: &mut FinalizeCrateStates,
        scopes: &mut ScopeMatrix,
        item_tree: &ItemTreeDb,
        imports: &mut [(CrateRef, CompilerTypeDeclarations)],
    ) -> anyhow::Result<bool> {
        let mut anchors = std::collections::HashMap::new();
        for (crate_ref, declarations) in imports
            .iter()
            .filter(|(_, declarations)| declarations.origin.is_none())
        {
            let state = states
                .crate_state(*crate_ref)
                .context("compiler anchor crate missing")?;
            Self::source_owner(state, item_tree, &declarations.path, declarations.kind)?;
            anchors.insert((*crate_ref, declarations.path.clone()), declarations.kind);
        }
        let mut created = std::collections::HashSet::new();
        let mut changed = false;
        for (crate_ref, declarations) in imports {
            let Some(origin) = &declarations.origin else {
                continue;
            };
            let kind = anchors
                .get(&(*crate_ref, origin.clone()))
                .context("compiler nominal has no selected source anchor")?;
            ensure!(
                declarations.path.len() > origin.len()
                    && declarations.path.starts_with(&origin[..origin.len() - 1]),
                "compiler nominal escapes its source anchor"
            );
            ensure!(
                declarations.modules.len() == declarations.path.len() - origin.len(),
                "compiler nominal module chain is incomplete"
            );
            let state = states
                .crate_state_mut(*crate_ref)
                .context("compiler nominal crate missing")?;
            let (mut module, owner, origin_source) =
                Self::source_owner(state, item_tree, origin, *kind)?;
            let file_id = owner.file_id;
            let span = owner.span;
            for (offset, (path, visibility)) in declarations.modules.iter().enumerate() {
                let depth = origin.len() + offset;
                ensure!(
                    path == &declarations.path[..depth],
                    "compiler nominal module chain disagrees with its path"
                );
                let name = rg_text::Name::new(&path[depth - 1]);
                let mut children = ExpectedUnique::new();
                for (child_name, child) in &state
                    .def_map_builder
                    .partial()
                    .module(module)
                    .context("compiler parent module missing")?
                    .children
                {
                    if child_name == &name {
                        children.push(*child);
                    }
                }
                match children {
                    ExpectedUnique::One(child) => module = child,
                    ExpectedUnique::Ambiguous => {
                        anyhow::bail!("compiler child module is ambiguous")
                    }
                    ExpectedUnique::Empty => {
                        let visibility =
                            state.def_map_builder.resolve_visibility(module, visibility);
                        let child = state.def_map_builder.alloc_module(ModuleData {
                            name: Some(name.clone()),
                            name_span: None,
                            docs: None,
                            user_facing_attrs: Default::default(),
                            visibility,
                            parent: Some(module),
                            children: Vec::new(),
                            local_defs: Vec::new(),
                            impls: Vec::new(),
                            imports: Vec::new(),
                            unresolved_imports: Vec::new(),
                            scope: ModuleScope::default(),
                            origin: ModuleOrigin::Inline {
                                declaration_file: file_id,
                                declaration_span: span,
                            },
                        });
                        state.base_scopes.push(Default::default());
                        scopes
                            .push_module_scope(*crate_ref, Default::default())
                            .context("compiler scope crate missing")?;
                        state
                            .def_map_builder
                            .module_mut(module)
                            .expect("checked compiler parent")
                            .children
                            .push((name.clone(), child));
                        let binding = ScopeBinding::new(
                            DefId::Module(ModuleRef::krate(*crate_ref, child)),
                            visibility,
                            ScopeBindingProvenance::Direct,
                        );
                        state.base_scopes[module.0].insert_binding(
                            &name,
                            Namespace::Types,
                            binding.clone(),
                        );
                        scopes
                            .module_scope_mut(*crate_ref, module)
                            .expect("checked compiler scope")
                            .insert_binding(&name, Namespace::Types, binding);
                        created.insert((*crate_ref, child));
                        module = child;
                        changed = true;
                    }
                }
            }
            let nominal = declarations
                .nominal
                .context("compiler supporting nominal missing")?;
            let mut node = declarations.items[nominal].clone();
            let name = node.name.clone().context("compiler nominal has no name")?;
            ensure!(
                name.as_str()
                    == declarations
                        .path
                        .last()
                        .context("compiler nominal path empty")?,
                "compiler nominal name disagrees with its path"
            );
            let kind = LocalDefKind::from_item_tag(declarations.kind)
                .context("compiler nominal kind invalid")?;
            ensure!(
                node.kind.tag() == declarations.kind,
                "compiler nominal payload has the wrong kind"
            );
            let mut existing = ExpectedUnique::new();
            for declaration in state.def_map_builder.partial().local_defs() {
                if declaration.module == module && declaration.name == name {
                    existing.push(declaration.kind);
                }
            }
            match existing {
                ExpectedUnique::One(existing) => {
                    ensure!(
                        existing == kind,
                        "compiler nominal has the wrong source kind"
                    );
                    declarations.nominal = None;
                    continue;
                }
                ExpectedUnique::Ambiguous => {
                    anyhow::bail!("compiler nominal source identity is ambiguous")
                }
                ExpectedUnique::Empty => ensure!(
                    created.contains(&(*crate_ref, module)),
                    "compiler nominal cannot replace a missing source declaration"
                ),
            }
            // The selected source item is provenance, not editable syntax for these declarations.
            node.file_id = file_id;
            let namespaces = kind.scope_namespaces(&node.kind);
            let visibilities = state.def_map_builder.resolve_local_def_visibilities(
                module,
                &node.kind,
                &node.visibility,
            );
            let mut items = rg_arena::Arena::new();
            let item = items.alloc(node.clone());
            let source = state
                .def_map_builder
                .alloc_generated_source(GeneratedSourceData {
                    origin_file_id: file_id,
                    origin_span: span,
                    origin_source,
                    top_level: vec![item],
                    items,
                });
            let local_def = state.def_map_builder.alloc_local_def(LocalDefData {
                module,
                name: name.clone(),
                kind,
                namespaces,
                visibility: node.visibility.clone(),
                source: ItemSource::synthetic(file_id, GeneratedItemRef { source, item }),
                file_id,
                name_span: None,
                span: node.span,
                user_facing_attrs: node.user_facing_attrs,
            });
            state
                .def_map_builder
                .module_mut(module)
                .expect("checked compiler module")
                .local_defs
                .push(local_def);
            if let ItemKind::Enum(data) = &node.kind {
                let visibility = state
                    .def_map_builder
                    .resolve_visibility(module, &node.visibility);
                state
                    .def_map_builder
                    .alloc_local_enum_variants(module, local_def, data, visibility, file_id);
            }
            for namespace in namespaces.iter() {
                let binding = ScopeBinding::new(
                    DefId::Local(LocalDefRef {
                        origin: DefMapRef::Crate(*crate_ref),
                        local_def,
                    }),
                    *visibilities.get(namespace),
                    ScopeBindingProvenance::Direct,
                );
                state.base_scopes[module.0].insert_binding(&name, namespace, binding.clone());
                scopes
                    .module_scope_mut(*crate_ref, module)
                    .expect("checked compiler scope")
                    .insert_binding(&name, namespace, binding);
            }
            changed = true;
        }
        Ok(changed)
    }

    pub(super) fn prepare<E>(
        env: &E,
        state: &CrateState,
        item_tree: &ItemTreeDb,
        mut declarations: CompilerTypeDeclarations,
    ) -> anyhow::Result<Self>
    where
        E: CrateResolutionEnv<Error = rg_package_store::PackageStoreError>,
    {
        let (module, owner_item, origin_source) =
            Self::source_owner(state, item_tree, &declarations.path, declarations.kind)?;
        let map = state.def_map_builder.partial();
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
            // Adding a method to a broader source impl would erase its compiler-established bounds:
            // `impl<T: Gate> Post<T>` must not extend an unconditional `impl<T> Post<T>`.
            // Exact syntax equality is conservative; a different spelling keeps its own header.
            let extension = matching.iter().find(|(_, written)| {
                imported.generics == written.generics && imported.is_unsafe == written.is_unsafe
            });
            if let Some((source, _)) = extension {
                if !missing.is_empty() {
                    extensions.push((*source, missing));
                }
            } else if matching.is_empty() || !missing.is_empty() {
                ensure!(
                    imported.trait_ref.is_none() || matching.is_empty(),
                    "rustdoc trait impl applicability differs from its source impl"
                );
                // Keep written members on their original source impl. Only the missing declarations
                // need a generated impl, which retains its original generics and where predicates.
                let ItemKind::Impl(header) = &mut declarations.items[*impl_id].kind else {
                    unreachable!("validated impl kind");
                };
                header.items = missing;
                new_impls.push(*impl_id);
            }
        }
        // Referenced declarations must exist in this candidate's graph with the compiler's item
        // kind. An export-local ID or a coincidentally matching value name is insufficient.
        let mut needed = std::collections::HashSet::new();
        if let Some(nominal) = declarations.nominal {
            needed.insert(nominal);
        }
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

    /// Resolve the exact nominal source anchor before any supporting identity is allocated.
    fn source_owner<'a>(
        state: &'a CrateState,
        item_tree: &'a ItemTreeDb,
        path: &[String],
        kind: rg_item_tree::ItemTag,
    ) -> anyhow::Result<(ModuleId, &'a ItemNode, rg_item_tree::ItemTreeRef)> {
        ensure!(
            path.first()
                .is_some_and(|name| name == &state.crate_name.replace('-', "_")),
            "rustdoc crate identity does not match {}",
            state.crate_name
        );
        let (name, modules) = path[1..]
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
                    path.join("::")
                );
            };
            module = child;
        }
        let kind = LocalDefKind::from_item_tag(kind).context("invalid rustdoc nominal kind")?;
        let mut owners = ExpectedUnique::new();
        for owner in map.local_defs() {
            if owner.module == module && owner.kind == kind && owner.name.as_str() == name {
                owners.push(owner);
            }
        }
        let ExpectedUnique::One(owner) = owners else {
            anyhow::bail!(
                "rustdoc owner {} cannot be mapped uniquely to source",
                path.join("::")
            );
        };
        let (owner_item, origin_source) = Self::source_item(state, item_tree, owner.source)?;
        let origin_source = origin_source.context("rustdoc owner has no source provenance")?;
        Ok((module, owner_item, origin_source))
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
