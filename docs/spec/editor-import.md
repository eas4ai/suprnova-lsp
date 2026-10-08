Prefix: EDT

# Explicit Rustdoc Inputs in the Editor

The developer selected LSP/VS Code wiring after Devlist User acceptance. This slice
exposes prepared compiler declarations through the existing editor lifecycle. It
does not generate exports, add a snapshot format, or schedule refresh work.

## Proposed configuration

`rust-glancer.rustdoc.inputs` is an optional array, empty by default. Each entry
names `workspaceRoot`, `manifestPath`, `targetName`, `targetKind`, `exportPath` and
`itemPath`. These same fields travel in `initializationOptions.rustdoc.inputs`.
The initial supported target kinds are `lib` and `bin`, matching analyzed Cargo
targets. A package's mixed library crate types still select its analyzed `lib`.

An absolute `workspaceRoot` selects one normalized Cargo workspace root. A relative
root is resolved against the sole editor workspace folder; a relative root with
zero or multiple workspace folders is rejected as ambiguous. Relative manifest
and export paths resolve against the selected Cargo workspace root. Inputs for a
different root are not forwarded to that engine. Within the selected workspace,
the existing importer validates the exact package manifest, target and item owner.
Changing settings or prepared export bytes requires restarting the server.

## Agreed obligations

[EDT-001] VS Code and the LSP initialization boundary MUST propagate every valid configured rustdoc input to its selected Cargo workspace engine, preserving manifest, target, export and item identity, and MUST report malformed entries instead of silently dropping them.
Falsifier: A configured input is lost or rewritten between extension settings and ProjectBuilder, a missing field or invalid target kind silently becomes source-only analysis, a relative path resolves against a different root, or an input reaches an unselected workspace engine.
Mechanism: rustdoc-editor-import
Rationale: Engine imports already exist; the missing consumer path is editor configuration through protocol and project construction.
Status: Agreed 2026-10-04

[EDT-002] With the provenance-checked Devlist User capture explicitly configured and Model in scope, ordinary LSP hover and inlay queries MUST expose Builder<User> for User::query(), User::without_global_scopes() and User::filter("email", "member@example.test") in faster-builds and lower-peak-memory construction.
Falsifier: Either mode yields an unknown or wrong result, the result appears only through an engine-only observer, deferred indexing is falsely reported complete, or a query stub substitutes for Suprnova's source-declared Model default method.
Mechanism: rustdoc-editor-import
Rationale: SUP-002 and SUP-003 prove engine identities; this requirement proves their ordinary editor-facing transport and rendering.
Status: Agreed 2026-10-04

[EDT-003] Ordinary LSP completion with Model in scope MUST offer query, without_global_scopes and filter for the configured User, preserve its source verify_password method, and uphold the existing package, target and owner isolation in both construction modes.
Falsifier: A required method is absent or duplicated, an unrelated same-name owner receives the imported API, or imported signatures displace the source verify_password result Result<bool, FrameworkError>.
Mechanism: rustdoc-editor-import
Rationale: Useful editor support requires completion as well as a successful inference query.
Status: Agreed 2026-10-04

[EDT-004] LSP startup MUST surface invalid selected exports as actionable workspace initialization errors without publishing a partially imported project; supported saved-body edits and manual reindexing MUST preserve the captured API, and failed saved-generation candidates MUST preserve the previous valid generation.
Falsifier: A missing file, unsupported format, duplicate selected input, wrong target or missing signature reference produces successful imported startup, an error lacks the responsible input or workspace context, a body-only save or reindex loses User's API, or a rejected saved candidate changes the previous generation's query result.
Mechanism: rustdoc-editor-import
Rationale: Reuse the existing transactional project boundary and observable LSP failure reporting.
Status: Agreed 2026-10-04

[EDT-005] Prepared-input editor import initialization and queries, with automatic generation disabled for that workspace, MUST complete without launching rust-analyzer or rustdoc, MUST retain lowered declaration facts rather than the full compiler JSON graph, and MUST report completed-indexing idle RSS for controlled LSP processes with and without the prepared inputs separately from indexing peaks.
Falsifier: A traced prepared-input import/query process with automatic generation disabled launches either tool, successful trace coverage is absent, the full compiler graph survives lowering in saved state, idle observations are absent or actually peaks, or compared runs differ in target, features, sysroot, residency, indexing preference or query workload without correcting the mismatch.
Mechanism: rustdoc-editor-import
Rationale: Preserve the repository's idle-memory priority and MAC-006 process boundary while adding the editor consumer.
Status: Agreed 2026-10-05

[EDT-006] With automatic generation disabled and no configured prepared inputs the LSP MUST preserve source-only behavior; with configured prepared inputs it MUST reuse its captured declarations for the session, suppress automatic generation for that workspace, and state in the prepared-input setting description that exports are prepared inputs, restart is required to reload them, and macro changes are not automatically synchronized in this mode.
Falsifier: An engine with automatic generation disabled and no prepared inputs acquires generated User APIs, a prepared-input workspace starts an automatic compiler job, deleting or changing its external export mutates an already captured generation during a body save or reindex, a fresh prepared-input startup accepts an invalid replacement, or prepared-input setting descriptions imply automatic compiler generation or freshness checking in that mode.
Mechanism: rustdoc-editor-import
Rationale: Explicit immutable input capture is the existing project model; refresh policy is separate work.
Status: Agreed 2026-10-05

## Observation and controls

The proposed mechanism combines extension setting/initialization tests, protocol
serialization and root-routing tests, genuine macro fixture LSP lifecycle tests,
and supervised real Devlist stdio LSP queries through `just agent-debug lsp-query`.
Application probes use unsaved source text and the recorded capture; they do not
edit Devlist source. A minimal invented model cannot replace real User acceptance.
Both indexing preferences wait for actual deferred completion before idle sampling.
Every requirement needs a demonstrated violating control before the mechanism is
trusted. A missing observation is not a pass. Trace only the controlled LSP-owned
process tree; compiler preparation is a completed separate step.

The confirmed AUT commitment revises EDT-005 and EDT-006 only to distinguish
prepared-input/source-only controls from automatic generation. Their completed
Agreed versions remain in the previous commitment's frozen contract; the two
revised blocks above were confirmed 2026-10-05. EDT-001 through EDT-004 remain
Agreed and unchanged.

MAC and SUP Agreed text stays unchanged and remains regression coverage. In
particular, MAC-006 says import and query "MUST work without starting a
rust-analyzer server or generating rustdoc during engine requests". SUP-002
requires the real User default query "in initial and batched construction".
The previous SUP scope excluded "editor settings"; EDT adds that consumer boundary.

## Cited change surface

| Boundary | Existing owner and expected change |
| --- | --- |
| Extension | `editors/code/package.json`, `src/config.ts` and `src/language-client/language-client-session.ts`: declare settings, validate entries and send initialization options. |
| Protocol | `crates/lsp/proto/src/config/mod.rs` and `analysis.rs`: parse and serialize explicit input configuration. |
| Workspace routing | `crates/lsp/server/src/config/mod.rs` and `engine_registry`: select inputs for the exact normalized Cargo root before engine startup. |
| Engine construction | `crates/lsp/engine/src/engine/project/config.rs` and `mod.rs:186`: translate inputs and use `ProjectBuilder::rustdoc_inputs`. |
| Saved lifecycle | `crates/engine/project/src/indexing/compiler/mod.rs:20` and LSP ProjectCoordinator: retain captured lowered facts and transactional publication. |
| Queries | Existing LSP hover, completion and inlay handlers: change only observed declaration gaps. |
| Acceptance | `crates/lsp/engine/src/tests`, `editors/code/test`, `tools/lsp-query.py:496` and bounded runner: test transport, failures, lifecycle, genuine queries and processes. |

Keep `/home/shawn/workspace2/suprnova` strictly read-only. Application testing uses
Devlist's pinned dependency and locked resolution; compiler/LSP artifacts belong
under this repository's `target/agent-debug/`. Extension tests run outside the
sandbox as AGENTS.md requires. No PR, packaging, release or rebranding is included.

The alternative is reduced snapshot caching first. Editor wiring is recommended
because it makes the proven engine API available to the actual consumer before
introducing a durable cache or compiler-process lifecycle. Automatic refresh,
production snapshot caching, Live/Inertia and general macro support remain later
work. No numerical RSS budget or export freshness guarantee is added here.
