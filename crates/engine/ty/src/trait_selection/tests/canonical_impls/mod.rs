use std::{collections::HashMap, convert::Infallible};

use rg_ir_model::{CrateRef, DefMapRef, FunctionId, FunctionRef, ImplId, ImplRef};
use rg_semantic_ir::{
    CrateItemQuery, ItemLookupIndex, ItemLookupIndexSource, ItemLookupQuery, ItemStore,
    ItemStoreSource, TraitImplSelfHead,
};
use rustc_type_ir::inherent::Ty as _;

use super::utils::TraitSelectionFixture;
use crate::{AdtTy, PrimitiveTy, Ty, TyContext, solver};

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
fn canonical_projection_skips_disjoint_direct_impl_candidates() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Probe
        structs
          struct#0 User
        impls
          impl#0 impl Probe for User
          impl#1 impl Probe for [User] [resolved self: empty]
        type aliases
          type#0 trait#0::Target
          type#1 impl#0::Target = bool
          type#2 impl#1::Target = bool
        functions
          fn#0 inspect<T> -> T
        "#,
        &[
            (0, TraitImplSelfHead::Adt(super::utils::type_def(0))),
            (1, TraitImplSelfHead::Slice),
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
            let table = solver::InferenceTable::new(solver, Default::default());
            let probe = fixture.fixture.trait_ref_by_name("Probe").unwrap();
            let target = fixture
                .fixture
                .associated_ty_by_name(probe, "Target")
                .unwrap();
            let mut before = (0, 0);
            cx.profile(|profile| before = (profile.evaluations, profile.impl_candidates));

            // Use a real projection root. Canonical evaluation turns the caller's rigid T
            // into a placeholder before requesting impls; testing the original T misses it.
            let normalized = table.normalize(cx.projection(solver::ProjectionTy {
                associated_ty: target,
                args: solver::List::new(cx, &[receiver.into()]),
            }));
            assert_eq!(table.fulfill(), solver::Outcome::NoSolution);
            let mut after = (0, 0);
            cx.profile(|profile| after = (profile.evaluations, profile.impl_candidates));
            assert!(after.0 > before.0, "exercise canonical root evaluation");
            assert_eq!(
                after.1 - before.1,
                0,
                "a rigid canonical receiver cannot match User or [User]"
            );
            assert!(table.resolve_root_var(normalized).is_var());
        })
        .expect("fixture declarations load");
}

#[test]
fn canonical_bounds_and_blanket_projection_preserve_pending_goals() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Marker
          trait#1 Derived: Marker
          trait#2 Probe
          trait#3 HasItem
        structs
          struct#0 User
        impls
          impl#0 impl Marker for User
          impl#1 impl Probe for User
          impl#2 impl<T> HasItem for T [resolved self: empty]
        type aliases
          type#0 trait#2::Target
          type#1 impl#1::Target = char
          type#2 trait#3::Item
          type#3 impl#2::Item = bool
        functions
          fn#0 inspect<T: Derived + Probe<Target = bool>> -> T
        "#,
        &[
            (0, TraitImplSelfHead::Adt(super::utils::type_def(0))),
            (1, TraitImplSelfHead::Adt(super::utils::type_def(0))),
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
            let source = *cx.params(owner).first().unwrap();
            let receiver = solver::Ty::new_param(cx, solver::Param { index: 0, source });
            let table = solver::InferenceTable::new(solver, cx.parameter_environment(owner));
            let marker = fixture.fixture.trait_ref_by_name("Marker").unwrap();
            assert_eq!(
                table.prove([solver::TraitApplication {
                    def: marker,
                    args: solver::List::new(cx, &[receiver.into()]),
                }
                .clause(cx)]),
                solver::Outcome::Proven
            );

            // The older unknown root must stay pending while independent rigid projections
            // use a supertrait environment and a blanket declaration, respectively.
            let variable = table.new_type_var();
            table.register(
                solver::TraitApplication {
                    def: marker,
                    args: solver::List::new(cx, &[variable.into()]),
                }
                .clause(cx),
            );
            assert_eq!(table.fulfill(), solver::Outcome::Ambiguous);
            for (trait_name, alias_name) in [("Probe", "Target"), ("HasItem", "Item")] {
                let trait_ref = fixture.fixture.trait_ref_by_name(trait_name).unwrap();
                let normalized = table.normalize(
                    cx.projection(solver::ProjectionTy {
                        associated_ty: fixture
                            .fixture
                            .associated_ty_by_name(trait_ref, alias_name)
                            .unwrap(),
                        args: solver::List::new(cx, &[receiver.into()]),
                    }),
                );
                assert_eq!(table.fulfill(), solver::Outcome::Ambiguous);
                assert_eq!(table.finalize(normalized), Ty::Primitive(PrimitiveTy::Bool));
                assert!(table.resolve_root_var(variable).is_var());
            }
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
fn canonical_projection_retains_alias_and_unresolved_headers() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Probe
          trait#1 Identity
        structs
          struct#0 User
        impls
          impl#0 impl Probe for <User as Identity>::Item [resolved self: empty]
          impl#1 impl Probe for Missing [resolved self: empty]
          impl#2 impl Identity for User
        type aliases
          type#0 trait#0::Target
          type#1 impl#0::Target = bool
          type#2 impl#1::Target = bool
          type#3 trait#1::Item
          type#4 impl#2::Item = User
        functions
          fn#0 inspect<T> -> T
        "#,
        &[(2, TraitImplSelfHead::Adt(super::utils::type_def(0)))],
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
            let table = solver::InferenceTable::new(solver, Default::default());
            let probe = fixture.fixture.trait_ref_by_name("Probe").unwrap();
            let mut before = 0;
            cx.profile(|profile| before = profile.impl_candidates);
            table.normalize(
                cx.projection(solver::ProjectionTy {
                    associated_ty: fixture
                        .fixture
                        .associated_ty_by_name(probe, "Target")
                        .unwrap(),
                    args: solver::List::new(cx, &[receiver.into()]),
                }),
            );
            assert_eq!(table.fulfill(), solver::Outcome::NoSolution);
            let mut after = 0;
            cx.profile(|profile| after = profile.impl_candidates);
            assert!(
                after - before >= 2,
                "alias and unresolved headers must reach exact checking"
            );
        })
        .expect("fixture declarations load");
}

#[test]
fn live_variable_and_alias_receivers_keep_concrete_projection_answers() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Probe
          trait#1 Identity
        structs
          struct#0 User
          struct#1 Vec<T>
        impls
          impl#0 impl Probe for User
          impl#1 impl Probe for [User] [resolved self: empty]
          impl#2 impl<T> Probe for Vec<T>
          impl#3 impl Identity for User
        type aliases
          type#0 trait#0::Target
          type#1 impl#0::Target = bool
          type#2 impl#1::Target = bool
          type#3 impl#2::Target = T
          type#4 trait#1::Item
          type#5 impl#3::Item = User
        "#,
        &[
            (0, TraitImplSelfHead::Adt(super::utils::type_def(0))),
            (1, TraitImplSelfHead::Slice),
            (2, TraitImplSelfHead::Adt(super::utils::type_def(1))),
            (3, TraitImplSelfHead::Adt(super::utils::type_def(0))),
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
            let probe = fixture.fixture.trait_ref_by_name("Probe").unwrap();
            let target = fixture
                .fixture
                .associated_ty_by_name(probe, "Target")
                .unwrap();
            let user = Ty::adt(AdtTy {
                def: fixture.fixture.type_ref_by_name("User").unwrap(),
                args: Default::default(),
            });
            let concrete_user = cx.lower_ty(&user, &[]);
            let vec = cx.lower_ty(
                &Ty::adt(AdtTy {
                    def: fixture.fixture.type_ref_by_name("Vec").unwrap(),
                    args: vec![crate::GenericArg::Type(Box::new(user.clone()))].into(),
                }),
                &[],
            );
            // The declarative fixture supports slices; reference syntax would be a literal path.
            let slice = cx.lower_ty(&Ty::Slice(Box::new(user.clone())), &[]);
            for (label, receiver, expected) in [
                ("User", concrete_user, Ty::Primitive(PrimitiveTy::Bool)),
                ("[User]", slice, Ty::Primitive(PrimitiveTy::Bool)),
                ("Vec<User>", vec, user.clone()),
            ] {
                let table = solver::InferenceTable::new(solver.clone(), Default::default());
                let variable = table.new_type_var();
                let normalized = table.normalize(cx.projection(solver::ProjectionTy {
                    associated_ty: target,
                    args: solver::List::new(cx, &[variable.into()]),
                }));
                assert_eq!(table.fulfill(), solver::Outcome::Ambiguous);
                assert!(table.resolve_root_var(variable).is_var());
                table.try_unify(variable, receiver).unwrap();
                assert_eq!(table.fulfill(), solver::Outcome::Proven, "receiver {label}");
                assert_eq!(table.finalize(normalized), expected, "receiver {label}");
            }

            // An alias receiver must normalize through its own concrete impl before the
            // outer projection can use the User declaration.
            let identity = fixture.fixture.trait_ref_by_name("Identity").unwrap();
            let table = solver::InferenceTable::new(solver, Default::default());
            let alias = cx.projection(solver::ProjectionTy {
                associated_ty: fixture
                    .fixture
                    .associated_ty_by_name(identity, "Item")
                    .unwrap(),
                args: solver::List::new(cx, &[concrete_user.into()]),
            });
            let normalized = table.normalize(cx.projection(solver::ProjectionTy {
                associated_ty: target,
                args: solver::List::new(cx, &[alias.into()]),
            }));
            assert_eq!(table.fulfill(), solver::Outcome::Proven);
            assert_eq!(table.finalize(normalized), Ty::Primitive(PrimitiveTy::Bool));
        })
        .expect("fixture declarations load");
}

#[test]
fn canonical_projection_keeps_cancellation_and_incomplete_environment_unavailable() {
    let fixture = IndexedFixture::new(
        r#"
        traits
          trait#0 Probe
        functions
          fn#0 inspect<T: Probe<Target = bool>> -> T
        type aliases
          type#0 trait#0::Target
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
            let probe = fixture.fixture.trait_ref_by_name("Probe").unwrap();
            let projection = cx.projection(solver::ProjectionTy {
                associated_ty: fixture
                    .fixture
                    .associated_ty_by_name(probe, "Target")
                    .unwrap(),
                args: solver::List::new(cx, &[receiver.into()]),
            });
            let missing = solver::InferenceTable::new(
                solver.clone(),
                cx.parameter_environment(solver::DefId::Function(FunctionRef {
                    id: FunctionId(99),
                    ..owner
                })),
            );
            let unresolved = missing.normalize(projection);
            for _ in 0..2 {
                assert_eq!(missing.fulfill(), solver::Outcome::Unavailable);
                assert!(missing.resolve_root_var(unresolved).is_var());
            }

            let table = solver::InferenceTable::new(
                solver.clone(),
                cx.parameter_environment(solver::DefId::Function(owner)),
            );
            let normalized = table.normalize(projection);
            assert_eq!(table.fulfill(), solver::Outcome::Proven);
            assert_eq!(table.finalize(normalized), Ty::Primitive(PrimitiveTy::Bool));

            let cancelled = solver::InferenceTable::new(
                solver,
                cx.parameter_environment(solver::DefId::Function(owner)),
            );
            let unresolved = cancelled.normalize(projection);
            cancellation.cancel();
            assert_eq!(cancelled.fulfill(), solver::Outcome::Unavailable);
            assert!(cancelled.resolve_root_var(unresolved).is_var());
        })
        .expect("fixture declarations load");
}
