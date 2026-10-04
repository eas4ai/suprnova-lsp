# Sudus Onboarding

## Progress

- Complete: Initialize authority records on `origin` using the developer's words, and verify both durable refs. Update origin to the renamed repository and verify remote HEAD.
- Complete: Write and verify a cited recon report; present the observed behavior and obtain confirmation of the recommended engine-import feature.
- Complete: Prepare and lint the glossary, Observed specification, roadmap, draft falsifiers, proposed mechanism plan, and proposed working agreement; obtain the developer's detailed specification and policy ruling.
- In progress: Mark the confirmed requirements Agreed and prepare executable mechanisms with reviewed failing controls.
- Pending: Obtain specification and working-agreement authorization, then open the commitment under the developer's chosen commit policy.

Keep exactly one item in progress while onboarding. Complete an item only after its artifacts and verification are complete.

## Evidence

- Initialization: `sudus init --remote origin --quote 'remote on my word'` succeeded. Init record: `421b5a87d96808461438a1a8b224b52713dce450`.
- Durable roots: `refs/sudus/log` and `refs/sudus/snapshots` both exist.
- Authority: `.sudus/settings.json` names `origin`. Its URL is `https://github.com/eas4ai/suprnova-lsp.git`; `git ls-remote --exit-code origin HEAD` returned `cd3d160826e389a2c85cbdf3bd4276577588de01`.
- No legacy Cairn record directories, `docs/commitments/`, or existing `docs/spec/overview.md` were present. No migration or deletion is required.
- Existing implementation changes remain uncommitted on `work/rustdoc-macro-support`.
- The developer confirmed the recommended first feature with “confirmed - recommendation”: engine lookup of `Post::query()` with `Builder<Post>` inference.
- `sudus lint docs/spec` passed on 2026-10-04. The six MAC requirements remain Draft; no agreement or start is recorded.
- Self-reviewed the six falsifiers against their proposed observations. Sudus lint passed again after adding crate-target isolation to MAC-003.
- Prepared the mechanism plan, adoption review, and full proposed agreement. Checked local links and whitespace in all ten onboarding draft files; `git diff --check` passed. The actual `AGENTS.md` matches HEAD byte for byte.

## Required developer gates

The developer confirmed the first feature. The existing-project skill next requires confirmation of its detailed falsifiers, followed by reviewed failing mechanisms and authorization of the final specification, agreement, and settings.

The existing `AGENTS.md` reserves commits and PRs for humans. The Sudus template requires agent commits, and `sudus start` creates a branch commit. The original instruction also protects an existing `AGENTS.md` against replacement. Prepare the proposed agreement for review before requesting an explicit ruling on those conflicting instructions; do not silently replace the file or run the committing start command.

Invoking this skill explicitly requests `docs/recon.md` and `docs/spec/`; it authorizes preparing those files. Existing documentation remains evidence until the developer requests changes to it.

## Detailed approval

The developer replied “ok” to the six MAC requirements and explicitly permitted
adopting the proposed agreement, replacing AGENTS.md, and allowing Sudus-required
agent commits. Applied that exact proposal and marked MAC-001 through MAC-006
Agreed 2026-10-04. Final authorization remains a later gate.
