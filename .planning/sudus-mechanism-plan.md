# Proposed Engine Import Verification

This is a reviewable plan, not a declared or passing mechanism. The detailed
contract is still Draft. No existing CLI receipt proves engine integration.

## Requirement observations

Proposed mechanism name: `rustdoc-engine-import`. Report a separate result for
each MAC requirement. Run semantic tests through `just agent-debug` with bounded
deadlines and process cleanup. Use Rust 1.98.1 for the existing workspace.

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
observation has not run. Fixtures must record their actual compiler and schema.

## Mechanism execution and inputs

Prepare an executable driver after the developer confirms the contract. It must
discover the expected named tests before running, require every case above, and
reject zero tests, skipped required tests, compilation failures, timeouts,
missing traces, and incomplete result groups. Record per-requirement outcomes
using Sudus's mechanism result format after checking its installed grammar.

Declare literal inputs covering the actual driver, selected engine/project/query
crates, fixtures, transitive local dependencies, workspace manifests/lockfile,
and bounded runner. Confirm the dependency closure before declaration. Capture
tool identity and relevant environment in the definition. Do not include
protected contract/settings paths as mechanism inputs. Ignore build outputs.

The existing `rustdoc-boundary` tests remain regression checks for the Observed
API blocks; they do not substitute for MAC tests.

## Failing controls before trust

Each requirement needs a failure caused by its stated violation. An absent test
or build failure does not establish that a mechanism detects the violation.

The valid macro fixture's unresolved engine query supplies the initial MAC-001
control. For other properties, use a compiling temporary mutation that removes
signature preservation, misroutes ownership, bypasses artifact isolation,
accepts an invalid candidate, or launches a harmless rustdoc-named marker during
the observed query. Verify the corresponding assertion fails, record it with
`sudus check`, bind the failure receipt with `sudus review mechanism`, and restore
the mutation. Each restored case must later pass before completion.

No control, receipt, declaration, or negative mutation is claimed complete here.
