# Devlist User mechanism preparation

The developer confirmed SUP-001 through SUP-006 with “confirmed”. The next
commitment is `suprnova-user-acceptance`; final Sudus authorization is pending.
The existing working agreement and settings stay intact.

## Observations

`suprnova-user-acceptance` runs `python3 tools/sudus-suprnova-user.py` and reports
each requirement separately. Its inputs include the source workspace, compiler
capture, provenance index, driver, integrity tests, manifests and bounded runner.
External source files are checked against the declared capture fingerprints;
the contract covers that captured revision, not arbitrary future application edits.

| Requirement | Evidence required |
| --- | --- |
| SUP-001 | Genuine export selection, retained actual impl ownership, and matching source/lockfile/compiler/target/features/export provenance. |
| SUP-002 | Ordinary type queries inside User's existing source method return the exact written Builder<User> identity in initial and batched construction. The lowerer supplies no query stub. |
| SUP-003 | Both modes return the exact builder and associated types. Inspection of the lowered filter parameters verifies canonical IntoColumn/IntoVal bounds. |
| SUP-004 | Source method inference and same-name local owner isolation hold; wrong-target and missing-reference candidates reject; the previous generation and query results stay unchanged. A separate private importer test observes target rejection before indexing. |
| SUP-005 | Linux strace observes each real import/query process tree. A forbidden launch fails even when import is blocked; incomplete query coverage cannot pass. |
| SUP-006 | Guard the inspected compact retained roots, complete deferred indexing, drop query views, purge through the shipped allocator, and sample five idle RSS values. Compare allocator, sysroot, target cfg, effective residency and query workload; report compiler/indexing peaks separately. |

The memory comparison uses the same workspace-resident policy in both processes.
This keeps the application package resident in the baseline too, so import's
required package retention does not change the effective comparison plan.
Invalid-candidate controls run after the idle sample. No numerical RSS limit is
claimed; source-only results are a baseline, not a measured import delta.

## Failure handling

Missing or duplicate probe reports, test events, compilation failures, timeouts,
missing traces and failed process cleanup cannot count as passes. Source method
preservation and process completion remain unverified until those queries run.
A valid supplied export that fails construction cannot deliver the required User
query APIs and fails their acceptance. Integrity tests check the observation
parser, each violated semantic predicate, provenance boundaries and mismatched
memory configuration. Their synthetic reports are parser controls only, never
application evidence or substitute fixtures.

## Reviewed controls

Preparation must record the genuine ownership failure and demonstrate an actual
invalid-target acceptance mutation and a harmless rustdoc-named process launch.
Each mutation must be restored before final authorization. Receipt identifiers
and verification results will be added after those checks run.
