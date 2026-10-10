Prefix: AUT

# Automatic Rustdoc Export Worker

The developer requested automatic rustdoc JSON export in a worker, added
"debounced", and selected automatic model and target discovery. This proposal
adds a compiler producer and live publication to the completed import pipeline.
It does not require a developer to prepare an export or list each model.

## Proposed behavior

Automatic generation is enabled by default for workspace member packages whose
resolved dependency graph contains Suprnova. Discover their analyzed library and
binary targets through Cargo, export each relevant target, then select local
nominal model owners from compiler-established Suprnova trait implementations.
The captured User export identifies `suprnova::eloquent::EloquentModel` and
`suprnova::eloquent::model::Model`; verify these identities against the resolved
framework dependency. An attribute's short spelling or an attached reverse
conversion alone does not identify a model owner.

Existing explicitly configured prepared inputs take precedence for their routed
workspace: that workspace keeps prepared-input mode with no automatic compiler
job. Other workspaces remain independent. Disabling automatic generation with
no prepared inputs preserves source-only behavior. Non-Suprnova workspaces do not
start export jobs. This changes EDT-005 and EDT-006's unqualified mode assumptions;
their proposed revisions retain the completed prepared-input acceptance.

Proposed settings under `rust-glancer.rustdoc.automatic` are `enabled` (true),
`debounceMs` (2000), `toolchain` (`nightly-2026-08-19`), `timeoutMs` (900000),
`jobs` (2), and optional `artifactRoot`. Initialization options carry the same
values. Reject malformed values; delays, deadlines and job counts are positive,
bounded integers. Setting changes require a server restart. The default producer
matches the reader's format 61; overrides still require compatible exports. A
missing producer produces an actionable failure, without automatic installation.

Startup schedules one background pass once source analysis is available. Saved
Rust files, manifests, lockfiles, Cargo configuration and toolchain files within
the watched workspace schedule replacement work after the trailing quiet period.
Every new relevant change resets that period. Unsaved typing does not compile.
A quiet interval does not prevent later disk changes; candidate identity checks
and ordered publication must still reject work made obsolete during compilation.
Manual reindex also requests a new pass. The first slice conservatively refreshes
the affected workspace's selected targets, including additions and removals.

One export command runs at a time across this LSP session's workspaces; Cargo uses
the configured finite job count. A running job made obsolete by a change is
cancelled and its owned descendants are drained before another export starts.
Keep only the latest pending request for each workspace. Capture producer,
package, target, feature, graph and watched-source identity; revalidate that
identity before publication. Workers return immutable candidates to the engine's
ordered publication boundary and never mutate its published project directly.

Compiler outputs use an LSP-owned directory, separate from ordinary Cargo output;
the default is below the selected workspace's `target/rust-glancer/rustdoc/`.
Acceptance overrides `artifactRoot` to this repository's `target/agent-debug/`.
Use locked dependency resolution and do not rewrite source or lockfiles. Cleanup
is limited to the worker's owned artifacts, never the configured parent directory
or unrelated files. Full
exports and lowered candidates from obsolete or failed passes are never reused
as current data. Cargo may reuse its own isolated build artifacts; this proposal
adds no cross-session reduced snapshot cache.

While work is pending or fails, preserve the last coherent published generation
and expose pending/stale/failed generated-API state. Source queries follow the
existing saved-generation and dirty-buffer rules. The worker does not promise
freshness for arbitrary external files, environment changes or unobserved macro
inputs. Settings describe the watched boundary. A failure ends the attempt; a
new relevant change or reindex can retry it, without an automatic failure loop.

## Agreed obligations

[AUT-001] Default automatic mode MUST discover Suprnova workspace member library and binary targets and their compiler-established local model owners without configured item paths or export files, preserving package, target and owner identity; prepared-input mode, disabled automatic mode and non-Suprnova workspaces MUST retain their stated behavior.
Falsifier: Devlist User requires a supplied export or model list, a second model is omitted, a renamed framework dependency prevents discovery, an unrelated same-name trait or reverse conversion selects the wrong owner, target/package boundaries leak, or prepared, disabled or non-Suprnova controls unexpectedly start a compiler.
Mechanism: rustdoc-automatic-worker
Rationale: The developer explicitly selected automatic model and target discovery.
Status: Agreed 2026-10-05

[AUT-002] The worker MUST schedule startup generation and coalesce relevant saved-input changes with trailing-edge debouncing, retaining only the latest pending request per workspace and excluding unsaved typing, duplicate unchanged events and its own output writes.
Falsifier: No startup pass occurs, a save or watched Rust/manifest/lock/config/toolchain change is missed, a burst starts a job before its final quiet period or starts duplicate replacement jobs, output writes create a refresh loop, or unsaved edits or ordinary queries launch compilation.
Mechanism: rustdoc-automatic-worker
Rationale: Debouncing applies to editor saves and external saved changes, not only one notification source.
Status: Agreed 2026-10-05

[AUT-003] Automatic exports MUST use a compatible explicitly selected producer, private and hidden item coverage, locked package resolution, and the selected workspace's Cargo target and feature configuration, writing only isolated managed compiler artifacts before existing export validation and lowering.
Falsifier: The worker uses another target/features/package, accepts an incompatible schema or missing private coverage, rewrites source or a lockfile, deletes unowned files during cleanup, uses ordinary Cargo output as a mutable publication artifact, silently substitutes a producer, installs a toolchain, or accepts an invalid generated signature reference.
Mechanism: rustdoc-automatic-worker
Rationale: A successful compiler exit alone does not establish an importable, correctly identified API.
Status: Agreed 2026-10-05

[AUT-004] Compiler execution MUST run outside the synchronous analysis lane with at most one active automatic export command per LSP session, a finite Cargo job count and deadline, and bounded cancellation and shutdown that terminate and reap the owned compiler process tree before replacement work starts.
Falsifier: A deliberately held compiler blocks a source query that otherwise completes, two automatic exports overlap across roots, descendant compilers survive supersession/timeout/shutdown, a replacement starts before cleanup, stderr/stdout is unbounded in memory, or compiler output corrupts the LSP stream.
Mechanism: rustdoc-automatic-worker
Rationale: A debounce timer and cancellation token do not by themselves supervise Cargo's children.
Status: Agreed 2026-10-05

[AUT-005] Automatic candidates MUST be tied to captured producer, workspace, target, feature, graph and watched-input identities and MUST be rejected if those identities change before publication, even when notifications or worker completions arrive late or out of order.
Falsifier: An old export publishes after a newer save/configuration/graph change, disk changes during compilation without an arrived notification yet the candidate is accepted as current, package/target slots are replayed into a different graph, or a late failure overwrites a newer successful worker state.
Mechanism: rustdoc-automatic-worker
Rationale: The compiler reads mutable disk; enqueue order alone cannot prove it exported one coherent saved state.
Status: Agreed 2026-10-05

[AUT-006] A successful current pass MUST validate and atomically publish a coherent project with fresh lowered model facts, refresh ordinary editor queries without restarting the server, and correctly rediscover model and target additions or removals; failed or superseded candidates MUST preserve the previous valid generation.
Falsifier: A genuine generated API change requires a restart, new model/target discovery fails, a removed model retains current generated declarations, a partial set of candidate imports is published, rejected work changes the last valid query result, or publication bypasses generation/deferred-indexing checks.
Mechanism: rustdoc-automatic-worker
Rationale: Producing JSON without updating the running analysis project would not deliver automatic editor support.
Status: Agreed 2026-10-05

[AUT-007] The editor-facing worker state MUST distinguish pending, current, stale and failed generated APIs with workspace/target context and actionable errors, retain usable prior analysis under the existing source rules, and permit a later save or reindex to recover without an unbounded retry loop.
Falsifier: Missing toolchain, compiler failure, unsupported format, timeout or rejected publication is reported as fresh success, its context is absent, prior coherent analysis is destroyed, a later valid pass cannot recover, or repeated failures compile again without a new trigger.
Mechanism: rustdoc-automatic-worker
Rationale: Source readiness and generated-API freshness are different observable states.
Status: Agreed 2026-10-05

[AUT-008] Genuine automatic Devlist acceptance MUST resolve User query, without_global_scopes and filter as Builder<User> through ordinary LSP hover, inlay and completion in both indexing preferences, preserve verify_password and owner isolation, and demonstrate live generated-API changes with a genuine compiler/macro fixture.
Falsifier: Either mode requires manually supplied JSON, returns unknown/wrong types or missing/duplicate methods, substitutes a Model query stub, loses source methods or isolation, or a saved genuine macro change never reaches ordinary LSP queries in the existing session.
Mechanism: rustdoc-automatic-worker
Rationale: Existing capture acceptance cannot prove the newly automatic producer or refresh path.
Status: Agreed 2026-10-05

[AUT-009] Automatic acceptance MUST attribute rustdoc/compiler launches to worker preparation, observe no rust-analyzer process, release complete compiler JSON and obsolete job payloads after lowering, and report comparable settled LSP idle RSS separately from compiler and indexing peaks with verified owned-process cleanup.
Falsifier: rust-analyzer launches, queries themselves launch the producer, complete JSON or obsolete candidates remain resident at idle, compiler descendants survive, or paired idle observations are missing, premature, actually peaks or use different target/features/residency/query workloads without correction.
Mechanism: rustdoc-automatic-worker
Rationale: Compiler bursts are allowed; the repository's priority remains retained idle memory.
Status: Agreed 2026-10-05

## Mechanism proposal and failing controls

`rustdoc-automatic-worker` will combine deterministic scheduling and fake-child
supervision tests, compiler-derived discovery/identity tests, generation-race and
rollback tests, genuine worker-produced Devlist stdio queries, a real macro-edit
fixture, editor transport/status checks, process tracing and supervised memory
observations. Fake commands observe lifecycle behavior only; they cannot replace
genuine compiler exports or framework query semantics in AUT-008.

For AUT-001 omit an owner or misroute a same-name control; for AUT-002 disable the
quiet timer or retrigger outputs; for AUT-003 alter producer/configuration or a
retained reference; for AUT-004 hold a child and omit descendant cleanup; for
AUT-005 allow a deliberately stale completion; for AUT-006 expose a partially
failed candidate or suppress live replacement; for AUT-007 falsely report fresh
after a failed job; for AUT-008 withhold automatic declarations; for AUT-009 keep
the full graph or remove an idle/trace/cleanup observation. Each must produce a
recorded failing receipt before the mechanism is trusted. Missing observations,
skipped cases and absent child processes are not successful controls.

Existing MAC/SUP mechanisms remain prepared-input regressions. EDT's revised
prepared/source-only controls explicitly disable automatic generation where
needed; AUT proves that enabling it is the default Suprnova consumer behavior.
Linux process/RSS evidence establishes the observed platform; other platforms
must not be claimed verified by that evidence.

## Cited change surface

| Boundary | Existing owner and expected work |
| --- | --- |
| Settings and routing | `editors/code/src/config.ts:36`, `package.json`, `crates/lsp/proto/src/config/rustdoc.rs`, `crates/lsp/server/src/config/mod.rs`: typed automatic policy, validation, root-specific prepared-input precedence. |
| Worker and debounce | `crates/lsp/engine/src/debounce.rs:20`, `diagnostics/mod.rs:46`, `diagnostics/command.rs:21`, `service/mod.rs:56`, server engine registry: reuse relevant patterns and add global export serialization, subprocess ownership and lifecycle triggers. |
| Watched inputs | `crates/lsp/server/src/project_watcher.rs:37` and `:392`, ordered save ingress: coalesce saves/external changes and cover Cargo/toolchain configuration without observing managed output as input. |
| Producer identity | `crates/engine/workspace/src/cargo.rs:20` and `:83`, real `devlist-user/producer.json`: selected target/features/package, compatible producer and pre/post input checks. |
| Model selection | `crates/engine/rustdoc/src/lib.rs:150` and `:211`: validated compiler-derived model discovery and reuse of exact owner/signature lowering. |
| Project publication | `crates/lsp/engine/src/engine/command.rs:32`, `project/mod.rs:97`, `project/deferred.rs:253`, `crates/engine/project/src/indexing/compiler/mod.rs:31` and `change/workspace.rs:15`: fresh imports, graph-safe construction and ordered atomic candidate publication. |
| Status and queries | Service notifications, server client status and extension status: report worker freshness and errors; preserve ordinary hover/completion/inlay handlers. |
| Acceptance | `tools/sudus-editor-import.py`, `tools/lsp-query.py`, `tools/agent-debug.py`, LSP and extension tests: genuine automatic exports, deterministic races, trace/cleanup, live queries and idle observations. |

The alternative is automatic compilation for explicitly listed model/target
entries first. The developer selected automatic discovery instead, so that
configuration shortcut is not this proposal. Snapshot caching, arbitrary macro
execution semantics, Live/Inertia, release work and a numerical RSS budget remain
outside this commitment.

Keep `/home/shawn/workspace2/suprnova` strictly read-only. Devlist acceptance uses
its pinned framework dependency and locked/offline resolution, with every
compiler/LSP artifact under this repository's `target/agent-debug/`. Changes for
macro-refresh tests belong in owned fixtures; no permanent Devlist source change
is required.
