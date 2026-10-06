# Suprnova LSP VS Code Extension

VS Code client for `suprnova-lsp lsp`.

## Development

Install dependencies and build the bundled extension:

```text
npm install
npm run compile
```

Launch `Run Suprnova LSP Extension` from VS Code. The launch configuration
opens the repository root in an Extension Development Host.

During development the extension starts the configured `suprnova-lsp`
executable, or `suprnova-lsp` from `PATH` when no path is configured. Build the
server binary first if needed:

```text
cargo build --release -p suprnova-lsp
```

Then point the extension at that binary:

```json
{
  "suprnova-lsp.server.path": "/absolute/path/to/checkout/target/release/suprnova-lsp"
}
```

## Testing

Run the unit tests:

```text
npm run test:unit
```

Run the extension-host smoke test:

```text
npm run test:e2e
```

The VS Code test runner uses a desktop Extension Development Host, so a short
lived VS Code window is expected locally. For Linux CI/headless environments,
run the same command under a virtual display such as `xvfb-run`.

For faster CI checks that do not launch VS Code:

```text
npm run fmt:check
npm run lint
npm run check
npm run check:test
npm run check:unit
```

The same checks are available through the client Justfile:

```text
just lint
# From the repository root:
just client lint
```

## Useful Settings

The extension ID is `eas4ai.suprnova-lsp`. Copy any desired `rust-glancer.*`
settings to `suprnova-lsp.*` manually; the old prefix controls the upstream
extension. Distinct installation IDs permit both extensions to be installed.
Enabling both Rust providers may produce separate editor results.

```json
{
  "suprnova-lsp.server.path": null,
  "suprnova-lsp.server.extraEnv": {},
  "suprnova-lsp.cargo.target": null,
  "suprnova-lsp.cfg.test": true,
  "suprnova-lsp.cfg.atoms": [],
  "suprnova-lsp.indexing.performancePreference": "faster-builds",
  "suprnova-lsp.indexing.packageBatchSize": 512,
  "suprnova-lsp.cache.packageResidency": "all-offloadable",
  "suprnova-lsp.rustdoc.inputs": [],
  "suprnova-lsp.rustdoc.automatic": {
    "enabled": true,
    "debounceMs": 2000
  },
  "suprnova-lsp.diagnostics.onStartup": false,
  "suprnova-lsp.diagnostics.onSave": false,
  "suprnova-lsp.diagnostics.command": "check",
  "suprnova-lsp.diagnostics.cargoArguments": ["--workspace"],
  "suprnova-lsp.diagnostics.extraEnv": {}
}
```

Use `suprnova-lsp.server.extraEnv` for server logs, for example:

```json
{
  "suprnova-lsp.server.extraEnv": {
    "SUPRNOVA_LSP_LOG": "rg_lsp_server=debug,rg_lsp_engine=debug"
  }
}
```

Use `suprnova-lsp.diagnostics.extraEnv` for environment variables that should
only affect Cargo diagnostics, for example custom cfg flags:

```json
{
  "suprnova-lsp.diagnostics.extraEnv": {
    "RUSTFLAGS": "--cfg tokio_unstable"
  }
}
```

## Troubleshooting

Open the `Suprnova LSP` output channel first. It records the workspace root,
server command, server source, process exit, server stderr, and engine startup
logs.

If the command palette does not show Suprnova LSP commands in the Extension
Development Host, VS Code probably launched with the wrong
`extensionDevelopmentPath`; use the checked-in launch configuration.
