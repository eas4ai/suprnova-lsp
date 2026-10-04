//! Construction-only declarations whose identities must be reconciled with source.

use rg_arena::Arena;
use rg_std::MemorySize;

use crate::{ItemNode, ItemTag, ItemTreeId, TypePath, VisibilityLevel};

/// One nominal owner's compiler-derived impls, without compiler-local item IDs.
#[derive(Debug, Clone, MemorySize)]
pub struct CompilerTypeDeclarations {
    pub path: Vec<String>,
    pub kind: ItemTag,
    pub items: Arena<ItemTreeId, ItemNode>,
    pub impls: Vec<ItemTreeId>,
    /// Supporting nominal declarations may introduce compiler-established child modules.
    /// The selected source owner remains mandatory and is the provenance for those additions.
    pub nominal: Option<ItemTreeId>,
    pub origin: Option<Vec<String>>,
    pub modules: Vec<(Vec<String>, VisibilityLevel)>,
    /// Canonical signature paths and their declaration kinds, checked against the source graph.
    pub references: Vec<(ItemTreeId, TypePath, ItemTag)>,
}
