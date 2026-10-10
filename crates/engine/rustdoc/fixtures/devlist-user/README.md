# Genuine Devlist User capture

`export.json.gz` preserves the full format-61 compiler export, including IDs and
references. It is compressed only for repository storage. `producer.json` records
the locked producer invocation, pinned Suprnova revision, resolved features,
application/dependency/sysroot source fingerprints, export digest and compiler
peak RSS. Runtime secrets and application data are not captured.

Prepare a new capture explicitly with `python3 tools/sudus-suprnova-user.py capture`.
This uses the authorized Devlist checkout and its pinned Git framework, with
nightly-2026-08-19 and rust-src installed. It writes compiler outputs under the LSP
repository's `target/agent-debug/`. It never uses the protected framework checkout.

Run acceptance with `python3 tools/sudus-suprnova-user.py`. The mechanism validates
the capture and external source fingerprints before and after its probes. A changed
application, dependency or sysroot cannot silently stand in for the captured input.
The engine reads a decompressed copy owned by that run. The fixture stays intact
when invalid-candidate controls mutate the run's copy.

The Rust executable uses the shipped memory backend, workspace residency, and
in-memory probes inside the original User source method. It does not edit Devlist.
The application target control is ignored in ordinary workspace tests and is
explicitly selected by the mechanism with its validated plan.
