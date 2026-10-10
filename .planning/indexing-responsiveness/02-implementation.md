# Plan 02 — Implement the Measured Fix

Status: Approved 2026-10-06; diagnostics support branch B, production execution pending. Depends on Plan 01. Requirements: RSP-002 through RSP-006.

Run only the branch or combination justified by the trace. Begin a Sudus lease
before declared-input edits, add a test exposing the actual failure, commit the
fix, end the exact lease, and check the affected requirement. Do not change the
Agreed target because the first implementation misses it.

## Common safeguards

Keep `ProjectCoordinator` as the saved-generation owner and retain immutable
editor document snapshots. A later hover cannot jump an earlier relevant save,
reindex, graph mutation, or stale-source recovery. Generation checks apply to
every resumed/published unit. Do not publish partially valid declarations or
report background completion before all current products are installed.

Reuse existing cancellation tokens, request responders, and deferred priorities.
Look for existing helpers before adding one. Use `mod.rs` for a new multi-file
module. Preserve package/target interpretations of files and applicability through
Suprnova's actual trait declarations.

## A. Reduce engine queue blocking

Owners: `crates/lsp/engine/src/engine/{dispatcher.rs,command.rs,mod.rs}`,
`engine/project/{mod.rs,deferred.rs,state.rs}`, and
`crates/engine/project/src/indexing/split/` if measured publication work lives there.

1. Identify the exact longest command from the trace. Background products are
   already delivered incrementally; measure their actual granularity first.
2. Split expensive preparation/publication into bounded, generation-checked units
   only where the project remains coherent between units. Prepare independent
   products outside the semantic lane where the existing ownership model permits.
3. Service waiting interactive work between safe background units. Preserve FIFO
   semantics across saved-state mutations; a blanket "hover first" policy is unsafe.
4. Bound pending products and ensure background progress under sustained hovers.
   Priorities must neither duplicate requests nor starve saves or indexing.
5. Add deterministic barriers to reproduce a hover arriving behind background
   work, a save preceding that hover, stale completion, and continuing progress.

Rustdoc project construction already runs outside the semantic lane. Its final
publication performs producer/disk/generation validation. Optimize a measured
scan only with an equally strong freshness proof, including disk changes whose
watcher notifications have not arrived; AUT-005 may not be weakened.

If the synchronous unit cannot safely be divided without a new ownership model,
present that architectural choice with trace evidence before implementing it.

## B. Reduce query-time saved-body preparation

Owners: `crates/lsp/engine/src/engine/query/mod.rs`, project materialization,
`crates/engine/project/src/indexing/split/{mod.rs,build.rs}`, and the existing
current-body selection/preparation owners reached from those functions.

1. Verify which file/crate/body facts are built for the measured saved-file hover.
   Existing `AnalysisSurface::Files` limits files; it does not prove body-granular work.
2. Reuse selected current-body analysis concepts to prepare only the hovered body
   and required dependencies if equivalent saved semantics can be maintained.
3. Keep sibling Cargo interpretations, local declarations, trait applicability,
   documentation and link coordinates correct. Falling back to broader analysis
   is acceptable for unsupported shapes only if measured acceptance still passes.
4. Test saved and unsaved forms of the same query, nested/body-local declarations,
   multiple crate contexts, genuine model methods, and cancellation mid-build.
5. Record the work actually avoided and replay the unchanged latency workload.

Do not introduce a durable body/query cache or retain whole-project snapshots as
a shortcut. If caching becomes necessary, it is a separate measured decision with
explicit version keys, eviction and retained-memory evidence.

### Measured starting points

The automatic-disabled Devlist control spent 729 ms materializing saved file
bodies before its first source hover. The same method with a body-neutral unsaved
newline took 206–300 ms, even though only one body was selected. These observations
support preparation work; their queue waits were 0–2 ms. They are small diagnostics,
not the full RSP acceptance series.

Investigate whether a hover on a declaration's name can use the already-published
declaration without preparing its expression body. Verify saved/current coordinates,
changed headers, enclosing impl generics, documentation links and body-local names
before choosing that route. Expression and binding hovers still need genuine body
facts; a declaration shortcut cannot establish the generated-variable target.

For current bodies, mechanical lowering measured only 108–128 microseconds in the
inner trace. Pattern bindings took 49–59 ms and body resolution 69–76 ms. The much
larger owner-to-lowering interval includes saved nested-body indexing and visible
item lookup. Time those two operations independently before changing them. Inspect
existing request-scoped lookup reuse and loading boundaries; preserve visibility,
language-item precedence, cancellation and release without a persistent cache.

Inner trace artifact: `20261007T002413229Z-rsp-diagnostic-2232792-4c3a52`. It adds
`rg_body_ir::build::current=trace` to the diagnostic logging policy; keep that
difference separate from the fixed logging used for final latency comparisons.

## C. Reduce cleanup delays between requests

Owners: `crates/lsp/engine/src/engine/query/lifecycle.rs` and existing project
memory release/allocator control.

1. Confirm that post-response release or purge, rather than inference, dominates
   the next request's delay.
2. Preserve immediate release of request-owned semantic payloads. Consider a
   bounded allocator-purge cadence at idle or between bounded bursts only if the
   trace establishes purge overhead; do not move mutable-project cleanup onto an
   unsynchronised thread.
3. Verify eventual settled release, no retained query snapshots, cancellation,
   and idle RSS under the same residency/workload settings.

## Verification and exit gate

Use the bounded focused test shape, substituting real discovered test filters:

```sh
just agent-debug --timeout 90s test -p rg_lsp_engine <focused-filter>
just agent-debug --timeout 90s test -p rg_project <focused-filter>
```

Place concurrency tests beside existing engine/project and query-lifecycle tests;
use barriers instead of timing assumptions. Repeat Plan 01's workload after each
meaningful change. Inspect Ripwire dependencies before edits and use edit-check,
quality-delta and test-gate alongside the actual project checks.

Exit when the measured bottleneck improves, required semantics/order/cancellation
tests pass, and the observer's full latency series meets RSP-002. A selected branch
is not complete because a microbenchmark or one fast hover passes. If the target
remains unmet, inspect the next trace and follow Sudus's attempt/escalation rules.
