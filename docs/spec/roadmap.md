# Roadmap

Current: rustdoc-automatic-worker

The engine-import, Devlist User and editor-input commitments are Done, recorded as `7ccb5de8ae9cd0586774b640dbb47cfe111d57b0`, `88088a0c347ffbc3cf7e526dd763bd539a18d6a0`, and `ec0b18211f3637882fea3bfb482706415a3664cd`. No commitment is open. The developer requests automatic rustdoc JSON export in a debounced worker and selected automatic model and target discovery. The [AUT contract](automatic-rustdoc.md), EDT mode clarifications and two-second debounce were confirmed 2026-10-05. Mechanisms and final start authorization are being prepared.

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

## rustdoc-automatic-worker

Requirements: AUT-001, AUT-002, AUT-003, AUT-004, AUT-005, AUT-006, AUT-007, AUT-008, AUT-009, EDT-005, EDT-006

Deliver automatic Suprnova model/target discovery, startup exports and debounced
saved-input refresh in a supervised worker. Publish only validated current
declarations into the running editor's project. Retain explicit prepared-input
mode, source-only controls, MAC/SUP behavior and external checkout boundaries.

Done when every confirmed requirement has a fresh passing receipt from
a mechanism with reviewed failing controls, genuine automatic Devlist queries
and a live macro-edit case pass in both construction modes, concurrency/race/
rollback and compiler-tree cleanup controls pass, comparable completed-indexing
idle RSS and separate compiler/indexing peaks are recorded, repository Rust and
extension checks pass, and Sudus review findings are resolved.

## Later work

| Step | Delivery boundary |
| --- | --- |
| Rustdoc snapshots | Reduced per-crate artifacts with producer, target, features, source, dependency, and macro-input validity. |
| Export refresh | Automatic debounced generation and recovery are proposed next; broader external macro-input tracking remains later work. |
| Further Suprnova acceptance | Live metadata and Inertia checks after User acceptance. |
| Broader macro semantics | Additional declaration shapes and blanket ownership after concrete model APIs work. |

These rows are plans, not Agreed requirement sets. [Detailed implementation plan](../../.planning/rustdoc-macro-support.md).
