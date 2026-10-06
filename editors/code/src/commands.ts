/**
 * Central registry of command identifiers used by the extension and the language server.
 *
 * Keeping command strings here prevents package manifest commands, VS Code registrations, hover
 * links, tests, and LSP execute-command calls from drifting apart.
 */
export const EXTENSION_COMMANDS = {
  showServerActions: "suprnova-lsp.showServerActions",
  startServer: "suprnova-lsp.startServer",
  restartServer: "suprnova-lsp.restartServer",
  stopServer: "suprnova-lsp.stopServer",
  reindexWorkspace: "suprnova-lsp.reindexWorkspace",
  openLogs: "suprnova-lsp.openLogs",
  goToTypeFromHover: "suprnova-lsp.gotoTypeFromHover",
  goToImplementationFromHover: "suprnova-lsp.gotoImplementationFromHover",
  testGetState: "suprnova-lsp.test.getState",
  testGetOutput: "suprnova-lsp.test.getOutput",
} as const;

export const SERVER_COMMANDS = {
  reindexWorkspace: "suprnova-lsp.internal.reindexWorkspace",
} as const;

export const SERVER_NOTIFICATIONS = {
  rustdocStatus: "suprnova-lsp/rustdocStatus",
  activeWorkspaceChanged: "suprnova-lsp/activeWorkspaceChanged",
  deferredIndexingStarted: "suprnova-lsp/deferredIndexingStarted",
  deferredIndexingFinished: "suprnova-lsp/deferredIndexingFinished",
} as const;
