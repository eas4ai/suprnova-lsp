#!/usr/bin/env python3
"""Guard acceptance accounting: absence, skipped work and mismatched evidence cannot pass."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("automatic_mechanism_test", ROOT / "tools/sudus-automatic-rustdoc.py")
mechanism = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mechanism
spec.loader.exec_module(mechanism)
probe = mechanism.editor.module("automatic_probe_test", ROOT / "tools/automatic-rustdoc-probe.py")


class Integrity(unittest.TestCase):
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
