#!/usr/bin/env python3
"""Ensure original measurement violations cannot become accepted baseline evidence."""

import copy
import asyncio
import hashlib
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


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
                 patch.object(check.ResponsivenessCheck, "build_candidate", create=True, new_callable=AsyncMock,
                              return_value={"runtimeSourcesSha256": "native-source", "binarySha256": candidate["identity"]["binarySha256"], "report": str(Path(scratch) / "native-build.json")}), \
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
    def test_all_independent_obligations_are_checked_when_baseline_fails(self):
        with tempfile.TemporaryDirectory() as scratch, \
             patch.object(check.ResponsivenessCheck, "baseline", side_effect=ValueError("baseline missing")), \
             patch.object(check.ResponsivenessCheck, "invariant_evidence", return_value={"passed": True}) as assess, \
             patch.object(check.observer.helpers, "module", return_value=SimpleNamespace(create_run_directory=lambda _: Path(scratch))), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(asyncio.run(check.ResponsivenessCheck.run()), 1)
            self.assertEqual([call.args[0] for call in assess.call_args_list],
                             ["RSP-003", "RSP-004", "RSP-005", "RSP-006"])
            results = json.loads((Path(scratch) / "observations.json").read_text())
            self.assertEqual(set(results), {"RSP-001", "RSP-003", "RSP-004", "RSP-005", "RSP-006"})
            self.assertFalse(results["RSP-001"]["passed"])

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
                 patch.object(check.ResponsivenessCheck, "build_candidate", create=True, new_callable=AsyncMock,
                              return_value={"runtimeSourcesSha256": "unit-test-native-source", "binarySha256": candidate["identity"]["binarySha256"], "report": str(Path(scratch) / "native-build.json")}), \
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


class CurrentBuildProtocol(unittest.TestCase):
    def test_current_source_is_built_once_before_frozen_no_build_series(self):
        candidate = json.loads((ROOT / 'tools/fixtures/responsiveness/baseline-source.json').read_text())
        baseline = copy.deepcopy(candidate['identity'])
        candidate.update(binaryUnchanged=True, runtimeSourcesUnchanged=True)
        candidate['identity']['runtimeSourcesSha256'] = 'current-native-source'
        proof = {'runtimeSourcesSha256': 'current-native-source',
                 'binarySha256': candidate['identity']['binarySha256'], 'report': 'native-build.json'}
        order = []
        async def build(*args):
            order.append('build')
            return proof
        async def series(*args):
            order.append('series')
            return Path('candidate.json')
        with tempfile.TemporaryDirectory() as scratch:
            runner = SimpleNamespace(create_run_directory=lambda _: Path(scratch))
            with patch.object(check.ResponsivenessCheck, 'baseline', return_value=baseline), \
                 patch.object(check.ResponsivenessCheck, 'read_report', return_value=candidate), \
                 patch.object(check.ResponsivenessCheck, 'build_candidate', create=True, new_callable=AsyncMock, side_effect=build) as built, \
                 patch.object(check.ResponsivenessCheck, 'invariant_evidence', return_value={'passed': True}), \
                 patch.object(check.observer.Diagnostic, 'runtime_fingerprint', return_value='current-native-source'), \
                 patch.object(check.observer.SourceSeries, 'run', new_callable=AsyncMock, side_effect=series) as collected, \
                 patch.object(check.observer.helpers, 'module', return_value=runner), \
                 redirect_stdout(io.StringIO()):
                asyncio.run(check.ResponsivenessCheck.run())
            built.assert_awaited_once_with(runner, Path(scratch))
            collected.assert_awaited_once_with(check.observer.MODES, True, 4096)
            self.assertEqual(order, ['build', 'series'])

    def test_source_series_cannot_substitute_a_binary_or_source_after_build(self):
        for corruption in ('binary', 'source'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory(dir=ROOT / 'target/agent-debug') as scratch:
                directory = Path(scratch)
                binary = directory / 'suprnova-lsp'
                binary.write_bytes(b'actual controlled current binary')
                candidate = json.loads((ROOT / 'tools/fixtures/responsiveness/baseline-source.json').read_text())
                baseline = copy.deepcopy(candidate['identity'])
                candidate.update(binaryUnchanged=True, runtimeSourcesUnchanged=True)
                candidate['identity'].update(binary=str(binary), binarySha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                                              runtimeSourcesSha256='current-source')
                proof = {'runtimeSourcesSha256': 'current-source', 'binarySha256': candidate['identity']['binarySha256'],
                         'report': str(directory / 'native-build.json')}
                async def series(*args):
                    if corruption == 'binary':
                        binary.write_bytes(b'substituted after build')
                        candidate['identity']['binarySha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
                    else:
                        candidate['identity']['runtimeSourcesSha256'] = 'substituted-source'
                    return directory / 'candidate.json'
                runner = SimpleNamespace(create_run_directory=lambda _: directory)
                with patch.object(check.ResponsivenessCheck, 'baseline', return_value=baseline), \
                     patch.object(check.ResponsivenessCheck, 'read_report', return_value=candidate), \
                     patch.object(check.ResponsivenessCheck, 'build_candidate', create=True, new_callable=AsyncMock, return_value=proof), \
                     patch.object(check.ResponsivenessCheck, 'invariant_evidence', return_value={'passed': True}), \
                     patch.object(check.ResponsivenessCheck, 'matrix') as matrix, \
                     patch.object(check.observer.Diagnostic, 'runtime_fingerprint', side_effect=lambda: candidate['identity']['runtimeSourcesSha256']), \
                     patch.object(check.observer.SourceSeries, 'run', new_callable=AsyncMock, side_effect=series), \
                     patch.object(check.observer.helpers, 'module', return_value=runner), redirect_stdout(io.StringIO()):
                    self.assertEqual(asyncio.run(check.ResponsivenessCheck.run()), 1)
                result = json.loads((directory / 'observations.json').read_text())
                self.assertFalse(result['RSP-001']['passed'])
                self.assertRegex(result['RSP-001']['reason'], 'build|binary|source')
                matrix.assert_not_called()


class GuardedCurrentBuild(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=ROOT / 'target/agent-debug')
        self.addCleanup(self.scratch.cleanup)
        self.directory = Path(self.scratch.name)
        build_root = self.directory / 'build'
        self.binary = build_root / 'x86_64-unknown-linux-gnu/release/suprnova-lsp'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'actual controlled current binary bytes')
        self.args = ['build', '--target-dir', str(build_root), '--target', 'x86_64-unknown-linux-gnu',
                     '--release', '-p', 'suprnova-lsp', '--locked', '--offline']
        self.outcome = {'command': 'cargo', 'args': self.args, 'pid': 101, 'code': 0,
            'timedOut': False, 'signal': None, 'spawnError': None, 'interruptedBy': None,
            'cleanup': {'processGroupId': 101, 'verifiedEmpty': True, 'remainingPids': []}}
        self.runner = SimpleNamespace(BUILD_ROOT=build_root,
            RunnerOptions=lambda **kwargs: SimpleNamespace(**kwargs),
            build_spec=Mock(side_effect=lambda options: SimpleNamespace(command='cargo', args=self.args[:-2].copy())),
            CommandSpec=lambda command, args: SimpleNamespace(command=command, args=args),
            rust_glancer_binary=Mock(return_value=self.binary),
            observe_command=AsyncMock(return_value=(self.outcome, 'actual controlled compiler output')),
            write_json=lambda path, value: Path(path).write_text(json.dumps(value)),
            summarize_cleanup=lambda commands: {'status': 'verified', 'runs': len(commands), 'verifiedRuns': len(commands)},
            install_signal_handlers=lambda: None)

    def build(self, fingerprints=('current-source', 'current-source')):
        with patch.object(check.observer.Diagnostic, 'runtime_fingerprint', side_effect=fingerprints):
            return asyncio.run(check.ResponsivenessCheck.build_candidate(self.runner, self.directory))

    def test_real_file_digest_and_exact_supervised_locked_release_layout(self):
        inherited = {name: 'inherited-wrong-value' for name in
                     ('RUSTUP_TOOLCHAIN', 'CARGO_BUILD_JOBS', 'CARGO_NET_OFFLINE', 'CARGO_TARGET_DIR', 'CARGO_BUILD_BUILD_DIR')}
        with patch.dict(os.environ, inherited):
            proof = self.build()
        self.assertEqual(proof['runtimeSourcesSha256'], 'current-source')
        self.assertEqual(proof['binarySha256'], hashlib.sha256(self.binary.read_bytes()).hexdigest())
        self.assertTrue(Path(proof['report']).is_file())
        self.runner.build_spec.assert_called_once()
        self.assertEqual(self.runner.build_spec.call_args.args[0].build_profile, 'release')
        self.runner.rust_glancer_binary.assert_called_once_with('release')
        self.runner.observe_command.assert_awaited_once()
        spec, cwd, environment, output, deadline = self.runner.observe_command.await_args.args
        self.assertEqual((spec.command, spec.args), ('cargo', self.args))
        self.assertEqual(cwd, ROOT)
        self.assertEqual(deadline, 20 * 60_000)
        self.assertTrue(Path(output).is_relative_to(self.directory))
        self.assertEqual({name: environment[name] for name in inherited}, {
            'RUSTUP_TOOLCHAIN': '1.98.1', 'CARGO_BUILD_JOBS': '2', 'CARGO_NET_OFFLINE': 'true',
            'CARGO_TARGET_DIR': str(self.runner.BUILD_ROOT), 'CARGO_BUILD_BUILD_DIR': str(self.runner.BUILD_ROOT)})
        report = json.loads(Path(proof['report']).read_text())
        self.assertEqual(report['commands'], [self.outcome])

    def test_failed_timeout_or_unverified_cleanup_cannot_produce_candidate_proof(self):
        for corruption in ('failed', 'timeout', 'cleanup', 'remaining-pids'):
            outcome = copy.deepcopy(self.outcome)
            if corruption == 'failed': outcome['code'] = 42
            elif corruption == 'timeout': outcome['timedOut'] = True
            elif corruption == 'cleanup': outcome['cleanup']['verifiedEmpty'] = False
            else: outcome['cleanup']['remainingPids'] = [102]
            self.runner.observe_command.return_value = (outcome, 'controlled failed build')
            with self.subTest(corruption=corruption), self.assertRaises((ValueError, OSError)):
                self.build()

    def test_changed_native_sources_reject_despite_successful_build(self):
        with self.assertRaisesRegex(ValueError, 'source'):
            self.build(('before-source', 'after-source'))

    def test_missing_actual_binary_rejects_despite_successful_build(self):
        self.binary.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.build()


class IndependentEvidenceRouting(unittest.TestCase):
    def test_policy_and_idle_proofs_use_their_own_evaluators(self):
        selected = object()
        for requirement, filename, owner, key in (
            ("RSP-005", "responsiveness-policy.py", "PolicyEvidence", "policy"),
            ("RSP-006", "responsiveness-idle.py", "IdleEvidence", "idle"),
        ):
            proof = {"rawArtifact": filename}
            evaluator = SimpleNamespace(assess=lambda evidence: proof if evidence is selected else None)
            modules = {"responsiveness-evidence.py": SimpleNamespace(Evidence=lambda _: selected),
                       filename: SimpleNamespace(**{owner: evaluator})}
            with self.subTest(requirement=requirement), \
                 patch.object(check.observer.helpers, "module", side_effect=lambda _, path: modules[path.name]):
                self.assertEqual(check.ResponsivenessCheck.invariant_evidence(requirement),
                                 {"passed": True, key: proof})

    def test_rejected_raw_evidence_preserves_observations_and_fails(self):
        class RejectedEvidence(ValueError):
            observations = {"attempts": [{"id": 7, "error": "retained real error"}]}

        def reject(_):
            raise RejectedEvidence("raw evidence failed")

        for requirement, filename, owner in (
            ("RSP-005", "responsiveness-policy.py", "PolicyEvidence"),
            ("RSP-006", "responsiveness-idle.py", "IdleEvidence"),
        ):
            modules = {"responsiveness-evidence.py": SimpleNamespace(Evidence=lambda _: object()),
                       filename: SimpleNamespace(**{owner: SimpleNamespace(assess=reject)})}
            with self.subTest(requirement=requirement), \
                 patch.object(check.observer.helpers, "module", side_effect=lambda _, path: modules[path.name]):
                result = check.ResponsivenessCheck.invariant_evidence(requirement)
                self.assertFalse(result["passed"])
                self.assertEqual(result["observations"], RejectedEvidence.observations)
                self.assertEqual(result["reason"], "raw evidence failed")


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
