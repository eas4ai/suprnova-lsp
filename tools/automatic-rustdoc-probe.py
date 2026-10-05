#!/usr/bin/env python3
"""Exercise automatic exports using the repository's existing managed LSP client."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
STATUS = "rust-glancer/rustdocStatus"


spec = importlib.util.spec_from_file_location("automatic_probe_helpers", ROOT / "tools/sudus-editor-import.py")
helpers = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helpers
spec.loader.exec_module(helpers)
lsp = helpers.module("automatic_lsp_query", ROOT / "tools/lsp-query.py")
runner = helpers.module("automatic_agent_debug", ROOT / "tools/agent-debug.py")


class ObservedClient(lsp.LspClient):
    def __init__(self, *args):
        self.observed = []
        super().__init__(*args)

    async def _on_message(self, message):
        if "id" not in message and message.get("method") == STATUS:
            if len(self.observed) >= 1024:
                raise RuntimeError("worker notification observation exceeded its bound")
            self.observed.append({"monotonicNs": time.monotonic_ns(), **message})
            status = message.get("params") or {}
            print(f"worker {status.get('workspaceRoot')} generation {status.get('generation')}: {status.get('state')} {str(status.get('message', ''))[:512]}", flush=True)
        await super()._on_message(message)


class AutomaticProbe:
    def __init__(self, plan):
        self.plan = plan
        self.root = Path(plan["root"]).resolve()
        self.control = Path(plan["control"])
        self.events_file = Path(plan["events"])
        self.artifacts = Path(plan["artifactRoot"])
        self.client = None
        self.texts = {}
        self.versions = {}
        self.results = {}
        self.evidence = {}
        self.originals = {}
        self.indexing_finished = asyncio.Event()
        self.memory_task = None

    def events(self):
        if not self.events_file.exists():
            return []
        return [json.loads(line) for line in self.events_file.read_text().splitlines()]

    def set_control(self, mode, **values):
        self.control.write_text(json.dumps({"mode": mode, "artifactRoot": str(self.artifacts), **values}))

    def status_events(self, state=None, since=0, root=None):
        result = []
        for event in self.client.observed:
            params = event.get("params") or {}
            selected = params.get("workspaceRoot", params.get("root"))
            if selected not in {str(root or self.root), (root or self.root).as_uri()}:
                continue
            if event["monotonicNs"] >= since and (state is None or params.get("state") == state):
                result.append(event)
        return result

    async def status(self, state, since=0, timeout=60, root=None):
        started = time.monotonic()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = self.status_events(state, since, root)
            if events:
                return events[-1]["params"]
            latest = self.status_events(since=since, root=root)
            if state == "current" and latest and latest[-1]["params"].get("state") == "failed":
                raise AssertionError(f"worker failed before current: {latest[-1]['params'].get('message')}")
            if self.client.exited:
                raise AssertionError("LSP exited before worker status")
            if time.monotonic() - started >= 10 and not self.status_events(root=root):
                raise AssertionError("no automatic worker startup was observed")
            await asyncio.sleep(0.02)
        raise AssertionError(f"no {state} worker observation for {root or self.root}")

    async def event(self, kind, since=0, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = [e for e in self.events() if e["event"] == kind and e["monotonicNs"] >= since]
            if events:
                return events[-1]
            await asyncio.sleep(0.02)
        raise AssertionError(f"no observed compiler {kind} event")

    async def open(self, path, text=None):
        path = Path(path).resolve()
        text = text if text is not None else path.read_text()
        self.texts[path] = text
        self.versions[path] = 1
        await self.client.notify("textDocument/didOpen", {"textDocument": {
            "uri": path.as_uri(), "languageId": "rust", "version": 1, "text": text}})

    async def change(self, path, text, save=True, write=True):
        path = Path(path).resolve()
        assert path.is_relative_to(self.root), "fixture edit escaped its workspace"
        assert not self.plan["scenario"].startswith("devlist"), "Devlist probes are unsaved only"
        self.originals.setdefault(path, path.read_bytes() if path.exists() else None)
        self.versions[path] = self.versions.get(path, 0) + 1
        self.texts[path] = text
        if write:
            path.write_text(text)
        await self.client.notify("textDocument/didChange", {
            "textDocument": {"uri": path.as_uri(), "version": self.versions[path]},
            "contentChanges": [{"text": text}]})
        if save:
            await self.client.notify("textDocument/didSave", {
                "textDocument": {"uri": path.as_uri()}, "text": text})

    async def hover(self, path, marker, timeout=30000):
        path = Path(path).resolve()
        position = lsp.query_position({"marker": marker}, self.texts[path])
        response = await self.client.request("textDocument/hover", {
            "textDocument": {"uri": path.as_uri()}, "position": position}, timeout)
        assert "error" not in response, response
        return lsp.hover_text(response.get("result")) or ""

    async def complete(self, path, marker, delta=None):
        path = Path(path).resolve()
        position = lsp.query_position({"marker": marker, "delta": len(marker) if delta is None else delta}, self.texts[path])
        response = await self.client.request("textDocument/completion", {
            "textDocument": {"uri": path.as_uri()}, "position": position})
        assert "error" not in response, response
        value = lsp.normalize_completions(response.get("result"), 1000)
        assert not value["isIncomplete"] and not value["truncated"], value
        return [item["label"].split("(")[0].split("<")[0].strip() for item in value["items"]]

    async def reindex(self):
        response = await self.client.request("workspace/executeCommand", {
            "command": "rust-glancer.internal.reindexWorkspace", "arguments": []})
        assert "error" not in response, response

    async def case(self, name, operation):
        if name in self.results:
            raise ValueError(f"duplicate acceptance case: {name}")
        try:
            value = await operation()
            self.results[name] = {"passed": True, "attempted": True, "evidence": value}
        except (AssertionError, RuntimeError, TimeoutError, OSError, ValueError, KeyError, lsp.LspQueryError) as error:
            self.results[name] = {"passed": False, "attempted": True, "error": str(error)}
            self.set_control("real")
        print(f"automatic case {name}: {'pass' if self.results[name]['passed'] else 'fail'}", flush=True)
        if not self.results[name]["passed"]:
            print(self.results[name]["error"], flush=True)
        if report := self.plan.get("report"):
            Path(report).write_text(json.dumps({"cases": self.results, "evidence": self.evidence}, indent=2) + "\n")

    async def start(self):
        self.client = await ObservedClient.start(Path(self.plan["binary"]), self.root, 300000, False)
        self.memory_task = asyncio.create_task(self.client.indexing_memory(self.indexing_finished))
        options = {"cfg": {"test": False}, "cache": {"packageResidency": "workspace"},
                   "indexing": {"performancePreference": self.plan["preference"]},
                   "rustdoc": {"automatic": {"artifactRoot": str(self.artifacts),
                       "debounceMs": self.plan.get("debounceMs", 2000),
                       "timeoutMs": self.plan.get("timeoutMs", 900000), "jobs": 2}}}
        # Omit enabled on the default-discovery run so a future default-off implementation fails.
        options["rustdoc"]["automatic"].update(self.plan.get("automatic", {}))
        if "inputs" in self.plan:
            options["rustdoc"]["inputs"] = self.plan["inputs"]
        if "cargo" in self.plan:
            options["cargo"] = self.plan["cargo"]
        roots = [self.root, *[Path(p).resolve() for p in self.plan.get("extraRoots", [])]]
        response = await self.client.request("initialize", {"processId": os.getpid(),
            "rootUri": self.root.as_uri(), "workspaceFolders": [{"uri": p.as_uri(), "name": p.name} for p in roots],
            "capabilities": {"experimental": {"serverStatusNotification": True, "rustdocStatusNotification": True},
                "textDocument": {"hover": {"contentFormat": ["markdown"]},
                    "completion": {"completionItem": {"snippetSupport": True}},
                    "inlayHint": {"dynamicRegistration": False}}}, "initializationOptions": options})
        assert "error" not in response, response
        await self.client.notify("initialized", {})
        for document in self.plan["documents"]:
            await self.open(self.root / document["file"], document.get("text"))
        await lsp.wait_until_ready(self.client, 300000)

    async def model_queries(self):
        await self.status("current", timeout=self.plan.get("workerWaitSeconds", 900))
        assert self.status_events("pending"), "startup did not expose pending generated APIs"
        path = self.root / "src/lib.rs"
        for marker, expected in [("automatic_post", "Builder<Post>"), ("automatic_other", "Builder<Other>")]:
            actual = await self.hover(path, marker)
            assert expected in actual, (marker, actual)
        binary = self.root / "src/main.rs"
        assert "Builder<BinaryPost>" in await self.hover(binary, "automatic_binary")
        other = await self.complete(path, "let automatic_unrelated = unrelated::")
        assert "query" not in other and "filter" not in other, other
        return {"owners": ["Post", "Other", "BinaryPost"], "renamedDependency": True,
                "isolated": True, "events": self.events()}

    async def producer_identity(self):
        await self.status("current", timeout=self.plan.get("workerWaitSeconds", 900))
        events = self.events()
        starts = [e for e in events if e["event"] == "started"]
        finishes = [e for e in events if e["event"] == "finished"]
        assert starts and len(finishes) == len(starts), events
        for entry in starts:
            args = entry["args"]
            assert "--locked" in args
            assert "--document-private-items" in args and "--document-hidden-items" in args
            assert "--output-format" in args and args[args.index("--output-format") + 1] == "json"
            assert "--jobs" in args and args[args.index("--jobs") + 1] == "2"
            assert "--target-dir" in args
            assert Path(args[args.index("--target-dir") + 1]).resolve().is_relative_to(self.artifacts.resolve())
            assert "+nightly-2026-08-19" in args or entry["toolchain"] == "nightly-2026-08-19"
            if self.plan.get("cargo", {}).get("target"):
                assert "--target" in args and args[args.index("--target") + 1] == self.plan["cargo"]["target"]
            if self.plan.get("cargo", {}).get("features"):
                assert "--features" in args and "extra" in args[args.index("--features") + 1].split(",")
        assert all(e["code"] == 0 and e["exports"] for e in finishes), finishes
        assert not (self.root / "target").exists(), "worker wrote ordinary Cargo output"
        return {"starts": starts, "finished": finishes}

    async def debounce(self):
        await self.status("current", timeout=5)
        path = self.root / "src/lib.rs"
        base = self.texts[path]
        since = time.monotonic_ns()
        for i in range(3):
            await self.change(path, base + f"\n// saved burst {i}\n")
            if i < 2:
                await asyncio.sleep(0.1)
        last_save = time.monotonic_ns()
        await self.status("current", since, timeout=180)
        starts = [e for e in self.events() if e["event"] == "started" and e["monotonicNs"] >= since]
        assert len(starts) == 2, "one pass must export exactly the fixture's library and binary"
        assert min(e["monotonicNs"] for e in starts) >= last_save + self.plan.get("debounceMs", 2000) * 1_000_000 - 100_000_000
        await asyncio.sleep(1.5)
        assert len(self.status_events("current", since)) == 1, "burst or outputs retriggered a pass"
        return {"lastSaveNs": last_save, "starts": starts}

    async def unsaved_and_duplicate(self):
        await self.status("current", timeout=5)
        path = self.root / "src/lib.rs"
        disk = path.read_text()
        since = time.monotonic_ns()
        await self.change(path, disk + "\n// unsaved typing\n", save=False, write=False)
        await asyncio.sleep(self.plan.get("debounceMs", 2000) / 1000 + 1)
        assert not [e for e in self.events() if e["event"] == "started" and e["monotonicNs"] >= since]
        await self.change(path, disk, save=True, write=False)
        await self.client.notify("textDocument/didSave", {"textDocument": {"uri": path.as_uri()}, "text": disk})
        await asyncio.sleep(self.plan.get("debounceMs", 2000) / 1000 + 1)
        assert not [e for e in self.events() if e["event"] == "started" and e["monotonicNs"] >= since]
        return {"unchangedSavedText": True, "unsavedTyping": True}

    async def live_refresh(self):
        await self.status("current", timeout=5)
        path = self.root / "src/lib.rs"
        original = self.texts[path]
        changed = original.replace("pub title: String,", "pub title: String,\n    pub rank: i32,", 1)
        changed = changed.replace("post::Column::Title", "post::Column::Rank")
        since = time.monotonic_ns()
        await self.change(path, changed)
        await self.status("current", since, timeout=180)
        assert "Rank" in await self.hover(path, "Rank"), "fresh generated column absent"
        columns = await self.complete(path, "let automatic_column = post::Column::")
        assert columns.count("Rank") == 1, columns
        since = time.monotonic_ns()
        await self.change(path, original)
        await self.status("current", since, timeout=180)
        columns = await self.complete(path, "let automatic_column = post::Column::")
        assert "Rank" not in columns and columns.count("Title") == 1, columns
        return {"addedColumn": "Rank", "removedColumn": "Rank", "sameSession": True}

    async def failed_candidate(self, mode):
        await self.status("current", timeout=5)
        path = self.root / "src/lib.rs"
        before = await self.hover(path, "automatic_post")
        columns = await self.complete(path, "let automatic_column = post::Column::")
        original = self.texts[path]
        changed = original.replace("pub title: String,", "pub title: String,\n    pub failed_column: i32,", 1)
        assert changed != original
        self.set_control(mode)
        since = time.monotonic_ns()
        await self.change(path, changed)
        failed = await self.status("failed", since, timeout=180)
        assert failed.get("message"), "failed export lacked actionable context"
        if mode == "flood":
            assert len(json.dumps(failed).encode()) < 64 * 1024, "compiler diagnostics were not bounded"
        assert not self.status_events("current", since), "failed candidate reported freshness"
        assert await self.hover(path, "automatic_post") == before, "failed candidate changed published facts"
        assert sorted(await self.complete(path, "let automatic_column = post::Column::")) == sorted(columns), "partial generated columns published"
        if mode in {"invalid-schema", "invalid-reference"}:
            finishes = [e for e in self.events() if e["event"] == "finished" and e["monotonicNs"] >= since]
            kind = "schema" if mode == "invalid-schema" else "reference"
            assert any(e.get("code") == 0 and any(m["kind"] == kind for m in e.get("mutations", []))
                       for e in finishes), "the export was not actually corrupted after successful compilation"
        starts = len([e for e in self.events() if e["event"] == "started"])
        await asyncio.sleep(self.plan.get("debounceMs", 2000) / 1000 + 1)
        assert len([e for e in self.events() if e["event"] == "started"]) == starts, "failure retried without a trigger"
        self.set_control("real")
        since = time.monotonic_ns()
        await self.change(path, original)
        await self.status("current", since, timeout=180)
        assert "Builder<Post>" in await self.hover(path, "automatic_post")
        return {"failure": failed, "preserved": True, "recovered": True}

    async def obsolete_failure(self):
        await self.status("current", timeout=5)
        self.set_control("late-failure")
        since = time.monotonic_ns()
        await self.reindex()
        child = await self.event("child", since)
        self.set_control("real")
        path = self.root / "src/lib.rs"
        await self.change(path, self.texts[path] + "\n// newer saved generation\n")
        current = await self.status("current", since, timeout=180)
        completed = time.monotonic_ns()
        await asyncio.sleep(2.5)
        assert not self.status_events("failed", completed), "obsolete failure replaced current state"
        assert not self.alive(child["pid"]) and not self.alive(child["childPid"])
        replacements = [e for e in self.events() if e["event"] == "started" and e["monotonicNs"] >= since]
        assert replacements and all(not e["livePriorChildren"] for e in replacements)
        return {"current": current, "obsoleteChild": child, "cleanup": True}

    async def held_query(self):
        await self.status("current", timeout=5)
        self.set_control("hold")
        since = time.monotonic_ns()
        await self.reindex()
        child = await self.event("child", since)
        assert self.alive(child["pid"]) and self.alive(child["childPid"]), "held compiler was not actually live"
        path = self.root / "src/lib.rs"
        # Reindex starts deferred body work and clears query caches. Prove the cold query also
        # finishes with the compiler held, then measure the same already-materialized source query.
        cold_started = time.monotonic_ns()
        assert "u64" in await self.hover(path, "automatic_source")
        cold_elapsed = (time.monotonic_ns() - cold_started) / 1_000_000
        started = time.monotonic_ns()
        assert "u64" in await self.hover(path, "automatic_source", timeout=1000)
        elapsed = (time.monotonic_ns() - started) / 1_000_000
        assert self.alive(child["pid"]) and self.alive(child["childPid"]), "held compiler was not actually live"
        self.set_control("real")
        since = time.monotonic_ns()
        await self.change(path, self.texts[path] + "\n// supersede held compiler\n")
        await self.status("current", since, timeout=180)
        assert not self.alive(child["pid"]) and not self.alive(child["childPid"]), "compiler descendant survived supersession"
        return {"elapsedMs": elapsed, "coldElapsedMs": cold_elapsed, "observedChild": child, "cleanup": True}

    @staticmethod
    def alive(pid):
        return (Path("/proc") / str(pid)).exists()

    async def no_worker(self):
        await lsp.wait_until_indexing_settled(self.client, 300000)
        await asyncio.sleep(self.plan.get("debounceMs", 2000) / 1000 + 1)
        assert not self.events(), "disabled, prepared or non-framework mode started an export"
        return {"noCompiler": True}

    async def fs_change(self, path, text):
        path = Path(path).resolve()
        assert path.is_relative_to(self.root), "fixture write escaped its workspace"
        assert not self.plan["scenario"].startswith("devlist"), "Devlist source is read-only in this probe"
        self.originals.setdefault(path, path.read_bytes() if path.exists() else None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    async def native_inputs(self):
        await self.status("current", timeout=5)
        observed = []
        for relative, text in [
            ("src/lib.rs", (self.root / "src/lib.rs").read_text() + "\n// external saved Rust\n"),
            ("Cargo.toml", (self.root / "Cargo.toml").read_text() + "\n# external manifest\n"),
            ("Cargo.lock", (self.root / "Cargo.lock").read_text() + "\n# external lockfile\n"),
            (".cargo/config.toml", "[build]\njobs = 1\n"),
            ("rust-toolchain.toml", '[toolchain]\nchannel = "1.98.1"\n'),
        ]:
            since = time.monotonic_ns()
            await self.fs_change(self.root / relative, text)
            await self.status("current", since, timeout=180)
            observed.append(relative)
        return {"watched": observed}

    async def model_add_remove(self):
        await self.status("current", timeout=5)
        path = self.root / "src/lib.rs"
        original = self.texts[path]
        declaration = '\n#[framework::model(table = "fresh_posts")]\npub struct Fresh { pub id: i64, pub title: String }\n'
        changed = original.replace("let automatic_other", "let automatic_fresh = Fresh::query();\n    let automatic_other") + declaration
        since = time.monotonic_ns()
        await self.change(path, changed)
        await self.status("current", since, timeout=180)
        assert "Builder<Fresh>" in await self.hover(path, "automatic_fresh")
        removed = original.replace("let automatic_other", "let automatic_fresh = 0u64;\n    let automatic_other")
        since = time.monotonic_ns()
        await self.change(path, removed)
        await self.status("current", since, timeout=180)
        assert "u64" in await self.hover(path, "automatic_fresh")
        assert "Fresh" not in await self.complete(path, "let automatic_fresh = ")
        return {"added": "Fresh", "removed": "Fresh", "sameSession": True}

    async def target_add_remove(self):
        await self.status("current", timeout=5)
        manifest = self.root / "Cargo.toml"
        original = manifest.read_text()
        extra = self.root / "src/extra.rs"
        source = (self.root / "src/main.rs").read_text().replace("BinaryPost", "ExtraPost")
        await self.fs_change(extra, source)
        since = time.monotonic_ns()
        await self.fs_change(manifest, original + '\n[[bin]]\nname = "extra_model"\npath = "src/extra.rs"\n')
        await self.status("current", since, timeout=180)
        await self.open(extra)
        assert "Builder<ExtraPost>" in await self.hover(extra, "automatic_binary")
        starts = [e for e in self.events() if e["event"] == "started" and e["monotonicNs"] >= since]
        assert any("extra_model" in e["args"] for e in starts), "new target was not exported"
        since = time.monotonic_ns()
        await self.fs_change(manifest, original)
        extra.unlink()
        await self.client.notify("textDocument/didClose", {"textDocument": {"uri": extra.as_uri()}})
        await self.status("current", since, timeout=180)
        starts = [e for e in self.events() if e["event"] == "started" and e["monotonicNs"] >= since]
        assert starts and not any("extra_model" in e["args"] for e in starts)
        assert "Builder<Post>" in await self.hover(self.root / "src/lib.rs", "automatic_post")
        return {"target": "extra_model", "added": True, "removed": True}

    async def stale_disk_result(self):
        await self.status("current", timeout=5)
        release = self.control.parent / "release-compiler"
        release.unlink(missing_ok=True)
        self.set_control("real", releaseFile=str(release))
        since = time.monotonic_ns()
        await self.reindex()
        await self.event("compiled", since, timeout=180)
        running = await self.status("running", since, timeout=5)
        assert type(running.get("generation")) is int, "worker lacked a run identity"
        path = self.root / "src/lib.rs"
        changed = self.texts[path].replace("pub title: String,", "pub title: String,\n    pub score: i32,", 1)
        changed = changed.replace("post::Column::Title", "post::Column::Score")
        changed_at = time.monotonic_ns()
        # Release the compiler before the native watcher's quiet wait can notify the worker.
        # Publication must notice the new disk identity rather than trusting notification order.
        await self.fs_change(path, changed)
        self.set_control("real")
        release.write_text("release\n")
        await self.status("current", changed_at, timeout=180)
        assert all(e["params"].get("generation") != running["generation"]
                   for e in self.status_events("current", changed_at)), "obsolete compiler run published"
        await self.change(path, changed, save=False, write=False)
        assert "Score" in await self.hover(path, "Score")
        return {"rejectedGeneration": running["generation"], "diskChangedBeforeNotification": True}

    async def timeout_or_shutdown(self):
        child = await self.event("child", timeout=10)
        if self.plan["scenario"] == "timeout":
            failed = await self.status("failed", timeout=15)
            assert "time" in failed.get("message", "").lower(), failed
        else:
            await asyncio.wait_for(self.client.close(), 15)
        assert not self.alive(child["pid"]) and not self.alive(child["childPid"]), "owned compiler process survived"
        return {"child": child, "cleanup": True}

    async def global_serial(self):
        await self.status("current", timeout=5)
        self.set_control("hold")
        since = time.monotonic_ns()
        await self.reindex()
        child = await self.event("child", since)
        second = Path(self.plan["extraRoots"][0]).resolve()
        await self.open(second / "src/lib.rs")
        assert "u64" in await self.hover(second / "src/lib.rs", "automatic_source", timeout=300000)
        await asyncio.sleep(self.plan.get("debounceMs", 2000) / 1000 + 1)
        starts = [e for e in self.events() if e["event"] == "started" and e["monotonicNs"] >= since]
        assert len(starts) == 1 and self.alive(child["pid"]), "automatic exports overlapped across workspaces"
        self.set_control("real")
        path = self.root / "src/lib.rs"
        await self.change(path, self.texts[path] + "\n// cancel first root for queued second root\n")
        await self.status("current", since, timeout=180, root=second)
        await self.status("current", since, timeout=180)
        assert not self.alive(child["pid"]) and not self.alive(child["childPid"])
        assert any(e["params"].get("state") == "stale" or e["params"].get("freshness") == "stale"
                   for e in self.status_events(since=since)), "obsolete generated API was never marked stale"
        second_columns = await self.complete(second / "src/lib.rs", "let automatic_column = post::Column::")
        assert "Score" not in second_columns and "Rank" not in second_columns, "compiler facts leaked between roots"
        return {"secondRoot": str(second), "heldFirstRoot": child, "serial": True}

    async def missing_producer(self):
        failed = await self.status("failed", timeout=15)
        assert "toolchain" in failed.get("message", "").lower() or "producer" in failed.get("message", "").lower(), failed
        assert not self.status_events("current")
        assert "u64" in await self.hover(self.root / "src/lib.rs", "automatic_source")
        return {"failure": failed, "sourceAvailable": True}

    async def devlist(self):
        current = await self.status("current", timeout=self.plan.get("workerWaitSeconds", 900))
        await lsp.wait_until_indexing_settled(self.client, 300000)
        self.indexing_finished.set()
        self.evidence["indexingMemory"] = await self.memory_task
        document = self.plan["documents"][0]
        path = self.root / document["file"]
        for marker in ["edt_query", "edt_without", "edt_filter"]:
            assert "Builder<User>" in await self.hover(path, marker), marker
        assert "Result<bool, FrameworkError>" in await self.hover(path, "edt_source")
        methods = await self.complete(path, "let edt_query = User::")
        assert all(methods.count(name) == 1 for name in ["query", "without_global_scopes", "filter"]), methods
        other = await self.complete(path, "let edt_other = User::")
        assert all(name not in other for name in ["query", "without_global_scopes", "filter"]), other
        source_methods = await self.complete(path, "self.verify_password", delta=5)
        assert source_methods.count("verify_password") == 1, source_methods
        hints = await self.client.request("textDocument/inlayHint", {"textDocument": {"uri": path.as_uri()},
            "range": {"start": {"line": 0, "character": 0},
                      "end": {"line": self.texts[path].count("\n"), "character": 0}}})
        assert "error" not in hints, hints
        for marker in ["edt_query", "edt_without", "edt_filter"]:
            line = self.texts[path][:self.texts[path].index("let " + marker)].count("\n")
            matching = [h for h in hints.get("result", []) if h["position"]["line"] == line]
            assert any("Builder<User>" in json.dumps(h["label"]) for h in matching), marker
        memory = await self.client.idle_memory()
        return {"status": current, "hover": True, "inlay": True, "completion": True,
                "source": True, "isolation": True, "idleMemory": memory, "events": self.events()}

    async def source_devlist(self):
        await lsp.wait_until_indexing_settled(self.client, 300000)
        self.indexing_finished.set()
        self.evidence["indexingMemory"] = await self.memory_task
        document = self.plan["documents"][0]
        path = self.root / document["file"]
        for marker in ["edt_query", "edt_without", "edt_filter"]:
            assert "Builder<User>" not in await self.hover(path, marker)
        methods = await self.complete(path, "let edt_query = User::")
        assert all(name not in methods for name in ["query", "without_global_scopes", "filter"])
        assert "Result<bool, FrameworkError>" in await self.hover(path, "edt_source")
        memory = await self.client.idle_memory()
        assert not self.events(), "source-only control started a producer"
        return {"sourceOnly": True, "source": True, "idleMemory": memory}

    async def prepared(self):
        await self.no_worker()
        assert "Builder<Post>" in await self.hover(self.root / "src/lib.rs", "automatic_post")
        return {"prepared": True, "automaticSuppressed": True}

    async def run(self):
        self.set_control(self.plan.get("controlMode", "real"))
        try:
            await self.start()
            if self.plan["scenario"] == "devlist":
                await self.case("genuine-user", self.devlist)
            elif self.plan["scenario"] == "devlist-source":
                await self.case("source-user", self.source_devlist)
            elif self.plan["scenario"] == "disabled":
                await self.case("no-worker", self.no_worker)
            elif self.plan["scenario"] == "prepared":
                await self.case("prepared", self.prepared)
            elif self.plan["scenario"] in {"timeout", "shutdown"}:
                await self.case(self.plan["scenario"] + "-cleanup", self.timeout_or_shutdown)
            elif self.plan["scenario"] == "missing-producer":
                await self.case("missing-producer", self.missing_producer)
            else:
                selected = self.plan.get("onlyCases")
                if selected:
                    await self.status("current", timeout=self.plan.get("workerWaitSeconds", 900))
                for name, operation in [
                    ("discovery", self.model_queries), ("producer", self.producer_identity),
                    ("debounce", self.debounce), ("unsaved-duplicate", self.unsaved_and_duplicate),
                    ("live-refresh", self.live_refresh), ("held-query-cleanup", self.held_query),
                    ("native-inputs", self.native_inputs), ("models", self.model_add_remove),
                    ("targets", self.target_add_remove), ("stale-disk", self.stale_disk_result),
                    ("obsolete-failure", self.obsolete_failure),
                    ("invalid-schema", lambda: self.failed_candidate("invalid-schema")),
                    ("invalid-reference", lambda: self.failed_candidate("invalid-reference")),
                    ("failed-recovery", lambda: self.failed_candidate("failure")),
                      ("bounded-output", lambda: self.failed_candidate("flood"))]:
                    if selected and name not in selected:
                        continue
                    await self.case(name, operation)
                if self.plan.get("extraRoots"):
                    await self.case("global-serial", self.global_serial)
        finally:
            if self.memory_task:
                self.indexing_finished.set()
                try:
                    self.evidence["indexingMemory"] = await self.memory_task
                except (lsp.LspQueryError, OSError) as error:
                    self.evidence["memoryError"] = str(error)
            if self.client:
                try:
                    await asyncio.wait_for(self.client.close(), 15)
                finally:
                    self.evidence["notifications"] = self.client.observed
                    self.evidence["events"] = self.events()
                    # Failed cleanup remains a failed observation. The bounded runner also
                    # cleans the known compiler groups so a deliberately broken worker test
                    # cannot leave its fake children running on the developer's machine.
                    live_groups = {e["pgid"] for e in self.events()
                                   if e["event"] == "started" and runner.group_exists(e["pgid"])}
                    for group in live_groups:
                        assert group != os.getpgrp(), "worker did not isolate its compiler group"
                        cleanup = await runner.clean_owned_process_group(group, self.control.parent)
                        self.evidence.setdefault("fallbackCleanup", []).append(cleanup)
                        assert cleanup["verifiedEmpty"], "controlled compiler cleanup incomplete"
            # Restore only explicitly edited owned fixture files, after the watcher stops.
            for path, data in self.originals.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
        return {"cases": self.results, "evidence": self.evidence}


async def main():
    plan = json.loads(Path(sys.argv[1]).read_text())
    probe = AutomaticProbe(plan)
    try:
        report = await probe.run()
    except Exception as error:
        report = {"cases": probe.results, "evidence": probe.evidence, "error": str(error)}
    Path(plan["report"]).write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report.get("cases") and all(c["passed"] for c in report["cases"].values()) and "error" not in report else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
