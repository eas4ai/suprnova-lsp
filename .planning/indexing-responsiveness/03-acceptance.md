# Plan 03 — Prove the Result

Status: Approved 2026-10-06; execution pending. Depends on Plan 02. Requirements: RSP-001 through RSP-006.

## Application and latency acceptance

Replay the frozen workload with the preserved baseline and candidate binaries on
the same host and configuration. Use genuine Devlist and its pinned Suprnova
dependency, locked resolution, repository-owned artifacts and inline editor text.
Do not modify the application lockfile or protected framework checkout.

Collect the full contract counts: at least 20 independent first-hover sessions and
100 repeated hover samples per required mode/symbol/window series. Record cold/warm
cache state separately. Include background events proving active overlap; wait for
actual completion only for settled series and idle RSS. Fail rather than filter
unexpected errors, unknown/wrong types, missing samples or insufficient overlap.

Required semantic results include User query, without_global_scopes and filter as
`Builder<User>`, the source verify_password signature, and existing owner/target
controls. Record completion and inlay behavior alongside hover; 200 ms is the
hover target, not an unmeasured completion promise. Exports run through the worker,
not a request handler; preserve prepared-input and source-only controls.

## Ordering, cancellation and editor proof

Exercise a queued hover, running hover cancellation, an unsaved edit during a
hover, a saved mutation before a later hover, stale worker completion, valid
publication, export failure and recovery, sustained interactive traffic with
background progress, and shutdown. Observer events must identify exact request,
document revision and saved generation; sleeping and hoping for overlap is not proof.

Extend existing genuine VS Code tests for request delivery/cancellation and correct
generated-model hover while indexing. Run extension tests outside the sandbox as
AGENTS.md requires. Editor observation complements the real stdio percentile;
do not conflate tooltip animation with server transport latency.

## Memory and process evidence

Run at least three matched baseline/candidate idle pairs and retain all five idle
samples per process tree after queries, compiler work and deferred indexing end.
Compare medians with identical residency, target/features/sysroot and workloads.
Report any repeatable increase and investigate retained payloads before calling
the change acceptable. No numerical RSS allowance is silently introduced.

Inspect saved state and request release for whole-project/query snapshots and full
compiler graphs. Observe no rust-analyzer launch, producer launches only from the
automatic worker, and successful owned-process cleanup for every managed run.
Report compiler/indexing peaks separately from server-plus-engine idle RSS.

## Mechanism integrity and repository checks

Use the driver and integrity tests prepared in Plan 01 before declaration. Emit
one result for each RSP requirement with raw evidence paths. Demonstrate each
violating control from the spec, verify it failed for that reason, restore its
bytes, and bind the reviewed failing receipt under Sudus. Missing toolchains,
fixtures or traces cannot count as passing controls.

Run relevant bounded engine/project tests and observer integrity tests first,
then required workspace checks, extension compilation/lint/unit tests, and genuine
editor acceptance. Preserve MAC/SUP/EDT/AUT/IDN behavior; run applicable existing
mechanisms rather than replacing them with the new performance result. Commit
declared inputs before `sudus check` and end their exact leases.

Existing check commands include:

```sh
just fmt --check
cargo clippy --workspace --all-targets --locked -- -D warnings
DYLINT_RUSTFLAGS='-D warnings' cargo dylint --all --workspace -- --all-targets
just agent-debug --timeout 15m test --workspace --locked
npm --prefix editors/code run check
npm --prefix editors/code run check:test
npm --prefix editors/code run lint
npm --prefix editors/code run test:unit
```

Run genuine editor acceptance through the existing bounded editor mechanism with
an isolated test host and prebuilt server. Preserve its process-tree cleanup;
do not replace it with an unmanaged VS Code launch. The proposed observer driver
and its new integrity-test command are added in Plan 01, not assumed to exist now.

The existing CI failures remain visible: Windows/macOS rustdoc identity and watcher
tests, macOS compiler cleanup, Linux offline editor fixture preparation, unavailable
release/CodSpeed credentials and restricted Actions policy. This plan does not
claim to repair those failures. If a required local check cannot run or exposes a
failure against RSP, resolve/escalate it; never silently skip it. Actions remain
disabled and no hosted acceptance is claimed.

## Completion and delivery

Produce a concise table for every required series showing count, first/p50/p95/max,
semantic status, background evidence and baseline/change idle RSS. Include build
and application identities, the measured cause, the selected implementation,
controls, process cleanup and platform limits.

All six RSP requirements need current passing receipts from reviewed mechanisms.
Complete the developer/agent review evidence, run the one fresh adversary when
`sudus wake` names `report`, and resolve or explicitly decline every finding before
`sudus done indexing-responsiveness`. Show the printed report to the developer.
Push completed branch and durable refs with `sudus push`; carry approved finished
work to main according to the developer's delivery direction. Packaging or a new
release is not part of this performance commitment.
