import * as assert from "node:assert/strict";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import * as vscode from "vscode";

import { EXTENSION_COMMANDS } from "../src/commands";
import { waitFor, withTimeout } from "./async";
import { RendererEditor } from "./renderer-editor";
import { ResponsivenessProtocol } from "./responsiveness-protocol";

suite("Suprnova LSP responsiveness", () => {
  test("RSP-004 cancels a genuine editor hover and serves the following request", async function () {
    this.timeout(180_000);
    const root = process.env.SUPRNOVA_LSP_RESPONSIVENESS_APPLICATION;
    if (root === undefined || process.env.SUPRNOVA_LSP_RESPONSIVENESS_EXPORT === undefined) {
      this.skip();
    }
    const extension = vscode.extensions.getExtension("eas4ai.suprnova-lsp");
    assert.ok(extension);
    await withTimeout(extension.activate(), "activate cancellation editor client", 30_000);
    const observations: Record<string, unknown> = { application: root };
    let document: vscode.TextDocument | undefined;
    let renderer: RendererEditor | undefined;
    try {
      document = await vscode.workspace.openTextDocument(
        vscode.Uri.file(path.join(root, "src/models/user.rs")),
      );
      assert.equal(vscode.workspace.getConfiguration("files", document.uri).get("autoSave"), "off");
      const editor = await vscode.window.showTextDocument(document);
      const signature =
        "pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {";
      const offset = document.getText().indexOf(signature);
      assert.ok(offset >= 0);
      assert.ok(
        await editor.edit((edit) =>
          edit.insert(
            document!.positionAt(offset + signature.length),
            '\n        use suprnova::eloquent::Model as _;\n        let rsp_editor_cancel = User::filter("email", "member@example.test");\n',
          ),
        ),
      );
      await waitFor(
        "cancellation document route",
        () => ResponsivenessProtocol.snapshot(),
        (snapshot) =>
          snapshot.output
            .split("\n")
            .some(
              (line) =>
                line.includes("editor document analysis route published") &&
                line.includes(document!.uri.fsPath) &&
                line.includes("ready=true"),
            ),
        60_000,
      );
      const position = document.positionAt(document.getText().indexOf("rsp_editor_cancel"));
      editor.selection = new vscode.Selection(position, position);
      editor.revealRange(new vscode.Range(position, position));
      renderer = await RendererEditor.connect();
      await renderer.focus();
      const before = await ResponsivenessProtocol.snapshot();
      const shown = Promise.resolve(vscode.commands.executeCommand("editor.action.showHover")).then(
        () => ({ completed: true }),
        (error) => ({ error: String(error) }),
      );
      const outstanding = await ResponsivenessProtocol.outstanding(before);
      const request = ResponsivenessProtocol.latestHover(outstanding);
      observations.outstanding = outstanding;
      observations.requestId = request.message.id;
      await renderer.escape();
      const cancelled = await waitFor(
        "client cancellation for the actual hover",
        () => ResponsivenessProtocol.snapshot(),
        (snapshot) =>
          snapshot.events.some(
            (event) =>
              event.message.method === "$/cancelRequest" &&
              (event.message.params as { id?: number } | undefined)?.id === request.message.id,
          ),
      );
      const released = await waitFor(
        "cancelled hover response",
        () => ResponsivenessProtocol.snapshot(),
        (snapshot) =>
          snapshot.events.some(
            (event) => event.type === "receive-response" && event.message.id === request.message.id,
          ),
      );
      const reply = released.events.find(
        (event) => event.type === "receive-response" && event.message.id === request.message.id,
      );
      assert.ok(reply?.message.error, "cancelled hover must not publish a successful response");
      assert.equal(
        (await renderer.hoverState()).visible,
        false,
        "cancelled tooltip must stay hidden",
      );
      observations.cancelled = cancelled;
      observations.released = released;
      observations.showOutcome = await withTimeout(shown, "cancelled hover command");
      observations.hidden = await renderer.hoverState();
      const following = await withTimeout(
        vscode.commands.executeCommand<vscode.Hover[]>(
          "vscode.executeHoverProvider",
          document.uri,
          position,
        ),
        "following valid editor hover",
      );
      observations.following = {
        text: ResponsivenessProtocol.text(following),
        snapshot: await ResponsivenessProtocol.snapshot(),
      };
      assert.ok(ResponsivenessProtocol.text(following).includes("Builder<User>"));
      await withTimeout(
        vscode.commands.executeCommand("editor.action.showHover"),
        "show following tooltip",
      );
      observations.visible = await waitFor(
        "rendered generated tooltip",
        () => renderer!.hoverState(),
        (state) => state.visible && state.text.includes("Builder<User>"),
      );
      observations.visibleObservedAt = Date.now();
      observations.visibleSnapshot = await ResponsivenessProtocol.snapshot();
    } finally {
      try {
        const report = process.env.SUPRNOVA_LSP_EXTENSION_TEST_REPORT;
        if (report !== undefined) {
          observations.final = await ResponsivenessProtocol.snapshot();
          await fs.writeFile(`${report}.protocol.json`, JSON.stringify(observations, null, 2));
        }
      } finally {
        renderer?.dispose();
        try {
          if (document?.isDirty) {
            await vscode.window.showTextDocument(document);
            await vscode.commands.executeCommand("workbench.action.files.revert");
          }
        } finally {
          await withTimeout(
            vscode.commands.executeCommand(EXTENSION_COMMANDS.stopServer),
            "stop cancellation editor server",
          );
        }
      }
    }
  });
});
