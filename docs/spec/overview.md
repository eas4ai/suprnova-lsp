# Suprnova LSP

Suprnova LSP is a Rust Glancer fork being specialized for the Suprnova framework. The immediate problem is making compiler-generated model APIs available to analysis without requiring a running rust-analyzer server. Low idle memory remains an architectural priority.

The inherited engine analyzes saved project generations and rebuilds individual dirty bodies for requests. Explicitly supplied rustdoc declarations now feed definition and semantic analysis, including generated methods and trait default methods. The minimal engine-import commitment is Done. See [the cited recon](../recon.md).

Devlist's real User acceptance, explicit LSP/VS Code input wiring, the automatic debounced worker and the independent Suprnova LSP identities are Done. Automatic mode discovers models and targets, with a two-second debounce. The developer requested a specification and plans for hover responsiveness during indexing; [the RSP contract](responsiveness.md) is Agreed 2026-10-06 and begins with measurement. A reduced snapshot cache, full Rust macro semantics, and framework-wide Live/Inertia support remain later work.

## Specification map

| File | Prefix | Covers |
| --- | --- | --- |
| compiler-api.md | API | Observed compiler-export inspection and validation. |
| engine-import.md | MAC | Agreed requirements for generated model API analysis. |
| suprnova-user.md | SUP | Agreed application acceptance, generated references and idle-memory observations. |
| editor-import.md | EDT | Agreed explicit editor configuration, workspace routing and real LSP query acceptance. |
| automatic-rustdoc.md | AUT | Agreed automatic discovery, debounced compiler worker, fresh project publication and consumer acceptance. |
| identity.md | IDN | Agreed independent extension/server identities and collision-free local packaging. |
| responsiveness.md | RSP | Agreed measured hover latency during indexing, ordering/cancellation safety and comparable idle-memory evidence. |
| local-runners.md | CIR | Agreed repository-scoped local runners, trusted workflow routing and actual three-platform job acceptance. |

Observed blocks describe inspected code and are not contract. Draft blocks propose the next behavior; none becomes Agreed without the developer's confirmation.

## Checkout boundaries and later work

The framework checkout `/home/shawn/workspace2/suprnova` is strictly read-only:
no source, Git, lockfile, cache, or build-output changes. The developer permits
application testing on `/home/shawn/workspace2/devlist.app`. Use that application's
pinned framework dependency for application acceptance, with locked dependency
resolution and LSP/compiler artifacts under this repository's `target/agent-debug/`.
Keep LSP implementation changes here. Direct testing against the protected
framework checkout is outside the authorized scope.

| Area | Existing owner and boundary |
| --- | --- |
| General Rust analysis | `crates/engine/analysis`, `body-ir`, and `ty`; preserve existing behavior while adding imported declarations. |
| Package storage | `crates/engine/project/src/storage`; avoid unkeyed reuse of imported payloads, defer a new rustdoc snapshot format. |
| Editor lifecycle | `crates/lsp` and `editors`; explicit inputs, automatic exports and independent fork identities are Done; measured hover responsiveness is the prepared next commitment. |
| Framework checks | Devlist User engine and real editor acceptance are Done; Live and Inertia metadata remain later work. |

## Evidence

[Project ownership](../../crates/engine/project/src/lib.rs:1), [generated-item handoff](../../crates/engine/project/src/indexing/phases.rs:187), [CLI boundary](../../crates/rust-glancer/src/rustdoc/mod.rs:6), and [memory priority](../../AGENTS.md) establish the baseline. MAC-001 through MAC-006 closed with Done record `7ccb5de8ae9cd0586774b640dbb47cfe111d57b0`. SUP-001 through SUP-006 closed with Done record `88088a0c347ffbc3cf7e526dd763bd539a18d6a0`. EDT-001 through EDT-006 closed with Done record `ec0b18211f3637882fea3bfb482706415a3664cd`, with all six requirements, 1,679 Rust tests and all four editor E2E flows passing. AUT-001 through AUT-009 and revised EDT-005/006 closed with Done record `c2422443e63e78e192ea9427de4d9853ae5bbdfb`: current AUT receipt `9332dd1e8d45c5d48e135f30f5dff001ff5b9c49` and EDT receipt `3f1264031b28184b5e9bde47a1b87164b59fbb79` pass; both Critical adversary findings were fixed. The IDN commitment is Done; the new RSP contract is Agreed 2026-10-06.
