# Suprnova LSP Recon

The initial sections record onboarding and mechanism preparation. The dated follow-up below records the completed engine commitment and the new application evidence.

## Repository and authority

| State | Finding | Evidence |
| --- | --- | --- |
| Exists | This working tree started at upstream release commit `cd3d160826e389a2c85cbdf3bd4276577588de01`; prepared rustdoc/acceptance inputs are committed as `3472d7dd` on `work/rustdoc-macro-support`. | `git rev-parse HEAD`, `git branch --show-current`, `git status --short`; [implementation plan](../.planning/rustdoc-macro-support.md). |
| Exists | Sudus authority uses `origin`, now `https://github.com/eas4ai/suprnova-lsp.git`. Initialization records the developer's words, “remote on my word”; both durable roots exist locally. | [settings](../.sudus/settings.json:3); `git remote get-url origin`; `git ls-remote origin HEAD`; `sudus show 421b5a87d96808461438a1a8b224b52713dce450`; `git show-ref refs/sudus/log refs/sudus/snapshots`. |
| Exists | Initialization found no legacy Cairn records or existing Sudus specification. Its initial verdict was `repair docs/spec`; after draft preparation, wake reports that no commitment is started. | Initial filesystem inspection; `sudus wake` after initialization and after draft preparation. |
| Documented | Package metadata, executable names, and editor identifiers retain inherited Rust Glancer branding. | [workspace manifest](../Cargo.toml:53), [CLI](../crates/rust-glancer/src/main.rs:23), [VS Code manifest](../editors/code/package.json:2). |

## Runtime and data

| State | Finding | Evidence |
| --- | --- | --- |
| Exists | The executable dispatches analysis, LSP/server-engine, comparison, and rustdoc inspection commands. LSP orchestration and analysis live in separate crates. | [CLI dispatch](../crates/rust-glancer/src/main.rs:106), [server](../crates/lsp/server/src/lib.rs:1), [engine adapter](../crates/lsp/engine/src/lib.rs:1). |
| Exists | Project construction lowers source item trees, builds DefMap and Semantic IR, then drops generated-item and item-tree stores before body analysis. | [phase construction](../crates/engine/project/src/indexing/phases.rs:111), [generated stores](../crates/engine/project/src/indexing/phases.rs:187), [Semantic IR](../crates/engine/project/src/indexing/phases.rs:235), [release](../crates/engine/project/src/indexing/phases.rs:268). |
| Exists | Generated declaration storage and builtin derive ingestion already exist. A semantic test covers generated generic structs and impl signatures. | [GeneratedSourceData](../crates/engine/def-map/src/source.rs:145), [GeneratedItemStore](../crates/engine/def-map/src/source.rs:165), [builtin derives](../crates/engine/def-map/src/build/macros/builtin_derive.rs:1), [semantic test](../crates/engine/semantic-ir/src/tests/mod.rs:601). |
| Exists | Saved project generations, package artifacts, and request working sets have different validity and ownership. Storage contains artifact writers, loaders, caches, and residency controls. | [project ownership](../crates/engine/project/src/lib.rs:1), [storage](../crates/engine/project/src/storage/mod.rs:1). |
| Exists | VS Code and Zed clients launch the LSP; Zed transports initialization settings and chooses a configured, installed, or managed binary. | [VS Code extension](../editors/code/src/extension.ts:18), [Zed adapter](../editors/zed/src/lib.rs:1). |

## Compiler API boundary

| State | Finding | Evidence |
| --- | --- | --- |
| Exists | `rg_rustdoc` validates a bounded format-61 export and selects one fully qualified nominal type. It retains compiler signatures, fields, associated items, and concrete impls, including reference and Box self types. | [reader and API view](../crates/engine/rustdoc/src/lib.rs:15), [schema dependency](../crates/engine/rustdoc/Cargo.toml:16), [regression tests](../crates/engine/rustdoc/src/tests/mod.rs:15). |
| Exists | `inspect-rustdoc` reads an existing file and emits JSON through the import boundary without starting project analysis. Its report explicitly excludes blanket/synthetic applicability and cannot establish hidden-item coverage. | [inspection handler](../crates/rust-glancer/src/rustdoc/mod.rs:6), [report limitations](../crates/engine/rustdoc/src/lib.rs:263), [CLI integration tests](../crates/rust-glancer/tests/rustdoc.rs:9). |
| Exists | The checked-in fixture includes a real procedural macro and compiler export, with producer and regeneration instructions. | [fixture](../crates/engine/rustdoc/fixtures/model/src/lib.rs:1), [macro](../crates/engine/rustdoc/fixtures/model/macros/src/lib.rs:1), [producer](../crates/engine/rustdoc/fixtures/model/producer.txt:1). |
| Unverified | Imported rustdoc APIs are not yet proven to drive completion or inference through the engine. The known entry point is CLI inspection; the recorded next gate is engine integration. | [CLI handler](../crates/rust-glancer/src/rustdoc/mod.rs:6), [phase inputs](../crates/engine/project/src/indexing/phases.rs:10), [next engine gate](../.planning/rustdoc-macro-support.md:105). |
| Unverified | Rustdoc snapshot caching, export refresh, full blanket applicability, expression-macro analysis, and resident LSP memory after ingestion have not been demonstrated. | [delivery sequence and limitations](../.planning/rustdoc-macro-support.md:18), [next engine gate](../.planning/rustdoc-macro-support.md:105). |

## Tests and project policy

| State | Finding | Evidence |
| --- | --- | --- |
| Exists | Earlier in this session, 1,640 workspace tests passed with two skipped; the final focused suite passed 25 tests. These are session results, not Sudus receipts. | [workspace run summary](../target/agent-debug/runs/20261004T130317562Z-test-2393924-98e071/summary.json), [focused summary](../target/agent-debug/runs/20261004T131225385Z-test-2625048-8bfbf3/summary.json), [verification record](../.planning/rustdoc-macro-support.md:92). |
| Documented | The Justfile provides Nextest, formatting, Clippy, Dylint, dependency checks, code generation checks, fixtures, and bounded debug workflows. CI runs tests on Linux, Windows, and macOS and builds editor/platform targets. | [Justfile](../Justfile:1), [CI](../.github/workflows/ci.yml:19), [debug skill](../.agents/skills/rust-glancer-debugging/SKILL.md:1). |
| Exists | Full linting passed with Rust 1.98.1 and a command-local 16 MiB compiler stack. Cargo Dylint 6.1.0 is installed; CI pins 6.0.4. Cross-platform/editor checks were not rerun for the import slice. | [observed verification](../.planning/rustdoc-macro-support.md:92), [CI tool versions](../.github/workflows/ci.yml:28). |
| Documented | Low idle memory is a repository priority; the inherited scope sets a broader target below 100 MB RSS per active engine. That target is not a verified Suprnova requirement. | [agent rules](../AGENTS.md), [inherited scope](src/intro/SCOPE.md:7). |
| Documented | The inherited human-only commit rule and AGENTS.md preservation instruction conflicted with Sudus adoption. The developer explicitly approved replacing the agreement and permitting Sudus-required agent commits; PRs remain human-owned. | [applied agreement](../AGENTS.md), [approval and verification](../.planning/sudus-onboarding.md), [adoption review](../.planning/sudus-adoption-review.md). |

## Confirmed feature and proposed contract

**Documented confirmation:** Connect compiler-generated declarations to the existing DefMap/Semantic IR pipeline and prove `Post::query()` lookup with `Builder<Post>` inference. The developer confirmed this recommendation with “confirmed - recommendation.” The developer subsequently confirmed the detailed six-requirement contract with “ok”; MAC-001 through MAC-006 are Agreed 2026-10-04. [Plan](../.planning/rustdoc-macro-support.md:25), [Agreed contract](spec/engine-import.md).

**Unverified follow-up:** A real Suprnova application must demonstrate the same behavior before framework integration is claimed. The framework's `Model` trait supplies `query() -> Builder<Self>`; a macro-generated impl establishes applicability. [Local framework source](/home/shawn/workspace2/suprnova/framework/src/eloquent/model.rs:593), [framework trait notes](/home/shawn/workspace2/suprnova/framework/src/eloquent/model.rs:1).

## Mechanism preparation

| State | Finding | Evidence |
| --- | --- | --- |
| Exists | The explicit ProjectBuilder rustdoc input boundary validates compiler exports, but the baseline still fails generated method/type integration and accepts an unmappable target. | [builder](../crates/engine/project/src/indexing/builder.rs), [acceptance cases](../crates/engine/project/src/tests/rustdoc_import/mod.rs), [receipt and verification record](../.planning/sudus-onboarding.md). |
| Exists | The declared mechanism runs 11 semantic acceptance cases, with per-requirement outcomes and Linux exec tracing. All six requirements have reviewed failing receipts. A harmless rustdoc-named launch was detected and its mutation restored. | [driver](../tools/sudus-engine-import.py), [mechanism entry](../.sudus/mechanisms/rustdoc-engine-import.json), [verification record](../.planning/sudus-onboarding.md). |
| Exists | After preparation, 1,640 existing workspace tests passed; 11 new acceptance cases and two existing cases were skipped in that regression run. Full lint, standalone fixture formatting, Cargo deny, and codegen-check passed. | [regression summary](../target/agent-debug/runs/20261004T141110520Z-test-3347278-3ec2a7/summary.json), [verification record](../.planning/sudus-onboarding.md). |
| Unverified | Generated model lookup/inference, retained memory after ingestion, and framework application acceptance remain delivery work. | [failing mechanism receipts](../.planning/sudus-onboarding.md), [Agreed contract](spec/engine-import.md). |

## Application test checkout

| State | Finding | Evidence |
| --- | --- | --- |
| Documented | The developer requires the Suprnova checkout to stay strictly read-only and permits application testing on devlist.app. | [agreement](../AGENTS.md), [scope](spec/overview.md), [recorded developer instructions](../.planning/sudus-onboarding.md). |
| Exists | Devlist's manifest and lockfile pin Suprnova to Git revision 3229aa9af542c991196274fa3c235cdce88a68e2. Its source User model uses the model attribute and calls the Model trait's query method. | [application manifest](/home/shawn/workspace2/devlist.app/Cargo.toml:30), [application lockfile](/home/shawn/workspace2/devlist.app/Cargo.lock), [User model](/home/shawn/workspace2/devlist.app/src/models/user.rs:18), [query call](/home/shawn/workspace2/devlist.app/src/models/user.rs:52). |
| Unverified | Engine-import acceptance on the real Devlist User model has not run; it follows the minimal engine fixtures. | [roadmap](spec/roadmap.md), [verification record](../.planning/sudus-onboarding.md). |

## Devlist User follow-up, 2026-10-04

| State | Finding | Evidence |
| --- | --- | --- |
| Exists | MAC-001 through MAC-006 are delivered. The final receipt passes all six obligations; all three adversarial findings were fixed before Done. | `sudus wake`: Done; Done record `7ccb5de8ae9cd0586774b640dbb47cfe111d57b0`; final receipt `dc65e50b371fafdb382c032703a2167f4a57cad8`; source commit `3e3adaed`. |
| Exists | Final verification passed 1,657 workspace tests with two skipped and 17 focused import tests. Formatting, Clippy, Dylint, Cargo deny and codegen-check passed. Ripwire quality-delta and test-gate did not pass; their findings were retained and disclosed. | [Workspace summary](../target/agent-debug/runs/20261004T165941653Z-test-2268033-e6fd35/summary.json), [focused summary](../target/agent-debug/runs/20261004T170135100Z-test-2385925-111353/summary.json), [audit](../target/agent-debug/engine-import-final-audit.md). |
| Exists | Devlist's locked library export completed using nightly-2026-08-19 and its pinned Git framework. The manifest and lockfile stayed unchanged; the owned process group was verified empty. | [Compiler summary](../target/agent-debug/runs/20261004T171041553Z-devlist-export-2609033-9f3514/summary.json), [compiler driver](../target/agent-debug/devlist-export.py). |
| Exists | The genuine private/hidden-item export is format 61, 54,572,583 bytes, with 56,616 indexed items. Its SHA-256 is `ef261b876e75981043d9022cf30e1ba38999a012137b0a956676cad97cea8754`. | [Full export](../target/agent-debug/devlist-target/doc/directory.json); structured JSON and digest inspection. |
| Exists | Selecting `directory::models::user::User` fails with `rustdoc impl Id(47415) does not belong to directory::models::user::User`. The compiler attached valid `impl From<User> for user::Model`; the selected User occurs in the trait argument, not the self type. | `target/debug/rust-glancer inspect-rustdoc target/agent-debug/devlist-target/doc/directory.json --item directory::models::user::User` exits 1; export items 45836, 47415 and 47291; [self-owner check](../crates/engine/rustdoc/src/lib.rs:193). |
| Exists | User's generated `filter` parameters use `impl IntoColumn` and `impl IntoVal`. The compiler also emits synthetic generic parameters for these arguments; the lowerer rejects synthetic parameters before source reconciliation. | Raw User inherent impl signatures in the genuine export; [lowerer](../crates/engine/rustdoc/src/lowering/mod.rs:350). |
| Exists | EloquentModel assigns Key = i64 and Entity/Column to declarations generated inside the model's inner module. The import preparation requires referenced source identities and does not create those nominal declarations or modules. | Genuine export assignments and path summaries; [User source and re-exports](/home/shawn/workspace2/devlist.app/src/models/user.rs:18); [reference validation](../crates/engine/def-map/src/build/compiler/mod.rs:202). |
| Exists | The pinned framework's Model trait provides the default `query() -> Builder<Self>` and related entity/conversion bounds. Reading the Cargo Git source uses the application's exact dependency, without testing or changing the protected framework checkout. | [Pinned Model source](/home/shawn/.cargo/git/checkouts/suprnova-efd29c0c4437ef3f/3229aa9/framework/src/eloquent/model.rs:68), [query default](/home/shawn/.cargo/git/checkouts/suprnova-efd29c0c4437ef3f/3229aa9/framework/src/eloquent/model.rs:427). |
| Unverified | Real application import, query inference, generated reference reconciliation, and imported completed-indexing idle RSS have not passed acceptance. The successful compiler export and failing selection are recon evidence, not passing application acceptance. | [Agreed SUP contract](spec/suprnova-user.md), [completed MAC contract](spec/engine-import.md). |

The declared mechanism freezes the full compiler export and 24,979 source fingerprints in repository inputs. It verifies locked package/features, compiler, sysroot, target cfg, invocation, compiler overrides and Cargo configuration against that capture. The captured acceptance revision is the Agreed contract boundary.

## Devlist User mechanism preparation, 2026-10-04

The developer confirmed SUP-001 through SUP-006. The specification is Agreed;
final authorization to start `suprnova-user-acceptance` remains pending. The
working agreement and settings were preserved.

The reviewed negative receipt `922c03b8762b50d3584c47895764311d0f83dc8d` caught
an actual wrong-target acceptance mutation and a harmless rustdoc-named launch.
Both controls were restored. Restored receipt
`5f892d6822333b13c760a1902493b47d36e332f4` fails SUP-001/002/003/006 and leaves
SUP-004/005 unverified because valid import still cannot complete. Its eleven
owned process groups were verified empty. The source-only idle baseline is
295.47 MiB; imported idle memory remains unmeasured.

Preparation passed 1,657 workspace tests with three skipped, formatting, Clippy,
Dylint, Cargo deny and codegen-check. Nine observer integrity tests passed.
The additional configuration guard was verified against a fresh genuine capture
with an unchanged export digest; altered configuration was rejected and all five
capture process groups were verified empty. Ripwire's test gate flags Markdown
headings as untested; its quality delta covers only the documentation delta.
[Detailed evidence](../.planning/suprnova-user-mechanism.md).
