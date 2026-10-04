# Suprnova LSP

Suprnova LSP is a Rust Glancer fork being specialized for the Suprnova framework. The immediate problem is making compiler-generated model APIs available to analysis without requiring a running rust-analyzer server. Low idle memory remains an architectural priority.

The inherited engine analyzes saved project generations and rebuilds individual dirty bodies for requests. The implemented rustdoc boundary inspects compiler declarations; it does not yet feed them into engine queries. See [the cited recon](../recon.md).

The first commitment integrates explicitly supplied exports with engine analysis. It does not deliver automatic rustdoc generation, refresh scheduling, a rustdoc snapshot cache, full Rust macro semantics, or framework-wide Live/Inertia support. Those remain later work in [the implementation plan](../../.planning/rustdoc-macro-support.md).

## Specification map

| File | Prefix | Covers |
| --- | --- | --- |
| compiler-api.md | API | Observed compiler-export inspection and validation. |
| engine-import.md | MAC | Agreed requirements for generated model API analysis. |

Observed blocks describe inspected code and are not contract. Draft blocks propose the next behavior; none becomes Agreed without the developer's confirmation.

## Areas outside the first commitment

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
| Editor lifecycle | `crates/lsp` and `editors`; editor configuration and automatic export refresh are later work. |
| Framework checks | Suprnova Live and Inertia metadata; a real application acceptance case follows the minimal engine fixture. |

## Evidence

[Project ownership](../../crates/engine/project/src/lib.rs:1), [generated-item handoff](../../crates/engine/project/src/indexing/phases.rs:187), [CLI boundary](../../crates/rust-glancer/src/rustdoc/mod.rs:6), and [memory priority](../../AGENTS.md) establish the baseline. The developer confirmed the engine-import recommendation in this session; the six MAC obligations are Agreed as of 2026-10-04; final Sudus authorization remains pending.
