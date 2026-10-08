/** Return hover text promptly; look up navigation destinations when a link is clicked. */
import { randomUUID } from "node:crypto";
import * as vscode from "vscode";
import type { LanguageClientOptions } from "vscode-languageclient/node";

import { EXTENSION_COMMANDS } from "../commands";
import {
  hoverAction,
  hoverActionLinkLine,
  locationsExcludingCurrentHover,
  protocolDefinitionLocations,
  uniqueLocations,
  type HoverOrigin,
  type ProtocolDefinitionLike,
} from "./hover-actions-model";

export class HoverActions {
  // A weak key distinguishes reopening a document from editing the same open
  // document, without retaining its text after VS Code closes it.
  private readonly sessions = new WeakMap<vscode.TextDocument, string>();

  public constructor(
    private readonly output: vscode.LogOutputChannel,
    private readonly load: (
      origin: HoverOrigin,
      kind: "type" | "implementation",
    ) => Promise<ProtocolDefinitionLike>,
  ) {}

  public middleware(): LanguageClientOptions["middleware"] {
    return {
      provideHover: async (document, position, token, next) => {
        const version = document.version;
        const hover = await next(document, position, token);
        if (token.isCancellationRequested || document.isClosed || document.version !== version) {
          return undefined;
        }
        if (hover == null) {
          return hover;
        }
        let session = this.sessions.get(document);
        if (session === undefined) {
          session = randomUUID();
          this.sessions.set(document, session);
        }
        const origin: HoverOrigin = {
          uri: document.uri.toString(),
          position: { line: position.line, character: position.character },
          // VS Code Range.toJSON() produces a tuple. Command arguments need the
          // same plain range shape that location filtering reads after decoding.
          range:
            hover.range === undefined
              ? undefined
              : {
                  start: { line: hover.range.start.line, character: hover.range.start.character },
                  end: { line: hover.range.end.line, character: hover.range.end.character },
                },
          version,
          session,
        };
        const line = hoverActionLinkLine([
          hoverAction(EXTENSION_COMMANDS.goToTypeFromHover, "type", origin),
          hoverAction(EXTENSION_COMMANDS.goToImplementationFromHover, "implementation", origin),
        ]);
        const links = new vscode.MarkdownString(line.markdown);
        // Trust only these local command links; server documentation remains untrusted.
        links.isTrusted = { enabledCommands: [...line.enabledCommands] };
        const contents = Array.isArray(hover.contents) ? [...hover.contents] : [hover.contents];
        return new vscode.Hover([...contents, links], hover.range);
      },
    };
  }

  public registerCommands(): vscode.Disposable {
    return vscode.Disposable.from(
      vscode.commands.registerCommand(EXTENSION_COMMANDS.goToTypeFromHover, (origin: unknown) =>
        this.navigate(origin, "type"),
      ),
      vscode.commands.registerCommand(
        EXTENSION_COMMANDS.goToImplementationFromHover,
        (origin: unknown) => this.navigate(origin, "implementation"),
      ),
    );
  }

  private document(origin: HoverOrigin | undefined): vscode.TextDocument | undefined {
    if (origin === undefined) {
      return undefined;
    }
    return vscode.workspace.textDocuments.find(
      (document) =>
        !document.isClosed &&
        document.uri.toString() === origin.uri &&
        document.version === origin.version &&
        this.sessions.get(document) === origin.session,
    );
  }

  private async navigate(value: unknown, kind: "type" | "implementation") {
    // Commands can also be invoked directly, without our trusted Markdown link.
    // Reject malformed positions and ranges before constructing VS Code values.
    const candidate =
      value !== null && typeof value === "object" ? (value as Partial<HoverOrigin>) : undefined;
    const positions = [candidate?.position];
    if (candidate?.range !== undefined) {
      positions.push(candidate.range?.start, candidate.range?.end);
    }
    if (
      candidate === undefined ||
      typeof candidate.uri !== "string" ||
      typeof candidate.session !== "string" ||
      !Number.isSafeInteger(candidate.version) ||
      positions.some(
        (position) =>
          position === undefined ||
          position === null ||
          !Number.isSafeInteger(position.line) ||
          position.line < 0 ||
          !Number.isSafeInteger(position.character) ||
          position.character < 0,
      )
    ) {
      this.output.warn("invalid hover navigation origin");
      return;
    }
    const origin = candidate as HoverOrigin;
    const document = this.document(origin);
    if (document === undefined || origin === undefined) {
      void vscode.window.showInformationMessage(
        "Hover target changed; hover again before navigating.",
      );
      return;
    }
    const position = new vscode.Position(origin.position.line, origin.position.character);
    try {
      const targets = await this.load(origin, kind);
      // An edit or close can overtake the lookup. Never move to an old location
      // just because the result arrived after the originating hover disappeared.
      if (this.document(origin) !== document) {
        void vscode.window.showInformationMessage(
          "Hover target changed; hover again before navigating.",
        );
        return;
      }
      const locations = locationsExcludingCurrentHover(
        uniqueLocations(protocolDefinitionLocations(targets)),
        origin.uri,
        origin.range,
      ).map(
        (target) =>
          new vscode.Location(
            vscode.Uri.parse(target.uri),
            new vscode.Range(
              target.range.start.line,
              target.range.start.character,
              target.range.end.line,
              target.range.end.character,
            ),
          ),
      );
      const missing = kind === "type" ? "No type definition found" : "No implementation found";
      if (locations.length === 0) {
        void vscode.window.showInformationMessage(missing);
        return;
      }
      await vscode.commands.executeCommand(
        "editor.action.goToLocations",
        document.uri,
        position,
        locations,
        "peek",
        missing,
      );
    } catch (error) {
      this.output.warn(`hover ${kind} navigation failed: ${String(error)}`);
      void vscode.window.showWarningMessage(`Suprnova LSP navigation failed: ${String(error)}`);
    }
  }
}
