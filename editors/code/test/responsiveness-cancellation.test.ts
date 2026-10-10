import * as assert from "node:assert/strict";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import * as vscode from "vscode";

import { EXTENSION_COMMANDS } from "../src/commands";
import type { HoverOrigin } from "../src/features/hover-actions-model";
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
      const visibleSnapshot = await ResponsivenessProtocol.snapshot();
      observations.visibleSnapshot = visibleSnapshot;
      assert.ok(
        !visibleSnapshot.events.some(
          (event) =>
            event.type === "send-request" &&
            ["textDocument/typeDefinition", "textDocument/implementation"].includes(
              event.message.method ?? "",
            ),
        ),
        "displaying hover must not wait for navigation requests",
      );
      const contents = following?.flatMap((hover) => hover.contents) ?? [];
      const link = contents
        .map((content) => (typeof content === "string" ? content : content.value))
        .join("\n")
        .match(/command:suprnova-lsp\.gotoTypeFromHover\?([^)]*)/);
      assert.ok(link, "hover should retain a type navigation action");
      const [origin] = JSON.parse(decodeURIComponent(link[1])) as [HoverOrigin];
      assert.equal(origin.uri, document.uri.toString());
      assert.equal(origin.version, document.version);
      assert.ok(
        origin.range?.start !== undefined && origin.range.end !== undefined,
        "encoded VS Code ranges must retain the serialized object shape",
      );
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.goToTypeFromHover, origin),
        "navigate using decoded hover origin",
      );
      const navigation = await ResponsivenessProtocol.snapshot();
      const lookup = navigation.events.find(
        (event) =>
          event.type === "send-request" && event.message.method === "textDocument/typeDefinition",
      );
      assert.ok(lookup, "navigation must query on click");
      const target = navigation.events.find(
        (event) => event.type === "receive-response" && event.message.id === lookup.message.id,
      );
      assert.ok(target && target.message.error === undefined, "type lookup must succeed");
      observations.navigation = { origin, snapshot: navigation };
      assert.ok(
        await editor.edit((edit) =>
          edit.insert(
            document!.positionAt(document!.getText().length),
            "\n// hover navigation revision fence\n",
          ),
        ),
      );
      await withTimeout(
        vscode.commands.executeCommand(EXTENSION_COMMANDS.goToTypeFromHover, origin),
        "reject navigation from an obsolete hover",
      );
      const obsolete = await ResponsivenessProtocol.snapshot();
      assert.equal(
        obsolete.events.filter(
          (event) =>
            event.type === "send-request" && event.message.method === "textDocument/typeDefinition",
        ).length,
        1,
        "editing the origin must suppress the obsolete navigation request",
      );
      observations.obsoleteNavigation = obsolete;
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
