// Keep E2E coverage about working user flows. Detailed completion and cancellation contracts
// belong in the engine and transport tests, where their inputs and ordering can be controlled.
import * as assert from "node:assert/strict";
import * as fs from "node:fs/promises";
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

suite("Suprnova LSP extension", () => {
  let projects: vscode.Uri;

  suiteSetup(async () => {
    const extension = vscode.extensions.getExtension("eas4ai.suprnova-lsp");
    assert.ok(extension, "VS Code should load Suprnova LSP");
    projects = vscode.Uri.file(path.resolve(extension.extensionPath, "../../test_targets"));
    await withTimeout(extension.activate(), "activate Suprnova LSP", 30_000);
  });

  teardown(async function () {
    if (this.currentTest?.state === "failed") {
      const evidence = await Promise.allSettled([clientState(), serverOutput()]);
      const reportPath = process.env.SUPRNOVA_LSP_EXTENSION_TEST_REPORT;
      if (reportPath !== undefined) {
        // VS Code can omit large console objects. Keep the full failure evidence
        // beside the managed test report so compiler diagnostics survive.
        await fs.writeFile(
          `${reportPath}.diagnostics.json`,
          JSON.stringify(
            {
              test: this.currentTest.fullTitle(),
              evidence: evidence.map((result) =>
                result.status === "fulfilled"
                  ? result
                  : { status: result.status, reason: String(result.reason) },
              ),
            },
            null,
            2,
          ),
        );
      }
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

  test("serves both Rust projects through one server and survives reindex and restart", async function () {
    // Several separately bounded startup/reindex operations share this workflow's deadline.
    this.timeout(180_000);
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
      "stop Suprnova LSP",
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
      "start Suprnova LSP again",
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

  test("AUT-001 sends automatic worker policy through the editor client", async function () {
    this.timeout(1_000_000);
    const root = process.env.SUPRNOVA_LSP_AUTOMATIC_RUSTDOC_FIXTURE;
    assert.ok(root, "the acceptance runner must supply its owned compiler fixture");
    const policy = {
      enabled: true,
      debounceMs: 2000,
      toolchain: "nightly-2026-08-19",
      timeoutMs: 900000,
      jobs: 2,
      artifactRoot: process.env.SUPRNOVA_LSP_AUTOMATIC_RUSTDOC_ARTIFACTS,
    };
    assert.ok(policy.artifactRoot, "compiler artifacts must stay in the acceptance directory");
    const settings = vscode.workspace.getConfiguration("suprnova-lsp");
    const previous = settings.get<unknown>("rustdoc.automatic");
    const previousInputs = settings.get<unknown>("rustdoc.inputs");
    const controlPath = process.env.AUTOMATIC_RUSTDOC_CONTROL;
    assert.ok(controlPath, "the acceptance runner must supply its compiler controls");
    const previousControl = await fs.readFile(controlPath, "utf8");
    try {
      await settings.update("rustdoc.automatic", policy, vscode.ConfigurationTarget.Global);
      await settings.update("rustdoc.inputs", [], vscode.ConfigurationTarget.Global);
      const config = JSON.parse(JSON.stringify(ExtensionConfig.read()));
      assert.deepEqual(config.rustdoc.automatic, policy, "the extension dropped worker policy");
      await settings.update("rustdoc.automatic", null, vscode.ConfigurationTarget.Global);
      assert.throws(() => ExtensionConfig.read(), /rustdoc.automatic/);
      await settings.update("rustdoc.automatic", policy, vscode.ConfigurationTarget.Global);
      const folder = vscode.Uri.file(root);
      assert.ok(
        vscode.workspace.updateWorkspaceFolders(vscode.workspace.workspaceFolders?.length ?? 0, 0, {
          uri: folder,
          name: "automatic-models",
        }),
      );
      await waitFor(
        "automatic workspace registered",
        () => vscode.workspace.getWorkspaceFolder(folder),
        (value) => value !== undefined,
      );
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.restartServer),
        "restart automatic editor server",
        30_000,
      );
      const document = await vscode.workspace.openTextDocument(
        vscode.Uri.file(path.join(root, "src/lib.rs")),
      );
      await vscode.window.showTextDocument(document);
      const automaticState = async () => {
        const state = await clientState();
        const reportPath = process.env.SUPRNOVA_LSP_EXTENSION_TEST_REPORT;
        if (reportPath !== undefined) {
          await fs.writeFile(`${reportPath}.worker-state.json`, JSON.stringify(state, null, 2));
        }
        if (state.session?.status.details.generatedApiState === "failed") {
          throw new Error(
            `Automatic model export failed: ${state.session.status.details.generatedApiMessage}`,
          );
        }
        return state;
      };
      await waitFor(
        "automatic workspace ready",
        automaticState,
        ({ session }) =>
          session?.running === true &&
          session.hasClient &&
          session.status.state === "ready" &&
          path.basename(session.status.details.activeWorkspaceRoot ?? "") === path.basename(root),
        900_000,
      );
      await waitFor(
        "automatic model hover",
        async () => {
          await automaticState();
          const hovers = await vscode.commands.executeCommand<vscode.Hover[]>(
            "vscode.executeHoverProvider",
            document.uri,
            document.positionAt(document.getText().indexOf("automatic_post")),
          );
          return (hovers ?? [])
            .flatMap((hover) =>
              hover.contents.map((content) =>
                typeof content === "string" ? content : content.value,
              ),
            )
            .join("\n");
        },
        (value) => value.includes("Builder<Post>"),
        900_000,
      );
      await waitFor("generated API reported current in the editor", clientState, ({ session }) =>
        /generated.*current/i.test(session?.status.text ?? ""),
      );
      await fs.writeFile(
        controlPath,
        JSON.stringify({ mode: "failure", artifactRoot: policy.artifactRoot }),
      );
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.reindexWorkspace),
        "request controlled compiler failure",
        300_000,
      );
      await waitFor(
        "generated API failure visible in the editor",
        clientState,
        ({ session }) => /generated.*failed/i.test(session?.status.text ?? ""),
        300_000,
      );
      await fs.writeFile(controlPath, previousControl);
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.reindexWorkspace),
        "recover generated editor API",
        300_000,
      );
      await waitFor(
        "generated API recovered in the editor",
        clientState,
        ({ session }) => /generated.*current/i.test(session?.status.text ?? ""),
        300_000,
      );
    } finally {
      await fs.writeFile(controlPath, previousControl);
      await settings.update("rustdoc.automatic", previous, vscode.ConfigurationTarget.Global);
      await settings.update("rustdoc.inputs", previousInputs, vscode.ConfigurationTarget.Global);
    }
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
    const settings = vscode.workspace.getConfiguration("suprnova-lsp");
    const previous = settings.get<unknown>("rustdoc.inputs");
    const modelFolder = vscode.Uri.file(root);
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
      assert.deepEqual(config.rustdoc.inputs, inputs, "the extension dropped its configured input");
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
      // The model lives outside test_targets. Make it an editor workspace before restarting
      // so the server's ordinary folder boundary permits this genuine fixture's document.
      assert.ok(
        vscode.workspace.updateWorkspaceFolders(vscode.workspace.workspaceFolders?.length ?? 0, 0, {
          uri: modelFolder,
          name: "model-default",
        }),
      );
      await waitFor(
        "model editor workspace registered",
        async () => vscode.workspace.getWorkspaceFolder(modelFolder),
        (folder) => folder?.uri.toString() === modelFolder.toString(),
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
      const hoverText = (hovers ?? [])
        .flatMap((hover) =>
          hover.contents.map((content) => (typeof content === "string" ? content : content.value)),
        )
        .join("\n");
      assert.ok(hoverText.includes("Builder<Post>"), `trait default query missing: ${hoverText}`);
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
      // The runner discards this isolated editor window; keeping its additional workspace
      // until shutdown avoids another asynchronous workspace conversion during cleanup.
    }
  });
});
