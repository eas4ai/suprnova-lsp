# Suprnova LSP for Zed

This extension connects Zed's built-in Rust language support to
`suprnova-lsp lsp`. It uses an explicitly configured executable or one installed
on `PATH` when available. Otherwise, the extension downloads its pinned
Suprnova LSP release and keeps it in Zed's extension work directory.

Managed installation is available for:

- macOS on Apple Silicon and Intel
- Linux with glibc 2.28 or newer on AArch64 and x86-64
- Windows on x86-64

Other platforms can still use the extension by configuring a compatible
`suprnova-lsp` executable explicitly.

## Development

Build the language server first:

```text
cargo build --release -p suprnova-lsp
```

In Zed, run `zed: extensions`, choose **Install Dev Extension**, and select the
`editors/zed` directory from this repository.

For a deterministic local setup, add the following project settings and replace
the binary path with the path to this checkout:

```json
{
  "languages": {
    "Rust": {
      "language_servers": ["suprnova-lsp", "!rust-analyzer"]
    }
  },
  "lsp": {
    "suprnova-lsp": {
      "binary": {
        "path": "/absolute/path/to/suprnova-lsp/target/release/suprnova-lsp",
        "arguments": ["lsp"]
      }
    }
  }
}
```

The extension uses `["lsp"]` when `binary.arguments` is omitted. If arguments
are configured, they replace that default and must retain the `lsp` subcommand.
When `binary.path` is omitted, the extension looks for `suprnova-lsp` on the
project's `PATH` and then falls back to its managed binary. Managed downloads
are versioned, so restarting Zed can use an already installed server without
network access. A new extension release pins a new server release instead of
following the latest GitHub release automatically.

Suprnova LSP applies its server-side configuration defaults when no
initialization options are provided. Overrides use Zed's standard
`initialization_options` object:

```json
{
  "lsp": {
    "suprnova-lsp": {
      "initialization_options": {
        "cfg": {
          "test": true,
          "atoms": []
        },
        "diagnostics": {
          "onSave": true,
          "command": "check",
          "cargoArguments": ["--workspace"]
        }
      }
    }
  }
}
```

See the project configuration guide at
[`docs/src/usage/CONFIGURE.md`](../../docs/src/usage/CONFIGURE.md) for the full
set of initialization options.

To enable server logs, set an environment override on the language-server
binary:

```json
{
  "lsp": {
    "suprnova-lsp": {
      "binary": {
        "env": {
          "SUPRNOVA_LSP_LOG": "rg_lsp_server=debug,rg_lsp_engine=debug"
        }
      }
    }
  }
}
```

Use Zed's `dev: open language server logs`, `editor: restart language server`,
and `editor: stop language server` actions while troubleshooting.

## Building the extension

Zed compiles development extensions automatically. To check the WebAssembly
build directly:

```text
rustup target add wasm32-wasip2
cargo build -p suprnova-lsp-zed --target wasm32-wasip2
```
