"""Reject missing, altered and stale native evidence rather than trusting summaries."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("bound_evidence_test", ROOT / "tools/responsiveness-evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)
fixtures = evidence.native.observer.helpers.module("bound_native_fixtures", ROOT / "tools/test_responsiveness_native.py")


class NativeBindingIntegrity(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT / "target/agent-debug")
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name)
        fixture = fixtures.NativeEvidenceIntegrity()
        fixture.setUp()
        self.discovery = json.dumps(fixture.discovery)
        self.events = fixture.events
        self.manifest = self.directory / "selection.json"

    def observe(self, *, failed=False, violation=None):
        if failed:
            self.events[1]["event"] = "failed"
        text = "\n".join(json.dumps(event) for event in self.events)
        code = 100 if failed else 0
        report = {"observationComplete": True, "runtimeSourcesUnchanged": True,
            "runtimeSourcesSha256": "unit-native-source", "observerUnchanged": True,
            "observerSha256": hashlib.sha256((ROOT / "tools/responsiveness-native.py").read_bytes()).hexdigest(),
            "tests": evidence.native.NativeEvidence.assess(self.discovery, text, code), "artifacts": {},
            "processCleanup": {"status": "verified", "runs": 2, "verifiedRuns": 2},
            "commands": [{"phase": label, "code": code if label == "native" else 0, "timedOut": False,
                "cleanup": {"verifiedEmpty": True, "remainingPids": []}} for label in ("discovery", "native")]}
        for label, output in (("discovery", self.discovery), ("native", text)):
            path = self.directory / label / "stdout.log"
            path.parent.mkdir(exist_ok=True)
            path.write_text(output)
            report["artifacts"][label] = hashlib.sha256(path.read_bytes()).hexdigest()
        if violation == "stale":
            report["runtimeSourcesSha256"] = "older-native-source"
        elif violation == "cleanup":
            report["commands"][-1]["cleanup"]["verifiedEmpty"] = False
        elif violation == "summary":
            report["tests"]["RSP-003"].clear()
        path = self.directory / "report.json"
        path.write_text(json.dumps(report))
        self.manifest.write_text(json.dumps({"schema": 1, "native": {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}))
        if violation == "events":
            (self.directory / "native/stdout.log").write_text("")
        elif violation == "report":
            path.write_text("{}")
        with patch.object(evidence.native.observer.Diagnostic, "runtime_fingerprint", return_value="unit-native-source"):
            return evidence.Evidence(self.manifest).native("RSP-002" if failed else "RSP-003")

    def test_accepts_complete_cases_and_preserves_actual_test_failure(self):
        self.assertEqual(len(self.observe()["cases"]), 20)
        with self.assertRaisesRegex(ValueError, "RSP-002 native invariant failed"):
            self.observe(failed=True)

    def test_rejects_stale_incomplete_or_altered_evidence(self):
        for violation in ("stale", "cleanup", "summary", "events", "report"):
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                self.observe(violation=violation)
