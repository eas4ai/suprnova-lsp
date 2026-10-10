//! Keep the field shapes of supporting compiler declarations without their export-local IDs.

use anyhow::{Context as _, ensure};
use rg_ir_model::{FieldKey, Span};
use rg_item_tree::{
    EnumItem, EnumVariantItem, FieldItem, FieldList, ItemKind, StructItem, TypeAliasItem, UnionItem,
};
use rg_text::Name;
use rustdoc_types as rd;

use crate::TypeApiView;

impl TypeApiView<'_> {
    pub(crate) fn supporting_item(&self) -> anyhow::Result<ItemKind> {
        Ok(match &self.declaration.inner {
            rd::ItemEnum::Struct(data) => ItemKind::Struct(StructItem {
                generics: self.generics(&data.generics)?,
                fields: match &data.kind {
                    rd::StructKind::Unit => FieldList::Unit,
                    rd::StructKind::Plain {
                        fields,
                        has_stripped_fields,
                    } => {
                        ensure!(
                            !has_stripped_fields,
                            "supporting rustdoc struct omits fields"
                        );
                        FieldList::Named(self.fields(fields, false)?)
                    }
                    rd::StructKind::Tuple(fields) => {
                        let fields = fields
                            .iter()
                            .map(|field| field.context("supporting rustdoc tuple omits a field"))
                            .collect::<anyhow::Result<Vec<_>>>()?;
                        FieldList::Tuple(self.fields(&fields, true)?)
                    }
                },
                derives: Default::default(),
            }),
            rd::ItemEnum::Enum(data) => {
                ensure!(
                    !data.has_stripped_variants,
                    "supporting rustdoc enum omits variants"
                );
                let mut variants = Vec::new();
                for id in &data.variants {
                    let variant = self
                        .members
                        .iter()
                        .find(|item| item.id == *id)
                        .context("supporting rustdoc variant missing")?;
                    let rd::ItemEnum::Variant(data) = &variant.inner else {
                        anyhow::bail!("supporting rustdoc variant has the wrong kind")
                    };
                    let fields = match &data.kind {
                        rd::VariantKind::Plain => FieldList::Unit,
                        rd::VariantKind::Tuple(fields) => {
                            let fields = fields
                                .iter()
                                .map(|field| {
                                    field.context("supporting rustdoc variant omits a field")
                                })
                                .collect::<anyhow::Result<Vec<_>>>()?;
                            FieldList::Tuple(self.fields(&fields, true)?)
                        }
                        rd::VariantKind::Struct {
                            fields,
                            has_stripped_fields,
                        } => {
                            ensure!(
                                !has_stripped_fields,
                                "supporting rustdoc variant omits fields"
                            );
                            FieldList::Named(self.fields(fields, false)?)
                        }
                    };
                    variants.push(EnumVariantItem {
                        name: Name::new(
                            variant
                                .name
                                .as_deref()
                                .context("supporting rustdoc variant has no name")?,
                        ),
                        span: Span { start: 0, end: 0 },
                        name_span: Span { start: 0, end: 0 },
                        docs: None,
                        user_facing_attrs: Default::default(),
                        fields,
                    });
                }
                ItemKind::Enum(EnumItem {
                    generics: self.generics(&data.generics)?,
                    variants,
                    derives: Default::default(),
                })
            }
            rd::ItemEnum::Union(data) => {
                ensure!(
                    !data.has_stripped_fields,
                    "supporting rustdoc union omits fields"
                );
                ItemKind::Union(UnionItem {
                    generics: self.generics(&data.generics)?,
                    fields: self.fields(&data.fields, false)?,
                })
            }
            rd::ItemEnum::TypeAlias(data) => ItemKind::TypeAlias(TypeAliasItem {
                generics: self.generics(&data.generics)?,
                bounds: Vec::new(),
                aliased_ty: Some(self.ty(&data.type_)?),
            }),
            _ => anyhow::bail!("unsupported supporting rustdoc declaration"),
        })
    }

    // The same export field records serve named, tuple, and enum-variant shapes. Keep their
    // declaration order; sorting IDs would change tuple positions and constructor arguments.
    fn fields(&self, ids: &[rd::Id], tuple: bool) -> anyhow::Result<Vec<FieldItem>> {
        ids.iter()
            .enumerate()
            .map(|(index, id)| {
                let field = self
                    .members
                    .iter()
                    .find(|item| item.id == *id)
                    .context("supporting rustdoc field missing")?;
                let rd::ItemEnum::StructField(ty) = &field.inner else {
                    anyhow::bail!("supporting rustdoc field has the wrong kind")
                };
                Ok(FieldItem {
                    key: Some(if tuple {
                        FieldKey::Tuple(index)
                    } else {
                        FieldKey::Named(Name::new(
                            field
                                .name
                                .as_deref()
                                .context("supporting rustdoc field has no name")?,
                        ))
                    }),
                    visibility: Self::visibility(&field.visibility),
                    ty: self.ty(ty)?,
                    span: Span { start: 0, end: 0 },
                    docs: None,
                })
            })
            .collect()
    }
}
