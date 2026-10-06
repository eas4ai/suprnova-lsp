# Suprnova LSP

An experimental Rust language server for Suprnova applications, designed to keep
idle memory low. It understands generated model APIs by importing compiler-produced
rustdoc JSON. It runs independently of rust-analyzer.

## Suprnova support

- Hover, completion, and type inference for generated model methods, including
  query builders such as `Post::query()` returning `Builder<Post>`.
- Automatic discovery of Suprnova models and their Cargo targets.
- Background rustdoc exports that refresh after saved changes, with a configurable
  two-second debounce.
- Indexing and cache settings for balancing memory use and indexing speed.

This is a preview. Initial indexing and compiler exports can take time; hover may
show **Loading…** while indexing is in progress. Compiler builds can use more memory
than the idle language server.

## Install in VS Code

Download a VSIX from the [GitHub releases](https://github.com/eas4ai/suprnova-lsp/releases)
page that matches your operating system and architecture:

| Platform            | VSIX target    |
| ------------------- | -------------- |
| Linux x64           | `linux-x64`    |
| Linux ARM64         | `linux-arm64`  |
| macOS Intel         | `darwin-x64`   |
| macOS Apple Silicon | `darwin-arm64` |
| Windows x64         | `win32-x64`    |

Run **Extensions: Install from VSIX…** in VS Code, select the downloaded file, and
reload the window. Each VSIX includes the matching server binary. The extension ID
is `eas4ai.suprnova-lsp`.

Install Rust with Cargo and rustup. Automatic model exports use a pinned nightly
toolchain, which you must install explicitly:

```sh
rustup toolchain install nightly-2026-08-19 --profile minimal
```

For standard-library indexing, install `rust-src` for your application's active
toolchain from its workspace:

```sh
rustup component add rust-src
```

Open your Suprnova application's Cargo workspace. Automatic discovery and exports
are enabled by default; you do not need to maintain a list of models. Exports use
locked dependency resolution, so keep the application's `Cargo.lock` up to date.

## Configuration

Settings use the `suprnova-lsp.*` prefix. For example, in VS Code's `settings.json`:

```json
{
  "suprnova-lsp.rustdoc.automatic": {
    "enabled": true,
    "debounceMs": 2000,
    "toolchain": "nightly-2026-08-19"
  },
  "suprnova-lsp.indexing.performancePreference": "lower-peak-memory"
}
```

The example selects smaller indexing batches to reduce peak memory; the default
is `faster-builds`. Restart the server after changing the indexing preference.
Increase `debounceMs` to give bursts of saved changes more time to settle.

Prepared rustdoc exports can also be configured through
`suprnova-lsp.rustdoc.inputs`. See the [extension guide](editors/code/README.md) for
additional settings and development commands.

For troubleshooting, open the **Suprnova LSP** output channel. After correcting a
toolchain or compilation error, run **Suprnova LSP: Reindex Workspace** to retry.
Server logging is controlled by `SUPRNOVA_LSP_LOG`.

## Build from source

Build the server from the repository root:

```sh
cargo build --release --locked -p suprnova-lsp
```

To build a local VS Code package:

```sh
cd editors/code
npm ci
npm run package:vsix
```

GitHub release CI builds native VSIX files and standalone server archives for all
five platforms above. Releases include `SHA256SUMS` for verifying downloads.

## Contributing

See the [contributor guide](docs/src/intro/CONTRIBUTING.md) for the development
workflow. Keep changes focused on Suprnova editor support and preserve low idle
memory use.

## License

Licensed under either the [MIT license](LICENSE-MIT) or
[Apache License, Version 2.0](LICENSE-APACHE), at your option.

## Attribution

Suprnova LSP builds on [Rust Glancer](https://github.com/rust-glancer/rust-glancer),
created by Igor Aleksanov. We thank its author and contributors for the language
server foundation. The original copyright notices and dual license are preserved.
