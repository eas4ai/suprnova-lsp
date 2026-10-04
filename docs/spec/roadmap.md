# Roadmap

Current: rustdoc-editor-import

The engine-import and Devlist User commitments are Done, recorded as `7ccb5de8ae9cd0586774b640dbb47cfe111d57b0` and `88088a0c347ffbc3cf7e526dd763bd539a18d6a0`. No commitment is open. The developer selected explicit editor input wiring with “confirmed” and confirmed its [EDT requirements](editor-import.md) with “ok”. Mechanisms and final authorization are being prepared.

## rustdoc-engine-import

Requirements: MAC-001, MAC-002, MAC-003, MAC-004, MAC-005, MAC-006

Deliver compiler-derived model declarations through the existing engine, using explicitly supplied genuine exports. Cover initial and batched project construction without requiring editor-specific policy.

Done when all six requirements have fresh passing mechanism receipts, each mechanism has a reviewed failing control, the existing Rust checks pass, and the required Sudus reviews close without unresolved findings. CLI inspection alone does not satisfy this commitment.

## suprnova-user-acceptance

Requirements: SUP-001, SUP-002, SUP-003, SUP-004, SUP-005, SUP-006

Deliver real Devlist User API analysis from its provenance-checked compiler export, including the generated declarations required for applicability. Preserve the external checkout boundaries and existing MAC behavior.

Done when all six requirements have fresh passing receipts from reviewed mechanisms, the application queries pass in initial and batched construction, comparable completed-indexing idle RSS is reported, the relevant Rust checks pass, and Sudus reviews close without unresolved findings. Compiler export success alone does not satisfy this commitment.

## rustdoc-editor-import

Requirements: EDT-001, EDT-002, EDT-003, EDT-004, EDT-005, EDT-006

Deliver explicit rustdoc input configuration through VS Code and LSP startup to the
correct Cargo workspace engine. Expose real Devlist User hover, inlay and completion
in both indexing modes, with visible import failures and captured-input lifecycle.
Preserve MAC/SUP behavior and the external checkout boundaries.

Done when all six requirements have fresh passing receipts from reviewed mechanisms,
real stdio LSP queries and editor transport checks pass, completed-indexing idle RSS
and process traces are recorded, relevant Rust and extension checks pass, and Sudus
reviews close without unresolved findings. Engine-only observations cannot satisfy
this commitment. Automatic generation, export refresh and snapshot caching remain
outside its scope.

## Later work

| Step | Delivery boundary |
| --- | --- |
| Rustdoc snapshots | Reduced per-crate artifacts with producer, target, features, source, dependency, and macro-input validity. |
| Export refresh | Explicit refresh first; bounded scheduling, cancellation, and failed-refresh recovery later. |
| Further Suprnova acceptance | Live metadata and Inertia checks after User acceptance. |
| Broader macro semantics | Additional declaration shapes and blanket ownership after concrete model APIs work. |

These rows are plans, not Agreed requirement sets. [Detailed implementation plan](../../.planning/rustdoc-macro-support.md).
