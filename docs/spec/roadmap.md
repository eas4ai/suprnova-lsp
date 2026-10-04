# Roadmap

Current: rustdoc-engine-import

The developer agreed the six MAC requirements on 2026-10-04. No commitment is open; executable mechanisms and final Sudus authorization are being prepared.

## rustdoc-engine-import

Requirements: MAC-001, MAC-002, MAC-003, MAC-004, MAC-005, MAC-006

Deliver compiler-derived model declarations through the existing engine, using explicitly supplied genuine exports. Cover initial and batched project construction without requiring editor-specific policy.

Done when all six requirements have fresh passing mechanism receipts, each mechanism has a reviewed failing control, the existing Rust checks pass, and the required Sudus reviews close without unresolved findings. CLI inspection alone does not satisfy this commitment.

## Later work

| Step | Delivery boundary |
| --- | --- |
| Rustdoc snapshots | Reduced per-crate artifacts with producer, target, features, source, dependency, and macro-input validity. |
| Export refresh | Explicit refresh first; bounded scheduling, cancellation, and failed-refresh recovery later. |
| Suprnova acceptance | Devlist's real User model using its pinned Git dependency, followed by Live metadata and Inertia checks; keep the framework checkout read-only. |
| Broader macro semantics | Additional declaration shapes and blanket ownership after concrete model APIs work. |

These rows are plans, not Agreed requirement sets. [Detailed implementation plan](../../.planning/rustdoc-macro-support.md).
