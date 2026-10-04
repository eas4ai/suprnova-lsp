//! Read compiler-produced API declarations at a transient import boundary.
//!
//! Rustdoc identifiers belong to one export. Consumers must resolve them before lowering into
//! project identities, and release this export before keeping resident analysis state.

mod lowering;

use std::{
    collections::{BTreeMap, HashSet},
    io::Read,
};

use anyhow::{Context as _, bail, ensure};
use rg_std::ExpectedUnique;
use rustdoc_types::{ItemEnum, ItemKind, StructKind, Type, VariantKind, Visibility};
use serde::{Deserialize, Serialize};

const MAX_EXPORT_BYTES: u64 = 256 * 1024 * 1024;

#[derive(Serialize)]
pub struct TypeApiView<'a> {
    pub format_version: u32,
    pub target: &'a str,
    pub path: &'a [String],
    pub declaration: &'a rustdoc_types::Item,
    pub members: Vec<&'a rustdoc_types::Item>,
    pub impls: Vec<ImplApiView<'a>>,
    pub type_paths: BTreeMap<u32, &'a rustdoc_types::ItemSummary>,
    pub excluded_blanket_impls: usize,
    pub limitations: [&'static str; 3],
    #[serde(skip)]
    crate_roots: Option<&'a BTreeMap<String, Option<rg_ir_model::CrateRef>>>,
}

#[derive(Serialize)]
pub struct ImplApiView<'a> {
    pub declaration: &'a rustdoc_types::Item,
    pub associated_items: Vec<&'a rustdoc_types::Item>,
}

pub struct RustdocExport {
    data: rustdoc_types::Crate,
}

impl RustdocExport {
    /// Lower the selected API and the concrete declarations it references in child modules.
    /// Source owners in the selected module still have to exist; this closure supplies generated
    /// storage types such as `user::Entity`, not replacements for missing source declarations.
    /// Crate roots come from the resolved workspace graph; an explicit `None` marks an ambiguous name.
    pub fn lower_type<'a>(
        &'a self,
        path: &str,
        crate_roots: &'a BTreeMap<String, Option<rg_ir_model::CrateRef>>,
    ) -> anyhow::Result<Vec<rg_item_tree::CompilerTypeDeclarations>> {
        let mut primary = self.type_api(path)?;
        primary.crate_roots = Some(crate_roots);
        let origin = primary.path.to_vec();
        let mut seen = HashSet::from([primary.declaration.id]);
        let mut pending = vec![primary];
        let mut declarations = Vec::new();
        while let Some(view) = pending.pop() {
            for (id, summary) in &view.type_paths {
                if summary.crate_id == view.declaration.crate_id
                    && matches!(
                        summary.kind,
                        ItemKind::Struct | ItemKind::Enum | ItemKind::Union | ItemKind::TypeAlias
                    )
                    && summary.path.starts_with(&origin[..origin.len() - 1])
                    && summary.path.len() > origin.len()
                    && seen.insert(rustdoc_types::Id(*id))
                {
                    let mut related = self.type_api(&summary.path.join("::"))?;
                    related.crate_roots = Some(crate_roots);
                    pending.push(related);
                }
            }
            let mut lowered = view.lower()?;
            if view.path != origin {
                let node = rg_item_tree::ItemNode::source(
                    view.supporting_item()?,
                    view.declaration.name.as_ref().map(rg_text::Name::new),
                    None,
                    TypeApiView::visibility(&view.declaration.visibility),
                    None,
                    rg_ir_model::Span { start: 0, end: 0 },
                    rg_ir_model::FileId(0),
                );
                let supporting_item = lowered.items.alloc(node);
                lowered
                    .references
                    .extend(view.references(view.declaration, supporting_item)?);
                for member in &view.members {
                    lowered
                        .references
                        .extend(view.references(member, supporting_item)?);
                }
                lowered.supporting_item = Some(supporting_item);
                lowered.origin = Some(origin.clone());
                // A path summary alone does not establish a generated module. Validate the actual
                // module declarations and child membership, walking from the type to the anchor.
                let mut child = view.declaration.id;
                for depth in (origin.len()..view.path.len()).rev() {
                    let mut modules = ExpectedUnique::new();
                    for (id, summary) in &self.data.paths {
                        if summary.crate_id == view.declaration.crate_id
                            && summary.kind == ItemKind::Module
                            && summary.path == view.path[..depth]
                        {
                            modules.push(*id);
                        }
                    }
                    let ExpectedUnique::One(id) = modules else {
                        bail!("rustdoc supporting parent module is missing or ambiguous")
                    };
                    let item = self
                        .data
                        .index
                        .get(&id)
                        .context("rustdoc supporting module declaration missing")?;
                    ensure!(
                        item.crate_id == view.declaration.crate_id,
                        "rustdoc supporting module belongs to another crate"
                    );
                    let ItemEnum::Module(module) = &item.inner else {
                        bail!("rustdoc supporting module has the wrong kind")
                    };
                    ensure!(
                        module.items.contains(&child),
                        "rustdoc supporting declaration is not a child of its module"
                    );
                    lowered.modules.push((
                        view.path[..depth].to_vec(),
                        TypeApiView::visibility(&item.visibility),
                    ));
                    child = id;
                }
                lowered.modules.reverse();
            }
            declarations.push(lowered);
        }
        // Keep the source anchor first and make the remaining candidate order deterministic.
        declarations[1..].sort_by(|left, right| left.path.cmp(&right.path));
        Ok(declarations)
    }

    /// Check the format before decoding declarations, so a newer schema gets a useful error.
    pub fn read(reader: impl Read) -> anyhow::Result<Self> {
        let mut bytes = Vec::new();
        reader
            .take(MAX_EXPORT_BYTES + 1)
            .read_to_end(&mut bytes)
            .context("read rustdoc JSON export")?;
        ensure!(
            bytes.len() as u64 <= MAX_EXPORT_BYTES,
            "rustdoc JSON export exceeds the {MAX_EXPORT_BYTES}-byte import limit"
        );

        #[derive(Deserialize)]
        struct Header {
            format_version: u32,
            includes_private: bool,
        }
        let header: Header = serde_json::from_slice(&bytes).context("read rustdoc JSON header")?;
        ensure!(
            header.format_version == rustdoc_types::FORMAT_VERSION,
            "unsupported rustdoc JSON format {}; expected {}",
            header.format_version,
            rustdoc_types::FORMAT_VERSION
        );
        ensure!(
            header.includes_private,
            "rustdoc export omits private items; regenerate with --document-private-items and --document-hidden-items"
        );
        let data: rustdoc_types::Crate =
            serde_json::from_slice(&bytes).context("decode rustdoc JSON declarations")?;

        // Index keys and item IDs must agree before an ID can be used as an owner reference.
        for (id, item) in &data.index {
            ensure!(
                id == &item.id,
                "rustdoc index key {id:?} disagrees with its item ID"
            );
        }
        let root = data
            .index
            .get(&data.root)
            .context("rustdoc root item is missing")?;
        ensure!(
            matches!(&root.inner, ItemEnum::Module(module) if module.is_crate),
            "rustdoc root must be a crate module"
        );
        let name = root
            .name
            .as_deref()
            .context("rustdoc root has no crate name")?;
        let path = data
            .paths
            .get(&data.root)
            .context("rustdoc root path is missing")?;
        ensure!(
            path.crate_id == root.crate_id && path.path == [name],
            "rustdoc root path disagrees with its crate identity"
        );
        Ok(Self { data })
    }

    /// Select one nominal type's explicit impls without treating a missing span as macro evidence.
    pub fn type_api(&self, path: &str) -> anyhow::Result<TypeApiView<'_>> {
        let root = &self.data.index[&self.data.root];
        let mut selected = ExpectedUnique::new();
        for (id, summary) in &self.data.paths {
            if summary.crate_id == root.crate_id
                && matches!(
                    summary.kind,
                    ItemKind::Struct | ItemKind::Enum | ItemKind::Union | ItemKind::TypeAlias
                )
                && summary.path.join("::") == path
            {
                selected.push((*id, summary));
            }
        }
        let (id, summary) = match selected {
            ExpectedUnique::One(value) => value,
            ExpectedUnique::Empty => {
                bail!("no local rustdoc type has the fully qualified path {path}")
            }
            ExpectedUnique::Ambiguous => bail!("rustdoc path {path} identifies more than one item"),
        };
        let declaration = self
            .data
            .index
            .get(&id)
            .context("rustdoc type declaration is missing")?;
        ensure!(
            declaration.crate_id == root.crate_id,
            "rustdoc type belongs to another crate"
        );
        ensure!(
            declaration.inner.item_kind() == summary.kind,
            "rustdoc type path has the wrong item kind"
        );
        let impl_ids: &[rustdoc_types::Id] = match &declaration.inner {
            ItemEnum::Struct(item) => &item.impls,
            ItemEnum::Enum(item) => &item.impls,
            ItemEnum::Union(item) => &item.impls,
            ItemEnum::TypeAlias(_) => &[],
            _ => bail!("rustdoc item {path} is not a struct, enum, union, or type alias"),
        };

        let mut type_paths = BTreeMap::new();
        self.collect_item_paths(declaration, &mut type_paths)?;
        // Fields and enum variants have their own items. Check their references too, so the
        // selected declaration never reports a field whose signature is missing from the export.
        let mut pending = Self::member_ids(&declaration.inner);
        let mut members = BTreeMap::new();
        while let Some((member_id, expected_kind)) = pending.pop() {
            ensure!(
                !members.contains_key(&member_id.0),
                "rustdoc member {member_id:?} is repeated or cyclic"
            );
            let member = self
                .data
                .index
                .get(&member_id)
                .context("rustdoc type member is missing")?;
            ensure!(
                member.crate_id == root.crate_id,
                "rustdoc type member belongs to another crate"
            );
            ensure!(
                member.inner.item_kind() == expected_kind,
                "rustdoc member has the wrong item kind"
            );
            self.collect_item_paths(member, &mut type_paths)?;
            pending.extend(Self::member_ids(&member.inner));
            members.insert(member_id.0, member);
        }

        let mut impls = Vec::new();
        let mut excluded_blanket_impls = 0;
        for impl_id in impl_ids {
            let implementation = self
                .data
                .index
                .get(impl_id)
                .context("rustdoc impl item is missing")?;
            let ItemEnum::Impl(data) = &implementation.inner else {
                bail!("rustdoc impl reference {impl_id:?} points to another item kind");
            };
            // Attached blanket impls include instantiated dependency declarations whose crate_id
            // looks local. Their original ownership needs reconciliation before engine ingestion.
            if data.blanket_impl.is_some() {
                excluded_blanket_impls += 1;
                continue;
            }
            if data.is_synthetic || implementation.crate_id != root.crate_id {
                continue;
            }
            // Rustdoc attaches impls for `&Post` and `Box<Post>` to Post. It also attaches
            // `From<Post> for Storage` because Post occurs in the trait argument. Neither
            // attachment changes the actual receiver retained in the declaration below.
            let mut owner = &data.for_;
            loop {
                match owner {
                    Type::ResolvedPath(path) if path.id == id => break,
                    Type::BorrowedRef { type_, .. } => owner = type_,
                    Type::ResolvedPath(wrapper)
                        if self
                            .data
                            .paths
                            .get(&wrapper.id)
                            .is_some_and(|summary| summary.path == ["alloc", "boxed", "Box"]) =>
                    {
                        let Some(rustdoc_types::GenericArgs::AngleBracketed { args, .. }) =
                            wrapper.args.as_deref()
                        else {
                            bail!("rustdoc Box impl has no type argument");
                        };
                        let Some(rustdoc_types::GenericArg::Type(inner)) = args.first() else {
                            bail!("rustdoc Box impl has an invalid type argument");
                        };
                        owner = inner;
                    }
                    _ => break,
                }
            }
            let trait_arguments = data
                .trait_
                .as_ref()
                .and_then(|trait_| trait_.args.as_deref())
                .map(serde_json::to_value)
                .transpose()
                .context("inspect rustdoc attachment arguments")?;
            let argument_ids = trait_arguments
                .as_ref()
                .map(Self::path_ids)
                .transpose()?
                .unwrap_or_default();
            ensure!(
                matches!(owner, Type::ResolvedPath(owner) if owner.id == id)
                    || argument_ids.contains(&id),
                "rustdoc impl {impl_id:?} does not belong to {path}"
            );
            let mut associated_items = Vec::new();
            for item_id in &data.items {
                let item = self
                    .data
                    .index
                    .get(item_id)
                    .context("rustdoc associated item is missing")?;
                ensure!(
                    item.crate_id == root.crate_id,
                    "rustdoc associated item {item_id:?} belongs to another crate"
                );
                ensure!(
                    matches!(
                        item.inner,
                        ItemEnum::Function(_)
                            | ItemEnum::AssocType { .. }
                            | ItemEnum::AssocConst { .. }
                    ),
                    "rustdoc associated item {item_id:?} has an unsupported kind"
                );
                self.collect_item_paths(item, &mut type_paths)?;
                associated_items.push(item);
            }
            self.collect_item_paths(implementation, &mut type_paths)?;
            // IDs belong to this export, but sorting makes repeated inspection reproducible.
            associated_items.sort_by_key(|item| item.id.0);
            impls.push(ImplApiView {
                declaration: implementation,
                associated_items,
            });
        }
        impls.sort_by_key(|implementation| implementation.declaration.id.0);
        Ok(TypeApiView {
            format_version: self.data.format_version,
            target: &self.data.target.triple,
            path: &summary.path,
            declaration,
            members: members.into_values().collect(),
            impls,
            type_paths,
            excluded_blanket_impls,
            crate_roots: None,
            limitations: [
                "Hidden-item coverage is unknown: rustdoc JSON has no coverage flag. Export with --document-hidden-items.",
                "Blanket and synthetic impls are excluded; this report does not establish complete trait applicability.",
                "IDs are local to this export. Function bodies and reference locations are not indexed.",
            ],
        })
    }

    fn member_ids(item: &ItemEnum) -> Vec<(rustdoc_types::Id, ItemKind)> {
        let expected_kind = if matches!(item, ItemEnum::Enum(_)) {
            ItemKind::Variant
        } else {
            ItemKind::StructField
        };
        let ids = match item {
            ItemEnum::Struct(item) => match &item.kind {
                StructKind::Unit => Vec::new(),
                StructKind::Tuple(fields) => fields.iter().flatten().copied().collect(),
                StructKind::Plain { fields, .. } => fields.clone(),
            },
            ItemEnum::Union(item) => item.fields.clone(),
            ItemEnum::Enum(item) => item.variants.clone(),
            ItemEnum::Variant(item) => match &item.kind {
                VariantKind::Plain => Vec::new(),
                VariantKind::Tuple(fields) => fields.iter().flatten().copied().collect(),
                VariantKind::Struct { fields, .. } => fields.clone(),
            },
            _ => Vec::new(),
        };
        ids.into_iter().map(|id| (id, expected_kind)).collect()
    }

    /// Validate retained signatures and module-restricted visibility before reporting an API.
    fn collect_item_paths<'a>(
        &'a self,
        item: &rustdoc_types::Item,
        paths: &mut BTreeMap<u32, &'a rustdoc_types::ItemSummary>,
    ) -> anyhow::Result<()> {
        if let Visibility::Restricted { parent, .. } = &item.visibility {
            let module = self
                .data
                .index
                .get(parent)
                .context("rustdoc visibility parent is missing")?;
            ensure!(
                module.crate_id == item.crate_id && matches!(module.inner, ItemEnum::Module(_)),
                "rustdoc visibility parent is not a local module"
            );
        }
        // The schema has already been decoded into typed declarations. Walking their canonical
        // serialized form covers paths nested inside generic arguments, bounds, and projections.
        // Only rustdoc_types::Path has this `path` + `id` + `args` shape in the pinned schema.
        let value = serde_json::to_value(&item.inner).context("inspect rustdoc signature paths")?;
        self.collect_paths(&value, paths)
    }

    fn collect_paths<'a>(
        &'a self,
        value: &serde_json::Value,
        paths: &mut BTreeMap<u32, &'a rustdoc_types::ItemSummary>,
    ) -> anyhow::Result<()> {
        for id in Self::path_ids(value)? {
            let summary = self.resolve_path(id)?;
            if summary.crate_id == self.data.index[&self.data.root].crate_id {
                let declaration = self
                    .data
                    .index
                    .get(&id)
                    .context("rustdoc local signature declaration is missing")?;
                ensure!(
                    declaration.crate_id == summary.crate_id
                        && declaration.inner.item_kind() == summary.kind,
                    "rustdoc signature path disagrees with its declaration"
                );
            }
            paths.insert(id.0, summary);
        }
        Ok(())
    }

    // The pinned schema's only `path` + `id` + `args` object is rustdoc_types::Path. Share this
    // traversal between export validation and per-declaration reference tracking during lowering.
    pub(crate) fn path_ids(value: &serde_json::Value) -> anyhow::Result<Vec<rustdoc_types::Id>> {
        let mut pending = vec![value];
        let mut ids = Vec::new();
        while let Some(value) = pending.pop() {
            match value {
                serde_json::Value::Object(object) => {
                    if object.get("path").is_some_and(serde_json::Value::is_string)
                        && object.contains_key("args")
                    {
                        ids.push(rustdoc_types::Id(serde_json::from_value(
                            object["id"].clone(),
                        )?));
                    }
                    pending.extend(object.values());
                }
                serde_json::Value::Array(array) => pending.extend(array),
                _ => {}
            }
        }
        Ok(ids)
    }

    /// Resolve export-local type references before translating them into project identities.
    pub fn resolve_path(
        &self,
        id: rustdoc_types::Id,
    ) -> anyhow::Result<&rustdoc_types::ItemSummary> {
        self.data
            .paths
            .get(&id)
            .with_context(|| format!("rustdoc type path {id:?} is missing"))
    }
}

#[cfg(test)]
mod tests;
