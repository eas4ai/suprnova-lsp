# Installation

Install `rust-src` for standard-library analysis:

```sh
rustup component add rust-src
```

Suprnova LSP runs independently of rust-analyzer. Choose the Rust provider you
want enabled in your editor. Automatic generated-model support also needs the
rustdoc toolchain described in the [repository README](https://github.com/eas4ai/suprnova-lsp#readme).

## VS Code, Cursor and VSCodium

Download the VSIX matching your platform from the
[Suprnova LSP GitHub releases](https://github.com/eas4ai/suprnova-lsp/releases).
Choose **Extensions → Install from VSIX**, then reload the editor. The extension
ID is `eas4ai.suprnova-lsp`; its settings and commands use `suprnova-lsp`.

For a local build, clone this repository and run `just package-vsix`. See the
[VS Code extension guide](https://github.com/eas4ai/suprnova-lsp/blob/main/editors/code/README.md) for development,
configuration and tests.

## Zed

Use **Install Dev Extension** and select this checkout's `editors/zed` directory.
The adapter first uses a configured executable, then `suprnova-lsp` on `PATH`.
Otherwise, it downloads its pinned `suprnova-v<VERSION>` release from this
repository.

Enable the adapter in your settings:

```json
{
  "languages": {
    "Rust": {
      "language_servers": ["suprnova-lsp", "!rust-analyzer"]
    }
  }
}
```

See the [Zed guide](https://github.com/eas4ai/suprnova-lsp/blob/main/editors/zed/README.md) for an explicit binary path
and initialization options. These instructions do not require a marketplace
publication.

## Neovim and other LSP clients

Configure your client's Rust language server command as `suprnova-lsp lsp`.
Download the matching server archive from GitHub releases, or build it with:

```sh
cargo build --release -p suprnova-lsp
```

Pass server configuration through LSP initialization options. See
[Configuration](CONFIGURE.md) for the option names. Report editor integration
issues in the [fork repository](https://github.com/eas4ai/suprnova-lsp/issues).
