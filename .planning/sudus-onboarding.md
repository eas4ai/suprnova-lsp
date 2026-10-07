# Sudus Onboarding

## Progress

- Complete: Initialize authority records on `origin` using the developer's words, and verify both durable refs. Update origin to the renamed repository and verify remote HEAD.
- Complete: Write and verify a cited recon report; present the observed behavior and obtain confirmation of the recommended engine-import feature.
- Complete: Prepare and lint the glossary, Observed specification, roadmap, draft falsifiers, proposed mechanism plan, and proposed working agreement; obtain the developer's detailed specification and policy ruling.
- Complete: Mark the confirmed requirements Agreed and prepare executable mechanisms with reviewed failing controls.
- In progress: Obtain final specification, working-agreement, and settings authorization, then open the commitment under the approved agent-commit policy.

Keep exactly one item in progress while onboarding. Complete an item only after its artifacts and verification are complete.

## Evidence

- Initialization: `sudus init --remote origin --quote 'remote on my word'` succeeded. Init record: `421b5a87d96808461438a1a8b224b52713dce450`.
- Durable roots: `refs/sudus/log` and `refs/sudus/snapshots` both exist.
- Authority: `.sudus/settings.json` names `origin`. Its URL is `https://github.com/eas4ai/suprnova-lsp.git`; `git ls-remote --exit-code origin HEAD` returned `cd3d160826e389a2c85cbdf3bd4276577588de01`.
- No legacy Cairn record directories, `docs/commitments/`, or existing `docs/spec/overview.md` were present. No migration or deletion is required.
- Prepared source and executable mechanism inputs are committed as `3472d7dd` on `work/rustdoc-macro-support`. Protected contract files await final authorization and the Sudus start commit.
- The developer confirmed the recommended first feature with “confirmed - recommendation”: engine lookup of `Post::query()` with `Builder<Post>` inference.
- `sudus lint docs/spec` passed on 2026-10-04 before and after detailed approval. The six MAC requirements are Agreed; no final authorization or start is recorded.
- Self-reviewed the six falsifiers against their proposed observations. Sudus lint passed again after adding crate-target isolation to MAC-003.
- Prepared the mechanism plan, adoption review, and full proposed agreement. Checked local links and whitespace in all ten onboarding draft files; `git diff --check` passed. Before approval, `AGENTS.md` matched HEAD byte for byte; after approval it matches the approved proposal exactly.

## Required developer gates

The developer confirmed the first feature and detailed falsifiers. The mechanisms have reviewed failing receipts. The existing-project skill now requires final authorization of the specification, agreement, and settings before start.

The inherited human-only commit policy and AGENTS.md preservation instruction were explicitly overridden by the developer's “ok” to the proposed agreement. Sudus-required agent commits are allowed; PRs remain human-owned. Final authorize/start is still a separate gate.

Invoking this skill explicitly requests `docs/recon.md` and `docs/spec/`; it authorizes preparing those files. Existing documentation remains evidence until the developer requests changes to it.

## Detailed approval

The developer replied “ok” to the six MAC requirements and explicitly permitted
adopting the proposed agreement, replacing AGENTS.md, and allowing Sudus-required
agent commits. Applied that exact proposal and marked MAC-001 through MAC-006
Agreed 2026-10-04. Final authorization remains a later gate.

## Verified mechanism preparation

- Definition: `.sudus/mechanisms/rustdoc-engine-import.json`, 35 literal inputs covering the 27 local dependency crates and runner/configuration. Identity records Rust 1.98.1, Nextest, Python, strace, and relevant non-secret environment values.
- Baseline receipt `fce831cd5ab82bef068689c30fecfb75b3b8e1a8` records fail for all six MAC requirements. Reviews for MAC-001 through MAC-005 bind that receipt.
- MAC-006 review binds control receipt `69213f23f0a3ac64b2dabff5308f6b8c30c2d0b0`. Its output records a successful exec of a harmless rustdoc-named marker, which the mechanism rejects. The temporary test mutation was restored exactly and its lease ended.
- Restored baseline receipt `699b52ca6b4cc1f7b55c42a14e650c3dac4dd188` again records fail for all six MAC requirements. No passing engine-import requirement is claimed.
- Existing workspace regression tests: 1,640 passed; 13 skipped (11 new commitment acceptance tests plus two existing skips). Artifact: `target/agent-debug/runs/20261004T141110520Z-test-3347278-3ec2a7`. Process cleanup verified.
- Mechanism integrity checks: four passed, including zero/ignored tests, exact completion/exit consistency, multiline trace output, and forbidden absolute/proxy process launches. Each mechanism invocation also runs these checks.
- Formatting, workspace Clippy and Dylint, both standalone fixture formatting checks, Cargo deny, and codegen-check passed. Cargo deny retains duplicate-dependency warnings.
- Ripwire quality-delta and test-gate returned success after the preparation commit; their comparison was the remaining working-tree documentation delta, so they do not replace the executed Rust checks.
- No cross-platform/editor tests or retained-memory measurements were run for mechanism preparation. Authority refs are local and no push has occurred.

## External checkout constraint

The developer instructed “do not modify the suprnova checkout in any way” and
then permitted testing on “~/workspace2/devlist.app”. The final agreement and
overview record this scope. Devlist's instructions and manifest were read; its
Suprnova dependency is pinned to Git revision 3229aa9af542c991196274fa3c235cdce88a68e2.
Its User model uses the real model attribute and a source trait query call.
No application compile, runtime test, or LSP query has run yet. No writes or
commands that could write were performed against the protected framework checkout.
