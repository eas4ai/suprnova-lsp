# Devlist User mechanism preparation

The developer confirmed SUP-001 through SUP-006 with “confirmed”. The next
commitment is `suprnova-user-acceptance`. The developer authorized it with “ok”;
`sudus start` recorded `2cfc4cb2f3bfa875cb13c37e451b9d8f4ce860b1`. The agreed
contract, working agreement and settings are frozen for this commitment.

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

Receipt `922c03b8762b50d3584c47895764311d0f83dc8d` records two deliberate
violations: the importer accepted the absent application target, and the initial
query process launched a harmless copy of `/usr/bin/true` named `rustdoc`.
The dedicated target test and exec observer each detected their violation.
All six mechanism reviews bind this failing receipt. Both mutations were restored
byte-for-byte; no violating code was committed.

Restored receipt `5f892d6822333b13c760a1902493b47d36e332f4` fails SUP-001,
SUP-002, SUP-003 and SUP-006 because valid User selection still rejects reverse
`From<User>` ownership and the imported memory sample cannot be reached.
SUP-004 and SUP-005 remain unverified until valid imported queries complete.
The wrong-target test passes again and no forbidden launches appear. All eleven
owned process groups were verified empty in both runs.

The source-only completed-indexing baseline is 295.47 MiB idle RSS; its indexing
peak is 3963.47 MiB. No imported idle delta has been measured. The retained-root
guard passes independently, so missing paired samples do not establish a leak.

Preparation commit `06b129fbc` passed 1,657 workspace tests with three skipped,
full formatting/Clippy/Dylint, Cargo deny and codegen-check. The mechanism runs
the ignored real-target test explicitly. Nine observer integrity tests pass.
Commit `a3086a3f` additionally binds Cargo configuration and compiler override
hashes. Its fresh genuine capture validates; its export digest is unchanged;
altered configuration is rejected; all five capture process groups were verified
empty. This guard was verified separately after the restored receipt.

Ripwire's test gate exits 4 on Markdown headings it classifies as untested;
its quality delta compares only the uncommitted documentation against the
preparation commit. Neither establishes a clean quality gate for the new harness.

## Generated declaration implementation

The importer lowers a referenced closure of structs, enums, unions and type aliases
in compiler-established child modules. Each new module is checked against the
export's actual parent membership and anchored to the selected source owner.
Existing source modules still reject absent declarations. Source re-exports are
resolved again before the private candidate generation is published.

Canonical compiler paths retain their unique workspace crate identity. They can
resolve private defining paths behind public re-exports without changing source
visibility or adding transitive dependencies to the extern prelude. Ambiguous
defining crate names reject the import. Cargo's rlib/cdylib combinations normalize
to one library analysis target, avoiding missing dependency artifacts in batches.

The genuine child-model fixture covers storage aliases, nested private modules,
reverse trait attachments, source trait-default query bodies, exact associated
types, fields, re-exports and invalid candidates in both construction modes.

The real initial User probe at
`target/agent-debug/runs/20261004T204423640Z-user-probe-2707869-4e24e4` passes all
eight query predicates, rejects both invalid candidates, and preserves the previous
project. All five idle samples are 81,622 pages (318.84 MiB on this host). This is
a development probe; traced paired acceptance is pending.
The batched probe at
`target/agent-debug/runs/20261004T205236123Z-user-probe-2862233-79902f` also passes
all eight query predicates and finishes indexing; its five samples are 80,405 pages
(314.08 MiB). Both owned process trees were verified empty in each probe.

Final implementation checks passed 1,670 workspace tests with three skipped,
49 focused importer/reader checks, all formatting/Clippy/Dylint checks, Cargo deny,
codegen-check and nine observer integrity checks. Ripwire quality-delta exits 2:
it flags test duplication/churn, added test-module length, two more path-walker
lines and the new reader's validation complexity. Its test gate exits 4 on a broad
524-symbol radius, with many runner-less or ambiguous Rust graph edges. Those are
not clean gate results. The workspace tests cover its listed Rust test modules;
the parser integrity tests it suggests alone are not engine coverage.
