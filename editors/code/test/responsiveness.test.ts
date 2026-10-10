import * as assert from "node:assert/strict";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import * as vscode from "vscode";

import { EXTENSION_COMMANDS } from "../src/commands";
import { waitFor, withTimeout } from "./async";
import { ResponsivenessProtocol, type ProtocolSnapshot } from "./responsiveness-protocol";

suite("Suprnova LSP responsiveness", () => {
  test("RSP-003 delivers generated hover during indexing and rejects an overtaken revision", async function () {
    this.timeout(180_000);
    const root = process.env.SUPRNOVA_LSP_RESPONSIVENESS_APPLICATION;
    const exportPath = process.env.SUPRNOVA_LSP_RESPONSIVENESS_EXPORT;
    if (root === undefined || exportPath === undefined) {
      // Ordinary fixture runs need no external application. The RSP mechanism
      // requires this exact case to pass and rejects a skipped observation.
      this.skip();
    }
    const extension = vscode.extensions.getExtension("eas4ai.suprnova-lsp");
    assert.ok(extension);
    await withTimeout(extension.activate(), "activate responsiveness editor client", 30_000);
    const observations: Record<string, unknown> = { application: root };
    let document: vscode.TextDocument | undefined;
    try {
      document = await vscode.workspace.openTextDocument(
        vscode.Uri.file(path.join(root, "src/models/user.rs")),
      );
      assert.equal(vscode.workspace.getConfiguration("files", document.uri).get("autoSave"), "off");
      const editor = await vscode.window.showTextDocument(document);
      const signature =
        "pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {";
      const offset = document.getText().indexOf(signature);
      assert.ok(offset >= 0, "pinned Devlist source method must exist");
      assert.ok(
        await editor.edit((edit) =>
          edit.insert(
            document!.positionAt(offset + signature.length),
            "\n        use suprnova::eloquent::Model as _;\n        let rsp_editor_query = User::query();\n",
          ),
        ),
      );
      const before = await waitFor(
        "prepared declarations and editor route during deferred indexing",
        () => ResponsivenessProtocol.snapshot(),
        (snapshot) => {
          const lines = snapshot.output.split("\n");
          return (
            lines.some(
              (line) =>
                line.includes("configured rustdoc declarations published") && line.includes(root),
            ) &&
            lines.some(
              (line) =>
                line.includes("editor document analysis route published") &&
                line.includes(document!.uri.fsPath) &&
                line.includes("ready=true"),
            ) &&
            snapshot.events.some(
              (event) =>
                event.message.method === "suprnova-lsp/deferredIndexingStarted" &&
                event.message.params?.root === root,
            ) &&
            !snapshot.events.some(
              (event) =>
                event.message.method === "suprnova-lsp/deferredIndexingFinished" &&
                event.message.params?.root === root,
            )
          );
        },
        60_000,
      );
      observations.beforeGenerated = before;
      const queryPosition = () =>
        document!.positionAt(document!.getText().indexOf("rsp_editor_query"));
      const generated = await withTimeout(
        vscode.commands.executeCommand<vscode.Hover[]>(
          "vscode.executeHoverProvider",
          document.uri,
          queryPosition(),
        ),
        "generated editor hover",
      );
      assert.ok(ResponsivenessProtocol.text(generated).includes("Builder<User>"));
      observations.generated = {
        version: document.version,
        text: ResponsivenessProtocol.text(generated),
        snapshot: await ResponsivenessProtocol.snapshot(),
      };

      const beforeEdit = await ResponsivenessProtocol.snapshot();
      const oldVersion = document.version;
      // Attach both outcomes immediately so a genuine obsolete-request rejection
      // cannot become an unhandled promise rejection while the edit is delivered.
      const oldHover = Promise.resolve(
        vscode.commands.executeCommand<vscode.Hover[]>(
          "vscode.executeHoverProvider",
          document.uri,
          queryPosition(),
        ),
      ).then(
        (value) => ({ text: ResponsivenessProtocol.text(value) }),
        (error: unknown) => ({ error: String(error) }),
      );
      const outstanding = await ResponsivenessProtocol.outstanding(beforeEdit);
      const request = ResponsivenessProtocol.latestHover(outstanding);
      const start = document.getText().indexOf("User::query()", offset);
      assert.ok(start >= 0);
      assert.ok(
        await editor.edit((edit) =>
          edit.replace(
            new vscode.Range(
              document!.positionAt(start),
              document!.positionAt(start + "User::query()".length),
            ),
            "23u32",
          ),
        ),
      );
      assert.ok(document.version > oldVersion);
      const newVersion = document.version;
      // Sending another provider request flushes the client's pending didChange.
      const changed = await withTimeout(
        vscode.commands.executeCommand<vscode.Hover[]>(
          "vscode.executeHoverProvider",
          document.uri,
          queryPosition(),
        ),
        "new revision editor hover",
      );
      const oldOutcome = await withTimeout(oldHover, "overtaken editor hover");
      assert.ok(/\bu32\b/.test(ResponsivenessProtocol.text(changed)));
      const afterEdit: ProtocolSnapshot = await ResponsivenessProtocol.snapshot();
      ResponsivenessProtocol.assertEditedResponse(
        afterEdit,
        request.message.id!,
        document.uri.toString(),
        newVersion,
      );
      observations.edit = {
        oldVersion,
        newVersion,
        requestId: request.message.id,
        outstanding,
        oldOutcome,
        newText: ResponsivenessProtocol.text(changed),
        snapshot: afterEdit,
      };
    } finally {
      const reportPath = process.env.SUPRNOVA_LSP_EXTENSION_TEST_REPORT;
      try {
        if (reportPath !== undefined) {
          observations.final = await ResponsivenessProtocol.snapshot();
          await fs.writeFile(`${reportPath}.protocol.json`, JSON.stringify(observations, null, 2));
        }
      } finally {
        try {
          if (document?.isDirty) {
            await vscode.window.showTextDocument(document);
            await vscode.commands.executeCommand("workbench.action.files.revert");
          }
        } finally {
          await withTimeout(
            vscode.commands.executeCommand(EXTENSION_COMMANDS.stopServer),
            "stop responsiveness editor server",
          );
        }
      }
    }
  });
});
