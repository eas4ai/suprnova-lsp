import * as assert from "node:assert/strict";
import * as nodePath from "node:path";
import * as vscode from "vscode";

import { EXTENSION_COMMANDS } from "../src/commands";
import { waitFor, withTimeout } from "./async";

export async function serverOutput(): Promise<string> {
  const output = await withTimeout(
    vscode.commands.executeCommand<string>(EXTENSION_COMMANDS.testGetOutput),
    "read server output",
  );
  assert.equal(typeof output, "string", "extension test output should be available");
  return output;
}

export interface ProtocolEvent {
  readonly type: string;
  readonly timestamp: number;
  readonly message: {
    readonly id?: number;
    readonly method?: string;
    readonly params?: {
      readonly root?: string;
      readonly state?: string;
      readonly textDocument?: { readonly uri: string; readonly version?: number };
    };
    readonly error?: { readonly code: number; readonly message: string };
    readonly result?: unknown;
  };
}

export interface ProtocolSnapshot {
  readonly output: string;
  readonly events: readonly ProtocolEvent[];
}

// Keep route flags across polls: the output channel evicts old records. Store
// only open paths and the preceding bounded snapshot, never the full trace.
export class DocumentRouteReadiness {
  private previousLines = new Set<string>();
  private nextSession = 0;
  private opened = new Map<string, { session: number; ready: boolean }>();

  public observe(snapshot: ProtocolSnapshot): boolean {
    const lines = snapshot.output.split("\n");
    for (const line of lines) {
      if (this.previousLines.has(line)) continue;
      const offset = line.indexOf('{"isLSPMessage":true');
      if (offset >= 0) {
        const event = JSON.parse(line.slice(offset)) as ProtocolEvent;
        if (event.type === "send-request" && event.message.method === "initialize") {
          this.opened.clear();
          this.nextSession = 0;
        }
        if (event.type !== "send-notification") continue;
        const uri = event.message.params?.textDocument?.uri;
        if (uri === undefined) continue;
        const parsed = vscode.Uri.parse(uri);
        if (parsed.scheme !== "file") continue;
        // Unix file URLs reject remote authorities; Windows also admits UNC paths.
        if (
          process.platform !== "win32" &&
          parsed.authority !== "" &&
          parsed.authority !== "localhost"
        )
          continue;
        const path = (parsed.authority === "localhost" ? parsed.with({ authority: "" }) : parsed)
          .fsPath;
        if (!nodePath.isAbsolute(path)) continue;
        // Ingress assigns sessions to file opens in wire order, including
        // non-Rust files. Match that identity rather than log arrival time:
        // an old publication can arrive after the client traces a reopen.
        if (event.message.method === "textDocument/didOpen") {
          const session = ++this.nextSession;
          if (path.endsWith(".rs")) this.opened.set(path, { session, ready: false });
        }
        if (event.message.method === "textDocument/didClose") this.opened.delete(path);
        continue;
      }
      if (
        !line.includes("editor document analysis route published") ||
        !line.includes("ready=true") ||
        !line.includes("current=true")
      )
        continue;
      // Rendered path fields quote Windows separators and whitespace.
      const field = line.match(/(?:^| )path=("(?:\\.|[^"\\])*"|[^ ]+)(?: |$)/)?.[1];
      const path = field?.startsWith('"') ? JSON.parse(field) : field;
      const session = Number(line.match(/(?:^| )session=OpenDocumentSession\((\d+)\)(?: |$)/)?.[1]);
      const opened = typeof path === "string" ? this.opened.get(path) : undefined;
      if (opened?.session === session) opened.ready = true;
    }
    this.previousLines = new Set(lines);
    return this.opened.size > 0 && [...this.opened.values()].every((document) => document.ready);
  }
}

// Read the client's JSON trace through the existing test output command. The
// request IDs and notifications come from the real connection, not the test.
export class ResponsivenessProtocol {
  public static async snapshot(): Promise<ProtocolSnapshot> {
    const output = await serverOutput();
    const lines = output.split("\n");
    const events: ProtocolEvent[] = [];
    let currentSession = 0;
    for (const [index, line] of lines.entries()) {
      const offset = line.indexOf('{"isLSPMessage":true');
      if (offset >= 0) {
        const event = JSON.parse(line.slice(offset)) as ProtocolEvent;
        assert.ok(Number.isInteger(event.timestamp), "client trace needs its actual timestamp");
        // Restarted connections reuse request IDs. Keep this observation in the
        // newest initialized connection so earlier replies cannot satisfy it.
        if (event.type === "send-request" && event.message.method === "initialize") {
          currentSession = index;
          events.length = 0;
        }
        events.push(event);
      }
    }
    return { output: lines.slice(currentSession).join("\n"), events };
  }

  public static async outstanding(previous: ProtocolSnapshot): Promise<ProtocolSnapshot> {
    const seen = new Set(
      previous.events
        .filter((event) => event.type === "send-request")
        .map((event) => event.message.id),
    );
    return waitFor(
      "a new outstanding editor hover",
      () => this.snapshot(),
      (snapshot) =>
        snapshot.events.some(
          (event) =>
            event.type === "send-request" &&
            event.message.method === "textDocument/hover" &&
            !seen.has(event.message.id) &&
            !snapshot.events.some(
              (reply) => reply.type === "receive-response" && reply.message.id === event.message.id,
            ),
        ),
    );
  }

  public static latestHover(snapshot: ProtocolSnapshot): ProtocolEvent {
    const hover = snapshot.events
      .filter(
        (event) => event.type === "send-request" && event.message.method === "textDocument/hover",
      )
      .slice(-1)[0];
    assert.ok(
      hover && Number.isInteger(hover.message.id),
      "editor hover needs an actual request ID",
    );
    return hover;
  }

  public static assertEditedResponse(
    snapshot: ProtocolSnapshot,
    id: number,
    uri: string,
    version: number,
  ): void {
    const sent = snapshot.events.findIndex(
      (event) => event.type === "send-request" && event.message.id === id,
    );
    const changed = snapshot.events.findIndex(
      (event) =>
        event.type === "send-notification" &&
        event.message.method === "textDocument/didChange" &&
        event.message.params?.textDocument?.uri === uri &&
        event.message.params.textDocument.version === version,
    );
    const replied = snapshot.events.findIndex(
      (event) => event.type === "receive-response" && event.message.id === id,
    );
    assert.ok(
      sent >= 0 && changed > sent && replied > changed,
      "new revision must reach ingress before the old hover response",
    );
    assert.equal(
      snapshot.events[replied].message.error?.code,
      -32801,
      "obsolete hover must report ContentModified",
    );
  }

  public static text(hovers: readonly vscode.Hover[] | undefined): string {
    return (hovers ?? [])
      .flatMap((hover) =>
        hover.contents.map((content) => (typeof content === "string" ? content : content.value)),
      )
      .join("\n");
  }
}
