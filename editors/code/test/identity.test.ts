import * as assert from "node:assert/strict";
import * as path from "node:path";
import * as vscode from "vscode";

import { EXTENSION_COMMANDS } from "../src/commands";
import { ExtensionConfig } from "../src/config";
import { withTimeout } from "./async";
import {
  assertDefinition,
  clientState,
  serverOutput,
  waitForReadyWorkspace,
} from "./extension-harness";

suite("Suprnova LSP identity", () => {
  test("IDN-001 keeps fork identity and upstream commands/settings independent", async function () {
    this.timeout(180_000);
    const fork = vscode.extensions.getExtension("eas4ai.suprnova-lsp");
    assert.ok(fork, "the extension host must load eas4ai.suprnova-lsp");
    assert.equal(fork.packageJSON.displayName, "Suprnova LSP");
    const upstream = vscode.extensions.getExtension("rust-glancer.rust-glancer");
    assert.ok(upstream, "the isolated upstream-identity fixture must be loaded beside the fork");
    await withTimeout(upstream.activate(), "activate upstream identity fixture", 30_000);
    await withTimeout(fork.activate(), "activate Suprnova LSP", 30_000);

    // Changing an upstream setting must not change the fork's captured startup policy.
    const configuration = vscode.workspace.getConfiguration("rust-glancer");
    const original = configuration.inspect("cargo.target")?.workspaceValue;
    const before = ExtensionConfig.read();
    try {
      await configuration.update(
        "cargo.target",
        "upstream-only-target",
        vscode.ConfigurationTarget.Workspace,
      );
      assert.deepEqual(ExtensionConfig.read(), before);
      for (const command of Object.values(EXTENSION_COMMANDS)) {
        assert.ok(command.startsWith("suprnova-lsp."), command);
      }
      const registered = await vscode.commands.getCommands(true);
      assert.ok(registered.includes("rust-glancer.reindexWorkspace"));
      assert.ok(registered.includes("suprnova-lsp.reindexWorkspace"));
      const count = await vscode.commands.executeCommand<number>("rust-glancer.reindexWorkspace");
      assert.equal(count, 1, "upstream command must invoke its own fixture handler");

      const document = await vscode.workspace.openTextDocument(
        vscode.Uri.file(
          path.resolve(fork.extensionPath, "../../test_targets/simple_crate/src/lib.rs"),
        ),
      );
      await vscode.window.showTextDocument(document);
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.startServer),
        "start fork",
        30_000,
      );
      await waitForReadyWorkspace("simple_crate");
      await assertDefinition(document, "|left + right", "|left: i32");
      await withTimeout(
        vscode.commands.executeCommand("suprnova-lsp.reindexWorkspace"),
        "reindex fork",
        30_000,
      );
      await waitForReadyWorkspace("simple_crate");
      await assertDefinition(document, "|left + right", "|left: i32");
      assert.equal(await vscode.commands.executeCommand("rust-glancer.reindexWorkspace"), 2);
      const output = await serverOutput();
      assert.ok(output.includes("suprnova-lsp client started"), output);
      assert.ok(!output.includes("Rust Glancer failed"), output);
      await withTimeout(
        vscode.commands.executeCommand("suprnova-lsp.stopServer"),
        "stop fork",
        30_000,
      );
      assert.equal((await clientState()).session, undefined);
    } finally {
      await configuration.update("cargo.target", original, vscode.ConfigurationTarget.Workspace);
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.stopServer),
        "stop identity test server",
        30_000,
      );
    }
  });
});
