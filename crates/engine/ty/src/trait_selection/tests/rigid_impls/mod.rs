use std::{collections::HashMap, convert::Infallible};

use rg_ir_model::{CrateRef, DefMapRef, FunctionId, FunctionRef, ImplId, ImplRef, Mutability};
use rg_semantic_ir::{
    CrateItemQuery, ItemLookupIndex, ItemLookupIndexSource, ItemLookupQuery, ItemStore,
    ItemStoreSource, TraitImplSelfHead,
};
use rustc_type_ir::inherent::Ty as _;

use super::utils::TraitSelectionFixture;
use crate::{AdtTy, PrimitiveTy, Ty, TyContext, lookup::TraitImplFilter, solver};

// The declarative fixture defaults every impl to the fallback lane. Supply explicit heads to
// exercise the same saved declaration index that production solver discovery reads.
struct IndexedFixture {
    fixture: TraitSelectionFixture,
    index: ItemLookupIndex,
}

impl IndexedFixture {
    fn new(source: &str, heads: &[(usize, TraitImplSelfHead)]) -> Self {
        let fixture = TraitSelectionFixture::new(source);
        let heads = heads
            .iter()
            .map(|&(id, head)| {
                (
                    ImplRef {
                        origin: DefMapRef::Crate(fixture.target),
                        id: ImplId(id),
                    },
                    head,
                )
            })
            .collect::<HashMap<_, _>>();
        let index = ItemLookupIndex::build_from_store(&fixture.store, &heads);
        Self { fixture, index }
    }

    fn lookup(&self, cancellation: &rg_std::CancellationToken) -> ItemLookupQuery<'_> {
        ItemLookupQuery::build_from(
            &CrateItemQuery::new(&self.fixture, self, self.fixture.target),
            cancellation,
        )
        .expect("indexed fixture lookup loads")
    }
}

impl<'a> ItemStoreSource<'a> for &'a IndexedFixture {
    type Error = Infallible;

    fn item_store_for_origin(
        &self,
        origin: DefMapRef,
    ) -> Result<Option<&'a ItemStore>, Self::Error> {
        (&self.fixture).item_store_for_origin(origin)
    }

    fn included_stores(&self) -> Result<Vec<&'a ItemStore>, Self::Error> {
        (&self.fixture).included_stores()
    }
}

impl<'a> ItemLookupIndexSource<'a> for &'a IndexedFixture {
    fn item_lookup_index(
        &self,
        crate_ref: CrateRef,
    ) -> Result<Option<&'a ItemLookupIndex>, Self::Error> {
        Ok((crate_ref == self.fixture.target).then_some(&self.index))
    }
}

#[test]
fn rigid_parameter_discovery_excludes_direct_impls_but_keeps_fallbacks() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Marker
          trait#1 Seed
        structs
          struct#0 User
        impls
          impl#0 impl Marker for User
          impl#1 impl Marker for &User [resolved self: empty]
          impl#2 impl<T> Marker for T [resolved self: empty]
          impl#3 impl Marker for <User as Seed>::Item [resolved self: empty]
          impl#4 impl Marker for Missing [resolved self: empty]
        type aliases
          type#0 trait#1::Item
        functions
          fn#0 inspect<T> -> T
        "#,
        &[
            (0, TraitImplSelfHead::Adt(super::utils::type_def(0))),
            (1, TraitImplSelfHead::Reference(Mutability::Shared)),
        ],
    );
    let cancellation = rg_std::CancellationToken::new();
    let context = TyContext::new(
        &fixture.fixture,
        &fixture,
        fixture.lookup(&cancellation),
        fixture.fixture.target,
        cancellation,
    );
    solver::SemanticDeclarations::new(&context, context.item_paths())
        .with_solver(|solver| {
            let cx = solver.interner();
            let owner = solver::DefId::Function(FunctionRef {
                origin: DefMapRef::Crate(fixture.fixture.target),
                id: FunctionId(0),
            });
            let source = *cx.params(owner).first().expect("inspect declares T");
            let receiver = solver::Ty::new_param(cx, solver::Param { index: 0, source });
            let marker = fixture.fixture.trait_ref_by_name("Marker").unwrap();
            let candidates = TraitImplFilter::from(receiver)
                .candidates(&context, marker)
                .expect("rigid discovery remains available");
            let ids = candidates
                .iter()
                .map(|candidate| candidate.impl_ref.id)
                .collect::<Vec<_>>();
            assert_eq!(
                ids,
                [ImplId(2), ImplId(3), ImplId(4)],
                "rigid T cannot acquire User or &User; blanket, alias and unresolved headers remain"
            );
        })
        .expect("fixture declarations load");
}

#[test]
fn live_variables_and_aliases_keep_concrete_impl_discovery() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Marker
          trait#1 Source
        structs
          struct#0 User
        impls
          impl#0 impl Marker for User
          impl#1 impl Marker for &User [resolved self: empty]
          impl#2 impl Source for User
        type aliases
          type#0 trait#1::Item
          type#1 impl#2::Item = User
        "#,
        &[
            (0, TraitImplSelfHead::Adt(super::utils::type_def(0))),
            (1, TraitImplSelfHead::Reference(Mutability::Shared)),
            (2, TraitImplSelfHead::Adt(super::utils::type_def(0))),
        ],
    );
    let cancellation = rg_std::CancellationToken::new();
    let context = TyContext::new(
        &fixture.fixture,
        &fixture,
        fixture.lookup(&cancellation),
        fixture.fixture.target,
        cancellation,
    );
    solver::SemanticDeclarations::new(&context, context.item_paths())
        .with_solver(|solver| {
            let table = solver::InferenceTable::new(solver, Default::default());
            let cx = table.interner();
            let user = Ty::adt(AdtTy {
                def: fixture.fixture.type_ref_by_name("User").unwrap(),
                args: Default::default(),
            });
            let receiver = cx.lower_ty(&user, &[]);
            let source = fixture.fixture.trait_ref_by_name("Source").unwrap();
            let alias = cx.projection(solver::ProjectionTy {
                associated_ty: fixture
                    .fixture
                    .associated_ty_by_name(source, "Item")
                    .unwrap(),
                args: solver::List::new(cx, &[receiver.into()]),
            });
            let variable = table.new_type_var();
            let marker = fixture.fixture.trait_ref_by_name("Marker").unwrap();
            for receiver in [variable, alias] {
                let ids = TraitImplFilter::from(receiver)
                    .candidates(&context, marker)
                    .unwrap()
                    .iter()
                    .map(|candidate| candidate.impl_ref.id)
                    .collect::<Vec<_>>();
                assert_eq!(ids, [ImplId(0), ImplId(1)]);
            }

            // Projection receivers can still normalize to a concrete type. A live variable can
            // acquire that type from later evidence; neither is a rigid parameter.
            let alias_goal = solver::TraitApplication {
                def: marker,
                args: solver::List::new(cx, &[alias.into()]),
            };
            assert_eq!(
                table.prove([alias_goal.clause(cx)]),
                solver::Outcome::Proven
            );
            table
                .try_unify(variable, receiver)
                .expect("later User evidence");
            let variable_goal = solver::TraitApplication {
                def: marker,
                args: solver::List::new(cx, &[variable.into()]),
            };
            assert_eq!(
                table.prove([variable_goal.clause(cx)]),
                solver::Outcome::Proven
            );
            assert_eq!(table.finalize(variable), user);
        })
        .expect("fixture declarations load");
}

#[test]
fn rigid_supertrait_bound_and_blanket_projection_preserve_pending_obligations() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Marker
          trait#1 Derived: Marker
          trait#2 HasItem
        structs
          struct#0 User
        impls
          impl#0 impl Marker for User
          impl#1 impl<T> HasItem for T [resolved self: empty]
        type aliases
          type#0 trait#2::Item
          type#1 impl#1::Item = bool
        functions
          fn#0 inspect<T: Derived> -> T
        "#,
        &[(0, TraitImplSelfHead::Adt(super::utils::type_def(0)))],
    );
    let cancellation = rg_std::CancellationToken::new();
    let context = TyContext::new(
        &fixture.fixture,
        &fixture,
        fixture.lookup(&cancellation),
        fixture.fixture.target,
        cancellation,
    );
    solver::SemanticDeclarations::new(&context, context.item_paths())
        .with_solver(|solver| {
            let cx = solver.interner();
            let owner = solver::DefId::Function(FunctionRef {
                origin: DefMapRef::Crate(fixture.fixture.target),
                id: FunctionId(0),
            });
            let source = *cx.params(owner).first().unwrap();
            let receiver = solver::Ty::new_param(cx, solver::Param { index: 0, source });
            let table = solver::InferenceTable::new(solver, cx.parameter_environment(owner));
            let marker = fixture.fixture.trait_ref_by_name("Marker").unwrap();
            let bound = solver::TraitApplication {
                def: marker,
                args: solver::List::new(cx, &[receiver.into()]),
            };
            assert_eq!(table.prove([bound.clause(cx)]), solver::Outcome::Proven);

            let variable = table.new_type_var();
            table.register(
                solver::TraitApplication {
                    def: marker,
                    args: solver::List::new(cx, &[variable.into()]),
                }
                .clause(cx),
            );
            assert_eq!(table.fulfill(), solver::Outcome::Ambiguous);
            let has_item = fixture.fixture.trait_ref_by_name("HasItem").unwrap();
            let item = table.normalize(
                cx.projection(solver::ProjectionTy {
                    associated_ty: fixture
                        .fixture
                        .associated_ty_by_name(has_item, "Item")
                        .unwrap(),
                    args: solver::List::new(cx, &[receiver.into()]),
                }),
            );
            assert_eq!(table.fulfill(), solver::Outcome::Ambiguous);
            assert_eq!(table.finalize(item), Ty::Primitive(PrimitiveTy::Bool));
            assert!(table.resolve_root_var(variable).is_var());

            // The rigid receiver's independent proof must not erase the older variable goal.
            let user = Ty::adt(AdtTy {
                def: fixture.fixture.type_ref_by_name("User").unwrap(),
                args: Default::default(),
            });
            table.try_unify(variable, cx.lower_ty(&user, &[])).unwrap();
            assert_eq!(table.fulfill(), solver::Outcome::Proven);
            assert_eq!(table.finalize(variable), user);
        })
        .expect("fixture declarations load");
}

#[test]
fn rigid_goal_keeps_incomplete_environment_and_cancellation_unavailable() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Marker
        functions
          fn#0 inspect<T: Marker> -> T
        "#,
        &[],
    );
    let cancellation = rg_std::CancellationToken::new();
    let context = TyContext::new(
        &fixture.fixture,
        &fixture,
        fixture.lookup(&cancellation),
        fixture.fixture.target,
        cancellation.clone(),
    );
    solver::SemanticDeclarations::new(&context, context.item_paths())
        .with_solver(|solver| {
            let cx = solver.interner();
            let owner = FunctionRef {
                origin: DefMapRef::Crate(fixture.fixture.target),
                id: FunctionId(0),
            };
            let source = *cx.params(solver::DefId::Function(owner)).first().unwrap();
            let receiver = solver::Ty::new_param(cx, solver::Param { index: 0, source });
            let goal = solver::TraitApplication {
                def: fixture.fixture.trait_ref_by_name("Marker").unwrap(),
                args: solver::List::new(cx, &[receiver.into()]),
            };
            let incomplete = solver::InferenceTable::new(
                solver.clone(),
                cx.parameter_environment(solver::DefId::Function(FunctionRef {
                    id: FunctionId(99),
                    ..owner
                })),
            );
            for _ in 0..2 {
                assert_eq!(
                    incomplete.prove([goal.clause(cx)]),
                    solver::Outcome::Unavailable
                );
            }
            let table = solver::InferenceTable::new(
                solver,
                cx.parameter_environment(solver::DefId::Function(owner)),
            );
            assert_eq!(table.prove([goal.clause(cx)]), solver::Outcome::Proven);
            cancellation.cancel();
            assert_eq!(table.prove([goal.clause(cx)]), solver::Outcome::Unavailable);
        })
        .expect("fixture declarations load");
}
