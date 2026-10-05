// Keep E2E coverage about working user flows. Detailed completion and cancellation contracts
// belong in the engine and transport tests, where their inputs and ordering can be controlled.
import * as assert from "node:assert/strict";
import * as path from "node:path";
import * as vscode from "vscode";

import { EXTENSION_COMMANDS } from "../src/commands";
import { ExtensionConfig, RustdocConfig } from "../src/config";
import { waitFor, withTimeout } from "./async";
import { completeInEditor } from "./completion-scenario";
import { inspectDocumentation } from "./documentation-scenario";
import {
  assertDefinition,
  clientState,
  serverOutput,
  waitForReadyWorkspace,
} from "./extension-harness";

suite("Rust Glancer extension", () => {
  let projects: vscode.Uri;

  suiteSetup(async () => {
    const extension = vscode.extensions.getExtension("rust-glancer.rust-glancer");
    assert.ok(extension, "VS Code should load Rust Glancer");
    projects = vscode.Uri.file(path.resolve(extension.extensionPath, "../../test_targets"));
    await withTimeout(extension.activate(), "activate Rust Glancer", 30_000);
  });

  teardown(async function () {
    if (this.currentTest?.state === "failed") {
      const evidence = await Promise.allSettled([clientState(), serverOutput()]);
      for (const result of evidence) {
        console.error(result.status === "fulfilled" ? result.value : result.reason);
      }
    }
  });

  suiteTeardown(async () => {
    await withTimeout(
      vscode.commands.executeCommand(EXTENSION_COMMANDS.stopServer),
      "stop test server",
    );
  });

  test("serves both Rust projects through one server and survives reindex and restart", async () => {
    const simple = await vscode.workspace.openTextDocument(
      vscode.Uri.joinPath(projects, "simple_crate", "src", "lib.rs"),
    );
    await vscode.window.showTextDocument(simple);
    await waitForReadyWorkspace("simple_crate");
    await assertDefinition(simple, "|left + right", "|left: i32");

    const commands = await vscode.commands.getCommands(true);
    for (const command of [
      EXTENSION_COMMANDS.showServerActions,
      EXTENSION_COMMANDS.startServer,
      EXTENSION_COMMANDS.restartServer,
      EXTENSION_COMMANDS.stopServer,
      EXTENSION_COMMANDS.reindexWorkspace,
      EXTENSION_COMMANDS.openLogs,
    ]) {
      assert.ok(commands.includes(command), `${command} should be registered`);
    }

    await withTimeout(
      vscode.commands.executeCommand(EXTENSION_COMMANDS.reindexWorkspace),
      "reindex simple_crate",
      30_000,
    );
    await waitForReadyWorkspace("simple_crate");
    await assertDefinition(simple, "|left + right", "|left: i32");

    const moderate = await vscode.workspace.openTextDocument(
      vscode.Uri.joinPath(projects, "moderate_crate", "src", "model.rs"),
    );
    await vscode.window.showTextDocument(moderate);
    await waitForReadyWorkspace("moderate_crate");
    await assertDefinition(moderate, "impl Display for |Note", "pub struct |Note");

    // Switching back must still query the first project without launching another LSP server.
    await vscode.window.showTextDocument(simple);
    await assertDefinition(simple, "|left + right", "|left: i32");
    const output = await serverOutput();
    assert.equal(output.match(/server process started/g)?.length ?? 0, 1, output);

    await withTimeout(
      vscode.commands.executeCommand(EXTENSION_COMMANDS.stopServer),
      "stop Rust Glancer",
    );
    await waitFor(
      "server stopped",
      clientState,
      (state) => state.session === undefined && state.status.state === "stopped",
    );
    await vscode.window.showTextDocument(moderate);
    assert.equal((await clientState()).session, undefined);

    await withTimeout(
      vscode.commands.executeCommand(EXTENSION_COMMANDS.startServer),
      "start Rust Glancer again",
      30_000,
    );
    await waitForReadyWorkspace("moderate_crate");
    await assertDefinition(moderate, "impl Display for |Note", "pub struct |Note");
  });

  test("colors documentation examples and resolves source links", async () => {
    const document = await vscode.workspace.openTextDocument(
      vscode.Uri.joinPath(projects, "moderate_crate", "src", "model.rs"),
    );
    await vscode.window.showTextDocument(document);
    await waitForReadyWorkspace("moderate_crate");
    await inspectDocumentation(document);
  });

  test("offers, accepts, and dismisses semantic completions in the editor", async () => {
    const document = await vscode.workspace.openTextDocument(
      vscode.Uri.joinPath(projects, "moderate_crate", "src", "lib.rs"),
    );
    await vscode.window.showTextDocument(document);
    await withTimeout(
      vscode.commands.executeCommand(EXTENSION_COMMANDS.startServer),
      "start completion test server",
      30_000,
    );
    await waitForReadyWorkspace("moderate_crate");
    await completeInEditor(document);
  });

  test("EDT-001 sends a configured compiler model through the editor client", async () => {
    const root = path.resolve(projects.fsPath, "../crates/engine/rustdoc/fixtures/model-default");
    const inputs = [
      {
        workspaceRoot: root,
        manifestPath: "Cargo.toml",
        targetName: "rustdoc_macro_support",
        targetKind: "lib",
        exportPath: "export.json",
        itemPath: "rustdoc_macro_support::Post",
      },
    ];
    const settings = vscode.workspace.getConfiguration("rust-glancer");
    const previous = settings.get<unknown>("rustdoc.inputs");
    let document: vscode.TextDocument | undefined;
    try {
      await settings.update("rustdoc.inputs", null, vscode.ConfigurationTarget.Global);
      assert.throws(() => ExtensionConfig.read(), /rustdoc.inputs/);
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.restartServer),
        "reject malformed editor configuration",
      );
      await waitFor(
        "malformed configuration reported",
        clientState,
        (state) => state.session === undefined && state.status.state === "failed",
      );
      await settings.update("rustdoc.inputs", inputs, vscode.ConfigurationTarget.Global);
      const config = ExtensionConfig.read();
      assert.deepEqual(
        config.rustdoc.inputs,
        inputs,
        "the extension dropped its configured input",
      );
      for (const malformed of [null, "invalid", [null], [{}]]) {
        assert.throws(() => RustdocConfig.read(malformed), /rustdoc.inputs/);
      }
      for (const field of Object.keys(inputs[0])) {
        const missing: Record<string, unknown> = { ...inputs[0] };
        delete missing[field];
        assert.throws(() => RustdocConfig.read([missing]), /rustdoc.inputs/);
        for (const value of [null, 1, "", "   "]) {
          assert.throws(
            () => RustdocConfig.read([{ ...inputs[0], [field]: value }]),
            /rustdoc.inputs/,
          );
        }
      }
      assert.throws(
        () => RustdocConfig.read([{ ...inputs[0], targetKind: "proc-macro" }]),
        /targetKind/,
      );
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.restartServer),
        "restart configured editor server",
        30_000,
      );
      document = await vscode.workspace.openTextDocument(
        vscode.Uri.file(path.join(root, "src/lib.rs")),
      );
      const editor = await vscode.window.showTextDocument(document);
      await waitForReadyWorkspace("model-default");
      const offset = document.getText().indexOf("        self.id");
      assert.ok(offset >= 0, "genuine fixture source method must exist");
      assert.ok(
        await editor.edit((edit) =>
          edit.insert(document!.positionAt(offset), "        let edt_query = Post::query();\n"),
        ),
      );
      const queryOffset = document.getText().indexOf("edt_query");
      const hovers = await withTimeout(
        vscode.commands.executeCommand<vscode.Hover[]>(
          "vscode.executeHoverProvider",
          document.uri,
          document.positionAt(queryOffset),
        ),
        "hover configured generated model",
      );
      assert.ok(
        JSON.stringify(hovers).includes("Builder<Post>"),
        `trait default query missing: ${JSON.stringify(hovers)}`,
      );
      const completionOffset = document.getText().indexOf("Post::query") + "Post::".length;
      const completions = await withTimeout(
        vscode.commands.executeCommand<vscode.CompletionList>(
          "vscode.executeCompletionItemProvider",
          document.uri,
          document.positionAt(completionOffset),
        ),
        "complete configured generated model",
      );
      assert.ok(completions, "completion provider returned no result");
      const queries = completions.items.filter((item) =>
        (typeof item.label === "string" ? item.label : item.label.label).startsWith("query"),
      );
      assert.equal(
        queries.length,
        1,
        `query completion missing or duplicated: ${JSON.stringify(completions)}`,
      );
    } finally {
      if (document?.isDirty) {
        await vscode.window.showTextDocument(document);
        await vscode.commands.executeCommand("workbench.action.files.revert");
      }
      await settings.update("rustdoc.inputs", previous, vscode.ConfigurationTarget.Global);
    }
  });
});
