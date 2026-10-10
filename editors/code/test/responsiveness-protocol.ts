import * as assert from "node:assert/strict";
import * as vscode from "vscode";

import { waitFor } from "./async";
import { serverOutput } from "./extension-harness";

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
