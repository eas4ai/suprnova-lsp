import * as assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ClientStatus, type ClientStatusView } from "../src/status/client-status";
import type { StatusDetails } from "../src/status/status-model";

const DETAILS: StatusDetails = {
  workspaceRoot: "/workspace/window",
  serverCommand: "suprnova-lsp lsp",
  serverSource: "test",
};

describe("client status state precedence", () => {
  it("keeps generated freshness scoped to its root and rejects obsolete status", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);
    status.activeWorkspace("/workspace/a", "ready", undefined, false);
    status.rustdocStatus(
      {
        workspaceRoot: "/workspace/b",
        generation: 2,
        state: "failed",
        message: "producer missing; install and reindex",
      },
      false,
    );
    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [a]");
    status.activeWorkspace("/workspace/b", "ready", undefined, false);
    assert.match(render(status), /generated APIs failed/);
    assert.match(status.snapshot().details?.generatedApiMessage ?? "", /install and reindex/);
    status.rustdocStatus(
      { workspaceRoot: "/workspace/b", generation: 3, state: "current", message: "current" },
      false,
    );
    status.rustdocStatus(
      { workspaceRoot: "/workspace/b", generation: 2, state: "failed", message: "late failure" },
      false,
    );
    assert.match(render(status), /generated APIs current/);
    status.refresh(true);
    assert.equal(status.snapshot().status.state, "stale");
    assert.match(render(status), /generated APIs current/);
    status.starting(DETAILS);
    status.ready(DETAILS);
    status.activeWorkspace("/workspace/b", "ready", undefined, false);
    assert.doesNotMatch(render(status), /generated APIs/);
  });
  it("lets engine state, dirty files, and diagnostics win in that order", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);
    status.handleWorkDoneProgress(
      "cargo",
      { kind: "begin", title: "Cargo diagnostics", message: "cargo check" },
      false,
    );

    assert.equal(
      render(status),
      "diagnostics-running: $(sync~spin) Suprnova LSP: cargo check running",
    );

    status.activeWorkspace("/workspace/project_a", "indexing", undefined, true);
    assert.equal(render(status), "indexing: $(sync~spin) Suprnova LSP: indexing [project_a]");

    status.activeWorkspace("/workspace/project_a", "ready", undefined, true);
    assert.equal(render(status), "stale: $(warning) Suprnova LSP: stale until save [project_a]");

    status.refresh(false);
    assert.equal(
      render(status),
      "diagnostics-running: $(sync~spin) Suprnova LSP: cargo check running [project_a]",
    );

    status.handleWorkDoneProgress("cargo", { kind: "end", message: "Failed" }, false);
    assert.equal(
      render(status),
      "diagnostics-failed: $(error) Suprnova LSP: cargo check failed [project_a]",
    );
  });

  it("keeps active workspace failure above dirty and diagnostics state", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);
    status.handleWorkDoneProgress(
      "cargo",
      { kind: "begin", title: "Cargo diagnostics", message: "cargo check" },
      false,
    );

    status.activeWorkspace("/workspace/project_b", "failed", "index failed", true);

    assert.equal(render(status), "failed: $(error) Suprnova LSP: failed [project_b]");
    assert.equal(status.snapshot().diagnosticsRunning, true);
    assert.equal(status.snapshot().failureReason, undefined);
  });

  it("shows a ready status while deferred indexing finishes", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);

    status.activeWorkspace("/workspace/project_c", "ready", undefined, false);
    status.deferredIndexingStarted("/workspace/project_c", false);
    assert.equal(render(status), "ready: ~ Suprnova LSP: ready [project_c]");

    status.deferredIndexingFinished("/workspace/project_c", "succeeded", undefined, false);
    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [project_c]");
  });

  it("keeps the workspace ready but warns when deferred indexing fails", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);
    status.activeWorkspace("/workspace/project_c", "ready", undefined, false);
    status.deferredIndexingStarted("/workspace/project_c", false);

    status.deferredIndexingFinished(
      "/workspace/project_c",
      "failed",
      "body indexing failed",
      false,
    );
    assert.equal(
      render(status),
      "ready: $(warning) Suprnova LSP: ready; background index failed [project_c]",
    );

    status.deferredIndexingStarted("/workspace/project_c", false);
    assert.equal(render(status), "ready: ~ Suprnova LSP: ready [project_c]");
    status.deferredIndexingFinished("/workspace/project_c", "succeeded", undefined, false);
    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [project_c]");
  });

  it("returns through indexing and explicit deferred-ready after a saved project rebuild", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);
    status.activeWorkspace("/workspace/project_c", "ready", undefined, false);
    status.deferredIndexingFinished("/workspace/project_c", "succeeded", undefined, false);
    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [project_c]");

    status.activeWorkspace("/workspace/project_c", "indexing", undefined, false);
    assert.equal(render(status), "indexing: $(sync~spin) Suprnova LSP: indexing [project_c]");

    status.deferredIndexingStarted("/workspace/project_c", false);
    status.activeWorkspace("/workspace/project_c", "ready", undefined, false);
    assert.equal(render(status), "ready: ~ Suprnova LSP: ready [project_c]");
    status.deferredIndexingFinished("/workspace/project_c", "succeeded", undefined, false);
    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [project_c]");
  });

  it("does not invent deferred work for an ordinary indexing cycle", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);
    status.activeWorkspace("/workspace/project_c", "ready", undefined, false);
    status.deferredIndexingStarted("/workspace/project_c", false);
    status.deferredIndexingFinished("/workspace/project_c", "succeeded", undefined, false);

    status.indexing();
    status.activeWorkspace("/workspace/project_c", "indexing", undefined, false);
    status.activeWorkspace("/workspace/project_c", "ready", undefined, false);

    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [project_c]");
  });

  it("keeps deferred indexing state scoped to workspace roots", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);

    status.activeWorkspace("/workspace/project_a", "ready", undefined, false);
    status.deferredIndexingStarted("/workspace/project_b", false);
    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [project_a]");

    status.activeWorkspace("/workspace/project_b", "ready", undefined, false);
    assert.equal(render(status), "ready: ~ Suprnova LSP: ready [project_b]");
  });

  it("does not show deferred indexing when finish arrives before ready", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);

    status.deferredIndexingStarted("/workspace/project_e", false);
    status.deferredIndexingFinished("/workspace/project_e", "succeeded", undefined, false);
    status.activeWorkspace("/workspace/project_e", "ready", undefined, false);

    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [project_e]");
  });

  it("preserves active workspace label across language-client ready transitions", () => {
    const status = clientStatus();
    status.starting(DETAILS);
    status.ready(DETAILS);
    status.activeWorkspace("/workspace/project_d", "ready", undefined, false);

    status.ready({
      ...DETAILS,
      workspaceRoot: "/workspace/restarted-window",
    });

    assert.equal(render(status), "ready: $(check) Suprnova LSP: ready [project_d]");
    assert.deepEqual(status.snapshot().details, {
      ...DETAILS,
      workspaceRoot: "/workspace/restarted-window",
      activeWorkspaceRoot: "/workspace/project_d",
    });
  });
});

function clientStatus(): ClientStatus {
  return new ClientStatus(noopView(), () => false);
}

function render(status: ClientStatus): string {
  const snapshot = status.snapshot().status;
  return `${snapshot.state}: ${snapshot.text}`;
}

function noopView(): ClientStatusView {
  return {
    starting() {},
    indexing() {},
    ready() {},
    readyWithDeferredIndexing() {},
    readyWithDeferredIndexingFailure() {},
    stale() {},
    diagnosticsRunning() {},
    diagnosticsFailed() {},
    stopped() {},
    failed() {},
  };
}
