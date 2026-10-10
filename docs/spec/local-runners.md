Prefix: CIR

# Local GitHub Actions Runners

Use the existing setup in `/home/shawn/workspace2/runners/` to add repository-scoped
registrations for `eas4ai/suprnova-lsp`. The available machines are Linux x64,
Windows x64 and macOS ARM64. Their registrations for `eas4ai/runners` remain intact.
This commitment enables CI; it does not publish a release, modify the framework
checkout, or repair unrelated platform failures in the language server.

## Agreed obligations

[CIR-001] GitHub Actions MUST be enabled for eas4ai/suprnova-lsp, with one online repository-scoped runner for each available platform using the matching self-hosted, OS, architecture and rust-ci labels. Registration MUST preserve the existing pilot registrations, service resource settings and owner default Rust toolchains, and MUST keep registration credentials and connection details out of tracked files, command arguments and reported output.
Falsifier: Actions is disabled, a required runner is absent, offline or incorrectly labeled, an existing pilot registration or service resource setting changes, an owner's default Rust toolchain changes, or registration exposes a credential or connection detail through a tracked file, command argument or reported output.
Mechanism: suprnova-local-runners
Status: Agreed 2026-10-10

[CIR-002] Supported native CI jobs and matching GitHub-release package jobs MUST select the local Linux x64, Windows x64 and macOS ARM64 runners only for owner-initiated pushes or manual dispatches, including owner-initiated reruns. Pull requests and other actors MUST use hosted runners; Linux ARM64 and Intel macOS release packages MUST remain hosted. Local jobs MUST explicitly select their required Rust toolchain, use the service's Cargo home and preserve existing artifact names and Linux release-container compatibility. Public fork workflow approval MUST require all external contributors. The obsolete marketplace release workflow MUST remain disabled.
Falsifier: A supported trusted native job selects a hosted runner, an untrusted event or actor selects a local runner, an unsupported release architecture selects unavailable local hardware, a local job relies on the owner's default Rust toolchain or ignores its configured Cargo home, release artifacts or Linux container compatibility change, fork approval admits external contributors without approval, or the obsolete marketplace workflow is enabled.
Mechanism: suprnova-local-runners
Status: Agreed 2026-10-10

[CIR-003] Acceptance MUST dispatch genuine jobs in eas4ai/suprnova-lsp on all three local runners, record their runner identities and platforms, compile and test a Rust probe with the explicitly selected toolchain, and download their output artifacts. It MUST also validate workflow syntax and routing controls, compare owner toolchain and pilot-registration observations before and after installation, and report any observed application CI failure separately from runner enablement.
Falsifier: A platform lacks a successful actual smoke job or matching downloaded artifact, observations do not identify the executing runner and platform, the Rust probe is skipped or uses an implicit toolchain, routing or syntax controls fail, preservation observations are absent or differ, or a failing application CI check is described as passing.
Mechanism: suprnova-local-runners
Status: Agreed 2026-10-10

## Observation and failing controls

The proposed mechanism combines GitHub Actions settings and runner API observations,
workflow parsing and event/actor routing tests, before/after host observations, and
actual dispatch job and artifact records. Controls cover disabled Actions or a
missing registration (CIR-001), pull-request routing to local labels or an enabled
marketplace workflow (CIR-002), and an absent or mismatched smoke artifact (CIR-003).
Recorded evidence must identify the checked workflow commit and run IDs; a runner's
successful private pilot alone does not establish this consumer's acceptance.
