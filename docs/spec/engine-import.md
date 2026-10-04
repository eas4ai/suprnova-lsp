Prefix: MAC

# Generated Model API Analysis

The first commitment uses genuine captured format-61 exports and explicitly supplied package/target inputs. Its acceptance fixtures isolate compiler-derived declaration support from automatic export generation and refresh.

## Agreed obligations

[MAC-001] The analysis engine MUST resolve Post::query() and infer Builder<Post> when a supplied compiler export establishes Post's implementation of a source-declared Model trait with query() -> Builder<Self>.
Falsifier: In the genuine macro fixture, lookup is unresolved or ambiguous, the inferred result is unknown or has the wrong generic argument, or an impl-only fixture cannot use the trait's default query method.
Mechanism: rustdoc-engine-import
Rationale: This is the developer-confirmed first feature; a trait-default-method case matches Suprnova's Model API.
Status: Agreed 2026-10-04

[MAC-002] The analysis engine MUST preserve the supplied fixture's generated generic method signature and associated type assignment when answering type queries.
Falsifier: generated_method(7u64) does not infer Builder<u64>, or the generated Model::Key assignment does not resolve to u64.
Mechanism: rustdoc-engine-import
Rationale: Concrete compiler signatures must survive lowering, rather than becoming guessed completion labels.
Status: Agreed 2026-10-04

[MAC-003] The import integration MUST reconcile supplied declarations by Cargo package, crate target, module, nominal owner, and item kind without duplicating a matching source declaration.
Falsifier: A second Post in another module, package, or crate target receives the first type's generated methods, a same-name value prevents import, or a method present in both source and export creates duplicate or ambiguous lookup.
Mechanism: rustdoc-engine-import
Rationale: Export-local IDs and short names cannot establish project ownership.
Status: Agreed 2026-10-04

[MAC-004] The import integration MUST isolate imported declarations from package artifacts whose validity does not include the supplied export.
Falsifier: A source-only cache hit suppresses the imported API, or a later build without the export reuses an unkeyed artifact and still sees its generated API.
Mechanism: rustdoc-engine-import
Rationale: Rebuilding or keeping affected packages resident is acceptable in this commitment; a new rustdoc snapshot cache is later work.
Status: Agreed 2026-10-04

[MAC-005] A candidate project build MUST reject an invalid or unresolvable supplied export without publishing a partially imported generation.
Falsifier: A format-mismatched export, missing signature reference, or unmappable selected owner succeeds, or a failed candidate changes the previously valid generation's query result.
Mechanism: rustdoc-engine-import
Rationale: Reuse the existing import validation and saved-generation publication boundaries.
Status: Agreed 2026-10-04

[MAC-006] Importing a supplied export and querying its model APIs MUST work without starting a rust-analyzer server or generating rustdoc during engine requests.
Falsifier: The query requires a rust-analyzer executable or the controlled acceptance run observes a rust-analyzer or rustdoc process launch during import or query handling.
Mechanism: rustdoc-engine-import
Rationale: Compiler export generation is a separate completed step; using existing parser and solver libraries is allowed.
Status: Agreed 2026-10-04

## Blast radius and evidence

| Boundary | Source and expected responsibility |
| --- | --- |
| Import validation | `crates/engine/rustdoc`; preserve the existing typed inspection boundary and resolve export-local references before engine identities. |
| Initial construction | `crates/engine/project/src/indexing/builder.rs` and `phases.rs`; accept explicit inputs and hand declarations to DefMap before Semantic IR. |
| Batched construction | `crates/engine/project/src/indexing/batch/mod.rs`; preserve the same import semantics when packages are processed in batches. |
| Definition ownership | `crates/engine/def-map/src/source.rs` and `build/macros`; generated-item storage, module/owner reconciliation, and concrete impl ingestion. |
| Declaration lowering | `crates/engine/semantic-ir`; preserve generic signatures and associated type assignments. |
| Queries and applicability | `crates/engine/ty`, `body-ir`, and `analysis`; use imported impl applicability and signatures through ordinary queries. |
| Cache validity | `crates/engine/project/src/storage`; avoid source-only artifact reuse for imported payloads. |
| Acceptance fixtures | `crates/engine/rustdoc/fixtures`, `crates/engine/project/src/tests`, and `crates/engine/analysis/src/tests`; real exports, semantic query assertions, and failure/cache controls. |

[Observed phase handoff](../../crates/engine/project/src/indexing/phases.rs:187), [builtin derive ingestion](../../crates/engine/def-map/src/build/macros/builtin_derive.rs:109), [generated semantic test](../../crates/engine/semantic-ir/src/tests/mod.rs:601), and [the recon](../recon.md) ground this proposed radius.
