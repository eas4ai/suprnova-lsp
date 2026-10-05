#!/usr/bin/env python3
"""Guard acceptance accounting: absence, skipped work and mismatched evidence cannot pass."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("automatic_mechanism_test", ROOT / "tools/sudus-automatic-rustdoc.py")
mechanism = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mechanism
spec.loader.exec_module(mechanism)
probe = mechanism.editor.module("automatic_probe_test", ROOT / "tools/automatic-rustdoc-probe.py")


class Integrity(unittest.TestCase):
    def test_cleanup_controls_allow_preparation_before_the_held_export(self):
        async def check():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for scenario in ["timeout", "shutdown"]:
                    value = probe.AutomaticProbe({"root": str(root), "scenario": scenario,
                        "workerWaitSeconds": 20, "control": str(root / "control"),
                        "events": str(root / "events"), "artifactRoot": str(root / "outputs")})
                    elapsed = 0
                    def clock():
                        nonlocal elapsed
                        elapsed += 1
                        return elapsed
                    # Cargo metadata and producer identity can take longer than ten
                    # seconds before the held compiler command even starts.
                    value.events = lambda: ([{"event": "child", "monotonicNs": 1,
                        "pid": 1, "childPid": 2}] if elapsed >= 12 else [])
                    async def status(state, timeout):
                        self.assertEqual((state, timeout), ("failed", 15))
                        return {"message": "automatic_models Lib timed out after 5000 ms"}
                    async def close():
                        return None
                    value.status = status
                    value.client = SimpleNamespace(close=close)
                    value.alive = lambda _pid: False
                    with mock.patch.object(probe, "time", SimpleNamespace(monotonic=clock)):
                        self.assertTrue((await value.timeout_or_shutdown())["cleanup"])
                        elapsed = 0
                        value.events = lambda: []
                        with self.assertRaisesRegex(AssertionError, "no observed compiler child event"):
                            await value.timeout_or_shutdown()
        asyncio.run(check())

    def test_global_serial_waits_for_the_new_roots_source_readiness(self):
        async def check():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                second = root / "second"
                file = second / "src/lib.rs"
                file.parent.mkdir(parents=True)
                file.write_text("let automatic_source = 7u64;\n")
                value = probe.AutomaticProbe({"root": str(root), "extraRoots": [str(second)],
                    "control": str(root / "control"), "events": str(root / "events"),
                    "artifactRoot": str(root / "outputs"), "debounceMs": 1})
                ready, live, queries = False, True, 0
                async def notify(*_args):
                    pass
                async def wait(predicate, _description, timeout):
                    nonlocal ready
                    self.assertEqual(timeout, 300000)
                    message = {"method": probe.lsp.ACTIVE_WORKSPACE_CHANGED,
                               "params": {"root": str(second), "state": "ready"}}
                    self.assertTrue(predicate(message))
                    wrong = copy.deepcopy(message)
                    wrong["params"]["root"] = str(root)
                    self.assertFalse(predicate(wrong), "another root's readiness is not a barrier")
                    ready = True
                    return message
                async def noop(*_args, **_kwargs):
                    return None
                async def event(*_args):
                    return {"pid": 1, "childPid": 2}
                async def hover(*_args, **_kwargs):
                    nonlocal queries
                    self.assertTrue(ready, "source query ran before the new root was ready")
                    queries += 1
                    if queries == 1:
                        raise probe.lsp.LspQueryError('textDocument/hover failed: ' + json.dumps({
                            "code": -32801, "message": "the document analysis route is still being resolved"}))
                    return "u64"
                async def change(*_args):
                    nonlocal live
                    live = False
                async def complete(*_args):
                    return ["Id"]
                value.client = SimpleNamespace(notify=notify, wait_for_notification=wait)
                value.status, value.reindex, value.event = noop, noop, event
                value.hover, value.change, value.complete = hover, change, complete
                value.alive = lambda _pid: live
                value.events = lambda: [{"event": "started", "monotonicNs": time.monotonic_ns()}]
                value.status_events = lambda **_kwargs: [{"params": {"state": "stale"}}]
                value.texts[root / "src/lib.rs"] = "source"
                self.assertTrue((await value.global_serial())["serial"])
                self.assertEqual(queries, 2)
                live = True
                async def failed_hover(*_args, **_kwargs):
                    raise probe.lsp.LspQueryError('textDocument/hover failed: {"code": -32603, "message": "bug"}')
                value.hover = failed_hover
                with self.assertRaisesRegex(probe.lsp.LspQueryError, "bug"):
                    await value.global_serial()
        asyncio.run(check())

    def test_server_refresh_request_cannot_complete_a_client_query_with_the_same_id(self):
        async def check():
            client = probe.lsp.LspClient.__new__(probe.lsp.LspClient)
            future = asyncio.get_running_loop().create_future()
            client.pending = {7: future}
            sent = []
            async def send(message):
                sent.append(message)
            client.send = send
            await client._on_message({"jsonrpc": "2.0", "id": 7,
                                      "method": "workspace/inlayHint/refresh"})
            self.assertFalse(future.done(), "server request stole the pending hover response")
            self.assertIs(client.pending[7], future)
            self.assertEqual(sent, [{"jsonrpc": "2.0", "id": 7, "result": None}])
            response = {"jsonrpc": "2.0", "id": 7, "result": {"contents": "fresh column"}}
            await client._on_message(response)
            self.assertEqual(await future, response)
            self.assertEqual(client.pending, {})
        asyncio.run(check())

    def test_current_barrier_rejects_historical_success_and_obsolete_failures(self):
        async def check():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                value = probe.AutomaticProbe({"root": str(root), "control": str(root / "control"),
                    "events": str(root / "events"), "artifactRoot": str(root / "outputs")})
                def event(generation, state):
                    return {"monotonicNs": 1, "params": {"workspaceRoot": str(root),
                        "generation": generation, "state": state, "message": state}}
                value.client = SimpleNamespace(exited=False, observed=[event(1, "current"),
                    event(2, "pending"), event(1, "failed")])
                async def complete():
                    await asyncio.sleep(0.04)
                    value.client.observed.append(event(2, "current"))
                completion = asyncio.create_task(complete())
                try:
                    current = await value.status("current", timeout=1)
                    self.assertEqual(current["generation"], 2)
                finally:
                    await completion
                value.client.observed.extend([event(3, "failed"), event(2, "current")])
                with self.assertRaisesRegex(AssertionError, "worker failed"):
                    await value.status("current", timeout=1)
        asyncio.run(check())

    def test_observation_requires_exact_attempted_cases_and_matching_exit(self):
        report = {"cases": {"no-worker": {"passed": True, "attempted": True, "evidence": {"noCompiler": True}}}}
        self.assertEqual(mechanism.observation(report, "disabled", 0), report)
        for mutation in [{"cases": {}}, {"cases": {"wrong": report["cases"]["no-worker"]}},
                         dict(report, error="aborted")]:
            with self.assertRaises(ValueError):
                mechanism.observation(mutation, "disabled", 0)
        for key, value in [("passed", 1), ("attempted", False), ("evidence", None)]:
            broken = copy.deepcopy(report)
            broken["cases"]["no-worker"][key] = value
            with self.assertRaises(ValueError):
                mechanism.observation(broken, "disabled", 0)
        with self.assertRaises(ValueError):
            mechanism.observation(report, "disabled", 1)

    def test_discovery_and_terminal_events_reject_skips_and_duplicates(self):
        cases = {name: {"ignored": False, "filter-match": {"status": "matches"}} for name in mechanism.PROTO}
        listing = {"rust-suites": {"suite": {"package-name": "rg_lsp_proto", "kind": "lib",
                   "binary-id": "rg_lsp_proto", "testcases": cases}}}
        wanted = mechanism.discover(json.dumps(listing))
        events = []
        for name in wanted:
            events.extend([{"type": "test", "name": name, "event": state} for state in ["started", "ok"]])
        output = "\n".join(json.dumps(event) for event in events)
        self.assertTrue(all(mechanism.editor.outcomes(output, wanted, 0).values()))
        for invalid in [output + "\n" + json.dumps(events[-1]), "\n".join(output.splitlines()[:-1]),
                        output.replace('"ok"', '"ignored"', 1)]:
            with self.assertRaises(ValueError):
                mechanism.editor.outcomes(invalid, wanted, 0)
        for field, value in [("ignored", True), ("filter-match", {"status": "mismatch"})]:
            broken = copy.deepcopy(listing)
            broken["rust-suites"]["suite"]["testcases"][mechanism.PROTO[0]][field] = value
            with self.assertRaises(ValueError):
                mechanism.discover(json.dumps(broken))

    def test_trace_and_compiler_memory_need_actual_observations(self):
        for text in ["", 'execve("rust-glancer", ["rust-glancer"], 0) = -1 ENOENT']:
            with self.assertRaises(ValueError):
                mechanism.traced(text)
        trace = '123 execve("/tools/rust-analyzer", ["rust-analyzer"], 0) = 0'
        self.assertTrue(mechanism.traced(trace)["rustAnalyzer"])
        self.assertEqual(mechanism.peak("Maximum resident set size (kbytes): 123"), 123 * 1024)
        for text in ["", "Maximum resident set size (kbytes): 0"]:
            with self.assertRaises(ValueError):
                mechanism.peak(text)

    def test_idle_requires_five_stable_lsp_samples_and_separate_indexing_peak(self):
        idle = {"indexingComplete": True, "metric": "sum-of-process-RSS", "indexingPeakRssBytes": 500,
                "indexingSamples": 5, "samplingIntervalMs": 100,
                "samples": [{"processRssBytes": {"1": 100, "2": 200}, "aggregateRssBytes": 300} for _ in range(5)]}
        self.assertTrue(mechanism.editor.memory_ok({"idleMemory": idle}))
        for field, value in [("indexingComplete", False), ("samples", idle["samples"][:4]),
                             ("indexingPeakRssBytes", None)]:
            broken = dict(idle, **{field: value})
            self.assertFalse(mechanism.editor.memory_ok({"idleMemory": broken}))

    def test_requirement_mapping_cannot_pass_with_all_runtime_failures(self):
        reports = {mode: {"cases": {name: {"passed": False} for name in names}} for mode, names in mechanism.EXPECTED.items()}
        results = mechanism.assess(dict.fromkeys(mechanism.PROTO, False), False, reports, {}, False, False)
        self.assertEqual(set(results), {f"AUT-{number:03}" for number in range(1, 10)})
        self.assertFalse(any(results.values()))

    def test_absent_held_children_and_devlist_writes_fail(self):
        async def check():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                file = root / "src/lib.rs"
                file.parent.mkdir()
                file.write_text("let automatic_source = 7u64;\n")
                plan = {"root": str(root), "scenario": "devlist-source", "control": str(root / "control"),
                        "events": str(root / "events"), "artifactRoot": str(root / "outputs")}
                value = probe.AutomaticProbe(plan)
                with self.assertRaises(AssertionError):
                    await value.fs_change(file, "changed")
                with self.assertRaises(AssertionError):
                    await value.change(file, "changed")
                self.assertEqual(file.read_text(), "let automatic_source = 7u64;\n")
                plan["scenario"] = "lifecycle"
                async def status(*_args, **_kwargs):
                    return {}
                async def reindex():
                    return None
                async def event(*_args):
                    return {"pid": 999999999, "childPid": 999999998}
                async def hover(*_args, **_kwargs):
                    return "u64"
                value.status, value.reindex, value.event, value.hover = status, reindex, event, hover
                await value.case("held-query-cleanup", value.held_query)
                self.assertFalse(value.results["held-query-cleanup"]["passed"])
                with self.assertRaises(ValueError):
                    await value.case("held-query-cleanup", value.held_query)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
