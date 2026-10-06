# Plan 01 — Measure the Delay

Status: Approved 2026-10-06; execution pending. Requirements: RSP-001; establish the baselines for RSP-002/006.

## Deliverables

- A checked workload definition and identity manifest pinned to the baseline Git
  commit, optimized build profile, Rust/producer versions, Devlist source and
  Cargo.lock digests, target/features/sysroot, indexing mode, residency, logging and cache state.
- A raw, monotonic request timeline with expected results and lifecycle events.
- Stage attribution and matched settled idle RSS, with a written diagnosis and
  the proposed branch of Plan 02. No unmeasured bottleneck is called proven.

## 1. Extend the existing observer

Reuse `tools/lsp-query.py` and `tools/agent-debug.py`. The existing query-plan
normalizer supports readiness/deferred barriers, inline text, and Linux RSS;
its cancellation option currently applies only to semantic tokens. Add only the
hover timing, repeated-request, event-correlation and cancellation observation
needed for this contract. All new loops, logs, payloads and requests have explicit
limits and use the supervisor's deadlines/process-tree cleanup.

Measure from request write through response receipt. Record request ID, document
version, symbol/cohort, generation/availability observation, active background
stage, status, full duration, and expected type result. Correlate queue/preparation
logs with the same request; operation labels alone cannot distinguish concurrent
hovers. Do not use a hover warm-up to declare readiness for its first-hover sample.
Use existing publication/initialized lifecycle evidence or a narrowly justified
test observer. Record a real deferred start and finish for overlap assertions.

The current lifecycle already logs `queued_ms` and `elapsed_ms`. Extend narrowly
where needed to distinguish current/saved body preparation, final candidate
validation, body-product publication and post-response memory release/purge.
Include cleanup in the next request's observed wait: it happens after sending the
previous result but before the dispatcher accepts another command.

Add integrity tests that reject missing/duplicate samples, wrong expected types,
mixed modes/cohorts, fabricated readiness, malformed/non-monotonic timestamps,
discarded errors, and missing overlap. Demonstrate the RSP-001/002 controls with
validating failures, not compiler errors. Proposed driver:
`tools/sudus-responsiveness.py`; do not declare this command until it exists.

## 2. Capture baseline experiments

Start with a small diagnostic sample before the full acceptance counts. Keep
first/repeated and cold/warm experiments separate. Pin settings identically for
baseline and candidate. Capture source hover and generated `User::query()`,
`without_global_scopes()` and `filter()` returning `Builder<User>`; also preserve
`verify_password`. Use Model in scope and genuine compiler-derived declarations.

Observe source declaration readiness separately from generated-API publication.
Use detailed tracing for diagnosis, then fixed low-overhead logging for timed
acceptance. Record the logging policy for both binaries so tracing overhead cannot
masquerade as an improvement.
Run before deferred completion where the mode permits it, during automatic
worker activity with usable declarations, and after full settlement. Add sessions
if active windows are short. Do not pad an active series with settled requests.
Controlled owned fixtures keep publication work pending for race observations;
they cannot replace genuine application latency results.

The existing invocation shape is:

```sh
just agent-debug --timeout 15m --measure \
  --log 'rg_lsp_engine=trace,rg_lsp_server=debug' \
  lsp-query --workspace-root /home/shawn/workspace2/devlist.app \
  --query-file target/agent-debug/queries/rsp-devlist.json --json
```

This command becomes runnable after the workload plan is generated and its
artifact-root/locked-resolution settings are checked. They are prerequisites,
not optional flags omitted for convenience. For active-body queries set the plan's
`readinessBarrier` to `ready`, `deferredBarrier` to `after-queries`; for settled
comparisons use `before-queries`. Generated availability requires separate export
publication evidence. `--measure` records peaks, not the settled idle RSS proof.

Record at least three matched baseline sessions for idle RSS after background
work and requests finish. Later acceptance uses three matched baseline/change
pairs, recording five server-plus-engine idle samples in each. Disclose RSS
double-counting of shared pages consistently. Preserve the exact baseline binary
and its metadata under owned artifacts for before/after replay.

## 3. Choose the change from the trace

| Observation                                                          | Next work                                                                                                                                                                      |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Queue wait dominates while products publish or validation scans run. | Plan 02A: bounded publication/scheduling at safe boundaries.                                                                                                                   |
| Saved-file Body IR preparation dominates execution.                  | Plan 02B: narrow the required body/dependency work.                                                                                                                            |
| Previous query cleanup dominates the next request's queue wait.      | Plan 02C: measured cleanup cadence that preserves idle release.                                                                                                                |
| Most apparent delay precedes declaration availability.               | Report that separately; investigate initial readiness only if necessary for the agreed eligible-hover target. Do not claim a scheduler fix addresses unavailable declarations. |
| Existing eligible samples already meet the target.                   | Expand controlled contention and the required sample counts, then present the result before deciding whether a production change is justified.                                 |

Report both queue and execution distributions. Treat 50% of total time as a
diagnostic heuristic, not an acceptance criterion; several stages can contribute.
Run the observer controls and a real bounded sample before trusting its report.

## Exit gate

RSP-001 evidence exists and the observer detects its violating controls. The
baseline includes correct results, background events, stage attribution, cleanup,
and idle observations. The chosen implementation names actual trace entries.
No production scheduling or body-analysis change is included in this plan.
