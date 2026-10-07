#!/usr/bin/env python3
"""Ensure original measurement violations cannot become accepted baseline evidence."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
