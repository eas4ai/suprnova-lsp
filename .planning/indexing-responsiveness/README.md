# Indexing Responsiveness Plans

Status: Contract confirmed 2026-10-06; bounded source and generated diagnostics
collected, mechanism preparation in progress. Full acceptance, mechanism
declaration, new commitment start and a production responsiveness fix remain pending.

Contract: [RSP-001 through RSP-006](../../docs/spec/responsiveness.md).
Commitment: `indexing-responsiveness`. The roadmap names this prepared commitment;
authorization is recorded; mechanisms must be prepared and reviewed before start.

| Plan                                        | Depends on                                             | Deliverable and exit condition                                                                                                 |
| ------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| [01 — Measurement](01-measurement.md)       | Confirmed RSP contract and reviewed observer controls. | Reproducible baseline, correct raw timing ledger, stage attribution, idle RSS, and a measured choice of implementation.        |
| [02 — Implementation](02-implementation.md) | Plan 01 diagnosis.                                     | The smallest measured fix with regression tests; only justified scheduling, body-preparation or cleanup branches are executed. |
| [03 — Acceptance](03-acceptance.md)         | Plan 02 and a passing observer integrity suite.        | Real Devlist latency/correctness and memory evidence, genuine editor proof, and completed Sudus review.                        |

Execute serially. Keep one todo item in progress. Before implementation, prepare
and demonstrate the proposed `suprnova-indexing-responsiveness` mechanism under
Sudus; it must emit separate results for every RSP requirement. Measurement work
can satisfy RSP-001 while RSP-002 remains honestly failing. An incomplete baseline
is not permission to rewrite the scheduler.

The application checkout is `/home/shawn/workspace2/devlist.app`. Its currently
locked Suprnova revision is `3229aa9af542c991196274fa3c235cdce88a68e2`. Read its current
instructions before testing, use that pinned dependency and locked resolution,
and put every LSP/compiler artifact under this repository's `target/agent-debug/`.
Use inline editor text and repository-owned fixtures for edits. Do not modify
`/home/shawn/workspace2/suprnova` in any way. Leave Actions disabled and keep the
existing cross-platform/CI defects visible as separate work.

## Preparation and authorization

The requirement text, including separate first-hover percentiles, is confirmed.
The roadmap names its six requirements. Sudus authorization
`eb14dcf8b7e5813f75b093ced669c9628deaa1ae` records the developer's `ok` and binds the
confirmed specification, existing AGENTS.md and settings. Prepare the observer
definition and prove its required failing controls before trust and
`sudus start indexing-responsiveness`. AGENTS.md and settings remain unchanged.
There is no permission to weaken 200 ms, conceal slow samples, or enlarge scope
silently if the baseline makes the goal difficult.

## Scope decisions

Prefer instrumentation of existing request and publication paths, bounded work,
and selected-body preparation over a new query pool or retained snapshot cache.
Use `sudus measure` followed by the appropriate decision/escalation if the evidence
requires a consequential architectural choice. If no production change is needed,
explain the measured result and agree any revised delivery instead of inventing
work. No existing failing test may be silently skipped to claim completion.
