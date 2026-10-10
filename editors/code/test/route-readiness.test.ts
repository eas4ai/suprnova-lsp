import * as assert from "node:assert/strict";
import * as vscode from "vscode";

import { formatServerLogRecord } from "../src/logging/server-log";
import { DocumentRouteReadiness } from "./responsiveness-protocol";

suite("Editor route readiness", () => {
  test("waits for every open Rust sibling and rejects a route from before reopen", () => {
    const first = vscode.Uri.file(
      process.platform === "win32"
        ? String.raw`C:\route fixture\first.rs`
        : String.raw`/route fixture/C:\first.rs`,
    );
    const second = vscode.Uri.file("/other-fixture/second.rs");
    let sequence = 0;
    const notification = (method: string, uri: vscode.Uri) =>
      JSON.stringify({
        isLSPMessage: true,
        type: "send-notification",
        timestamp: ++sequence,
        message: { method, params: { textDocument: { uri: uri.toString() } } },
      });
    const route = (uri: vscode.Uri, session: number, current = true) =>
      formatServerLogRecord({
        level: "debug",
        component: "server",
        message: "editor document analysis route published",
        fields: {
          path: uri.fsPath,
          ready: true,
          current,
          session: `OpenDocumentSession(${session})`,
        },
      });
    const lines = [
      notification("textDocument/didOpen", first),
      notification("textDocument/didOpen", second),
      route(first, 1),
    ];
    const routes = new DocumentRouteReadiness();
    const ready = () => routes.observe({ output: lines.join("\n"), events: [] });
    assert.equal(ready(), false, "an unresolved sibling must prevent queries");
    lines.push(route(second, 2));
    assert.equal(ready(), true);
    lines.push(notification("textDocument/didClose", second));
    assert.equal(ready(), true, "closed siblings no longer need routes");
    lines.push(notification("textDocument/didOpen", second));
    assert.equal(ready(), false, "a reopened document cannot reuse its previous route");
    lines.push(route(second, 2, false));
    assert.equal(ready(), false, "a late old-session publication is not current");
    lines.push(route(second, 2));
    assert.equal(ready(), false, "old ingress can publish before it processes the client reopen");
    lines.push(route(second, 3));
    assert.equal(ready(), true);
    assert.equal(
      routes.observe({ output: "unrelated record", events: [] }),
      true,
      "evicting old trace records must not erase observed readiness",
    );
    assert.equal(
      routes.observe({ output: notification("textDocument/didOpen", first), events: [] }),
      false,
    );
    assert.equal(
      routes.observe({ output: "another unrelated record", events: [] }),
      false,
      "an unresolved sibling must survive trace eviction too",
    );
    assert.equal(
      routes.observe({
        output: JSON.stringify({
          isLSPMessage: true,
          type: "send-request",
          timestamp: ++sequence,
          message: { method: "initialize" },
        }),
        events: [],
      }),
      false,
      "a new connection must discard old route flags",
    );
    assert.equal(
      routes.observe({
        output: [
          notification("textDocument/didOpen", vscode.Uri.file("/notes.txt")),
          notification("textDocument/didOpen", first),
          route(first, 1),
        ].join("\n"),
        events: [],
      }),
      false,
      "session identity counts every file open, including non-Rust files",
    );
    assert.equal(routes.observe({ output: route(first, 2), events: [] }), true);
    if (process.platform !== "win32") {
      const remote = vscode.Uri.parse("file://remote/notes.txt");
      assert.equal(
        routes.observe({
          output: [
            notification("textDocument/didOpen", remote),
            notification("textDocument/didOpen", second),
            route(second, 3),
          ].join("\n"),
          events: [],
        }),
        true,
        "a rejected remote file URI must not consume an ingress session",
      );
    }
  });
});
