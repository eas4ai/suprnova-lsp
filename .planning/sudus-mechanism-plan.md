# Engine Import Verification

The contract is Agreed. The executable mechanism is declared and all six
requirements have reviewed failing receipts. Engine integration remains failing;
no CLI inspection result substitutes for passing semantic queries.

## Requirement observations

Mechanism: `rustdoc-engine-import`, executing `python3 tools/sudus-engine-import.py`.
It reports a separate result for each MAC requirement. It reuses the repo-owned
agent-debug supervisor for deadlines, logs, signals, and process cleanup.
Ordinary focused tests use `just agent-debug`. Runs select Rust 1.98.1.

| Requirement | Required observation |
| --- | --- |
| MAC-001 | Query the analysis engine on the genuine macro fixture and assert `Post::query()` resolves uniquely to `Builder<Post>`. Add a genuine impl-only export fixture where `query` comes from the source trait's default method. Run both initial and batched project construction. |
| MAC-002 | Query `generated_method(7u64)` and the generated associated `Model::Key`; assert `Builder<u64>` and `u64` through engine types. |
| MAC-003 | Use same-name nominal types in different modules, packages, and crate targets; only the selected owner receives generated methods. Retain a same-name function. Assert a source/export method overlap resolves once. |
| MAC-004 | Build a source-only package artifact, then import the export and query it. Build again without the export and assert the generated method disappears. Exercise artifact loading or assert that the affected package stays resident and bypasses source-only reuse. |
| MAC-005 | Try an unsupported format, missing retained signature path, and unmappable selected owner. Assert each candidate build fails with context, and the previous valid project's query still succeeds unchanged. |
| MAC-006 | Prebuild the acceptance executable, then run import and query under `strace -f` with `execve`/`execveat` tracing on Linux. Reject any rust-analyzer or rustdoc launch, including absolute executable paths. Also run with neither tool available on PATH. Trace failure or unavailable tracing is a failed observation, never a pass. |

Compiler invocations that prepare fixtures or build tests occur before the
MAC-006 observation window. `strace` is installed on this machine; the tracing
observation ran and detected the temporary compiler-launch control. Fixtures must record their actual compiler and schema.

## Mechanism execution and inputs

The driver discovers all 11 expected named tests and rejects absent, ignored,
filtered, or incomplete cases. It requires exact start/completion events and
matching exit status. Compilation failures, timeouts, missing traces, or failed
cleanup produce no requirement result; Sudus records them as unverified. Four
integrity tests guard these paths and run with every mechanism invocation.

The definition covers 35 literal inputs, including the 27 local dependency
crates, both genuine fixtures, manifests/lockfile, configuration, driver, and
bounded supervisor. Identity records tool versions and relevant non-secret
environment values. Protected contract/settings files are excluded; ignored
build outputs are not snapshotted.

The existing `rustdoc-boundary` tests remain regression checks for the Observed
API blocks; they do not substitute for MAC tests.

## Failing controls before trust

Each requirement needs a failure caused by its stated violation. An absent test
or build failure does not establish that a mechanism detects the violation.

The baseline demonstrates absent generated query/signature/ownership support,
source-only artifact reuse that suppresses imported APIs, and acceptance of an
unmappable selected target. MAC-001 through MAC-005 reviews bind receipt
`fce831cd5ab82bef068689c30fecfb75b3b8e1a8`.

MAC-006 uses a temporary compiling mutation that launches a harmless executable
named rustdoc during the observed test. The trace identifies its successful
exec and rejects it. Its review binds receipt
`69213f23f0a3ac64b2dabff5308f6b8c30c2d0b0`. The mutation was restored exactly and
the lease ended. Restored receipt `699b52ca6b4cc1f7b55c42a14e650c3dac4dd188`
still fails all six requirements because integration is not implemented.

Each reviewed failure proves the observed violation is detected. It does not
claim that every edge case or isolation assertion has already been reached.
All required acceptance cases must pass after implementation and before Done.
