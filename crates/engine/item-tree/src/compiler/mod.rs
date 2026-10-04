//! Construction-only declarations whose identities must be reconciled with source.

use rg_arena::Arena;
use rg_std::MemorySize;

use crate::{ItemNode, ItemTag, ItemTreeId, TypePath};

/// One nominal owner's compiler-derived impls, without compiler-local item IDs.
#[derive(Debug, Clone, MemorySize)]
pub struct CompilerTypeDeclarations {
    pub path: Vec<String>,
    pub kind: ItemTag,
    pub items: Arena<ItemTreeId, ItemNode>,
    pub impls: Vec<ItemTreeId>,
    /// Canonical signature paths and their declaration kinds, checked against the source graph.
    pub references: Vec<(ItemTreeId, TypePath, ItemTag)>,
}
