import * as assert from "node:assert/strict";
import * as vscode from "vscode";
import { ResponseError, type LanguageClient } from "vscode-languageclient/node";

import { EXTENSION_COMMANDS } from "../src/commands";
import { HoverActions } from "../src/features/hover-actions";
import type {
  HoverOrigin,
  ProtocolDefinitionLike,
  SerializedRange,
} from "../src/features/hover-actions-model";
import { LanguageClientSession } from "../src/language-client/language-client-session";
import type { ClientStatus } from "../src/status/client-status";
import { StatusView } from "../src/status/status-view";
import { waitFor, withTimeout } from "./async";

class Pending<T> {
  public readonly promise: Promise<T>;
  public resolve!: (value: T) => void;

  public constructor() {
    this.promise = new Promise<T>((resolve) => {
      this.resolve = resolve;
    });
  }
}

class HoverFixture {
  public readonly output = vscode.window.createOutputChannel("Hover actions boundary tests", {
    log: true,
  });
  public readonly token = new vscode.CancellationTokenSource();
  public readonly range = new vscode.Range(0, 0, 0, 6);
  public readonly documentation = new vscode.MarkdownString("server documentation");
  public readonly loads: { origin: HoverOrigin; kind: "type" | "implementation" }[] = [];
  public load: (
    origin: HoverOrigin,
    kind: "type" | "implementation",
  ) => Promise<ProtocolDefinitionLike> = async () => null;
  public readonly actions = new HoverActions(this.output, (origin, kind) => {
    this.loads.push({ origin, kind });
    return this.load(origin, kind);
  });
  private commands: vscode.Disposable | undefined;

  public constructor(public readonly document: vscode.TextDocument) {}

  public hover(): vscode.Hover {
    return new vscode.Hover(this.documentation, this.range);
  }

  public async provide(next: () => vscode.ProviderResult<vscode.Hover>) {
    const provide = this.actions.middleware()?.provideHover;
    assert.ok(provide);
    return await provide(this.document, this.range.start, this.token.token, next);
  }

  public link(hover: vscode.Hover, kind: "type" | "implementation" = "type") {
    const markdown = hover.contents.at(-1);
    assert.ok(markdown instanceof vscode.MarkdownString);
    const links = [
      ...markdown.value.matchAll(/\[([^\]]+)\]\(command:([^?]+)\?([^\s)]+) "([^"]+)"\)/g),
    ];
    assert.equal(links.length, 2);
    const link = links.find((entry) => entry[1] === kind);
    assert.ok(link);
    assert.equal(link[4], `Go to ${kind}`);
    const args: unknown = JSON.parse(decodeURIComponent(link[3]!));
    assert.ok(Array.isArray(args));
    assert.equal(args.length, 1);
    return { command: link[2]!, origin: args[0] as HoverOrigin };
  }

  public async registerCommands(): Promise<boolean> {
    const registered = await vscode.commands.getCommands(true);
    const ids = [
      EXTENSION_COMMANDS.goToTypeFromHover,
      EXTENSION_COMMANDS.goToImplementationFromHover,
    ];
    // The whole extension suite already owns these IDs. The dedicated grep run starts
    // in test_targets with plaintext documents, so these cases must run without skips.
    if (ids.some((id) => registered.includes(id))) {
      return false;
    }
    this.commands = this.actions.registerCommands();
    return true;
  }

  public async edit(): Promise<void> {
    const version = this.document.version;
    const edit = new vscode.WorkspaceEdit();
    edit.insert(this.document.uri, new vscode.Position(1, 0), "edited ");
    assert.equal(await vscode.workspace.applyEdit(edit), true);
    assert.ok(this.document.version > version);
  }

  public async close(): Promise<void> {
    if (!this.document.isClosed) {
      await vscode.window.showTextDocument(this.document);
      await withTimeout(
        vscode.commands.executeCommand("workbench.action.revertAndCloseActiveEditor"),
        "close plaintext hover fixture",
      );
      await waitFor("plaintext hover fixture closed", () => this.document.isClosed, Boolean);
    }
  }

  public async dispose(): Promise<void> {
    this.commands?.dispose();
    this.token.dispose();
    try {
      await this.close();
    } finally {
      this.output.dispose();
    }
  }
}

suite("Hover actions boundaries", () => {
  let fixture: HoverFixture;

  setup(async () => {
    const document = await vscode.workspace.openTextDocument({
      language: "plaintext",
      content: "origin\ntarget\n",
    });
    fixture = new HoverFixture(document);
    await vscode.window.showTextDocument(document);
  });

  teardown(async () => {
    await fixture.dispose();
  });

  test("real Range becomes an object in command JSON without preloading navigation", async () => {
    const hover = await fixture.provide(() => fixture.hover());
    assert.ok(hover);
    const type = fixture.link(hover);
    const implementation = fixture.link(hover, "implementation");
    assert.deepEqual(type.origin.range, {
      start: { line: 0, character: 0 },
      end: { line: 0, character: 6 },
    });
    assert.equal(Array.isArray(type.origin.range), false);
    assert.equal(type.origin.uri, fixture.document.uri.toString());
    assert.equal(type.origin.version, fixture.document.version);
    assert.deepEqual(type.origin.position, { line: 0, character: 0 });
    assert.equal(typeof type.origin.session, "string");
    assert.ok(type.origin.session.length > 0);
    assert.deepEqual(implementation.origin, type.origin);
    assert.equal(type.command, EXTENSION_COMMANDS.goToTypeFromHover);
    assert.equal(implementation.command, EXTENSION_COMMANDS.goToImplementationFromHover);
    assert.deepEqual(fixture.loads, []);
    assert.equal(hover.contents[0], fixture.documentation);
    assert.equal(fixture.documentation.isTrusted, undefined);
    const links = hover.contents.at(-1) as vscode.MarkdownString;
    assert.deepEqual(links.isTrusted, {
      enabledCommands: [type.command, implementation.command],
    });
  });

  for (const obsolete of ["edited", "cancelled", "closed"] as const) {
    test(`middleware discards a ${obsolete} hover that completes late`, async () => {
      const result = new Pending<vscode.Hover>();
      const response = fixture.provide(() => result.promise);
      if (obsolete === "edited") {
        await fixture.edit();
      } else if (obsolete === "cancelled") {
        fixture.token.cancel();
      } else {
        await fixture.close();
      }
      result.resolve(fixture.hover());
      assert.equal(await withTimeout(response, "obsolete hover response"), undefined);
      assert.deepEqual(fixture.loads, []);
    });
  }

  test("a decoded click performs exactly one navigation lookup", async function () {
    if (!(await fixture.registerCommands())) {
      this.skip();
    }
    const hover = await fixture.provide(() => fixture.hover());
    assert.ok(hover);
    const link = fixture.link(hover);
    assert.deepEqual(fixture.loads, []);
    await withTimeout(
      vscode.commands.executeCommand(link.command, link.origin),
      "decoded hover click",
    );
    assert.deepEqual(fixture.loads, [{ origin: link.origin, kind: "type" }]);
  });

  for (const obsolete of ["edited", "closed"] as const) {
    test(`navigation ignores a destination arriving after its document is ${obsolete}`, async function () {
      if (!(await fixture.registerCommands())) {
        this.skip();
      }
      const started = new Pending<void>();
      const destinations = new Pending<ProtocolDefinitionLike>();
      fixture.load = async () => {
        started.resolve(undefined);
        return await destinations.promise;
      };
      const hover = await fixture.provide(() => fixture.hover());
      assert.ok(hover);
      const link = fixture.link(hover);
      const click = vscode.commands.executeCommand(link.command, link.origin);
      await withTimeout(started.promise, "navigation lookup started");
      if (obsolete === "edited") {
        await fixture.edit();
      } else {
        await fixture.close();
      }
      let consumed = 0;
      destinations.resolve({
        uri: fixture.document.uri.toString(),
        // Processing an obsolete result is itself a failure. Throwing also prevents
        // a regressed handler from reaching VS Code's real navigation command.
        get range(): SerializedRange {
          consumed += 1;
          throw new Error("obsolete navigation destination consumed");
        },
      });
      await withTimeout(click, "obsolete navigation response");
      assert.equal(consumed, 0, "obsolete destinations must not reach location conversion");
      assert.equal(fixture.loads.length, 1);
      await withTimeout(
        vscode.commands.executeCommand(link.command, link.origin),
        "click obsolete hover again",
      );
      assert.equal(fixture.loads.length, 1, "an obsolete link must not send another lookup");
    });
  }

  for (const kind of ["type", "implementation"] as const) {
    test(`${kind} navigation rejects a response while the actual session stop is draining`, async () => {
      const hover = await fixture.provide(() => fixture.hover());
      assert.ok(hover);
      const { origin } = fixture.link(hover, kind);
      const response = new Pending<ProtocolDefinitionLike>();
      const shutdown = new Pending<void>();
      const status = new StatusView();
      const workspace = vscode.workspace.workspaceFolders?.[0];
      assert.ok(workspace, "the extension test host must have its test_targets workspace");
      const session = new LanguageClientSession(
        fixture.output,
        fixture.output,
        status,
        workspace.uri,
        workspace,
        fixture.actions,
      );
      const requests: { method: string; params: unknown }[] = [];
      let stopCalls = 0;
      const client = {
        sendRequest(request: { method: string }, params: unknown) {
          requests.push({ method: request.method, params });
          return response.promise;
        },
        stop(timeout: number) {
          stopCalls += 1;
          assert.equal(timeout, 30_000);
          return shutdown.promise;
        },
      };
      // Only inject the live transport and set up the real ClientStatus. Calling
      // stop() below exercises the production identity fence during its await.
      const live = session as unknown as {
        client: LanguageClient | undefined;
        clientStatus: ClientStatus;
      };
      live.client = client as unknown as LanguageClient;
      live.clientStatus.ready({ workspaceRoot: workspace.uri.fsPath });
      assert.equal(session.isRunning(), true);
      const navigation = session.hoverNavigation(origin, kind);
      const rejected = assert.rejects(navigation, /server changed during navigation/);
      const stopping = session.stop();
      try {
        assert.equal(stopCalls, 1);
        assert.equal(session.isRunning(), false);
        assert.equal(live.clientStatus.isRunning(), true, "transport shutdown is still pending");
        response.resolve(null);
        await withTimeout(rejected, "navigation response rejected during stop");
        assert.deepEqual(requests, [
          {
            method: kind === "type" ? "textDocument/typeDefinition" : "textDocument/implementation",
            params: { textDocument: { uri: origin.uri }, position: origin.position },
          },
        ]);
      } finally {
        response.resolve(null);
        shutdown.resolve(undefined);
        await withTimeout(stopping, "finish session shutdown");
        status.dispose();
      }
      assert.equal(live.clientStatus.isRunning(), false);
    });
  }

  test("session navigation preserves an actual LSP content-modified error", async () => {
    const hover = await fixture.provide(() => fixture.hover());
    assert.ok(hover);
    const { origin } = fixture.link(hover);
    const status = new StatusView();
    const workspace = vscode.workspace.workspaceFolders?.[0];
    assert.ok(workspace);
    const session = new LanguageClientSession(
      fixture.output,
      fixture.output,
      status,
      workspace.uri,
      workspace,
      fixture.actions,
    );
    const error = new ResponseError(-32801, "source changed before navigation");
    const live = session as unknown as {
      client: LanguageClient | undefined;
      clientStatus: ClientStatus;
    };
    live.client = {
      sendRequest: async () => {
        throw error;
      },
      stop: async () => {},
    } as unknown as LanguageClient;
    live.clientStatus.ready({ workspaceRoot: workspace.uri.fsPath });
    try {
      await assert.rejects(session.hoverNavigation(origin, "type"), (actual) => actual === error);
    } finally {
      await session.stop();
      status.dispose();
    }
  });
});
