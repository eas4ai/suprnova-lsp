#!/usr/bin/env python3
"""Ensure original measurement violations cannot become accepted baseline evidence."""

import copy
import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rsp_check", ROOT / "tools/sudus-responsiveness-check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class BaselineIntegrity(unittest.TestCase):
    def setUp(self):
        self.reports = {name: json.loads((ROOT / f"tools/fixtures/responsiveness/baseline-{name}.json").read_text())
                        for name in ("source", "current", "generated")}

    def validate(self, reports):
        with patch.object(check.ResponsivenessCheck, "read_report", side_effect=lambda path: reports[path.stem.removeprefix("baseline-")]), \
             patch.object(check.observer.Diagnostic, "inventory", return_value=self.reports["source"]["identity"]["sources"]):
            return check.ResponsivenessCheck.baseline()

    def test_accepts_original_observations_without_claiming_generated_idle_memory(self):
        self.assertEqual(self.validate(self.reports)["frameworkRevision"], check.observer.REVISION)

    def test_rejects_absent_readiness_queue_or_input_fingerprint(self):
        for violation in ("readiness", "queue", "fingerprint", "materialization"):
            reports = copy.deepcopy(self.reports)
            source = reports["source"]
            raw = source["reports"]["faster-builds"]["sessions"][0]["raw"]
            if violation == "readiness":
                raw["lifecycle"] = [row for row in raw["lifecycle"] if row["method"] != "suprnova-lsp/activeWorkspaceChanged"]
            elif violation == "queue":
                for event in raw["stages"]:
                    event["fields"].pop("queued_ms", None)
            elif violation == "fingerprint":
                source["identity"]["sources"]["Cargo.lock"] = "0" * 64
            else:
                for session in source["reports"]["faster-builds"]["sessions"]:
                    session["raw"]["stages"] = [event for event in session["raw"]["stages"]
                        if event.get("fields", {}).get("phase") != "saved file materialization"]
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                self.validate(reports)

    def test_rejects_original_wrong_generated_type_or_late_publication(self):
        for violation in ("type", "publication"):
            reports = copy.deepcopy(self.reports)
            raw = reports["generated"]["reports"]["faster-builds"]["raw"]
            if violation == "type":
                raw["results"][0]["text"] = "Builder<Unrelated>"
            else:
                raw["lifecycle"] = [row for row in raw["lifecycle"] if row["method"] != "suprnova-lsp/rustdocStatus"]
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                self.validate(reports)

    def test_compares_measured_file_limits_without_requiring_the_same_parent_shell(self):
        baseline = self.reports["source"]["identity"]
        candidate = copy.deepcopy(self.reports["source"])
        candidate["binaryUnchanged"] = True
        candidate["runtimeSourcesUnchanged"] = True
        candidate["identity"]["runtimeSourcesSha256"] = "native-source"
        candidate["identity"]["openFileLimits"]["inherited"][0] = 524288
        for effective, expected in (([4096, 524288], True), ([1024, 524288], False)):
            candidate["identity"]["openFileLimits"]["effective"] = effective
            with self.subTest(effective=effective), tempfile.TemporaryDirectory() as scratch, \
                 patch.object(check.ResponsivenessCheck, "baseline", return_value=baseline), \
                 patch.object(check.ResponsivenessCheck, "read_report", return_value=candidate), \
                 patch.object(check.observer.Diagnostic, "inventory", return_value=baseline["sources"]), \
                 patch.object(check.observer.Diagnostic, "runtime_fingerprint", return_value="native-source"), \
                 patch.object(check.observer.SourceSeries, "run", new_callable=AsyncMock, return_value=Path(scratch) / "candidate.json"), \
                 patch.object(check.observer.helpers, "module", return_value=SimpleNamespace(create_run_directory=lambda _: Path(scratch))), \
                 redirect_stdout(io.StringIO()):
                asyncio.run(check.ResponsivenessCheck.run())
                results = json.loads((Path(scratch) / "observations.json").read_text())
                self.assertIs(results["RSP-001"]["passed"], expected)


if __name__ == "__main__":
    unittest.main()
