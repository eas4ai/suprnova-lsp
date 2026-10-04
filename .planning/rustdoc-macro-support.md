# Rustdoc Macro Support Implementation Plan

**Goal:** Make compiler-generated Suprnova APIs available to this LSP without running rust-analyzer, while preserving low idle memory.

**Architecture:** Export declarations with a pinned nightly rustdoc process. Validate and reduce each export before importing it into the existing DefMap/generated-item/Semantic IR pipeline. Source analysis continues to handle editable function bodies. Export processes exit after refresh; their full JSON graphs never become resident project state.

**Contract:** The implementation discussion in this session and `AGENTS.md`. Commits and PRs remain human responsibilities. Existing `docs/` files and Suprnova's source are outside this change.

## Progress

- Complete: Establish a real macro-generated export, select its matching schema, and finalize the first implementation slice. Export generation exited successfully and the saved fixture matches format 61.
- Complete: Implement and test a compiler-API import boundary and a runnable inspection command. All 25 focused tests passed.
- Complete: Verify the slice and record observed results and remaining integration work. Full linting, including Dylint, passed.

This initial slice is complete. Keep exactly one item in progress during implementation; mark it complete only after its code and verification pass.

## Delivery sequence

1. **Import foundation:** Capture a genuine procedural-macro fixture. Pin a compatible `rustdoc-types` schema. Validate export format, crate root, private coverage, and referenced signatures. Report unverifiable hidden coverage explicitly. Inspect concrete declarations, preserving compiler types, generic bounds, and associated types. Provide a runnable inspection path so real exports can be evaluated before altering project indexing.
2. **Engine integration:** Translate imported declarations into generated items. Reconcile source and imported identity by Cargo package, crate target, module, item owner, and kind. Add generated impls before Semantic IR construction. Test trait-method lookup and return-type inference through a generated model API.
3. **Saved snapshot cache:** Store reduced per-crate data with producer version, JSON format, target, features, dependencies, and source fingerprints. Load through existing package storage. Replace snapshots atomically. Reject incompatible artifacts and invalidate results whose macro inputs changed.
4. **Refresh and editor behavior:** Start with explicit refresh. Add bounded background refresh after save, cancellation, and recovery after export failure. Preserve unrelated valid snapshots. Use source bodies for interactive analysis and expose artifact freshness without claiming stale APIs are current.
5. **Suprnova integration:** Exercise a real application model, then Live component metadata and Inertia navigation. Resolve imported external types through actual Cargo dependencies. Add precise navigation only where source provenance is known.

## First acceptance case

A small Cargo workspace contains a procedural macro that adds a trait impl and a method to a model. Rustdoc JSON must expose those declarations and their real signatures. The import boundary must preserve ownership and generic/associated type information, distinguish local declarations from external or synthetic items, and reject unsupported exports with actionable errors.

The subsequent engine gate is completion and inference for `Post::query()`, whose signature is supplied by Suprnova's `Model` trait and whose applicability comes from the generated impl. An inspection command alone does not satisfy this engine gate.

## Risks and required checks

- Missing spans do not prove an item came from a procedural macro. Treat spanless items as declarations without exact source locations; do not fabricate provenance.
- Attribute macros can modify the annotated declaration. Import reconciliation must handle replacement, not only append impls.
- Rustdoc does not export expression bodies or a reference index. Expression-macro inference is separate work.
- Private and hidden generated types can occur in signatures. Incomplete exports must not silently claim completeness.
- Targets with different Cargo features must not share an incompatible snapshot.
- JSON IDs are local to an export and must not be persisted as cross-export identities.
- Initial measurement uses the repo-owned `just agent-debug` workflow. Record compiler peak memory separately from resident LSP memory; do not claim a memory target until measured.

## Verification

Use test-first implementation with the real captured export and independently derived assertions. Cover successful extraction, ownership, schema mismatch, incomplete coverage, malformed references, and generic signatures. Run targeted tests through `just agent-debug test`, then workspace Rust tests, formatting, linting, `ripwire --quality-delta`, and `ripwire --test-gate` as appropriate to the completed slice. Do not run VS Code tests for a Rust import-boundary change.

## Decisions and evidence

- Work branch: `work/rustdoc-macro-support`; no commits are made by the agent.
- Plan location: `.planning/` respects the repository's restriction on `docs/`.
- Static inspection found `GeneratedItemStore` in `crates/engine/def-map/src/source.rs`, builtin derive ingestion in `crates/engine/def-map/src/build/macros/builtin_derive.rs`, and a generated-signature semantic test in `crates/engine/semantic-ir/src/tests/mod.rs`.
- Installed nightly reports `rustc 1.100.0-nightly (e71c0f1e3 2026-08-18)`. The export's actual format version will determine the compatible pinned schema dependency.
- The installed nightly manifest is dated `2026-08-19`; it emits format 61, matching the pinned `rustdoc-types = "=0.61.0"` dependency.
- The genuine macro export gives generated methods the macro invocation's source span. The importer never uses absence of a span to classify an item as macro-generated.
- Rustdoc reports a module-private method in the crate root as crate-visible. The fixture's visibility assertion follows that resolved scope.
- The machine's default stable is 1.97.1; existing dependencies `edition` and `stdx` require 1.98. Checks use installed `RUSTUP_TOOLCHAIN=1.98.1` without changing repository toolchain settings.
- Independent review reproduced valid reference and `Box<Post>` impls attached to Post, as well as a same-name function in another namespace. The regenerated fixture covers these cases.
- The inspection report excludes all blanket impls, including local blanket declarations. It counts exclusions and states that complete trait applicability is not established. Engine integration must reconcile their original ownership rather than assuming an attached impl's crate ID identifies its origin.
- Rustdoc JSON has no hidden-coverage flag. Reports state this limitation; unresolved retained signature paths are rejected. The importer does not claim hidden APIs are complete.

## Active slice: compiler export inspection

This slice establishes and exercises the import boundary. It does not yet inject declarations into the saved project generation or satisfy the `Post::query()` completion gate.

### Task 1: Validate and inspect an export

**Files:** `crates/engine/rustdoc/Cargo.toml`, `crates/engine/rustdoc/src/lib.rs`, `crates/engine/rustdoc/src/tests/mod.rs`, and `crates/engine/rustdoc/fixtures/model/`.

**Interfaces:** `RustdocExport::read(impl Read) -> anyhow::Result<RustdocExport>`; `type_api(&self, path: &str) -> anyhow::Result<TypeApiView<'_>>`; `resolve_path(&self, rustdoc_types::Id) -> anyhow::Result<&rustdoc_types::ItemSummary>`.

- [x] Capture real attribute-macro output, its source Cargo workspace, and exact producer information.
- [x] Write behavioral tests for identity, generics, associated types, visibility, malformed references, and schema incompatibility.
- [x] Run them before implementation: 12 tests ran; 9 failed against the deliberately unimplemented import boundary.
- [x] Implement the validated, transient export reader and exact nominal-type selection. Filter synthetic and dependency blanket impls; preserve explicit declarations and compiler signatures.
- [x] Run `just agent-debug --timeout 120s --env CARGO_BUILD_JOBS=2 test -p rg_rustdoc --offline`: 12 passed; owned-process cleanup verified.

### Task 2: Exercise the boundary through the executable

**Files:** `crates/rust-glancer/src/main.rs`, `crates/rust-glancer/src/rustdoc/mod.rs`, `crates/rust-glancer/tests/rustdoc.rs`, and `crates/rust-glancer/Cargo.toml`.

**Interface:** `rust-glancer inspect-rustdoc EXPORT --item CRATE::MODULE::TYPE` prints the selected declaration, explicit impls, associated item signatures, target, schema version, and export-local type paths as JSON. Invalid input exits with an error and no report.

- [x] Write executable integration tests for the real model API, unknown type, and missing export.
- [x] Run the tests and observe the missing command fail: all three exited through Clap with code 2.
- [x] Add the command and serialize the selected borrowed view without starting an LSP or compiler. Report selected fields/variants and resolved signature paths; validate module-restricted visibility.
- [x] Run `just agent-debug --timeout 5m --env RUSTUP_TOOLCHAIN=1.98.1 --env CARGO_BUILD_JOBS=2 test -p rg_rustdoc -p rust-glancer --test rustdoc --lib --offline`: all 25 tests passed, including the three executable cases. Owned-process cleanup verified. The two final review regressions failed before their ownership/member-kind fixes and passed afterwards.

### Task 3: Verify and review the slice

- [x] Run workspace Rust tests, formatting, workspace Clippy, custom Dylint checks, dependency policy checks, and code generation checks.
- [x] Run `ripwire --quality-delta` and `ripwire --test-gate`; resolve material findings.
- [x] Record actual verification, limitations, and the next engine integration gate in this plan. Leave the code uncommitted for human review.

### Observed verification

- Workspace tests: 1,640 passed, 2 skipped through `just agent-debug`, with two test threads and Rust 1.98.1. Artifact: `target/agent-debug/runs/20261004T130317562Z-test-2393924-98e071`.
- Focused tests after final review and custom lint fixes: 25 passed. Artifact: `target/agent-debug/runs/20261004T131225385Z-test-2625048-8bfbf3`.
- `just fmt --check`, standalone fixture formatting, `cargo +1.98.1 clippy --workspace --all-targets --offline -- -D warnings`, `cargo +1.98.1 deny check`, `just codegen-check`, and `git diff --check` passed.
- `ripwire --quality-delta` returned success with zero gating findings. Non-gating findings identify the deliberately inline import workflow's size and complexity, repeated test setup, and false-positive dead-code warnings for registered Rust tests. No unrelated code was refactored to satisfy heuristics.
- `ripwire --test-gate` returned success. Its changed-symbol graph omits untracked files, so the actual new-crate tests supply the required evidence.
- Independent read-only review found no remaining material defects after namespace, reference/Box owner, signature path, visibility, and exact member-kind fixes.
- Installed `cargo-dylint 6.1.0` after the user's request; matching `dylint-link 6.1.0` was already available. The pinned custom lint library uses Dylint 6.0.4 and nightly-2026-05-28.
- The first custom check crashed while compiling `serde_core`. Retrying with `RUST_MIN_STACK=16777216` passed that point and caught an impl-placement violation. Moving RustdocExport immediately before its impl corrected the violation.
- Full `RUSTUP_TOOLCHAIN=1.98.1 RUST_MIN_STACK=16777216 CARGO_BUILD_JOBS=2 just lint` passed formatting, workspace Clippy, and all workspace Dylint checks. The larger stack is a command-local workaround; no global compiler configuration was changed.
- Manual inspection command succeeded and saved `target/agent-debug/rustdoc-model-api.json`: six concrete impls, generated query/methods, associated Key, and nine explicitly excluded blanket impls.

### Next engine gate

The next implementation phase must translate this validated declaration data into existing generated items and reconcile it with source declarations. Acceptance is actual `Post::query()` completion and `Builder<Post>` inference through the analysis engine. This slice has no snapshot cache or automatic refresh, does not establish complete blanket applicability, and makes no measured LSP-memory claim.
