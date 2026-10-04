# Roadmap

Current: suprnova-user-acceptance

The engine-import commitment is Done, recorded as `7ccb5de8ae9cd0586774b640dbb47cfe111d57b0`. No commitment is open. The developer selected Devlist User validation; its [requirements](suprnova-user.md) are Agreed 2026-10-04 following “confirmed”. Mechanisms and final authorization are being prepared.

## rustdoc-engine-import

Requirements: MAC-001, MAC-002, MAC-003, MAC-004, MAC-005, MAC-006

Deliver compiler-derived model declarations through the existing engine, using explicitly supplied genuine exports. Cover initial and batched project construction without requiring editor-specific policy.

Done when all six requirements have fresh passing mechanism receipts, each mechanism has a reviewed failing control, the existing Rust checks pass, and the required Sudus reviews close without unresolved findings. CLI inspection alone does not satisfy this commitment.

## suprnova-user-acceptance

Requirements: SUP-001, SUP-002, SUP-003, SUP-004, SUP-005, SUP-006

Deliver real Devlist User API analysis from its provenance-checked compiler export, including the generated declarations required for applicability. Preserve the external checkout boundaries and existing MAC behavior.

Done when all six requirements have fresh passing receipts from reviewed mechanisms, the application queries pass in initial and batched construction, comparable completed-indexing idle RSS is reported, the relevant Rust checks pass, and Sudus reviews close without unresolved findings. Compiler export success alone does not satisfy this commitment.

## Later work

| Step | Delivery boundary |
| --- | --- |
| Rustdoc snapshots | Reduced per-crate artifacts with producer, target, features, source, dependency, and macro-input validity. |
| Export refresh | Explicit refresh first; bounded scheduling, cancellation, and failed-refresh recovery later. |
| Further Suprnova acceptance | Live metadata and Inertia checks after User acceptance. |
| Broader macro semantics | Additional declaration shapes and blanket ownership after concrete model APIs work. |

These rows are plans, not Agreed requirement sets. [Detailed implementation plan](../../.planning/rustdoc-macro-support.md).
