Prefix: API

# Observed Compiler API Boundary

These blocks describe the implemented import/inspection slice. Their falsifiers name observable counterexamples; the proposed mechanism is the existing importer and executable test suite. They remain Observed and outside the first commitment's frozen requirement set.

[API-001] RustdocExport::read rejects an export whose format differs from the pinned rustdoc-types format before decoding its declaration shapes.
Falsifier: An unsupported-format export is accepted, or an incompatible item-shape error hides the format mismatch.
Mechanism: rustdoc-boundary
Rationale: See crates/engine/rustdoc/src/lib.rs and rejects_format_mismatch_before_decoding_item_shapes.
Status: Observed

[API-002] RustdocExport::read rejects an export that omits private items with a regeneration instruction.
Falsifier: includes_private=false succeeds or its error lacks --document-private-items.
Mechanism: rustdoc-boundary
Rationale: See rejects_export_without_private_items.
Status: Observed

[API-003] RustdocExport::type_api selects a local struct, enum, or union by its exact fully qualified path without confusing a same-name function or another module's type.
Falsifier: A short path succeeds, a same-name function makes the nominal selection ambiguous, or another module's Post acquires the selected Post's impls.
Mechanism: rustdoc-boundary
Rationale: See selects_explicit_impls_for_the_exact_type and requires_a_fully_qualified_local_type.
Status: Observed

[API-004] RustdocExport::type_api rejects unresolved retained signature references and invalid retained item ownership or kind.
Falsifier: A retained signature has a missing path, a local type declaration is absent, an impl belongs to another owner, a visibility parent is invalid, or a variant is accepted as a struct field.
Mechanism: rustdoc-boundary
Rationale: See signature, impl-owner, restricted-visibility, and member-kind regression tests.
Status: Observed

[API-005] The inspection report states that hidden coverage is unknown and blanket and synthetic impl applicability is excluded.
Falsifier: A successful report omits those limitations or presents excluded blanket impls as a complete local applicability set.
Mechanism: rustdoc-boundary
Rationale: See TypeApiView limitations and the executable report assertions.
Status: Observed

[API-006] The inspect-rustdoc command validates its input before writing the selected JSON report.
Falsifier: A missing export or unknown type exits successfully or emits a partial report.
Mechanism: rustdoc-boundary
Rationale: See crates/rust-glancer/tests/rustdoc.rs.
Status: Observed
