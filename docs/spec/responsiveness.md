Prefix: RSP

# Hover Responsiveness During Indexing

This contract addresses the developer's report that hover shows "Loading…" while
indexing. The developer requested a specification and plans for measuring the
delay and reducing it without sacrificing generated-model correctness or low
idle memory. The commitment is `indexing-responsiveness`.

## Scope and measurement boundary

Keep the existing single owner of the saved project generation. Investigate
queue wait, query-time Body IR preparation, background-product publication,
candidate validation, and request cleanup before selecting an implementation.
Background indexing, open-document package priority, and cancellation already
exist. A priority queue alone cannot interrupt a running synchronous operation.

An **eligible hover** targets a known symbol in an open document whose required
declarations are already available in a coherent published generation. Record
that availability independently of the hover result, before sending the request;
do not determine eligibility retrospectively from a fast or successful response.
Body IR need not be complete. Generated User queries become eligible only when
the genuine imported declarations have been published.

Latency is monotonic elapsed time from writing the real stdio LSP request to
receiving its full response. Queue time, preparation, inference, rendering, and
transport all count. An error, timeout, empty result for an expected symbol, or
wrong result fails the semantic observation; it is never discarded to improve a
percentile. Deliberate cancellation and unavailable-declaration controls are
separate cases with their own recorded outcomes.

Use two cohorts: first hover after each fresh server session, and subsequent
hovers within that session. Separate source and generated-model symbols, indexing
preferences, and active-background versus settled windows. Do not pool their
percentiles. Each required first-hover series contains at least 20 independent
sessions; each repeated-hover series contains at least 100 requests. Compute p95
by nearest rank, `sorted_ns[ceil(0.95 * count) - 1]`, without rounding before
comparison. Record first response, p50, p95, maximum, failures, and raw samples.

Measure Devlist with its pinned Suprnova dependency in `faster-builds` and
`lower-peak-memory`. At minimum cover source hovers during active automatic
export work, generated User hovers with published declarations while subsequent
background work is active, and both symbol groups after settling. Also exercise
source and generated hovers before deferred Body IR finishes in `faster-builds`.
`lower-peak-memory` finishes bodies before initial publication: record that
initial deferred-body window as structurally absent, and still require its
automatic-worker and settled windows. Never manufacture a passed overlap case
from requests sent only after background work finished. Where genuine Devlist
overlap is too short, use additional sessions and retain event evidence; controlled
fixtures supplement concurrency coverage rather than replacing application data.

Report startup-to-declaration availability, compiler/export time, and hovers sent
before availability separately. The 200 ms goal does not claim instantaneous
startup or instantaneous macro expansion. Cold and warm LSP caches are separate
experiments with disclosed compiler-cache state; never erase unrelated caches.

## Agreed obligations

[RSP-001] Acceptance MUST preserve a reproducible before/after Devlist workload and raw request timings, distinguish declaration readiness from background completion, and attribute slow hovers to measured initialization, queue, preparation, publication, cleanup or analysis work before selecting a production change.
Falsifier: A baseline or stage observation is absent, the compared build/configuration/application differs without correction, background overlap is asserted without matching events, timings start only after dispatch, or the chosen change has no supporting measured delay.
Mechanism: suprnova-indexing-responsiveness
Rationale: Existing queue and execution logs permit a causal investigation rather than an assumed scheduling rewrite.
Status: Agreed 2026-10-06

[RSP-002] Eligible real Devlist hover responses MUST have p95 below 200 ms in every required first-hover and repeated-hover series defined above, preserving correct source and compiler-derived types while background work is active and after it settles.
Falsifier: Any required series is missing, undersampled, has p95 at or above 200 ms, mixes cohorts or modes, silently drops failures or slow samples, returns an empty or incorrect result to meet the target, or contains no observed active-background requests where overlap is required.
Mechanism: suprnova-indexing-responsiveness
Rationale: Fast repeated hovers must not hide the initial response that motivated this feature.
Status: Agreed 2026-10-06

[RSP-003] Scheduling and narrower analysis MUST preserve captured document revisions, saved-change ordering, package/target/owner identity, and atomic generation publication; obsolete body products and rustdoc candidates MUST remain rejected.
Falsifier: A hover overtakes an earlier relevant saved mutation, a stale revision or generation becomes a current response, a partially built project is visible, a late candidate replaces newer analysis, an owner control acquires User's methods, or genuine User/source signatures regress.
Mechanism: suprnova-indexing-responsiveness
Rationale: The FIFO lane currently enforces safety that any scheduling change must preserve explicitly.
Status: Agreed 2026-10-06

[RSP-004] Queued and running hover cancellation MUST release request-owned work, suppress obsolete successful responses, and allow a subsequent valid hover to complete; bounded background work MUST continue making progress under sustained interactive requests.
Falsifier: A cancelled request still performs its next controlled analysis unit or publishes success, following work remains blocked beyond the bounded test deadline, continuous hovers prevent all background progress, unfinished work is falsely reported complete, or shutdown leaves owned jobs running.
Mechanism: suprnova-indexing-responsiveness
Rationale: Responsiveness includes cancellation, progress and cleanup, not only a favourable latency percentile.
Status: Agreed 2026-10-06

[RSP-005] Interactive handlers MUST preserve genuine typed query results and actionable errors; pending indexing presentation MUST NOT fabricate results, and ordinary queries MUST NOT launch rustdoc or rust-analyzer, lengthen RPC deadlines as a latency fix, or introduce automatic retry loops.
Falsifier: A result is replaced with an empty success or guessed type because indexing is active, an internal error is hidden, a hover starts a compiler, rust-analyzer is required, a larger transport deadline substitutes for meeting RSP-002, or a query retries indefinitely.
Mechanism: suprnova-indexing-responsiveness
Rationale: The current 30-second analysis deadline prevents premature transport failure but does not make hover responsive.
Status: Agreed 2026-10-06

[RSP-006] Acceptance MUST preserve existing residency and request-release rules, retain no new long-lived whole-project or full compiler-JSON snapshots, and report matched settled server-plus-engine idle RSS before and after the change, separately from compiler/indexing peaks, with verified owned-process cleanup and external checkout boundaries.
Falsifier: Idle/cleanup evidence is absent or premature, a new whole-project/query snapshot or complete export graph remains retained, measurement conditions differ without correction, unbounded pending payloads accumulate, ordinary application outputs or lockfiles change, or the protected framework checkout is modified.
Mechanism: suprnova-indexing-responsiveness
Rationale: There is no numerical RSS budget before the baseline; a repeatable retained-memory increase must be investigated and presented before acceptance, not hidden in peak measurements.
Status: Agreed 2026-10-06

## Observation and violating controls

The proposed `suprnova-indexing-responsiveness` mechanism combines an extended
bounded stdio observer, real Devlist results, deterministic engine race tests,
editor transport checks, process observations, memory samples, and report-integrity
tests. It is proposed, not implemented, declared or reviewed yet. Each RSP result
must include its own evidence. Missing or skipped observations are unverified.

| Requirement | Observation                                                                                                                     | Demonstrated failing control before trust                                                                                                                  |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RSP-001     | Frozen workload/identity manifest, raw lifecycle timeline and stage durations, baseline diagnosis.                              | Delete readiness/queue evidence or substitute a different lockfile fingerprint.                                                                            |
| RSP-002     | Real transport timings and expected hover content, separately scored cohorts/windows/modes.                                     | Add a controlled delay exceeding 200 ms to eligible real responses; separately remove slow samples and require the integrity checker to reject the report. |
| RSP-003     | Save/edit/publication races and existing genuine User/owner assertions.                                                         | Permit a stale-generation product or bypass the document-revision check.                                                                                   |
| RSP-004     | Barrier-controlled queued/running cancellation, continuing background progress under a finite managed stream, shutdown cleanup. | Ignore cancellation or stop publishing background progress while queries continue.                                                                         |
| RSP-005     | Pending/failed export controls, genuine error results, process-launch trace and deadline/retry inspection.                      | Fabricate empty success, invoke a producer from hover, or replace the query deadline with a larger value.                                                  |
| RSP-006     | Three matched baseline/change RSS pairs after all work settles; retained-state inspection and supervised cleanup.               | Retain a whole-project snapshot or remove an idle/cleanup observation.                                                                                     |

Controls must fail because of their stated violation. A build error, tool failure,
missing fixture, or unavailable platform is not proof of violation detection.
Use deterministic unit barriers for ordering; do not make wall-clock unit tests
depend on an arbitrarily slow development machine.

## Relationship to the existing contract

No Agreed requirement is revised. MAC-006 requires queries to work "without
starting a rust-analyzer server or generating rustdoc during engine requests".
AUT-005 requires rejecting candidates when captured identities change "even when
notifications or worker completions arrive late or out of order". AUT-006 requires
"atomically publish a coherent project". EDT-002/003 and AUT-008 retain the genuine
generated User hover, inlay, completion and source-method obligations. SUP-006 and
AUT-009 retain completed-indexing idle-memory and process observations.

The captured `hover-response-latency` item matches this proposal. The separately
captured `indexing-query-deadlines` item is not declared delivered: reproducing and
changing retryable transport-error classification beyond these hover cases stays
separate unless the trace proves it necessary and its scope is agreed.

## Cited change surface

| Owner                 | Evidence and possible work                                                                                                                                                                                                                                                                                                         |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Request ingress       | [Hover handler](../../crates/lsp/server/src/methods/text_document/hover.rs), [RPC wrapper](../../crates/lsp/server/src/engine_client/mod.rs): measure transport and readiness; preserve typed result/cancellation policy.                                                                                                          |
| Engine ordering       | [Dispatcher](../../crates/lsp/engine/src/engine/dispatcher.rs), [command definitions](../../crates/lsp/engine/src/engine/command.rs): single-owner ordering and, only if measured necessary, bounded scheduling between safe operations.                                                                                           |
| Request preparation   | [Document analysis](../../crates/lsp/engine/src/engine/query/mod.rs), [lifecycle](../../crates/lsp/engine/src/engine/query/lifecycle.rs): saved-file materialization, selected current bodies, timings and post-response cleanup.                                                                                                  |
| Deferred bodies       | [Split indexing](../../crates/engine/project/src/indexing/split/mod.rs), [body builder](../../crates/engine/project/src/indexing/split/build.rs), [deferred controller](../../crates/lsp/engine/src/engine/project/deferred.rs): existing priorities and incremental products; investigate measured build/publication granularity. |
| Candidate publication | [Project coordinator](../../crates/lsp/engine/src/engine/project/mod.rs): rustdoc candidates are already built off-lane; measure final disk-identity validation and replacement rather than assuming another compiler worker is needed.                                                                                            |
| Acceptance            | [Stdio observer](../../tools/lsp-query.py), [bounded runner](../../tools/agent-debug.py), engine tests and editor tests: extend existing observations, not a second unmanaged protocol client.                                                                                                                                     |

Implementation follows the [three staged plans](../../.planning/indexing-responsiveness/README.md).
No new cache format, concurrent semantic-query pool, broad macro feature, compiler
debounce change, release, CI-policy change, or unrelated CI repair is included.
Actions remain disabled. Initial runtime evidence is Linux; do not claim it
establishes macOS/Windows correctness or fixes their existing failing CI cases.
