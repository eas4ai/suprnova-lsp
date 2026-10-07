Prefix: IDN

# Suprnova LSP Identity

The developer requested "rename it from Rust Glancer to Suprnova LSP or it will
clash" and supplied notifications whose source still reads Rust Glancer. The
automatic-worker commitment is Done (`c2422443e63e78e192ea9427de4d9853ae5bbdfb`).
This slice gives the fork its own editor, server and artifact identities.

## Proposed names and behavior

Use display name `Suprnova LSP`, local extension ID `eas4ai.suprnova-lsp`, command
and setting prefix `suprnova-lsp`, server package/executable `suprnova-lsp`, custom
notification prefix `suprnova-lsp/`, and environment prefix `SUPRNOVA_LSP_`.
The publisher matches the repository owner; marketplace registration or publishing
is outside this commitment. The repository URL is
`https://github.com/eas4ai/suprnova-lsp`.

Settings retain their existing suffixes, values, validation and initialization
option shapes. For example, the automatic policy moves to
`suprnova-lsp.rustdoc.automatic` with its two-second debounce. Old Rust Glancer
settings and commands belong to the upstream extension and are not aliases for
the fork. Existing users must copy desired settings to the new prefix and install
the new local VSIX; no user settings or installed extension is changed silently.
Distinct identities allow installation together; they do not promise that two
simultaneously enabled Rust providers produce one combined result.

Use `target/suprnova_lsp/` for package caches and
`target/suprnova-lsp/rustdoc/` for the default worker parent. Preserve explicitly
configured artifact paths and existing ownership/cleanup rules. Do not migrate,
reuse or delete upstream artifacts. Keep internal `rg_*` crate names and the
checkout directory; neither is an installation identity. Preserve canonical
licenses, original author credits and clearly labeled upstream acknowledgements.

## Agreed obligations

[IDN-001] The local extension and its VSIX MUST identify as eas4ai.suprnova-lsp with display name Suprnova LSP, and the active language client, diagnostics, output channels, status UI and user-facing errors MUST identify the fork as Suprnova LSP.
Falsifier: The VSIX or loaded extension has the upstream extension ID, ordinary fork UI or request-error source still identifies as Rust Glancer, or the manifest, packaged metadata and active client disagree about the fork identity.
Mechanism: suprnova-lsp-identity
Rationale: The observed notification source is the language client's name, not only the manifest title.
Status: Agreed 2026-10-06

[IDN-002] Extension settings, registered commands, hover command links, custom LSP commands and notifications, diagnostic collection and log language IDs MUST use the fork's distinct namespace consistently; old upstream settings and commands MUST neither configure nor control the fork.
Falsifier: An upstream-prefixed command or setting is still contributed or consumed by the fork, old settings change its startup options, a command link targets the wrong extension, a client/server custom identifier disagrees, or registering an upstream-identity fixture makes a fork command unavailable or alters its configuration.
Mechanism: suprnova-lsp-identity
Rationale: Renaming the display text alone leaves command and configuration collisions.
Status: Agreed 2026-10-06

[IDN-003] Cargo and local packaging MUST produce and launch suprnova-lsp, LSP initialization MUST report Suprnova LSP, and fork environment variables, log schema and default cache/worker paths MUST be distinct from upstream while preserving explicitly configured paths and unowned artifacts.
Falsifier: Cargo or a local server archive/VSIX emits or bundles the old executable, default PATH/bundled discovery launches rust-glancer, initialization or structured logs retain the upstream identity, an upstream environment variable changes fork behavior, or fork startup/reindex/shutdown modifies an upstream cache sentinel or uses its default artifact namespace.
Mechanism: suprnova-lsp-identity
Rationale: The executable and persistent artifacts can clash even when the extension ID differs.
Status: Agreed 2026-10-06

[IDN-004] Fork installation metadata, local build/test/package entry points and root/extension instructions MUST refer to the fork accurately while preserving the dual license and upstream attribution; affected release workflow references MUST remain consistent without publishing or installing anything.
Falsifier: A documented local invocation or packaging entry point requires the old package/binary/settings identity, repository metadata directs fork users to the upstream repository as the fork, packaged license texts differ from the canonical licenses, original attribution is removed, or an affected workflow expects an artifact name the packaging code no longer emits.
Mechanism: suprnova-lsp-identity
Rationale: A renamed client must be buildable and locally packageable with its matching server.
Status: Agreed 2026-10-06

[IDN-005] Genuine editor and stdio acceptance MUST exercise the new identity through startup, generated-model hover/inlay/completion, save refresh, reindex and shutdown, preserving prepared-input, automatic and source-only behavior in both indexing preferences with verified owned-process cleanup and no rust-analyzer dependency.
Falsifier: A renamed command fails to control the running fork, either indexing mode loses generated or source query behavior, a genuine saved macro change fails to refresh, prepared/source-only controls launch an unexpected producer, acceptance uses a stub instead of the actual server/compiler, or cleanup/process-launch observations are missing or fail.
Mechanism: suprnova-lsp-identity
Rationale: String replacements cannot prove that the renamed editor and server still communicate.
Status: Agreed 2026-10-06

## Observation and failing controls

The proposed `suprnova-lsp-identity` mechanism combines manifest and packaged VSIX
inspection, Cargo executable metadata, exact client/server identifier checks,
configuration isolation controls, ordinary Rust/extension tests, and the existing
bounded editor/LSP runners. It must observe the built executable's initialize
reply and structured output, actual local server-archive contents, and default
cache/worker paths beside preserved upstream sentinels. Missing observations,
skipped runtime cases or an absent binary are not passes.

A tiny local upstream-identity extension fixture contributes the original IDs
and contrasting settings; it is enabled beside the fork in an isolated test host.
This checks registration/configuration coexistence without downloading or running
an upstream analysis server. Packaging inspection proves separate install IDs.
It does not establish marketplace publisher ownership or combined-provider UX.

For each requirement, show a violating control: retain the old client/VSIX name
(IDN-001), consume an upstream setting or misroute a command (IDN-002), restore an
old binary/cache identity (IDN-003), mismatch an archive name or license copy
(IDN-004), or suppress a real query/refresh/cleanup observation (IDN-005). Bind
the mechanism only after each recorded control fails for its stated reason.

Reuse prepared Devlist acceptance with its pinned framework dependency and genuine
owned macro fixtures for live automatic refresh. Record both indexing preferences
and run relevant Rust, extension, lint/format and acceptance-integrity checks.
Compiler/LSP artifacts remain under this repository's `target/agent-debug/`.
The framework checkout remains strictly read-only. Linux local packaging and
runtime results do not establish Windows/macOS execution or release success.

## Cited change surface and contract relationship

| Boundary | Observed owner and change |
| --- | --- |
| Extension identity and UI | `editors/code/package.json:2`, `src/language-client/language-client-session.ts:108`, `src/extension.ts:20`, status/logging modules: independent manifest and active-client identities. |
| Configuration and commands | `editors/code/src/config.ts:191`, `src/commands.ts:7`, hover actions; LSP command/notification producers and consumers: consistent fork namespaces. |
| Server executable and logs | `crates/rust-glancer/Cargo.toml:2`, `src/main.rs:23`, `src/logging.rs:19`, `crates/lsp/server/src/methods/mod.rs:38`, `engine_process.rs:39`: rename public executable/package and external environment/log identities. |
| Artifact ownership | `crates/engine/project/src/storage/cache/instance.rs:17`, `crates/lsp/server/src/rustdoc_worker/task.rs:286`: separate default namespaces without touching upstream contents. |
| Packaging and entry points | `editors/code/scripts/package-vsix.mjs:42`, `.github/scripts/package_server_archive.py:18`, affected workflows, Justfiles, bounded runners and CLI tests: matching binary/archive references. |
| Metadata and instructions | Workspace Cargo metadata, root `README.md`, extension `README.md`, `.vscode/launch.json` and tasks: local fork usage, with licenses and author credits preserved. |

No existing Agreed obligation is weakened. In particular, EDT-001 requires
propagating "every valid configured rustdoc input to its selected Cargo workspace
engine"; AUT-003 requires "writing only isolated managed compiler artifacts";
AUT-009 requires "observe no rust-analyzer process". Their descriptive old setting
and default path examples are superseded by the names above, with behavior and
initialization option shapes preserved. Historical contracts and durable receipts
retain the names they observed; broad internal identifier or documentation rewrites
are outside this slice. Other files under `docs/` are not edited.

The alternative is changing display text only. Recommend the coherent identity
rename because display-only changes cannot prevent installation, setting, command,
binary or cache collisions. Hover/indexing performance and retryable query errors
remain separately captured work, after this requested priority.
