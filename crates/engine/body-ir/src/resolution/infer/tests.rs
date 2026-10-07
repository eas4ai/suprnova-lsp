use std::cell::Cell;

use rg_ir_model::{
    BindingId, BodyId, BodyRef, CrateId, CrateRef, DefMapRef, ExprId, PackageSlot, StructId,
    TypeDefId, TypeDefRef,
};
use rg_std::CancellationToken;
use rg_ty::{AdtTy, ClosureTyId, GenericArg, PrimitiveTy, Ty};

use super::state::InferenceState;

fn type_def(index: usize) -> TypeDefRef {
    TypeDefRef {
        origin: DefMapRef::Crate(CrateRef {
            package: PackageSlot(0),
            crate_id: CrateId(0),
        }),
        id: TypeDefId::Struct(StructId(index)),
    }
}

fn user_ty() -> Ty {
    Ty::adt(AdtTy::bare(type_def(0)))
}

fn vec_ty(inner: Ty) -> Ty {
    Ty::adt(AdtTy {
        def: type_def(1),
        args: vec![GenericArg::Type(Box::new(inner))].into(),
    })
}

fn closure_ty(index: usize) -> Ty {
    Ty::closure(
        ClosureTyId::new(body_ref(), ExprId(index)),
        Vec::new(),
        Ty::Unknown,
    )
}

fn body_ref() -> BodyRef {
    BodyRef {
        crate_ref: CrateRef {
            package: PackageSlot(0),
            crate_id: CrateId(0),
        },
        body: BodyId(0),
    }
}

fn default_int_ty() -> Ty {
    Ty::Primitive(PrimitiveTy::DEFAULT_INT)
}

// These state tests use real declaration identities, including generic ADT metadata used by
// compiler type relations. The scoped closure also verifies that no live fact escapes its arena.
fn with_state(exprs: usize, bindings: usize, run: impl for<'s> FnOnce(InferenceState<'s>)) {
    let fixture = crate::testonly::BodyIrFixture::build(
        r#"
//- /Cargo.toml
[package]
name = "inference_state"
version = "0.1.0"
edition = "2024"
//- /src/lib.rs
pub struct User;
pub struct Vec<T>(T);
"#,
    );
    let target = body_ref().crate_ref;
    let def_map = fixture
        .def_map_db()
        .read_txn(rg_def_map::DefMapLoader::resident_only("inference fixture"));
    let semantic_ir =
        fixture
            .semantic_ir_db()
            .read_txn(rg_semantic_ir::SemanticIrLoader::resident_only(
                "inference fixture",
            ));
    let lookup = rg_semantic_ir::ItemLookupQuery::build_from(
        &rg_semantic_ir::CrateItemQuery::new(&def_map, &semantic_ir, target),
        &CancellationToken::new(),
    )
    .expect("fixture lookup");
    let context = rg_ty::TyContext::new(
        &def_map,
        &semantic_ir,
        lookup,
        target,
        CancellationToken::new(),
    );
    let declarations = rg_ty::solver::SemanticDeclarations::new(&context, context.item_paths());
    declarations
        .with_solver(|solver| {
            run(InferenceState::new(
                exprs,
                bindings,
                rg_ty::solver::InferenceTable::new(solver, Default::default()),
            ))
        })
        .expect("fixture declarations");
}

#[test]
fn stores_closure_types_as_body_local_facts() {
    with_state(1, 0, |mut context| {
        context.set_expr_closure_ty(body_ref(), ExprId(0), 0);

        let rg_ty::solver::TyShape::Closure(closure) = context.expr_ty(ExprId(0)).shape() else {
            panic!("closure expression should retain its callable signature");
        };
        assert_eq!(closure.id, ClosureTyId::new(body_ref(), ExprId(0)));
        assert!(closure.params.is_empty());
        assert!(closure.ret.has_var());
        assert_eq!(context.finalize_expr_ty(ExprId(0)), closure_ty(0));
    });
}

#[test]
fn copies_closure_types_through_binding_reads() {
    with_state(2, 1, |mut context| {
        context.set_expr_closure_ty(body_ref(), ExprId(0), 0);
        context.set_binding_ty(BindingId(0), context.expr_ty(ExprId(0)));

        context.set_expr_from_binding(ExprId(1), BindingId(0));
        let rg_ty::solver::TyShape::Closure(closure) = context.expr_ty(ExprId(1)).shape() else {
            panic!("binding reads should preserve closure identity and signature");
        };
        assert_eq!(closure.id, ClosureTyId::new(body_ref(), ExprId(0)));
        assert_eq!(context.finalize_expr_ty(ExprId(1)), closure_ty(0));
    });
}

#[test]
fn creates_body_inference_context_with_body_sized_slots() {
    with_state(2, 3, |context| {
        let var = context.table.new_type_var();

        assert_eq!(context.finalize_expr_ty(ExprId(0)), Ty::Unknown);
        assert_eq!(context.finalize_expr_ty(ExprId(1)), Ty::Unknown);
        assert_eq!(context.finalize_binding_ty(BindingId(0)), Ty::Unknown);
        assert_eq!(context.finalize_binding_ty(BindingId(1)), Ty::Unknown);
        assert_eq!(context.finalize_binding_ty(BindingId(2)), Ty::Unknown);
        assert_eq!(context.table.finalize(var), Ty::Unknown);
    });
}

#[test]
fn stores_expression_type_variables_until_expected_type_evidence_arrives() {
    with_state(1, 0, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        let var = context.table.new_type_var();

        context.set_expr_ty(ExprId(0), var);
        assert_eq!(context.finalize_expr_ty(ExprId(0)), Ty::Unknown);

        context.constrain_expr_ty(ExprId(0), &live(&user_ty()));
        assert_eq!(context.finalize_expr_ty(ExprId(0)), user_ty());
    });
}

#[test]
fn expected_type_seeds_an_expression_without_producer_evidence() {
    with_state(1, 0, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);

        context.constrain_expr_ty(ExprId(0), &live(&user_ty()));

        assert_eq!(context.finalize_expr_ty(ExprId(0)), user_ty());
    });
}

#[test]
fn repeated_nested_unknown_instantiation_reuses_expression_slots() {
    with_state(1, 0, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        let return_ty = live(&vec_ty(Ty::Unknown));

        context.instantiate_expr_nested_unknown_ty(ExprId(0), &return_ty);
        let first_inference_ty = context.expr_ty(ExprId(0));

        context.instantiate_expr_nested_unknown_ty(ExprId(0), &return_ty);
        assert_eq!(context.expr_ty(ExprId(0)), first_inference_ty);
    });
}

#[test]
fn never_expression_does_not_solve_its_expected_type_slot() {
    with_state(1, 0, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        context.set_expr_ty(ExprId(0), cx.never());
        let expected = context.table.new_type_var();

        context.constrain_expr_ty(ExprId(0), &expected);
        context.table.unify(expected, live(&user_ty()));

        assert_eq!(context.finalize_expr_ty(ExprId(0)), Ty::Never);
        assert_eq!(context.table.finalize(expected), user_ty());
    });
}

#[test]
fn binding_path_equality_carries_early_expected_type_back_to_the_binding() {
    with_state(1, 1, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        context.constrain_expr_ty(ExprId(0), &live(&vec_ty(user_ty())));

        context.set_expr_from_binding(ExprId(0), BindingId(0));

        assert_eq!(context.finalize_binding_ty(BindingId(0)), vec_ty(user_ty()));
    });
}

#[test]
fn weaker_expression_evidence_preserves_known_type() {
    with_state(1, 0, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        context.set_expr_ty(ExprId(0), live(&user_ty()));

        context.set_expr_ty(ExprId(0), cx.unknown());
        assert_eq!(context.finalize_expr_ty(ExprId(0)), user_ty());
    });
}

#[test]
fn linked_expression_variables_share_later_evidence() {
    with_state(1, 0, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        let first = context.table.new_type_var();
        context.set_expr_ty(ExprId(0), first);

        let replacement = context.table.new_type_var();
        context.set_expr_ty(ExprId(0), replacement);
        context.table.unify(first, live(&user_ty()));
        assert_eq!(context.table.finalize(replacement), user_ty());
        assert_eq!(context.finalize_expr_ty(ExprId(0)), user_ty());
    });
}

#[test]
fn weaker_pattern_evidence_does_not_replace_a_settled_binding_fact() {
    with_state(0, 1, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        let settled = Ty::tuple(vec![user_ty(), user_ty()]);
        context.set_binding_ty(BindingId(0), live(&settled));

        context.set_binding_ty(BindingId(0), live(&Ty::tuple(vec![user_ty(), Ty::Unknown])));

        assert_eq!(context.finalize_binding_ty(BindingId(0)), settled);
    });
}

#[test]
fn conflicting_evidence_does_not_discard_an_established_type() {
    with_state(1, 0, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        let slot = context.table.new_type_var();
        context.set_expr_ty(ExprId(0), slot);

        context.set_expr_ty(ExprId(0), live(&user_ty()));
        context.set_expr_ty(ExprId(0), live(&default_int_ty()));

        assert_eq!(context.finalize_expr_ty(ExprId(0)), user_ty());
    });
}

#[test]
fn treats_equivalent_variable_aliases_as_stable_body_facts() {
    with_state(1, 1, |mut context| {
        let cx = context.table.interner();
        let live = |ty: &Ty| cx.lower_ty(ty, &[]);
        let original = context.table.new_type_var();
        let alias = context.table.new_type_var();
        let unrelated = context.table.new_type_var();

        context.set_binding_ty(BindingId(0), original);
        context.set_expr_ty(ExprId(0), original);

        context.set_binding_ty(BindingId(0), alias);
        context.set_binding_ty(BindingId(0), original);
        context.set_expr_from_binding(ExprId(0), BindingId(0));

        context.set_expr_ty(ExprId(0), unrelated);
        context.set_expr_from_binding(ExprId(0), BindingId(0));

        context.table.unify(alias, live(&user_ty()));
        assert_eq!(context.table.finalize(original), user_ty());
        assert_eq!(context.table.finalize(unrelated), user_ty());
        assert_eq!(context.finalize_binding_ty(BindingId(0)), user_ty());
        assert_eq!(context.finalize_expr_ty(ExprId(0)), user_ty());
    });
}

thread_local! {
    static CANCEL_AFTER_EXPRESSIONS: Cell<Option<usize>> = const { Cell::new(None) };
}

pub(super) fn before_expression(cancellation: &CancellationToken) {
    CANCEL_AFTER_EXPRESSIONS.with(|remaining| {
        if let Some(count) = remaining.get() {
            remaining.set(count.checked_sub(1));
            if count == 0 {
                cancellation.cancel();
            }
        }
    });
}

#[test]
fn cancelling_recursive_inference_never_finalizes_partial_body_facts() {
    let fixture = crate::testonly::BodyIrFixture::build(
        r#"
//- /Cargo.toml
[package]
name = "cancelled_inference"
version = "0.1.0"
edition = "2024"

//- /src/lib.rs
pub fn compute() -> u32 { let first = 1_u32; let second = first + 2; second + 3 }
"#,
    );
    let target = CrateRef {
        package: PackageSlot(0),
        crate_id: CrateId(0),
    };
    let bodies = fixture
        .body_ir_db()
        .resident_package(target.package)
        .expect("fixture package exists")
        .crate_bodies(target.crate_id)
        .expect("fixture crate exists");
    let body = &bodies.bodies()[0];
    let def_map = fixture
        .def_map_db()
        .read_txn(rg_def_map::DefMapLoader::resident_only("inference fixture"));
    let semantic_ir =
        fixture
            .semantic_ir_db()
            .read_txn(rg_semantic_ir::SemanticIrLoader::resident_only(
                "inference fixture",
            ));
    let lookup = rg_semantic_ir::ItemLookupQuery::build_from(
        &rg_semantic_ir::CrateItemQuery::new(&def_map, &semantic_ir, target),
        &CancellationToken::new(),
    )
    .expect("fixture lookup builds");
    for cancel in [true, false] {
        let cancellation = CancellationToken::new();
        CANCEL_AFTER_EXPRESSIONS.with(|remaining| remaining.set(cancel.then_some(2)));
        let result = super::InferenceContext::new(
            &def_map,
            &semantic_ir,
            &lookup,
            BodyRef {
                crate_ref: target,
                body: BodyId(0),
            },
            body,
            &cancellation,
        )
        .infer_body(None);
        if cancel {
            let error = result.expect_err("unfinished inference must have no facts");
            let cancelled = error
                .chain()
                .find_map(|cause| cause.downcast_ref::<rg_std::Cancelled>())
                .expect("inference preserves the cancellation cause");
            assert_eq!(cancelled.checkpoint(), "expression resolution");
        } else {
            let facts = result.expect("fresh inference can finish");
            assert_eq!(facts.exprs.len(), body.exprs().len());
        }
        assert!(CANCEL_AFTER_EXPRESSIONS.with(|remaining| remaining.get().is_none()));
    }
}

#[test]
fn binding_hover_skips_unrelated_statements_only_after_its_type_settles() {
    let fixture = crate::testonly::BodyIrFixture::build(
        r#"
//- /Cargo.toml
[package]
name = "binding_hover"
version = "0.1.0"
edition = "2024"
//- /src/lib.rs
struct Model;
struct Builder<T>(T);
impl Model { fn query() -> Builder<Model> { loop {} } }
fn concrete() { let selected = Model::query(); let unrelated = 1_u8; }
fn number() { let selected = 1; let later: u64 = selected; }
fn generic() { let selected = make(); let later: Builder<Model> = selected; }
fn nested() { { let selected = Model::query(); let later = 1_u8; } }
fn labelled() { 'done: { let selected = Model::query(); let later = 1_u8; } }
fn closure() { let selected = || 1; let later: u64 = selected(); }
fn array() { let selected = [1_u8; 3]; let later = selected; }
trait Source { type Value; }
fn projection<T: Source>(value: T::Value) { let selected = value; let later = 1_u8; }
fn make<T>() -> Builder<T> { loop {} }
"#,
    );
    let target = body_ref().crate_ref;
    let bodies = fixture
        .body_ir_db()
        .resident_package(target.package)
        .unwrap()
        .crate_bodies(target.crate_id)
        .unwrap();
    let def_map = fixture
        .def_map_db()
        .read_txn(rg_def_map::DefMapLoader::resident_only("hover fixture"));
    let semantic_ir =
        fixture
            .semantic_ir_db()
            .read_txn(rg_semantic_ir::SemanticIrLoader::resident_only(
                "hover fixture",
            ));
    let cancellation = CancellationToken::new();
    let lookup = rg_semantic_ir::ItemLookupQuery::build_from(
        &rg_semantic_ir::CrateItemQuery::new(&def_map, &semantic_ir, target),
        &cancellation,
    )
    .unwrap();
    let mut compared = 0;
    for (id, body) in bodies.bodies().iter().enumerate() {
        let Some((selected, binding)) = body.bindings().iter().enumerate().find(|(_, binding)| {
            binding
                .name
                .as_ref()
                .is_some_and(|name| name.as_str() == "selected")
        }) else {
            continue;
        };
        let infer = |offset| {
            super::InferenceContext::new(
                &def_map,
                &semantic_ir,
                &lookup,
                BodyRef {
                    crate_ref: target,
                    body: BodyId(id),
                },
                body,
                &cancellation,
            )
            .infer_body(offset)
            .unwrap()
        };
        let full = infer(None);
        let hovered = infer(Some(binding.name_span.unwrap().start));
        let selected = BindingId(selected);
        assert_eq!(hovered.bindings[selected], full.bindings[selected]);
        assert!(
            !hovered.bindings[selected].has_unknown(),
            "full binding type: {:?}",
            full.bindings[selected]
        );
        let unrelated = body.bindings().iter().enumerate().find(|(_, binding)| {
            binding
                .name
                .as_ref()
                .is_some_and(|name| name.as_str() == "unrelated")
        });
        if let Some((unrelated, _)) = unrelated {
            let unrelated = BindingId(unrelated);
            assert_ne!(full.bindings[unrelated], Ty::Unknown);
            assert_eq!(
                hovered.bindings[unrelated],
                Ty::Unknown,
                "unrelated work must not run after a concrete hover type settles"
            );
        } else {
            // Numeric defaults and generic arguments must receive their later constraints.
            assert_eq!(hovered, full);
        }
        compared += 1;
    }
    assert_eq!(compared, 8);
}
