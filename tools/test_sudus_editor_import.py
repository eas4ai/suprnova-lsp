#!/usr/bin/env python3
"""Reject absent, partial and contradictory acceptance observations."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import importlib.util
import sys


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("editor_import", ROOT / "tools/sudus-editor-import.py")
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)
query = editor.module("lsp_query", ROOT / "tools/lsp-query.py")


class ObservationIntegrity(unittest.TestCase):
    def setUp(self):
        self.wanted = {"pkg::binary$case": "case"}
        self.events = [{"type": "test", "name": "pkg::binary$case", "event": state} for state in ["started", "ok"]]

    def test_tests_require_unique_start_and_completion_and_matching_exit(self):
        text = "\n".join(json.dumps(event) for event in self.events)
        self.assertEqual(editor.outcomes(text, self.wanted, 0), {"case": True})
        for events, code in [(self.events[:1], 0), (self.events[1:], 0), (self.events * 2, 0), (self.events, 100),
                             ([dict(self.events[0], event="ignored")], 0)]:
            with self.assertRaises(ValueError):
                editor.outcomes("\n".join(json.dumps(event) for event in events), self.wanted, code)

    def test_editor_zero_tests_or_pending_is_not_a_pass(self):
        case = {"fullTitle": editor.EDITOR_CASE}
        report = {"stats": {"pending": 0}, "tests": [case], "passes": [case], "failures": []}
        self.assertTrue(editor.editor_outcome(json.dumps(report), 0))
        for key, value in [("tests", []), ("passes", []), ("stats", {"pending": 1})]:
            invalid = dict(report, **{key: value})
            with self.assertRaises(ValueError):
                editor.editor_outcome(json.dumps(invalid), 0)
        with self.assertRaises(ValueError):
            editor.editor_outcome(json.dumps(report), 1)

    def test_discovery_rejects_missing_ignored_or_filtered_tests(self):
        suites = {}
        for packages in editor.CASES.values():
            for package, cases in packages.items():
                suite = suites.setdefault(package, {"package-name": package, "kind": "lib", "binary-id": package, "testcases": {}})
                suite["testcases"].update({case: {"ignored": False, "filter-match": {"status": "matches"}} for case in cases})
        value = {"rust-suites": suites}
        self.assertEqual(len(editor.discover(json.dumps(value), editor.CASES)), 8)
        for modification in [None, {"ignored": True, "filter-match": {"status": "matches"}},
                             {"ignored": False, "filter-match": {"status": "mismatch"}}]:
            invalid = copy.deepcopy(value)
            entries = invalid["rust-suites"]["rg_lsp_proto"]["testcases"]
            case = next(iter(entries))
            if modification is None:
                del entries[case]
            else:
                entries[case] = modification
            with self.assertRaises(ValueError):
                editor.discover(json.dumps(invalid), editor.CASES)

    def test_idle_samples_are_separate_positive_consistent_process_sums(self):
        memory = {"indexingComplete": True, "metric": "sum-of-process-RSS", "indexingPeakRssBytes": 100000, "indexingSamples": 10, "samplingIntervalMs": 100, "samples": [
            {"processRssBytes": {"1": 4096, "2": 8192}, "aggregateRssBytes": 12288} for _ in range(5)]}
        self.assertTrue(editor.memory_ok({"idleMemory": memory}))
        for key, value in [("indexingComplete", False), ("metric", "peakRSS"), ("samples", memory["samples"][:4]), ("indexingSamples", 0), ("indexingPeakRssBytes", 0)]:
            self.assertFalse(editor.memory_ok({"idleMemory": dict(memory, **{key: value})}))
        for invalid in [{"processRssBytes": {"1": 4096}, "aggregateRssBytes": 4096},
                        {"processRssBytes": {"1": 4096, "2": 8192}, "aggregateRssBytes": 1},
                        {"processRssBytes": {"1": 4096, "3": 8192}, "aggregateRssBytes": 12288}]:
            altered = copy.deepcopy(memory)
            altered["samples"][-1] = invalid
            self.assertFalse(editor.memory_ok({"idleMemory": altered}))

    def test_lsp_requires_all_queries_and_completed_indexing(self):
        report = {"results": [{"label": label, "kind": "hover"} for label in editor.LABELS],
                  "barriers": {"readiness": "ready", "deferred": "before-queries"}}
        self.assertEqual(editor.lsp_observation(json.dumps(report), 0), report)
        invalid = copy.deepcopy(report)
        invalid["results"].pop()
        with self.assertRaises(ValueError):
            editor.lsp_observation(json.dumps(invalid), 0)
        invalid = dict(report, barriers={"readiness": "ready", "deferred": "none"})
        with self.assertRaises(ValueError):
            editor.lsp_observation(json.dumps(invalid), 0)
        with self.assertRaises(ValueError):
            editor.lsp_observation(json.dumps(report), 1)

    def test_each_requirement_rejects_its_observed_violation(self):
        tests = {case: True for packages in editor.CASES.values() for cases in packages.values() for case in cases}
        memory = {"indexingComplete": True, "metric": "sum-of-process-RSS", "indexingPeakRssBytes": 100000, "indexingSamples": 10, "samplingIntervalMs": 100, "samples": [
            {"processRssBytes": {"1": 4096, "2": 4096}, "aggregateRssBytes": 8192} for _ in range(5)]}
        reports = {mode: {"idleMemory": copy.deepcopy(memory), "indexingPeakRssBytes": 100000,
                         "comparison": {"preference": preference},
                         "facts": {key: True for key in ["hover", "inlay", "completion", "source", "isolation", "sourceOnly"]}}
                   for mode, preference in editor.MODES.items()}
        traces = dict.fromkeys(editor.MODES, [])
        self.assertTrue(all(editor.assess(tests, True, reports, traces, True, True).values()))
        for req, field in [("EDT-002", "inlay"), ("EDT-003", "isolation"), ("EDT-006", "sourceOnly")]:
            invalid = copy.deepcopy(reports)
            invalid["source-initial" if req == "EDT-006" else "initial"]["facts"][field] = False
            self.assertFalse(editor.assess(tests, True, invalid, traces, True, True)[req])
        self.assertFalse(editor.assess(tests, False, reports, traces, True, True)["EDT-001"])
        invalid_tests = dict(tests, **{editor.CASES["EDT-004"]["rg_lsp_engine"][0]: False})
        self.assertFalse(editor.assess(invalid_tests, True, reports, traces, True, True)["EDT-004"])
        for invalid_traces in [{}, dict(traces, initial=["rustdoc launch"])]:
            self.assertFalse(editor.assess(tests, True, reports, invalid_traces, True, True)["EDT-005"])
        invalid = copy.deepcopy(reports)
        invalid["source-initial"]["comparison"] = {"preference": "changed"}
        self.assertFalse(editor.assess(tests, True, invalid, traces, True, True)["EDT-005"])

    def test_idle_plan_requires_boolean_and_explicit_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lib.rs").write_text("fn test() {}")
            plan = {"file": "lib.rs", "queries": [{"kind": "hover", "marker": "test"}], "idleMemory": True}
            with self.assertRaises(query.LspQueryError):
                query.normalize_plan(plan, root, query.Options())
            plan["deferredBarrier"] = "before-queries"
            with patch.object(query.sys, "platform", "linux"):
                self.assertTrue(query.normalize_plan(plan, root, query.Options())["idleMemory"])
                plan["idleMemory"] = 1
                with self.assertRaises(query.LspQueryError):
                    query.normalize_plan(plan, root, query.Options())


class OwnedMemoryObservation(unittest.IsolatedAsyncioTestCase):
    async def test_samples_owned_server_and_engine_and_rejects_unrelated_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid, rss, children in [(1, 2, "2"), (2, 3, "")]:
                process = root / str(pid)
                task = process / "task" / str(pid)
                task.mkdir(parents=True)
                (process / "cmdline").write_bytes(b"/repo/suprnova-lsp\0lsp\0")
                (process / "statm").write_text(f"10 {rss} 0")
                (task / "children").write_text(children)
            client = query.LspClient.__new__(query.LspClient)
            client.process = SimpleNamespace(pid=1)
            original_path = Path
            def substitute(value):
                return root if value == "/proc" else original_path(value)
            with patch.object(query, "Path", side_effect=substitute), patch.object(query.asyncio, "sleep", new=AsyncMock()):
                report = await client.idle_memory()
                self.assertEqual(len(report["samples"]), 5)
                self.assertEqual(report["samples"][0]["aggregateRssBytes"], 5 * query.os.sysconf("SC_PAGE_SIZE"))
                finished = query.asyncio.Event()
                count = 0
                async def finish_after_five_samples(_delay):
                    nonlocal count
                    count += 1
                    if count == 5:
                        finished.set()
                with patch.object(query.asyncio, "sleep", side_effect=finish_after_five_samples):
                    peak = await client.indexing_memory(finished)
                self.assertEqual(peak["indexingSamples"], 5)
                self.assertEqual(peak["indexingPeakRssBytes"], report["samples"][0]["aggregateRssBytes"])
                (root / "2/cmdline").write_bytes(b"/bin/sleep\0")
                with self.assertRaises(query.LspQueryError):
                    await client.idle_memory()
                self.assertEqual(client.owned_rss(settled=False), {"1": 2 * query.os.sysconf("SC_PAGE_SIZE")})
                (root / "1/task/1/children").write_text("")
                with self.assertRaises(query.LspQueryError):
                    await client.idle_memory()


if __name__ == "__main__":
    unittest.main()
