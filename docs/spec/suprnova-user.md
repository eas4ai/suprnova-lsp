Prefix: SUP

# Devlist User Acceptance

This commitment covers Devlist's `directory` library and `directory::models::user::User`, using its locked Suprnova Git dependency at `3229aa9af542c991196274fa3c235cdce88a68e2`. Acceptance records the captured application source and lockfile fingerprints, compiler invocation, target, features, and full export digest. A changed application requires a new capture before its results count.

Compiler generation is a separate preparation step. Tests use the authorized Devlist checkout or a provenance-checked capture of it; reduced test data must preserve compiler IDs, declarations and references used by the assertions. A small invented model cannot substitute for application acceptance. Implementation and compiler artifacts stay in this repository under the existing checkout rules.

## Agreed obligations

[SUP-001] Acceptance MUST select User from the genuine Devlist compiler export, verify the recorded input provenance, and distinguish directly owned impls from valid impls attached because User appears in a trait argument.
Falsifier: The pinned export's impl From<User> for user::Model is rejected merely because its self type differs, is installed as an impl for User, or acceptance passes with a mismatched source, lockfile, producer configuration or export digest.
Mechanism: suprnova-user-acceptance
Rationale: Rustdoc attaches a valid reverse conversion to User; attachment alone does not establish its self type.
Status: Agreed 2026-10-04

[SUP-002] Analysis of the captured Devlist workspace with its supplied export and Model in scope MUST resolve User::query() through Suprnova's source-declared Model default method and infer suprnova::eloquent::Builder<User> in initial and batched construction.
Falsifier: Either construction mode produces unresolved or ambiguous lookup, an unknown result, the wrong Builder identity or generic argument, or a query stub replaces the framework's default method to make the check pass.
Mechanism: suprnova-user-acceptance
Rationale: The completed minimal fixture does not prove applicability through the real framework's trait bounds.
Status: Agreed 2026-10-04

[SUP-003] The imported User API MUST preserve generated without_global_scopes() and filter() signatures, argument-position impl Trait bounds, and EloquentModel's Key, Entity and Column assignments with their compiler-established identities.
Falsifier: User::without_global_scopes() or User::filter("email", "member@example.test") does not infer Builder<User>, retained filter parameters lose their IntoColumn or IntoVal bounds, Key does not normalize to i64, or Entity and Column remain unresolved or identify a different nominal declaration.
Mechanism: suprnova-user-acceptance
Rationale: The export contains synthetic generic parameters and generated nominal types absent from the original source.
Status: Agreed 2026-10-04

[SUP-004] Application import MUST preserve source methods and owner boundaries, and reject invalid candidate imports without changing the previous valid User generation.
Falsifier: verify_password(&self, &str) becomes ambiguous or loses Result<bool, FrameworkError>, a same-name owner control receives User's API, a missing required reference or wrong target succeeds, or a failed candidate changes the previous generation's query result.
Mechanism: suprnova-user-acceptance
Rationale: The application case must uphold MAC-003 and MAC-005 rather than relax their validation.
Status: Agreed 2026-10-04

[SUP-005] Import and semantic queries on the captured application MUST complete without launching rust-analyzer or rustdoc after export preparation.
Falsifier: The controlled import/query process tree executes either tool, or acceptance lacks successful process-launch observation covering the import and query phases.
Mechanism: suprnova-user-acceptance
Rationale: MAC-006's process boundary also applies to real application analysis.
Status: Agreed 2026-10-04

[SUP-006] Acceptance MUST release the full compiler JSON graph after lowering and report comparable engine idle RSS with and without the export after indexing and queries finish, separately from compiler and indexing peaks.
Falsifier: The saved project retains the full export graph, an idle measurement is missing, compiler or indexing peaks are presented as idle cost, or the comparison changes target, features, sysroot, residency policy or query workload without disclosing and correcting the mismatch.
Mechanism: suprnova-user-acceptance
Rationale: Measure the real retained cost before choosing a memory budget; peak-only measurements do not answer the repository's idle-memory priority.
Status: Agreed 2026-10-04

## Scope and observation

The proposed mechanism combines provenance checks, ordinary engine query assertions, identity and failed-candidate controls, Linux process tracing, retained-state inspection, and supervised RSS samples. Each requirement must have a demonstrated failing control before the mechanism is trusted. Existing MAC checks remain regression obligations; their Agreed text stays unchanged.

Generated declarations and related concrete impls may be imported only as needed to establish this User API and its applicability. Automatic export refresh, production snapshot caching, editor settings, arbitrary procedural macro execution, Live and Inertia support remain later work. No numerical RSS limit is proposed before measurements exist.

## Blast radius

| Boundary | Evidence and expected change |
| --- | --- |
| Export selection | [Reader](../../crates/engine/rustdoc/src/lib.rs:175): validate actual impl self types separately from rustdoc attachment. |
| Signature lowering | [Lowerer](../../crates/engine/rustdoc/src/lowering/mod.rs:350): represent argument-position impl Trait without rejecting its synthetic parameters or erasing bounds. |
| Generated definitions | [Import preparation](../../crates/engine/def-map/src/build/compiler/mod.rs:40): reconcile required generated modules, nominal types and related impls before validating signature references. |
| Project construction | [Explicit inputs](../../crates/engine/project/src/indexing/builder.rs:21), [capture and residency](../../crates/engine/project/src/indexing/compiler/mod.rs:1), initial and batched phases: retain atomic publication and package ownership. |
| Inference | Semantic IR, type applicability and body queries: use real generated identities and the framework default method; change only demonstrated gaps. |
| Acceptance and memory | [Existing mechanism](../../tools/sudus-engine-import.py:1), [bounded runner](../../tools/agent-debug.py:1), project memory hooks and tests: add application evidence and completed-indexing idle samples. |

[Application recon](../recon.md#devlist-user-follow-up-2026-10-04) records the producer, observed failure, and remaining unverified boundaries. The alternative is reduced snapshot caching first; application acceptance is recommended now because it exposes the declarations a useful snapshot must retain.
