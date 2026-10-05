#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const workspaceRoot = resolve(extensionRoot, "../..");
const executableName = process.platform === "win32" ? "rust-glancer.exe" : "rust-glancer";
const server =
  process.env.RUST_GLANCER_TEST_SERVER ?? join(workspaceRoot, "target", "release", executableName);
const testCli = join(extensionRoot, "node_modules", "@vscode", "test-cli", "out", "bin.mjs");

if (!existsSync(server)) {
  fail(`Expected server binary does not exist: ${server}`);
}
if (!existsSync(testCli)) {
  fail(`Expected local VS Code test CLI does not exist: ${testCli}. Run npm install.`);
}

let fixtureEnvironment = {};
if (process.env.RUST_GLANCER_AUTOMATIC_RUSTDOC_FIXTURE === undefined) {
  const prepared = spawnSync("python3", [join(workspaceRoot, "tools/prepare-automatic-editor-fixture.py")], {
    cwd: workspaceRoot,
    env: process.env,
    encoding: "utf8",
    timeout: 150_000,
  });
  if (prepared.error !== undefined || prepared.status !== 0) {
    fail(`Could not prepare automatic model fixture: ${prepared.error?.message ?? prepared.stderr}`);
  }
  fixtureEnvironment = JSON.parse(prepared.stdout);
}

const result = spawnSync(process.execPath, [testCli, ...process.argv.slice(2)], {
  cwd: extensionRoot,
  env: {
    ...process.env,
    ...fixtureEnvironment,
    RUST_GLANCER_EXTENSION_TEST: "1",
    __RUST_GLANCER_SERVER: server,
  },
  stdio: "inherit",
});

if (result.error !== undefined) {
  fail(result.error.message);
}
if (result.signal !== null) {
  fail(`VS Code test CLI terminated by signal ${result.signal}.`);
}
if (result.status !== 0) {
  fail(`VS Code test CLI exited with status ${result.status}.`);
}

function fail(message) {
  console.error(message);
  process.exit(1);
}
