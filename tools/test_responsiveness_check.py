#!/usr/bin/env python3
"""Ensure original measurement violations cannot become accepted baseline evidence."""

import copy
import asyncio
import hashlib
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
matrix_tests = check.observer.helpers.module("rsp_matrix_fixtures", ROOT / "tools/test_responsiveness.py")


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
                   patch.object(check.ResponsivenessCheck, "invariant_evidence", return_value={"passed": True}), \
                 patch.object(check.observer.Diagnostic, "inventory", return_value=baseline["sources"]), \
                 patch.object(check.observer.Diagnostic, "runtime_fingerprint", return_value="native-source"), \
                 patch.object(check.observer.SourceSeries, "run", new_callable=AsyncMock, return_value=Path(scratch) / "candidate.json"), \
                 patch.object(check.observer.helpers, "module", return_value=SimpleNamespace(create_run_directory=lambda _: Path(scratch))), \
                 redirect_stdout(io.StringIO()):
                asyncio.run(check.ResponsivenessCheck.run())
                results = json.loads((Path(scratch) / "observations.json").read_text())
                self.assertIs(results["RSP-001"]["passed"], expected)



class SourceLatencyIntegrity(unittest.TestCase):
    def test_slow_source_fails_for_latency_and_fast_source_still_requires_the_matrix(self):
        original = json.loads((ROOT / "tools/fixtures/responsiveness/baseline-source.json").read_text())
        baseline = original["identity"]
        for slow in (True, False):
            candidate = copy.deepcopy(original)
            candidate["binaryUnchanged"] = True
            candidate["runtimeSourcesUnchanged"] = True
            candidate["identity"]["runtimeSourcesSha256"] = "unit-test-native-source"
            if not slow:
                for mode in check.observer.MODES:
                    fixture = matrix_tests.SourceSeriesIntegrity()
                    fixture.setUp()
                    for session in fixture.sessions:
                        session["plan"]["initializationOptions"]["indexing"]["performancePreference"] = mode
                    candidate["reports"][mode]["sessions"] = fixture.sessions
            with self.subTest(slow=slow), tempfile.TemporaryDirectory() as scratch, \
                 patch.object(check.ResponsivenessCheck, "baseline", return_value=baseline), \
                 patch.object(check.ResponsivenessCheck, "read_report", return_value=candidate), \
                 patch.object(check.ResponsivenessCheck, "matrix", side_effect=ValueError("complete latency matrix evidence has not been selected")) as matrix, \
                   patch.object(check.ResponsivenessCheck, "invariant_evidence", return_value={"passed": True}), \
                 patch.object(check.observer.Diagnostic, "inventory", return_value=baseline["sources"]), \
                 patch.object(check.observer.Diagnostic, "runtime_fingerprint", return_value="unit-test-native-source"), \
                 patch.object(check.observer.SourceSeries, "run", new_callable=AsyncMock, return_value=Path(scratch) / "candidate.json"), \
                 patch.object(check.observer.helpers, "module", return_value=SimpleNamespace(create_run_directory=lambda _: Path(scratch))), \
                 redirect_stdout(io.StringIO()):
                code = asyncio.run(check.ResponsivenessCheck.run())
                results = json.loads((Path(scratch) / "observations.json").read_text())
                self.assertEqual(code, 1)
                self.assertTrue(results["RSP-001"]["passed"])
                self.assertFalse(results["RSP-002"]["passed"])
                if slow:
                    self.assertGreaterEqual(results["RSP-002"]["sourceCohorts"]["faster-builds"]["first"]["p95Ns"], 200_000_000)
                    self.assertTrue(results["RSP-002"]["reason"].startswith("cohort exceeds 200 ms:"))
                    matrix.assert_not_called()
                else:
                    self.assertEqual(results["RSP-002"]["reason"], "complete latency matrix evidence has not been selected")
                    matrix.assert_called_once()


class MatrixReceiptIntegrity(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT / "target/agent-debug")
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name)
        self.manifest = self.directory / "selection.json"
        self.path = self.directory / "report.json"
        self.binary = self.directory / "suprnova-lsp"
        self.binary.write_bytes(b"test native executable identity")
        (self.directory / "directory.json").write_bytes(b"test captured export identity")
        self.identity = copy.deepcopy(json.loads((ROOT / "tools/fixtures/responsiveness/baseline-source.json").read_text())["identity"])
        self.identity.update(purpose=check.observer.AcceptanceMatrix.purpose, workload="generated-captured",
            binary=str(self.binary), binarySha256=hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            runtimeSourcesSha256="test-native-inputs", capturedExportSha256=hashlib.sha256(b"test captured export identity").hexdigest(),
            cacheState="existing LSP/compiler caches; fresh owned export artifact root",
            observers={name: hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest()
                       for name in ("lsp-query.py", "sudus-responsiveness.py", "agent-debug.py")})
        reports, commands = {}, []
        fixture = matrix_tests.AcceptanceMatrixIntegrity()
        cell = 0
        for mode in check.observer.MODES:
            series = {}
            for symbol in check.observer.AcceptanceMatrix.symbols:
                for window in check.observer.AcceptanceMatrix.windows:
                    if mode == "lower-peak-memory" and window == "deferred":
                        continue
                    sessions, _ = fixture.series(mode, symbol, window, self.directory)
                    references = []
                    for number, session in enumerate(sessions):
                        session["raw"]["session"]["serverPid"] += cell * 10000
                        label = f"matrix-{mode}-{symbol}-{window}-{number:03}"
                        path = self.directory / f"{label}-session.json"
                        path.write_text(json.dumps(session))
                        references.append({"label": label, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                        commands.append({"phase": label, "code": 0, "timedOut": False,
                                         "cleanup": {"verifiedEmpty": True, "remainingPids": []}})
                    series[symbol + "/" + window] = {"sessions": references}
                    cell += 1
            reports[mode] = {"series": series}
        self.report = {"identity": self.identity, "reports": reports, "commands": commands,
            "observationComplete": True, "applicationInputsUnchanged": True, "binaryUnchanged": True,
            "runtimeSourcesUnchanged": True, "processCleanup": {"status": "verified", "runs": len(commands), "verifiedRuns": len(commands)}}
        self.select()

    def select(self):
        self.path.write_text(json.dumps(self.report))
        self.manifest.write_text(json.dumps({"schema": 1, "matrix": {
            "path": str(self.path), "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest()}}))

    def validate(self, candidate_identity=None):
        with patch.object(check, "EVIDENCE", self.manifest), \
             patch.object(check.observer.Diagnostic, "inventory", return_value=self.identity["sources"]), \
             patch.object(check.observer.Diagnostic, "runtime_fingerprint", return_value="test-native-inputs"):
            return check.ResponsivenessCheck.matrix(self.identity if candidate_identity is None else candidate_identity)

    def test_accepts_all_twenty_cells_from_raw_evidence(self):
        summaries = self.validate()
        self.assertEqual(len(summaries), 20)
        self.assertTrue(all(summary[cohort]["belowTarget"] for summary in summaries.values() for cohort in ("first", "repeated")))

    def test_accepts_identical_candidate_binary_at_distinct_owned_path(self):
        candidate_binary = self.directory / "archived-suprnova-lsp"
        candidate_binary.write_bytes(self.binary.read_bytes())
        candidate_identity = copy.deepcopy(self.identity)
        candidate_identity["binary"] = str(candidate_binary)
        self.assertNotEqual(candidate_binary.resolve(), self.binary.resolve())
        self.assertEqual(hashlib.sha256(candidate_binary.read_bytes()).hexdigest(), self.identity["binarySha256"])

        summaries = self.validate(candidate_identity)

        self.assertEqual(len(summaries), 20)
        for key, summary in summaries.items():
            with self.subTest(cell=key):
                self.assertTrue(summary["completeCounts"])
                self.assertEqual(summary["first"]["count"], 20)
                self.assertEqual(summary["repeated"]["count"], 100)
                self.assertTrue(summary["first"]["belowTarget"])
                self.assertTrue(summary["repeated"]["belowTarget"])

    def test_rejects_changed_candidate_binary_despite_matching_claimed_sha(self):
        candidate_binary = self.directory / "changed-suprnova-lsp"
        candidate_binary.write_bytes(b"changed native executable bytes")
        candidate_identity = copy.deepcopy(self.identity)
        candidate_identity["binary"] = str(candidate_binary)
        self.assertEqual(candidate_identity["binarySha256"], self.report["identity"]["binarySha256"])
        self.assertNotEqual(hashlib.sha256(candidate_binary.read_bytes()).hexdigest(), candidate_identity["binarySha256"])

        with self.assertRaisesRegex(ValueError, "binary"):
            self.validate(candidate_identity)

    def test_rejects_changed_matrix_binary_with_identical_owned_candidate(self):
        candidate_binary = self.directory / "archived-suprnova-lsp"
        candidate_binary.write_bytes(self.binary.read_bytes())
        candidate_identity = copy.deepcopy(self.identity)
        candidate_identity["binary"] = str(candidate_binary)
        report_bytes = self.path.read_bytes()
        selection_bytes = self.manifest.read_bytes()

        self.binary.write_bytes(b"changed matrix executable bytes")

        self.assertEqual(candidate_identity["binarySha256"], self.identity["binarySha256"])
        self.assertEqual(hashlib.sha256(candidate_binary.read_bytes()).hexdigest(), self.identity["binarySha256"])
        self.assertNotEqual(hashlib.sha256(self.binary.read_bytes()).hexdigest(), self.identity["binarySha256"])
        self.assertEqual(self.path.read_bytes(), report_bytes)
        self.assertEqual(self.manifest.read_bytes(), selection_bytes)
        with self.assertRaisesRegex(ValueError, "binary"):
            self.validate(candidate_identity)

    def test_rejects_candidate_binary_outside_owned_root(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "target") as outside:
            candidate_binary = Path(outside) / "suprnova-lsp"
            candidate_binary.write_bytes(self.binary.read_bytes())
            candidate_identity = copy.deepcopy(self.identity)
            candidate_identity["binary"] = str(candidate_binary)
            self.assertFalse(candidate_binary.resolve().is_relative_to((ROOT / "target/agent-debug").resolve()))
            self.assertEqual(hashlib.sha256(candidate_binary.read_bytes()).hexdigest(), candidate_identity["binarySha256"])

            with self.assertRaisesRegex(ValueError, "binary"):
                self.validate(candidate_identity)

    def test_rejects_candidate_binary_symlink_escaping_owned_root(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "target") as outside:
            outside_binary = Path(outside) / "suprnova-lsp"
            outside_binary.write_bytes(self.binary.read_bytes())
            candidate_link = self.directory / "escaped-suprnova-lsp"
            candidate_link.symlink_to(outside_binary)
            candidate_identity = copy.deepcopy(self.identity)
            candidate_identity["binary"] = str(candidate_link)
            self.assertTrue(candidate_link.is_relative_to((ROOT / "target/agent-debug").resolve()))
            self.assertFalse(candidate_link.resolve().is_relative_to((ROOT / "target/agent-debug").resolve()))
            self.assertEqual(hashlib.sha256(candidate_link.read_bytes()).hexdigest(), candidate_identity["binarySha256"])

            with self.assertRaisesRegex(ValueError, "binary"):
                self.validate(candidate_identity)

    def test_rejects_missing_altered_or_escaped_report(self):
        for violation in ("missing", "altered", "escaped", "symlink", "schema"):
            self.select()
            manifest = json.loads(self.manifest.read_text())
            if violation == "missing":
                manifest["matrix"] = None
            elif violation == "altered":
                self.path.write_text(self.path.read_text() + " ")
            elif violation in {"escaped", "symlink"}:
                manifest["matrix"]["path"] = "/etc/hosts"
                if violation == "symlink":
                    link = self.directory / "escaped.json"
                    link.symlink_to("/etc/hosts")
                    manifest["matrix"]["path"] = str(link)
            else:
                manifest["schema"] = 2
            self.manifest.write_text(json.dumps(manifest))
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                self.validate()

    def test_rejects_stale_identity_incomplete_cleanup_and_changed_export(self):
        original = copy.deepcopy(self.report)
        for violation in ("native", "observer", "binary", "metadata", "compiler", "limits", "partial", "command-cleanup", "command-timeout", "cleanup-count", "export"):
            self.report = copy.deepcopy(original)
            identity = self.report["identity"]
            if violation == "native":
                identity["runtimeSourcesSha256"] = "old-source"
            elif violation == "observer":
                identity["observers"]["lsp-query.py"] = "0" * 64
            elif violation == "binary":
                identity["binarySha256"] = "0" * 64
            elif violation == "metadata":
                identity["metadataSha256"] = "0" * 64
            elif violation == "compiler":
                identity["producerCompiler"] = "wrong compiler"
            elif violation == "limits":
                identity["openFileLimits"]["effective"] = [1024, 524288]
            elif violation == "partial":
                self.report["observationComplete"] = False
            elif violation == "command-cleanup":
                self.report["commands"][0]["cleanup"]["verifiedEmpty"] = False
            elif violation == "command-timeout":
                self.report["commands"][0]["timedOut"] = True
            elif violation == "cleanup-count":
                self.report["processCleanup"]["runs"] += 1
                self.report["processCleanup"]["verifiedRuns"] += 1
            else:
                identity["capturedExportSha256"] = "0" * 64
            self.select()
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                self.validate()

    def test_recomputes_slow_cohort_and_rejects_removed_sample(self):
        references = self.report["reports"]["faster-builds"]["series"]["source/worker"]["sessions"]
        for reference in references:
            path = Path(reference["path"])
            session = json.loads(path.read_text())
            for row in session["raw"]["transport"]:
                if row["method"] == "textDocument/hover":
                    row["writtenNs"] += 300_000_000 * (row["id"] - 2)
                    row["receivedNs"] = row["writtenNs"] + 250_000_000
                    row["durationNs"] = 250_000_000
            for result, row in zip(session["raw"]["results"], session["raw"]["transport"][1:]):
                result["transport"] = row
            path.write_text(json.dumps(session))
            reference["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.select()
        summary = self.validate()["faster-builds/source/worker"]
        self.assertFalse(summary["first"]["belowTarget"])
        self.assertFalse(summary["repeated"]["belowTarget"])
        path = Path(references[0]["path"])
        session = json.loads(path.read_text())
        session["raw"]["results"].pop()
        path.write_text(json.dumps(session))
        references[0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.select()
        with self.assertRaisesRegex(ValueError, "every planned hover"):
            self.validate()

    def test_warm_compiler_provenance_is_verified(self):
        prime = {"observationComplete": True, "applicationInputsUnchanged": True,
            "sources": self.identity["sources"], "toolchain": self.identity["producerToolchain"],
            "target": "x86_64-unknown-linux-gnu", "commands": [
                {"phase": phase, "code": 0, "cleanup": {"verifiedEmpty": True}}
                for phase in ("copy-owned-cache", "prime-lib", "prime-directory", "prime-console")]}
        path = self.directory / "prime.json"
        path.write_text(json.dumps(prime))
        self.report["identity"]["compilerCache"] = {
            "manifest": str(path), "manifestSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "observerSha256": hashlib.sha256((ROOT / "tools/responsiveness-cargo-cache.py").read_bytes()).hexdigest()}
        self.report["compilerSeedUnchanged"] = True
        self.select()
        self.assertEqual(len(self.validate()), 20)
        for violation in ("changed-prime", "changed-observer", "changed-seed"):
            original = copy.deepcopy(self.report)
            if violation == "changed-prime":
                path.write_text(json.dumps(dict(prime, observationComplete=False)))
            elif violation == "changed-observer":
                self.report["identity"]["compilerCache"]["observerSha256"] = "0" * 64
            else:
                self.report["compilerSeedUnchanged"] = False
            self.select()
            with self.subTest(violation=violation), self.assertRaisesRegex(ValueError, "compiler cache provenance"):
                self.validate()
            self.report = original
            path.write_text(json.dumps(prime))


if __name__ == "__main__":
    unittest.main()
