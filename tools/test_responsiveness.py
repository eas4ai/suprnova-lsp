#!/usr/bin/env python3
"""Check transport timing at pipe boundaries and preserve failed observations."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import sys


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("responsiveness_lsp", ROOT / "tools/lsp-query.py")
lsp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lsp
spec.loader.exec_module(lsp)
spec = importlib.util.spec_from_file_location("responsiveness_observation", ROOT / "tools/sudus-responsiveness.py")
observation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observation)


class LedgerIntegrity(unittest.TestCase):
    def setUp(self):
        sent = [{"id": n + 1, "method": "textDocument/hover", "status": "success",
                 "writtenNs": 100 + 20 * n, "receivedNs": 110 + 20 * n, "durationNs": 10} for n in range(3)]
        self.report = {"transport": sent,
            "results": [{"kind": "hover", "label": f"source-{n}", "transport": row,
                         "text": "fn verify_password(&self, password: &str) -> Result<bool, FrameworkError>"}
                        for n, row in enumerate(sent)],
            "lifecycle": [{"method": lsp.ACTIVE_WORKSPACE_CHANGED, "receivedNs": 90,
                           "params": {"root": str(observation.APP), "state": "ready"}}],
            "stages": [{"message": "editor document analysis route published", "observedNs": 95,
                        "fields": {"path": str(observation.APP / "src/models/user.rs"), "ready": True}}]}
        self.report["barriers"] = {"hoverCleanup": True}
        for _ in range(3):
            self.report["stages"].extend([
                {"message": "memory report", "observedNs": 170, "fields": {"label": "hover"}},
                {"message": "analysis query completed", "observedNs": 160, "fields": {"query": "hover", "status": "ok", "queued_ms": "0", "elapsed_ms": "0"}},
                {"message": "document analysis prepared", "fields": {"query": "hover", "source": "saved_exact", "elapsed_us": "0"}},
            ])

    def test_small_diagnostic_never_claims_acceptance(self):
        result = observation.Diagnostic.source_observation(self.report)
        self.assertTrue(result["acceptance"].startswith("unverified"))
        self.assertEqual(result["firstNs"], 10)
        self.assertEqual(result["repeatedNs"], [10, 10])

    def test_nearest_rank_preserves_integer_threshold(self):
        values = [1] * 18 + [200_000_000, 300_000_000]
        self.assertEqual(observation.Diagnostic.percentile(values, 95), 200_000_000)
        for invalid in [[], [1.0], [True], [-1]]:
            with self.assertRaises(ValueError):
                observation.Diagnostic.percentile(invalid, 95)

    def test_preparation_phases_keep_request_order_and_reject_unfinished_work(self):
        report = copy.deepcopy(self.report)
        index = next(n for n, event in enumerate(report["stages"]) if event["message"] == "analysis query completed")
        phase = {"message": "document analysis phase", "fields": {"query": "hover", "phase": "saved file materialization", "elapsed_us": "7"}}
        report["stages"].insert(index, phase)
        result = observation.Diagnostic.source_observation(report)
        self.assertEqual(result["stages"][0]["preparationPhases"], [{"phase": "saved file materialization", "durationNs": 7000}])
        self.assertEqual(result["stages"][1]["preparationPhases"], [])
        report["stages"].append(phase)
        with self.assertRaisesRegex(ValueError, "lack an analysis completion"):
            observation.Diagnostic.source_observation(report)

    def test_rejects_dropped_failed_duplicated_or_changed_samples(self):
        for alteration in ["drop", "error", "duplicate", "wrong-signature", "different-ledger", "timestamp", "duration"]:
            report = copy.deepcopy(self.report)
            if alteration == "drop":
                report["results"].pop()
            elif alteration == "error":
                report["transport"][0]["status"] = "rpc-error"
            elif alteration == "duplicate":
                report["transport"][1]["id"] = 1
            elif alteration == "wrong-signature":
                report["results"][0]["text"] = "Builder<Wrong>"
            elif alteration == "different-ledger":
                report["results"][0]["transport"] = dict(report["transport"][0], id=200)
            elif alteration == "timestamp":
                report["transport"][0]["receivedNs"] = 99
            elif alteration == "duration":
                report["transport"][0]["durationNs"] = 0
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                observation.Diagnostic.source_observation(report)

    def test_success_cannot_establish_readiness_retroactively(self):
        for events in [[], [dict(self.report["lifecycle"][0], receivedNs=1000)],
                       [dict(self.report["lifecycle"][0], params={"root": "/other", "state": "ready"})]]:
            with self.assertRaisesRegex(ValueError, "readiness"):
                observation.Diagnostic.source_observation(dict(self.report, lifecycle=events))
        stages = [event for event in self.report["stages"] if event["message"] != "editor document analysis route published"]
        with self.assertRaisesRegex(ValueError, "route"):
            observation.Diagnostic.source_observation(dict(self.report, stages=stages))

    def test_missing_queue_evidence_cannot_pass_diagnostic(self):
        del self.report["stages"][2]["fields"]["queued_ms"]
        with self.assertRaisesRegex(ValueError, "stage durations"):
            observation.Diagnostic.source_observation(self.report)

    def test_generated_readiness_is_fenced_before_each_request(self):
        report = copy.deepcopy(self.report)
        for result, label in zip(report["results"], ("rsp_query", "rsp_without", "rsp_filter")):
            result.update(label=label, text="let value: Builder<User>")
        for event in report["stages"]:
            if event["message"] == "document analysis prepared":
                event["fields"]["source"] = "current"
        current = {"method": "suprnova-lsp/rustdocStatus", "receivedNs": 99,
                   "params": {"workspaceRoot": str(observation.APP), "generation": 1, "state": "current"}}
        report["lifecycle"].append(current)
        report["idleMemory"] = {"indexingComplete": True, "metric": "sum-of-process-RSS",
            "indexingPeakRssBytes": 1000, "indexingSamples": 10, "samplingIntervalMs": 100,
            "samples": [{"observedNs": 180, "processRssBytes": {"1": 40, "2": 60}, "aggregateRssBytes": 100}] * 5}
        self.assertEqual(observation.Diagnostic.generated_observation(report)["idleRssBytes"], [100] * 5)
        for violation in ("late", "pending", "missing-memory", "wrong-type", "premature-idle", "missing-purge"):
            broken = copy.deepcopy(report)
            if violation == "late":
                broken["lifecycle"][-1]["receivedNs"] = 1000
            elif violation == "pending":
                broken["lifecycle"].append({"method": current["method"], "receivedNs": 99,
                    "params": dict(current["params"], generation=2, state="pending")})
                broken["lifecycle"].append(current)
            elif violation == "missing-memory":
                broken["idleMemory"]["samples"].pop()
            elif violation == "premature-idle":
                broken["idleMemory"]["samples"][0]["observedNs"] = 169
            elif violation == "missing-purge":
                broken["stages"] = [event for event in broken["stages"] if event["message"] != "memory report"]
            else:
                broken["results"][0]["text"] = "Builder<Unrelated>"
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                observation.Diagnostic.generated_observation(broken)

    def test_disabled_control_rejects_worker_activity_and_missing_idle_samples(self):
        report = copy.deepcopy(self.report)
        report["idleMemory"] = {"indexingComplete": True, "metric": "sum-of-process-RSS",
            "indexingPeakRssBytes": 1000, "indexingSamples": 10, "samplingIntervalMs": 100,
            "samples": [{"observedNs": 180, "processRssBytes": {"1": 40, "2": 60}, "aggregateRssBytes": 100}] * 5}
        self.assertEqual(observation.Diagnostic.source_only_observation(report)["idleRssBytes"], [100] * 5)
        report["lifecycle"].append({"method": "suprnova-lsp/rustdocStatus"})
        with self.assertRaisesRegex(ValueError, "worker activity"):
            observation.Diagnostic.source_only_observation(report)
        report["lifecycle"].pop()
        report["idleMemory"]["samples"].pop()
        with self.assertRaisesRegex(ValueError, "memory"):
            observation.Diagnostic.source_only_observation(report)


class SourceSeriesIntegrity(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        for session in range(20):
            start = 1_000_000_000 * session
            sent = [{"id": n + 2, "method": "textDocument/hover", "status": "success",
                     "writtenNs": start + 100 + 20 * n, "receivedNs": start + 110 + 20 * n,
                     "durationNs": 10} for n in range(6)]
            raw = {"session": {"serverPid": session + 1000},
                   "transport": [{"id": 1, "method": "initialize", "status": "success",
                                  "writtenNs": start, "receivedNs": start + 10}, *sent],
                   "results": [{"kind": "hover", "label": f"source-{n}", "transport": row,
                                "text": "fn verify_password(&self, password: &str) -> Result<bool, FrameworkError>"}
                               for n, row in enumerate(sent)],
                   "lifecycle": [
                       {"method": lsp.ACTIVE_WORKSPACE_CHANGED, "receivedNs": start + 90,
                        "params": {"root": str(observation.APP), "state": "ready"}},
                       {"method": "suprnova-lsp/rustdocStatus", "receivedNs": start + 99,
                        "params": {"workspaceRoot": str(observation.APP), "generation": 1, "state": "running"}}],
                   "stages": [{"message": "editor document analysis route published", "observedNs": start + 95,
                               "fields": {"path": str(observation.APP / "src/models/user.rs"), "ready": True}}]}
            for _ in sent:
                raw["stages"].extend([
                    {"message": "analysis query completed", "fields": {
                        "query": "hover", "status": "ok", "queued_ms": "0", "elapsed_ms": "0"}},
                    {"message": "document analysis prepared", "fields": {
                        "query": "hover", "source": "saved_exact", "elapsed_us": "0"}},
                ])
            plan = observation.Diagnostic.workload_plan("faster-builds", Path("/tmp/owned"), "source")
            plan["queries"] = [{"kind": "hover", "label": f"source-{n}", "marker": "pub fn verify_password", "delta": 7}
                               for n in range(6)]
            plan["workerRunningBarrier"] = True
            self.sessions.append({"raw": raw, "plan": plan})

    def test_first_and_repeated_cohorts_are_separate_and_preserve_raw_counts(self):
        for session in self.sessions:
            row = session["raw"]["results"][0]["transport"]
            row.update(durationNs=200_000_000, receivedNs=row["writtenNs"] + 200_000_000)
            # Keep subsequent requests sequential after that slow first result.
            for result in session["raw"]["results"][1:]:
                later = result["transport"]
                later["writtenNs"] += 200_000_000
                later["receivedNs"] += 200_000_000
        summary = observation.SourceSeries.assess(self.sessions, "faster-builds")
        self.assertEqual(summary["first"]["count"], 20)
        self.assertEqual(summary["repeated"]["count"], 100)
        self.assertEqual(summary["first"]["p95Ns"], 200_000_000)
        self.assertFalse(summary["first"]["belowTarget"])
        self.assertTrue(summary["repeated"]["belowTarget"])
        self.assertEqual(summary["first"]["rawNs"], [200_000_000] * 20)
        self.assertIn("unverified", summary["acceptance"])

    def test_rejects_missing_duplicate_or_reused_sessions_and_dropped_replies(self):
        for violation in ("missing-session", "duplicate-session", "missing-initialize", "dropped-reply"):
            sessions = copy.deepcopy(self.sessions)
            if violation == "missing-session":
                sessions.pop()
            elif violation == "duplicate-session":
                sessions[-1] = copy.deepcopy(sessions[0])
            elif violation == "missing-initialize":
                sessions[0]["raw"]["transport"].pop(0)
            else:
                sessions[0]["raw"]["results"].pop(0)
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                observation.SourceSeries.assess(sessions, "faster-builds")

    def test_requires_running_worker_for_each_entire_response(self):
        for violation in ("other-root", "late-start", "settled", "newer-pending", "finished-during-response"):
            sessions = copy.deepcopy(self.sessions)
            events = sessions[0]["raw"]["lifecycle"]
            worker = events[-1]
            if violation == "other-root":
                worker["params"]["workspaceRoot"] = "/other"
            elif violation == "late-start":
                worker["receivedNs"] = 101
            elif violation == "settled":
                worker["params"]["state"] = "current"
            else:
                newer = copy.deepcopy(worker)
                newer["receivedNs"] = 99 if violation == "newer-pending" else 105
                newer["params"].update(generation=2, state="pending")
                events.append(newer)
            with self.subTest(violation=violation), self.assertRaisesRegex(ValueError, "worker"):
                observation.SourceSeries.assess(sessions, "faster-builds")

    def test_rejects_mixed_mode_and_configuration(self):
        for violation in ("mode", "automatic-disabled", "body-overlay", "warm-up", "changed-symbol"):
            sessions = copy.deepcopy(self.sessions)
            plan = sessions[0]["plan"]
            if violation == "mode":
                plan["initializationOptions"]["indexing"]["performancePreference"] = "lower-peak-memory"
            elif violation == "automatic-disabled":
                plan["initializationOptions"]["rustdoc"]["automatic"]["enabled"] = False
            elif violation == "body-overlay":
                plan["text"] = "changed body"
            elif violation == "warm-up":
                sessions[0]["raw"]["transport"].insert(1, {
                    "id": 50, "method": "textDocument/hover", "status": "success"})
            else:
                plan["queries"][0]["marker"] = "other_symbol"
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                observation.SourceSeries.assess(sessions, "faster-builds")


class PipeTiming(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = 100
        self.patch = patch.object(lsp.time, "monotonic_ns", side_effect=lambda: self.clock)
        self.patch.start()
        self.written = asyncio.Event()
        self.drained = asyncio.Event()
        self.exit = asyncio.Event()
        self.messages = []

        def write(data):
            self.messages.append(json.loads(data.split(b"\r\n\r\n", 1)[1]))
            self.written.set()

        async def drain():
            await self.drained.wait()

        async def wait():
            await self.exit.wait()
            return 0

        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.process = SimpleNamespace(
            stdin=SimpleNamespace(is_closing=lambda: False, write=write, drain=drain),
            stdout=self.stdout, stderr=self.stderr, wait=wait,
        )
        self.client = lsp.LspClient(self.process, 1000, False)
        self.client.enable_observations()
        self.requests = []

    async def asyncTearDown(self):
        for task in [*self.requests, self.client.stdout_task, self.client.stderr_task, self.client.exit_task]:
            task.cancel()
        await asyncio.gather(*self.requests, self.client.stdout_task, self.client.stderr_task,
                             self.client.exit_task, return_exceptions=True)
        self.patch.stop()

    def frame(self, message):
        body = json.dumps(message).encode()
        return f"Content-Length: {len(body)}\r\n\r\n".encode() + body

    async def begin(self, **options):
        task = asyncio.create_task(self.client.request("textDocument/hover", {}, **options))
        self.requests.append(task)
        await asyncio.wait_for(self.written.wait(), 1)
        return task

    async def test_times_write_and_complete_read_before_drain_and_waiter_resume(self):
        task = await self.begin()
        frame = self.frame({"jsonrpc": "2.0", "id": 1, "result": {"contents": "User"}})
        self.clock = 150
        self.stdout.feed_data(frame[:-2])
        await asyncio.sleep(0)
        self.assertNotIn("receivedNs", self.client.request_observations[1])
        self.clock = 200
        self.stdout.feed_data(frame[-2:])
        await asyncio.sleep(0)
        self.assertFalse(task.done(), "drain is intentionally still blocked")
        self.clock = 300
        self.drained.set()
        await asyncio.wait_for(task, 1)
        row = self.client.request_observations[1]
        self.assertEqual((row["writtenNs"], row["receivedNs"], row["durationNs"]), (100, 200, 100))
        self.assertEqual(row["status"], "success")
        self.assertNotIn("result", row, "the ledger must not retain semantic payloads")

    async def test_server_request_with_same_id_cannot_finish_hover(self):
        task = await self.begin()
        self.drained.set()
        self.stdout.feed_data(self.frame({"jsonrpc": "2.0", "id": 1, "method": "workspace/inlayHint/refresh"}))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertNotIn("receivedNs", self.client.request_observations[1])
        self.stdout.feed_data(self.frame({"jsonrpc": "2.0", "id": 1, "result": None}))
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.client.request_observations[1]["status"], "success")

    async def test_rpc_error_is_retained_and_still_raises(self):
        task = await self.begin()
        self.drained.set()
        self.stdout.feed_data(self.frame({"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "broken"}}))
        with self.assertRaises(lsp.LspQueryError):
            await task
        self.assertEqual(self.client.request_observations[1]["status"], "rpc-error")
        self.assertEqual(self.client.request_observations[1]["errorCode"], -32603)

    async def test_timeout_is_retained_and_late_reply_cannot_overwrite_it(self):
        self.drained.set()
        task = await self.begin(timeout_ms=1)
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            await task
        self.stdout.feed_data(self.frame({"jsonrpc": "2.0", "id": 1, "result": None}))
        await asyncio.sleep(0)
        self.assertEqual(self.client.request_observations[1]["status"], "timeout")
        self.assertNotIn("receivedNs", self.client.request_observations[1])
        self.assertFalse(self.client.pending)

    async def test_caller_cancellation_releases_pending_request(self):
        task = await self.begin()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.client.pending)
        self.assertEqual(self.client.request_observations[1]["status"], "caller-cancelled")

    async def test_lifecycle_is_retained_before_waiter_consumes_it(self):
        self.drained.set()
        waiter = asyncio.create_task(self.client.wait_for_notification(
            lambda message: message.get("method") == lsp.ACTIVE_WORKSPACE_CHANGED, "ready"))
        self.requests.append(waiter)
        await asyncio.sleep(0)
        self.stdout.feed_data(self.frame({"jsonrpc": "2.0", "method": lsp.ACTIVE_WORKSPACE_CHANGED,
            "params": {"state": "ready", "workspaceRoot": "/app", "generation": 7}}))
        await asyncio.wait_for(waiter, 1)
        self.assertFalse(self.client.notifications)
        self.assertEqual(self.client.lifecycle_observations[0]["params"]["generation"], 7)
        self.assertEqual(self.client.lifecycle_observations[0]["receivedNs"], 100)

    async def test_observation_limit_fails_instead_of_dropping_requests(self):
        self.drained.set()
        with patch.object(lsp, "MAX_OBSERVED_REQUESTS", 0):
            with self.assertRaisesRegex(lsp.LspQueryError, "observation"):
                await self.client.request("textDocument/hover", {})
        self.assertFalse(self.messages)
        self.assertFalse(self.client.pending)

    async def test_deliberate_cancellation_records_its_actual_wire_send(self):
        self.drained.set()
        task = await self.begin(cancel_after_ms=0)

        async def wait_for_cancel():
            while len(self.messages) < 2:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_cancel(), 1)
        self.assertEqual(self.messages[1]["method"], "$/cancelRequest")
        self.assertEqual(self.messages[1]["params"], {"id": 1})
        self.stdout.feed_data(self.frame({"jsonrpc": "2.0", "id": 1, "error": {"code": -32800, "message": "cancelled"}}))
        reply = await asyncio.wait_for(task, 1)
        self.assertEqual(reply["error"]["code"], -32800)
        self.assertEqual(self.client.request_observations[1]["cancelWrittenNs"], 100)

    async def test_truncated_response_remains_transport_failure(self):
        self.drained.set()
        task = await self.begin()
        self.stdout.feed_data(self.frame({"id": 1, "result": None})[:-2])
        self.stdout.feed_eof()
        with self.assertRaisesRegex(RuntimeError, "middle of a message"):
            await task
        self.assertEqual(self.client.request_observations[1]["status"], "transport-error")

    async def test_document_barrier_uses_complete_log_event_without_warmup_query(self):
        path = observation.APP / "src/models/user.rs"
        waiter = asyncio.create_task(self.client.wait_for_document_ready(path))
        self.requests.append(waiter)
        await asyncio.sleep(0)
        event = {"schema": "suprnova-lsp-log/v1", "message": "editor document analysis route published",
                 "fields": {"path": str(path), "ready": True, "session": "1"}}
        frame = json.dumps(event).encode() + b"\n"
        self.stderr.feed_data(frame[:-2])
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        self.stderr.feed_data(frame[-2:])
        await asyncio.wait_for(waiter, 1)
        self.assertFalse(self.messages, "the barrier must not prepare analysis with a query")
        self.assertEqual(self.client.observation.stages[0]["observedNs"], 100)

    async def test_missing_route_event_cannot_pass_barrier(self):
        self.client.timeout_ms = 1
        with self.assertRaisesRegex(lsp.LspQueryError, "route publication"):
            await self.client.wait_for_document_ready(observation.APP / "src/models/user.rs")

    async def test_rustdoc_barrier_rejects_old_success_and_other_workspace(self):
        def status(generation, state, root=observation.APP):
            return {"method": "suprnova-lsp/rustdocStatus", "params": {
                "workspaceRoot": str(root), "generation": generation, "state": state}}

        self.client.observation.notification(status(1, "current"), 100)
        self.client.observation.notification(status(2, "pending"), 100)
        waiter = asyncio.create_task(self.client.wait_for_rustdoc_state(observation.APP, "current"))
        self.requests.append(waiter)
        await asyncio.sleep(0)
        self.client.observation.notification(status(1, "current"), 110)
        self.client.observation.notification(status(2, "current", Path('/tmp/unrelated')), 110)
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        self.client.observation.notification(status(2, "current"), 120)
        await asyncio.wait_for(waiter, 1)
        self.assertFalse(self.messages, "worker readiness must not prepare bodies with a hover")

    async def test_rustdoc_failure_is_not_readiness(self):
        self.client.observation.notification({"method": "suprnova-lsp/rustdocStatus", "params": {
            "workspaceRoot": str(observation.APP), "generation": 3, "state": "failed",
            "message": "export rejected"}}, 100)
        with self.assertRaisesRegex(lsp.LspQueryError, "export rejected"):
            await self.client.wait_for_rustdoc_state(observation.APP, "current")

    async def test_running_worker_barrier_waits_for_latest_generation_without_hover(self):
        self.client.observation.notification({"method": "suprnova-lsp/rustdocStatus", "params": {
            "workspaceRoot": str(observation.APP), "generation": 1, "state": "running"}}, 100)
        self.client.observation.notification({"method": "suprnova-lsp/rustdocStatus", "params": {
            "workspaceRoot": str(observation.APP), "generation": 2, "state": "pending"}}, 100)
        waiter = asyncio.create_task(self.client.wait_for_rustdoc_state(observation.APP, "running"))
        self.requests.append(waiter)
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        self.client.observation.notification({"method": "suprnova-lsp/rustdocStatus", "params": {
            "workspaceRoot": str(observation.APP), "generation": 2, "state": "running"}}, 120)
        await asyncio.wait_for(waiter, 1)
        self.assertFalse(self.messages, "worker overlap cannot use a hover warm-up")

    async def test_idle_barrier_waits_for_purge_after_successful_reply(self):
        self.drained.set()
        task = await self.begin()
        self.stdout.feed_data(self.frame({"jsonrpc": "2.0", "id": 1, "result": {"contents": "User"}}))
        await asyncio.wait_for(task, 1)
        waiter = asyncio.create_task(self.client.wait_for_observation(
            self.client.observation.hover_cleanup_complete, "hover cleanup"))
        self.requests.append(waiter)

        def log(message, **fields):
            self.stderr.feed_data(json.dumps({"schema": "suprnova-lsp-log/v1", "message": message,
                                             "fields": fields}).encode() + b"\n")

        log("analysis query completed", query="hover", status="ok")
        await asyncio.sleep(0)
        self.assertFalse(waiter.done(), "response and analysis completion do not prove released loads")
        log("memory report", label="after deferred indexing finish")
        await asyncio.sleep(0)
        self.assertFalse(waiter.done(), "an unrelated purge cannot settle this query")
        self.clock = 120
        log("memory report", label="hover")
        await asyncio.wait_for(waiter, 1)
        self.assertEqual(self.client.observation.stages[-1]["observedNs"], 120)


class FailedCliEvidence(unittest.IsolatedAsyncioTestCase):
    async def test_disappearing_proc_task_is_tolerated_only_for_peak_sampling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / "1"
            process.mkdir()
            (process / "cmdline").write_bytes(b"/repo/suprnova-lsp\0lsp\0")
            (process / "statm").write_text("10 2 0")
            client = lsp.LspClient.__new__(lsp.LspClient)
            client.process = SimpleNamespace(pid=1)
            original = Path.iterdir

            def disappeared(path):
                if path == process / "task":
                    raise ProcessLookupError("process exited during task enumeration")
                return original(path)

            with patch.object(lsp, "Path", side_effect=lambda value: root if value == "/proc" else Path(value)), \
                 patch.object(Path, "iterdir", side_effect=disappeared, autospec=True):
                self.assertEqual(client.owned_rss(False), {"1": 2 * lsp.os.sysconf("SC_PAGE_SIZE")})
                with self.assertRaises(ProcessLookupError):
                    client.owned_rss(True)

    async def test_supervisor_retains_nonzero_control_exit_but_rejects_incomplete_cleanup(self):
        runner = observation.helpers.module("responsiveness_test_runner", ROOT / "tools/agent-debug.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stdout.log").write_text("real control failure\n")
            result = {"code": 1, "cleanup": {"verifiedEmpty": True}}
            call = runner.CommandSpec("fake", [])
            with patch.object(runner, "run_supervised", new=AsyncMock(return_value=result)):
                observed, text = await runner.observe_command(call, root, {}, root, 1000)
                self.assertEqual(observed["code"], 1)
                self.assertIn("control failure", text)
            for change in [{"cleanup": {"verifiedEmpty": False}}, {"timedOut": True},
                           {"spawnError": "missing executable"}, {"signal": "SIGTERM"}, {"code": None}]:
                with self.subTest(change=change), patch.object(runner, "run_supervised", new=AsyncMock(return_value=dict(result, **change))):
                    with self.assertRaises(ValueError):
                        await runner.observe_command(call, root, {}, root, 1000)

    async def test_failed_query_persists_ledger_even_without_success_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text('[package]\nname="probe"\nversion="0.1.0"\n')
            (root / "lib.rs").write_text("fn source() {}")
            binary = root / "server"
            binary.write_text("fake binary; startup is mocked")
            row = {"id": 1, "method": "initialize", "status": "rpc-error", "errorCode": -32603}
            client = SimpleNamespace(
                enable_observations=lambda: None, request_observations={1: row}, lifecycle_observations=[],
                observation=SimpleNamespace(stages=[]),
                request=AsyncMock(side_effect=lsp.LspQueryError("real error")), close=AsyncMock(), stderr="",
                server_log_file=root / "lsp-server.stderr.log",
            )
            plan = {"file": "lib.rs", "queries": [{"kind": "hover", "marker": "source"}]}
            with patch.object(lsp.LspClient, "start", new=AsyncMock(return_value=client)):
                with self.assertRaisesRegex(lsp.LspQueryError, "real error"):
                    await lsp.run(["--workspace-root", str(root), "--binary", str(binary), "--query-json", json.dumps(plan)])
            persisted = json.loads((root / "lsp-transport.json").read_text())
            self.assertEqual(persisted["transport"], [row])
            client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
