"""Reject stream evidence that loses accepted progress, completion or request release."""

import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("progress_test", Path(__file__).with_name("responsiveness-progress.py"))
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


class ProgressIntegrity(unittest.TestCase):
    def setUp(self):
        root = str(progress.observer.APP)
        sent = [{"id": n + 1, "method": "textDocument/hover", "status": "success",
                 "writtenNs": 100 + n * 20, "receivedNs": 110 + n * 20, "durationNs": 10} for n in range(20)]
        self.raw = {"transport": sent,
                    "results": [{"kind": "hover", "label": f"stream-{n}", "text": "Builder<User>", "transport": row} for n, row in enumerate(sent)],
                    "lifecycle": [{"method": "suprnova-lsp/activeWorkspaceChanged", "receivedNs": 90, "params": {"root": root, "state": "ready"}}],
                    "barriers": {"hoverCleanup": True},
                    "stages": [{"message": "editor document analysis route published", "observedNs": 95, "fields": {"path": root + "/src/models/user.rs", "ready": True}},
                               {"message": "deferred indexing lifecycle started", "observedNs": 96, "fields": {"root": root, "generation": 1}},
                               {"message": "deferred indexing progress", "observedNs": 300, "fields": {"root": root, "generation": 1}},
                               {"message": "deferred indexing lifecycle finished", "observedNs": 500, "fields": {"root": root, "generation": 1, "outcome": "Succeeded"}}],
                    "idleMemory": {"indexingComplete": True, "metric": "sum-of-process-RSS", "indexingPeakRssBytes": 1000, "indexingSamples": 10, "samplingIntervalMs": 100,
                                   "samples": [{"observedNs": 600 + n, "processRssBytes": {"1": 40, "2": 60}, "aggregateRssBytes": 100} for n in range(5)]}}
        for n in range(20):
            self.raw["stages"].extend([
                {"message": "analysis query completed", "observedNs": 110 + n * 20, "fields": {"query": "hover", "status": "ok", "queued_ms": "0", "elapsed_ms": "0"}},
                {"message": "document analysis prepared", "fields": {"query": "hover", "source": "current", "elapsed_us": "0"}},
                {"message": "memory report", "observedNs": 115 + n * 20, "fields": {"label": "hover"}},
            ])

    def test_preserves_progress_completion_and_all_five_settled_samples(self):
        result = progress.Progress.assess(self.raw)
        self.assertEqual(result["progress"][0]["fields"]["generation"], 1)
        self.assertEqual(result["summary"]["idleRssBytes"], [100] * 5)

    def test_rejects_missing_progress_wrong_generation_or_premature_completion(self):
        for violation in ("absent", "generation", "late", "failed", "early-finish", "premature-idle", "missing-idle", "lost-reply"):
            raw = copy.deepcopy(self.raw)
            if violation == "absent":
                raw["stages"].pop(2)
            elif violation == "generation":
                raw["stages"][2]["fields"]["generation"] = 2
            elif violation == "late":
                raw["stages"][2]["observedNs"] = 501
            elif violation == "failed":
                raw["stages"][3]["fields"]["outcome"] = "Failed"
            elif violation == "early-finish":
                raw["stages"][3]["observedNs"] = 299
            elif violation == "premature-idle":
                raw["idleMemory"]["samples"][0]["observedNs"] = 499
            elif violation == "missing-idle":
                raw["idleMemory"]["samples"].pop()
            else:
                raw["results"].pop()
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                progress.Progress.assess(raw)


if __name__ == "__main__":
    unittest.main()
