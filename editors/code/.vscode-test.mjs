import { defineConfig } from "@vscode/test-cli";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const extensionRoot = dirname(fileURLToPath(import.meta.url));
const userDataDir = mkdtempSync(resolve(tmpdir(), "suprnova-lsp-user-data-"));
const extensionsDir = mkdtempSync(resolve(tmpdir(), "suprnova-lsp-extensions-"));
const workspaceFile = resolve(userDataDir, "acceptance.code-workspace");
// Seed the existing client trace in this isolated workspace. VS Code's settings
// API writes only registered keys, while LanguageClient reads this key directly.
const settings =
  process.env.SUPRNOVA_LSP_EXTENSION_TEST_TRACE === "json"
    ? { "suprnova-lsp.trace.server": { verbosity: "verbose", format: "json" } }
    : {};
const responsivenessRoot = process.env.SUPRNOVA_LSP_RESPONSIVENESS_APPLICATION;
if (responsivenessRoot !== undefined) {
  Object.assign(settings, {
    "files.autoSave": "off",
    "files.hotExit": "off",
    "suprnova-lsp.rustdoc.inputs": [
      {
        workspaceRoot: responsivenessRoot,
        manifestPath: resolve(responsivenessRoot, "Cargo.toml"),
        targetName: "directory",
        targetKind: "lib",
        exportPath: process.env.SUPRNOVA_LSP_RESPONSIVENESS_EXPORT,
        itemPath: "directory::models::user::User",
      },
    ],
    "suprnova-lsp.rustdoc.automatic": { enabled: false },
    "suprnova-lsp.cfg.test": false,
    "suprnova-lsp.cache.packageResidency": "workspace",
    "suprnova-lsp.indexing.performancePreference": "faster-builds",
  });
}
// Tests add genuine Cargo roots. Start in workspace mode so adding the first one cannot
// leave a single-folder conversion pending and block every subsequent folder addition.
writeFileSync(
  workspaceFile,
  JSON.stringify({
    folders: [{ path: responsivenessRoot ?? resolve(extensionRoot, "../../test_targets") }],
    settings,
  }),
);

export default defineConfig({
  files: "out/test/**/*.test.js",
  version: vscodeTestVersion(),
  extensionDevelopmentPath:
    process.env.SUPRNOVA_LSP_IDENTITY_FIXTURE === undefined
      ? extensionRoot
      : [extensionRoot, process.env.SUPRNOVA_LSP_IDENTITY_FIXTURE],
  workspaceFolder: workspaceFile,
  env: {
    SUPRNOVA_LSP_VSCODE_USER_DATA_DIR: userDataDir,
  },
  // The completion smoke test needs renderer input: programmatic document edits do not exercise
  // automatic suggestions. Keep the debugger local and let Chromium choose the port.
  launchArgs: [
    "--disable-extensions",
    "--disable-workspace-trust",
    "--enable-smoke-test-driver",
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=0",
    `--user-data-dir=${userDataDir}`,
    `--extensions-dir=${extensionsDir}`,
  ],
  mocha: {
    timeout: 60_000,
    ...(process.env.SUPRNOVA_LSP_EXTENSION_TEST_GREP === undefined
      ? {}
      : {
          grep: process.env.SUPRNOVA_LSP_EXTENSION_TEST_GREP,
          reporter: "json",
          reporterOptions: { output: process.env.SUPRNOVA_LSP_EXTENSION_TEST_REPORT },
        }),
  },
});

function vscodeTestVersion() {
  const packageJsonPath = resolve(extensionRoot, "package.json");
  const packageJson = JSON.parse(readFileSync(packageJsonPath, "utf8"));
  const engine = packageJson.engines?.vscode;
  const version = typeof engine === "string" ? engine.match(/\d+\.\d+\.\d+/)?.[0] : undefined;

  if (version === undefined) {
    throw new Error(`Could not read VS Code engine version from ${packageJsonPath}`);
  }

  // The extension must load on the minimum supported VS Code version. Keeping
  // this derived from package.json makes Renovate engine bumps move the smoke
  // test version along with the manifest.
  return version;
}
