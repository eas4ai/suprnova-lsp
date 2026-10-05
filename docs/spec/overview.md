# Suprnova LSP

Suprnova LSP is a Rust Glancer fork being specialized for the Suprnova framework. The immediate problem is making compiler-generated model APIs available to analysis without requiring a running rust-analyzer server. Low idle memory remains an architectural priority.

The inherited engine analyzes saved project generations and rebuilds individual dirty bodies for requests. Explicitly supplied rustdoc declarations now feed definition and semantic analysis, including generated methods and trait default methods. The minimal engine-import commitment is Done. See [the cited recon](../recon.md).

Devlist's real User acceptance is also Done. The developer selected explicit rustdoc input wiring through LSP/VS Code next; its EDT requirements are Agreed 2026-10-04. Automatic rustdoc generation, refresh scheduling, a rustdoc snapshot cache, full Rust macro semantics, and framework-wide Live/Inertia support remain later work.

## Specification map

| File | Prefix | Covers |
| --- | --- | --- |
| compiler-api.md | API | Observed compiler-export inspection and validation. |
| engine-import.md | MAC | Agreed requirements for generated model API analysis. |
| suprnova-user.md | SUP | Agreed application acceptance, generated references and idle-memory observations. |
| editor-import.md | EDT | Agreed explicit editor configuration, workspace routing and real LSP query acceptance. |

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
| Editor lifecycle | `crates/lsp` and `editors`; explicit input configuration is proposed next, automatic export refresh is later work. |
| Framework checks | Devlist User engine acceptance is Done; real editor queries are proposed next, Live and Inertia metadata remain later work. |

## Evidence

[Project ownership](../../crates/engine/project/src/lib.rs:1), [generated-item handoff](../../crates/engine/project/src/indexing/phases.rs:187), [CLI boundary](../../crates/rust-glancer/src/rustdoc/mod.rs:6), and [memory priority](../../AGENTS.md) establish the baseline. MAC-001 through MAC-006 closed with Done record `7ccb5de8ae9cd0586774b640dbb47cfe111d57b0`. SUP-001 through SUP-006 closed with Done record `88088a0c347ffbc3cf7e526dd763bd539a18d6a0`; its six final requirements and 1,671 workspace tests passed. The developer then selected the recommended LSP/VS Code wiring with “confirmed”. The subsequent “ok” confirms the EDT contract; mechanism preparation and final authorization follow.
