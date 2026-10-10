//! Translate validated compiler signatures into the existing declaration pipeline.

mod supporting;

use anyhow::{Context as _, bail, ensure};
use rg_arena::Arena;
use rg_ir_model::{FileId, Mutability, Span};
use rg_item_tree::{
    CompilerTypeDeclarations, ConstExpr, ConstItem, ConstParamData, FunctionItem,
    FunctionQualifiers, GenericArg, GenericParams, ImplItem, ItemKind, ItemNode, ItemTag,
    ItemTreeId, LifetimeParamData, ParamItem, ParamKind, SelfParamKind, TraitBoundModifier,
    TypeAliasItem, TypeBound, TypeParamData, TypePath, TypePathAnchor, TypePathSegment, TypeRef,
    VisibilityLevel, WherePredicate,
};
use rg_text::Name;
use rustdoc_types as rd;

use crate::TypeApiView;

impl TypeApiView<'_> {
    /// Release the compiler schema at this boundary. Paths come from resolved IDs rather than
    /// the use-site spelling in rustdoc, so `other::Post` cannot capture a root `Post` signature.
    pub fn lower(&self) -> anyhow::Result<CompilerTypeDeclarations> {
        let mut items = Arena::new();
        let mut impls = Vec::new();
        let mut references = Vec::new();
        for implementation in &self.impls {
            let rd::ItemEnum::Impl(data) = &implementation.declaration.inner else {
                unreachable!("validated impl kind");
            };
            // This importer covers direct nominal impls. Reference and fundamental-wrapper impls
            // need their own applicability reconciliation; the inspection view still reports them.
            if !matches!(&data.for_, rd::Type::ResolvedPath(path) if path.id == self.declaration.id)
            {
                continue;
            }
            ensure!(
                !data.is_negative,
                "negative rustdoc impls cannot be imported"
            );
            let mut children = Vec::new();
            for item in &implementation.associated_items {
                let kind = match &item.inner {
                    rd::ItemEnum::Function(function) => {
                        // `fn filter(value: impl IntoVal)` repeats its opaque argument bounds
                        // in a synthetic generic parameter. The argument already carries those
                        // bounds; importing both would add an explicit, unused type parameter.
                        let mut generics = function.generics.clone();
                        let mut arguments = function
                            .sig
                            .inputs
                            .iter()
                            .filter_map(|(_, ty)| {
                                if let rd::Type::ImplTrait(bounds) = ty {
                                    Some(bounds)
                                } else {
                                    None
                                }
                            })
                            .collect::<Vec<_>>();
                        let mut explicit = Vec::new();
                        for parameter in generics.params {
                            if let rd::GenericParamDefKind::Type {
                                bounds,
                                default,
                                is_synthetic: true,
                            } = &parameter.kind
                            {
                                let matching =
                                    arguments.iter().position(|argument| *argument == bounds);
                                ensure!(
                                    default.is_none() && !bounds.is_empty() && matching.is_some(),
                                    "synthetic rustdoc parameter has no matching opaque argument"
                                );
                                arguments.remove(matching.expect("checked opaque argument"));
                                // Removed synthetic names must not escape into another part of
                                // the signature or a where clause as an ordinary generic type.
                                let mut pending = vec![
                                    serde_json::to_value(&function.sig)?,
                                    serde_json::to_value(&generics.where_predicates)?,
                                ];
                                while let Some(value) = pending.pop() {
                                    match value {
                                        serde_json::Value::Object(object) => {
                                            ensure!(
                                                object
                                                    .get("generic")
                                                    .and_then(serde_json::Value::as_str)
                                                    != Some(parameter.name.as_str()),
                                                "synthetic rustdoc parameter escapes its opaque argument"
                                            );
                                            pending.extend(object.into_values());
                                        }
                                        serde_json::Value::Array(array) => pending.extend(array),
                                        _ => {}
                                    }
                                }
                            } else {
                                explicit.push(parameter);
                            }
                        }
                        generics.params = explicit;
                        ItemKind::Function(FunctionItem {
                            generics: self.generics(&generics)?,
                            params: function
                                .sig
                                .inputs
                                .iter()
                                .map(|(name, ty)| {
                                    Ok(ParamItem {
                                        pat: name.clone(),
                                        ty: Some(self.ty(ty)?),
                                        kind: if name == "self" {
                                            ParamKind::SelfParam(SelfParamKind::Explicit)
                                        } else {
                                            ParamKind::Normal
                                        },
                                    })
                                })
                                .collect::<anyhow::Result<_>>()?,
                            ret_ty: function
                                .sig
                                .output
                                .as_ref()
                                .map(|ty| self.ty(ty))
                                .transpose()?,
                            qualifiers: FunctionQualifiers {
                                is_async: function.header.is_async,
                                is_const: function.header.is_const,
                                is_unsafe: function.header.is_unsafe,
                            },
                            has_body: false,
                            proc_macro: None,
                        })
                    }
                    rd::ItemEnum::AssocType {
                        generics,
                        bounds,
                        type_,
                        ..
                    } => ItemKind::TypeAlias(TypeAliasItem {
                        generics: self.generics(generics)?,
                        bounds: self.bounds(bounds)?,
                        aliased_ty: type_.as_ref().map(|ty| self.ty(ty)).transpose()?,
                    }),
                    rd::ItemEnum::AssocConst { type_, value, .. } => ItemKind::Const(ConstItem {
                        generics: GenericParams::default(),
                        ty: Some(self.ty(type_)?),
                        has_value: value.is_some(),
                    }),
                    _ => bail!("unsupported rustdoc associated declaration"),
                };
                let child = items.alloc(ItemNode::source(
                    kind,
                    item.name.as_ref().map(Name::new),
                    None,
                    Self::visibility(&item.visibility),
                    None,
                    Span { start: 0, end: 0 },
                    FileId(0),
                ));
                references.extend(self.references(item, child)?);
                children.push(child);
            }
            let header = ImplItem {
                generics: self.generics(&data.generics)?,
                trait_ref: data
                    .trait_
                    .as_ref()
                    .map(|path| self.path(path).map(TypeRef::Path))
                    .transpose()?,
                self_ty: self.ty(&data.for_)?,
                items: children,
                is_unsafe: data.is_unsafe,
            };
            let impl_id = items.alloc(ItemNode::source(
                ItemKind::Impl(header),
                None,
                None,
                VisibilityLevel::Private,
                None,
                Span { start: 0, end: 0 },
                FileId(0),
            ));
            references.extend(self.references(implementation.declaration, impl_id)?);
            impls.push(impl_id);
        }
        Ok(CompilerTypeDeclarations {
            path: self.path.to_vec(),
            kind: Self::item_tag(self.declaration.inner.item_kind())?,
            items,
            impls,
            supporting_item: None,
            origin: None,
            modules: Vec::new(),
            references,
        })
    }

    pub(super) fn references(
        &self,
        item: &rd::Item,
        lowered: ItemTreeId,
    ) -> anyhow::Result<Vec<(ItemTreeId, TypePath, ItemTag)>> {
        let value =
            serde_json::to_value(&item.inner).context("inspect rustdoc declaration references")?;
        crate::RustdocExport::path_ids(&value)?
            .into_iter()
            .map(|id| {
                let summary = self
                    .type_paths
                    .get(&id.0)
                    .context("rustdoc signature path missing during lowering")?;
                Ok((
                    lowered,
                    self.canonical_path(summary)?,
                    Self::item_tag(summary.kind)?,
                ))
            })
            .collect()
    }

    fn item_tag(kind: rd::ItemKind) -> anyhow::Result<ItemTag> {
        Ok(match kind {
            rd::ItemKind::Struct => ItemTag::Struct,
            rd::ItemKind::Enum => ItemTag::Enum,
            rd::ItemKind::Union => ItemTag::Union,
            rd::ItemKind::Trait => ItemTag::Trait,
            rd::ItemKind::TypeAlias => ItemTag::TypeAlias,
            _ => bail!("unsupported rustdoc signature path kind {kind:?}"),
        })
    }

    pub(super) fn visibility(visibility: &rd::Visibility) -> VisibilityLevel {
        match visibility {
            rd::Visibility::Public => VisibilityLevel::Public,
            rd::Visibility::Crate => VisibilityLevel::Crate,
            rd::Visibility::Default => VisibilityLevel::Private,
            rd::Visibility::Restricted { path, .. } => VisibilityLevel::Restricted(path.clone()),
        }
    }

    fn canonical_path(&self, summary: &rd::ItemSummary) -> anyhow::Result<TypePath> {
        let names = &summary.path;
        let local = summary.crate_id == self.declaration.crate_id;
        let mut path = Self::named_path(names.iter().map(String::as_str));
        let root = path
            .segments
            .first_mut()
            .context("empty rustdoc signature path")?;
        if local {
            root.name = Name::new("crate");
        } else {
            path.absolute = true;
        }
        // `crate::` belongs to the importing target. A binary and its library may share the
        // same crate name; looking up that spelling would redirect binary signatures to the library.
        path.resolved_crate = match (!local)
            .then(|| self.crate_roots.and_then(|roots| roots.get(&names[0])))
            .flatten()
        {
            Some(Some(crate_ref)) => Some(*crate_ref),
            Some(None) => bail!(
                "rustdoc defining crate {} is ambiguous in the dependency closure",
                names[0]
            ),
            None => None,
        };
        Ok(path)
    }

    fn named_path<'a>(names: impl IntoIterator<Item = &'a str>) -> TypePath {
        TypePath {
            source_span: Span { start: 0, end: 0 },
            absolute: false,
            resolved_crate: None,
            anchor: None,
            segments: names
                .into_iter()
                .map(|name| TypePathSegment {
                    name: Name::new(name),
                    args: Vec::new(),
                    span: Span { start: 0, end: 0 },
                })
                .collect(),
        }
    }

    fn path(&self, path: &rd::Path) -> anyhow::Result<TypePath> {
        let summary = self
            .type_paths
            .get(&path.id.0)
            .context("rustdoc signature path missing during lowering")?;
        let mut result = self.canonical_path(summary)?;
        result
            .segments
            .last_mut()
            .context("empty rustdoc signature path")?
            .args = self.args(path.args.as_deref())?;
        Ok(result)
    }

    fn args(&self, args: Option<&rd::GenericArgs>) -> anyhow::Result<Vec<GenericArg>> {
        let Some(args) = args else {
            return Ok(Vec::new());
        };
        match args {
            rd::GenericArgs::AngleBracketed { args, constraints } => {
                let mut result = args
                    .iter()
                    .map(|arg| {
                        Ok(match arg {
                            rd::GenericArg::Type(ty) => GenericArg::Type(self.ty(ty)?),
                            rd::GenericArg::Lifetime(name) => GenericArg::Lifetime(Name::new(name)),
                            rd::GenericArg::Const(value) => GenericArg::Const(ConstExpr::new(
                                value.expr.clone(),
                                Span { start: 0, end: 0 },
                            )),
                            rd::GenericArg::Infer => GenericArg::Type(TypeRef::Infer),
                        })
                    })
                    .collect::<anyhow::Result<Vec<_>>>()?;
                for constraint in constraints {
                    ensure!(
                        constraint.args.is_none(),
                        "generic associated rustdoc bindings cannot be imported"
                    );
                    let rd::AssocItemConstraintKind::Equality(rd::Term::Type(ty)) =
                        &constraint.binding
                    else {
                        bail!("unsupported rustdoc associated binding");
                    };
                    result.push(GenericArg::AssocType {
                        name: Name::new(&constraint.name),
                        name_span: Span { start: 0, end: 0 },
                        ty: Some(self.ty(ty)?),
                    });
                }
                Ok(result)
            }
            rd::GenericArgs::Parenthesized { inputs, output } => {
                Ok(vec![GenericArg::FnTraitArgs {
                    params: inputs
                        .iter()
                        .map(|ty| self.ty(ty))
                        .collect::<anyhow::Result<_>>()?,
                    ret: Box::new(
                        output
                            .as_ref()
                            .map(|ty| self.ty(ty))
                            .transpose()?
                            .unwrap_or(TypeRef::Unit),
                    ),
                }])
            }
            rd::GenericArgs::ReturnTypeNotation => {
                bail!("rustdoc return type notation cannot be imported")
            }
        }
    }

    fn ty(&self, ty: &rd::Type) -> anyhow::Result<TypeRef> {
        Ok(match ty {
            rd::Type::ResolvedPath(path) => TypeRef::Path(self.path(path)?),
            rd::Type::Generic(name) => TypeRef::Path(Self::named_path([name.as_str()])),
            rd::Type::Primitive(name) if name == "never" => TypeRef::Never,
            rd::Type::Primitive(name) => TypeRef::Path(Self::named_path([name.as_str()])),
            rd::Type::Tuple(types) if types.is_empty() => TypeRef::Unit,
            rd::Type::Tuple(types) => TypeRef::Tuple(
                types
                    .iter()
                    .map(|ty| self.ty(ty))
                    .collect::<anyhow::Result<_>>()?,
            ),
            rd::Type::BorrowedRef {
                lifetime,
                is_mutable,
                type_,
            } => TypeRef::Reference {
                lifetime: lifetime.as_ref().map(Name::new),
                mutability: Self::mutability(*is_mutable),
                inner: Box::new(self.ty(type_)?),
            },
            rd::Type::RawPointer { is_mutable, type_ } => TypeRef::RawPointer {
                mutability: Self::mutability(*is_mutable),
                inner: Box::new(self.ty(type_)?),
            },
            rd::Type::Slice(ty) => TypeRef::Slice(Box::new(self.ty(ty)?)),
            rd::Type::Array { type_, len } => TypeRef::Array {
                inner: Box::new(self.ty(type_)?),
                len: Some(ConstExpr::new(len.clone(), Span { start: 0, end: 0 })),
            },
            rd::Type::Infer => TypeRef::Infer,
            rd::Type::ImplTrait(bounds) => TypeRef::ImplTrait(self.bounds(bounds)?),
            rd::Type::DynTrait(data) => {
                let mut bounds = Vec::new();
                for bound in &data.traits {
                    ensure!(
                        bound.generic_params.is_empty(),
                        "higher-ranked rustdoc object bounds cannot be imported"
                    );
                    bounds.push(TypeBound::Trait {
                        ty: TypeRef::Path(self.path(&bound.trait_)?),
                        modifier: TraitBoundModifier::None,
                    });
                }
                if let Some(lifetime) = &data.lifetime {
                    bounds.push(TypeBound::Lifetime(Name::new(lifetime)));
                }
                TypeRef::DynTrait(bounds)
            }
            rd::Type::QualifiedPath {
                name,
                args,
                self_type,
                trait_,
            } => TypeRef::Path(TypePath {
                source_span: Span { start: 0, end: 0 },
                absolute: false,
                resolved_crate: None,
                anchor: Some(TypePathAnchor::from_parts(
                    self.ty(self_type)?,
                    trait_
                        .as_ref()
                        .map(|path| self.path(path).map(TypeRef::Path))
                        .transpose()?,
                )),
                segments: vec![TypePathSegment {
                    name: Name::new(name),
                    args: self.args(args.as_deref())?,
                    span: Span { start: 0, end: 0 },
                }],
            }),
            _ => bail!("unsupported rustdoc type {ty:?}"),
        })
    }

    fn mutability(mutable: bool) -> Mutability {
        if mutable {
            Mutability::Mutable
        } else {
            Mutability::Shared
        }
    }

    fn generics(&self, generics: &rd::Generics) -> anyhow::Result<GenericParams> {
        let mut result = GenericParams::default();
        for param in &generics.params {
            let name = Name::new(&param.name);
            match &param.kind {
                rd::GenericParamDefKind::Lifetime { outlives } => {
                    result.lifetimes.push(LifetimeParamData {
                        name,
                        bounds: outlives.iter().map(Name::new).collect(),
                    })
                }
                rd::GenericParamDefKind::Type {
                    bounds,
                    default,
                    is_synthetic,
                } => {
                    ensure!(
                        !is_synthetic,
                        "synthetic rustdoc generic parameters cannot be imported"
                    );
                    result.push_type(TypeParamData {
                        name,
                        bounds: self.bounds(bounds)?,
                        default: default.as_ref().map(|ty| self.ty(ty)).transpose()?,
                    });
                }
                rd::GenericParamDefKind::Const { type_, default } => {
                    result.push_const(ConstParamData {
                        name,
                        ty: Some(self.ty(type_)?),
                        default: default
                            .as_ref()
                            .map(|value| ConstExpr::new(value.clone(), Span { start: 0, end: 0 })),
                    })
                }
            }
        }
        for predicate in &generics.where_predicates {
            result.where_predicates.push(match predicate {
                rd::WherePredicate::BoundPredicate {
                    type_,
                    bounds,
                    generic_params,
                } => {
                    ensure!(
                        generic_params.is_empty(),
                        "higher-ranked rustdoc predicates cannot be imported"
                    );
                    WherePredicate::Type {
                        ty: self.ty(type_)?,
                        bounds: self.bounds(bounds)?,
                    }
                }
                rd::WherePredicate::LifetimePredicate { lifetime, outlives } => {
                    WherePredicate::Lifetime {
                        lifetime: Name::new(lifetime),
                        bounds: outlives.iter().map(Name::new).collect(),
                    }
                }
                _ => bail!("unsupported rustdoc where predicate"),
            });
        }
        Ok(result)
    }

    fn bounds(&self, bounds: &[rd::GenericBound]) -> anyhow::Result<Vec<TypeBound>> {
        bounds
            .iter()
            .map(|bound| {
                Ok(match bound {
                    rd::GenericBound::TraitBound {
                        trait_,
                        generic_params,
                        modifier,
                    } => {
                        ensure!(
                            generic_params.is_empty(),
                            "higher-ranked rustdoc bounds cannot be imported"
                        );
                        TypeBound::Trait {
                            ty: TypeRef::Path(self.path(trait_)?),
                            modifier: match modifier {
                                rd::TraitBoundModifier::None => TraitBoundModifier::None,
                                rd::TraitBoundModifier::Maybe => TraitBoundModifier::Maybe,
                                rd::TraitBoundModifier::MaybeConst => {
                                    bail!("const rustdoc trait bounds cannot be imported")
                                }
                            },
                        }
                    }
                    rd::GenericBound::Outlives(name) => TypeBound::Lifetime(Name::new(name)),
                    _ => bail!("unsupported rustdoc generic bound"),
                })
            })
            .collect()
    }
}
