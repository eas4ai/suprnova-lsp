# Compiler child-module fixture

The attribute macro emits storage declarations in `generated`, direct and reverse
`Bridge` impls, and the `Model` impl. The source trait supplies `query`; the export
must not supply a replacement stub. The project tests use the original source and
this genuine format-61 compiler export to exercise module creation, storage aliases through private child modules, field types,
associated identities, re-exports, failed candidates and both indexing modes.

Regenerate from the workspace root with an owned target directory:

```sh
cargo +nightly-2026-08-19 rustdoc \
  --manifest-path crates/engine/rustdoc/fixtures/child-model/Cargo.toml \
  --lib --locked --target-dir target/agent-debug/child-model -- \
  -Z unstable-options --output-format json \
  --document-private-items --document-hidden-items
```

Copy `target/agent-debug/child-model/doc/rustdoc_macro_support.json` to `export.json`
and update `producer.txt` with its digest. This fixture covers regression cases;
Devlist's full captured export remains the application acceptance evidence.
