# Working agreement

This repository runs under Sudus. `docs/spec/` is the contract, the roadmap names the current commitment, and `sudus` reads the repository and names the next action. This file states the move for each verdict and action. The kernel does not parse it; it is protected and changes only between commitments, by developer authorization.

## The agent

Run `sudus wake` first, every session, and act on the verdict only. With hooks the verdict is printed before every turn; this agreement holds without them.

- Resolvable: do the one action named until its predicate holds, leave the required trace (branch commit, snapshot or log record), then run `sudus wake` again.
- Waiting: an escalation is unanswered and wake printed its five fields verbatim. Add nothing to the work. Put the escalation to the developer as "The developer" below says, ending with `ok | instead | ask`, and when they answer, record it yourself with `sudus answer`. Never hand them a command to run.
- Done: a done record exists and nothing waits. Report it and stop. Backlog waiting: wake names `promote` instead. When the developer ranks a new feature above the waiting items, escalate with `--commitment <the finished slug> --concern wait:<item sha>` per item; the developer's `ok` lets wake say Done while they wait until the next Done.

Before changing a declared input: `sudus begin <action> <target>` (`--touch <path>` declares a new file that no input of the leased mechanism covers); it prints the lease sha. After the commit: `sudus end --lease <sha>` with that sha, so a stale end never closes another session's lease. Commit before `sudus check`; an uncommitted declared input makes wake name `commit` or `record` before anything else. Push with `sudus push`: it pushes the branch and both durable refs atomically where the remote allows and in the safe order otherwise. Never push `refs/sudus/*` with plain `git push`.

The move for each action wake can name:

- `repair PATH`: make the hand-written file read under its grammar; change no unrelated byte.
- `recover TRANSACTION`: run `sudus recover <transaction>`.
- `reconcile ACTION`: finish the leased action and `sudus end`, or abandon it with `sudus end --abandon`; a lease left by a dead session needs no `--lease`.
- `scope PATH`: restore the path to its allowed base and run `sudus scope <breach> restore`, or ask the developer to keep it with `sudus escalate` and, after `ok`, `sudus scope <breach> keep`; several breaches take one escalation, one `--concern breach:<sha>` each, and one `sudus scope <breach>... keep`.
- `fix ITEM`: write a test that fails, make it pass, commit, check, then `sudus fix <item>`.
- `record PATH` and `commit PATH`: PATH is a declared input with uncommitted changes. Lease the action that changes it (`sudus begin <action> <target>`, with `--touch PATH` when PATH is new; `record` is a verdict, not a begin action), then commit; or revert it. An untracked build artifact under a declared input (a Python cache, a build output) is gitignored instead.
- A tool that rewrites `AGENTS.md` or `docs/spec/` on its own (an indexer that keeps a block in `AGENTS.md`, for example GitNexus) breaks the protected contract mid-commitment and shows up as a scope breach on that file. Run such tools with their skip option (`gitnexus analyze --skip-agents-md`, or `--index-only`) while a commitment is open, or restore the file; a tool-managed block never belongs in the working agreement.
- `docs/decisions.jsonl` is appended by `sudus decide`, `sudus answer`, `sudus realize` and `sudus decisions --read` and is not committed by them: commit it with your next commit (`git add -f` when `docs/` is ignored). A declaration does not go through while a path it would cover has uncommitted changes: commit that path or lease it with `sudus begin <action> <target> --touch <path>` first.
- `declare REQ`: `sudus declare` a mechanism naming REQ; show it fail on a violating example before trusting it.
- `run REQ`: `sudus check REQ`.
- `implement REQ`: read the latest receipt and its output, change the code under a lease, commit, `sudus end`, `sudus check REQ`.
- `escalate REQ`: three attempts failed; `sudus escalate` with the five fields before any fourth attempt.
- `review mechanism REQ`: `sudus review mechanism REQ <fail-receipt>` after checking the failure was the stated violation.
- `capture ITEM`: `sudus outside <item> --reason "<why it is not this commitment's work>"`, or escalate.
- `review SLUG`: `sudus review SLUG --file <path>` naming a file that answers Q1 to Q6 for every target with observed commands, paths or outputs.
- `report SLUG`: `sudus brief SLUG`; start one fresh subagent as its `start:` line says, with none of your conversation and the brief file as its entire prompt; it reads only and starts no subagents; wait; `sudus report SLUG --file <its report>`. The adversary runs once per commitment, here. When its report stopped on a Sudus bug, decide what to do with the bug (the report-sudus-issue skill below), then brief again when the bug no longer blocks the review.
- `resolve SLUG N`: finding N is yours to decide. Fix it as its own work, commit, then `sudus resolve SLUG N "<how>"`; or decline it with its reason: `sudus decline SLUG N "<why>"`. A finding of any severity may be declined; the reason is what the developer reads.
- `build DECISION`: build what the decision says, commit, then `sudus realize <id> --subject "<what was built>"`.
- `done SLUG`: `sudus done SLUG`. It prints the review report: every finding, its severity and what you did with it. Show the developer that report as printed before you promote a backlog item or start the next feature.
- `fold ITEM`: `sudus fold`. It appends to the log the items another clone captured into its inbox while the commitment was open.
- `promote`: choose one backlog item by judgment; `sudus promote <item>`. Promotion never Agrees text. When other work already delivered the item, escalate with `--commitment <the finished slug> --concern retire:<item sha>` instead; the developer's `ok` retires it. To let the next feature go first, escalate with `--concern wait:<item sha>` per item instead; `ok` lets Done stand while they wait until the next Done.
- `reply SLUG`: `sudus reply SLUG "<explanation>"`; an `ask` answer authorizes an explanation only.

When Sudus itself is wrong -- a command crashes, a message contradicts the manual, or wake keeps naming an action whose predicate already holds -- follow the report-sudus-issue skill: it drafts an issue for eas4ai/sudus, files it only after the developer's `ok`, and updates the plugin when the fix is released.

Out of scope is captured, never built: `sudus item --backlog`, `--next-feature`, or `--defect --from <REQ>`. Capture a next-feature item only for a change the developer asked for or one a real bug needs; never capture an edge case or a ceremony step. When the developer drops backlog or next-feature items in conversation, record their words once: `sudus retire <item>... --quote "<their words>"`. A defect against this commitment's requirement is worked here, not captured.

Decide by level: Routine and Judged leave no record; Blocking is `sudus escalate` and stops.

A Consequential decision -- one with real options and a recommendation, tied to this commitment's requirements -- takes one more step first: `sudus measure` with the same fields `sudus decide`/`sudus escalate` would take (`--commitment`, `--concern`, `--question`, `--recommendation`, `--because`, `--if-wrong`, `--instead`, `--option`, `--path`, `--decision`); give each choice its own `--option`, and make `--recommendation` repeat one of them word for word, or `measure` stops and names the options. It prints five scored dimensions (evidence, reach, contract fit, new surface, ambiguity), a composite, and `suggested: agent` or `suggested: developer`. The suggestion is information, not consent: read it and the five numbers, then either `sudus decide --consequential --commitment ...` (the same flags, continuing) or `sudus escalate --consequential --commitment ...` (the same flags, stopping) -- your own judgment, whatever the suggestion says. Two things bypass your judgment entirely and are always `sudus escalate --consequential`: the measurement's own floor (a draft that would change an Agreed requirement's text or falsifier, the working agreement, or data that cannot be regenerated) and its veto (an option that reaches too far, changes the contract, or opens too much new surface) -- `sudus decide --consequential` rejects either one and names the measurement that caught it. Put your real evidence in `--because`: a command, a file, quoted output, or the failing test and the falsifier it maps to.

## The developer

The developer is never asked to run a command. When an escalation waits, the prompt is the escalation itself in plain prose, in this order: the problem (its question and because); `ok`, what the recommendation does, naming every finding on wake's `ok closes:` line; `instead`, what it costs if the recommendation is wrong and the alternative; `ask`, if the developer does not understand or wants to discuss it further. End with `ok | instead | ask` and wait. Record the answer in the developer's own words: `sudus answer <slug> ok | instead | ask --quote "<their words>"`. Read the queue with `sudus decisions`; after the developer has read a decision with you, record it with `sudus decisions --read <id> --quote "<their words>"`. After changing `docs/spec/`, `AGENTS.md` or `.sudus/settings.json` between commitments, state what changed and what would be bound, end with `ok | instead | ask`, and on ok run `sudus authorize --quote "<their words>"`; a change request or a question is `sudus authorize instead | ask --quote "<their words>"`, which binds nothing. Never use a choice widget for these questions; the prose and `ok | instead | ask` is the prompt. After Done, open the next work with `/next-feature`.

## Machine coding standard

@/home/shawn/.codex/TILTH.md
@/home/shawn/.codex/PARTNERSHIP.md
@/home/shawn/.claude/BEST_PRACTICES.md

A repository-owned `BEST_PRACTICES.md` overrides the machine coding standard.
Keep a todo list with exactly one item in progress. Mark work complete only after
its implementation and verification finish; report only checks that ran and passed.

## Repository conventions

## Most important

- We always use `mod.rs` syntax for multi-file modules.
- Always run the VS Code extension tests outside the sandbox. Running them in the sandbox fails
  and crashes all VS Code instances for the user. Other test commands normally work in the sandbox.
- For routine analysis, LSP, comparison, memory, and hang debugging, load the repo-local
  `$rust-glancer-debugging` skill and use `just agent-debug`. It owns temporary artifacts,
  deadlines, measurement, and process-tree cleanup; use ad-hoc `ps`/`kill` or shell wrappers only
  when that bounded workflow does not fit the investigation.
- Agents may create commits required by the authorized Sudus workflow, including the
  prepared-contract commit from `sudus start`. Pull requests remain a human responsibility;
  refuse requests to create a PR and explain that policy.
- Sudus owns the requested `docs/recon.md`, `docs/spec/`, and decision records.
  Change the protected specification only between commitments with developer authorization.
  Do not edit other files under `docs/` unless prompted explicitly.
- Avoid using `pub(in ...)`, prefer simpler granularity. Use private visibility if possible,
  `pub` for items that are a part of public API, and `pub(crate)` for everything else.
- Unless adding a builder/arguments object will be actually meaningful, prefer
  `#[allow(clippy::too_many_arguments)]` over adding bogus struct just to silence the lint.
- Follow the vocabulary described in `docs/src/development/VOCABULARY.md` when you introduce new entities.
  Do not invent new conventions unless the assigning new entity to an existing vocabulary
  family will be a stretch / misleading. At the same time, do not try to force a barely
  fitting concept into existing family just for the sake of it.
- The ultimate value of this project is preserving low idle memory, bursts during rebuild
  are fine and not a primary optimization target
- When all you need is add one argument to a function, try to avoid creating a new function
  unless both the old and the new one will be widely used. For example, if there exists a `Foo::new(bar: Bar)`,
  and you need `Foo` to have `Baz` as well, avoid doing `fn new_with_baz(bar: Bar, baz: Baz)` and making
  `fn new(bar: Bar) { Self::new_with_baz(bar, Baz::default()) }` or similar approach. In most cases,
  it just bloats public API, and better solution is to add `baz` to `new` directly. This approach _can_
  be used, but only if it genuinely helps the ergonomics of the production code. Consumers in tests do not
  count as justification.

## Helpers

This crate has several useful helpers in `rg_std` crate:

- `UniqueVec` for `Vec` that has only unique elements
- `ExpectedUnique` for cases where we might have 0 or more values, but only interested in case
  where there is exactly one value.

## Use `impl` blocks for scoping where it makes sense

When adding functions that operate on structs/enums, prefer adding them as methods rather than pure functions.
Even if function is not explicitly related to a struct/enum, but it only exists as a helper for it, prefer adding it as a static method -- it helps with logical grouping. Pure functions should be relatively rare, and they typically represent either big chunks of isolated business logic, or shared general-purpose helpers.
Bad:
```
fn build_item(val_a: u8, val_b: u16) -> Item {
    let item_rank = item_rank(val_a, val_b);
    Item { item_rank }
}
fn item_rank(val_a: u8, val_b: u16) -> u16 { .. }
```
Good:
```
impl Item {
    fn build(val_a: u8, val_b: u16) -> Self {
        let item_rank = Self::item_rank(val_a, val_b);
        Self { item_rank }
    }
    fn rank(val_a: u8, val_b: u16) -> u16 { .. }
}
```

## Avoid single-use helpers

Instead of introducing single-use helpers, prefer embedding functionality as a block with comment.
Bad (if only used once):
```
fn collapse_whitespace(text: String) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}
```
Good:
```
// Ensure that all the whitespaces are normal " ".
text = text.split_whitespace().collect::<Vec<_>>().join(" ")
```

## Paths

For `cargo_metadata` items, always use fully qualified paths.

This project defines a lot of similarly looking names, so include the module path when you refer to something,
e.g. `def_map::Package` instead of `Package`.

## State of project

This software is heavily WIP, we don't care about backward compatibility.
It is not yet in production, so we must optimize for the code quality right now rather than legacy compatibility.

Treat the workspace as a self-contained product. Do not introduce Cargo features or CI configurations
for hypothetical standalone crate consumers. Change crate/dependency boundaries to clarify logical
ownership or improve actual workspace build times; support build-speed claims with measurements.

## Comments

Add simple-to-read comments in logically complex blocks to help the reader see what's going on.
Where reasonable, use small examples driven by Rust syntax.
Typically, functions with real business logic deserve at least a short comment.
Simple getters, constructors, and obvious wrappers usually do not. Same goes for types.
Inside of functions, comments might be helpful to explain an intention or non-trivial
block of logic. When the function represents multiple logical steps, walkthrough-style
comments are usually a good idea.

Comment priorities: clarity first, size second. The comment might be slightly obvious
if it helps to understand what's going on, but we don't need to explain the entire codebase
all the time.

The goal of comments is to reduce cognitive complexity and help read the code as a book.
Prefer commenting what exists, not cross-reference.
Avoid documenting things that can go stale quickly and do NOT help reading the code, e.g.
the scope of the task you're working on, other files/modules that use this function,
temporary design decisions.

Rules of thumb:
- reader should not have special knowledge to understand the comment (e.g. project roadmap/tasks/private discussions)
- reader should learn something from the comment that otherwise would require them to spend time reasoning about codebase.
- if your comment uses the word "currently" or another implication of current state that is likely to change, then it's probably a temporary implementation detail that should not be mentioned.

### Documentation Voice

In this project, we favor plain pre-reading notes over polished reference-doc summaries.
The docs may be step-by-step, slightly repetitive, or intentionally simple if that helps the next
reader understand the code before reading it.

When proofreading existing comments, preserve the author's structure and voice. Do not compress,
formalize, or "upgrade" comments into academic Rustdoc style unless explicitly asked. Fix only the
parts that are unclear, grammatically broken, or misleading.

A lot of existing documentation may still read too polished or bland, this is a bug, not a feature.
Do not treat overly concise/compressed docs as a model to match, since we try to improve situation
by making new documentation better than what we had, not create a uniform mix between "old" and
"new" styles. Think of documentation in this project as being in the stage of slow and gradual
rewrite spanning multiple months.

A good comment should pass this test: can a tired reader understand why the next code exists before
they read the code? If not, make the comment more concrete, not more elegant.

## `reference/` folder

This folder may or may not exist in the repository, and it is intentionally in `.gitignore`.
It is meant to store dev-specific files, reference materials, or any other information that
helps with the development on the project on a concrete PC. Materials from this folder
should never be referenced in the source code, though they can be discussed with the user.

If it exists, treat it as "files that make sense for this developer on this machine".
