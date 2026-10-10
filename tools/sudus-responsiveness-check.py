#!/usr/bin/env python3
"""Check the responsiveness contract using frozen baselines and bounded observations."""

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "tools/fixtures/responsiveness/candidate-evidence.json"
spec = importlib.util.spec_from_file_location("rsp_observer", ROOT / "tools/sudus-responsiveness.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ResponsivenessCheck:
    @staticmethod
    def invariant_evidence(requirement):
        try:
            binding = observer.helpers.module("rsp_evidence_binding", ROOT / "tools/responsiveness-evidence.py")
            evidence = binding.Evidence(EVIDENCE)
            if requirement == "RSP-005":
                policy = observer.helpers.module("rsp_policy_binding", ROOT / "tools/responsiveness-policy.py")
                return {"passed": True, "policy": policy.PolicyEvidence.assess(evidence)}
            if requirement == "RSP-006":
                idle = observer.helpers.module("rsp_idle_binding", ROOT / "tools/responsiveness-idle.py")
                return {"passed": True, "idle": idle.IdleEvidence.assess(evidence)}
            # Check deterministic races first so a genuine failed invariant remains
            # the reason for rejection even when other observations are unavailable.
            invariants = evidence.native(requirement)
            delivery = observer.helpers.module("rsp_editor_binding", ROOT / "tools/responsiveness-delivery.py")
            if requirement == "RSP-003":
                semantic = observer.helpers.module("rsp_semantic_evidence", ROOT / "tools/responsiveness-semantic.py")
                return {"passed": True, "native": invariants,
                        "semantic": semantic.SemanticEvidence.assess(evidence), "editor": delivery.EditorEvidence.assess(evidence)}
            if requirement != "RSP-004":
                raise ValueError("unsupported invariant requirement: " + requirement)
            progress = observer.helpers.module("rsp_progress_binding", ROOT / "tools/responsiveness-progress.py")
            return {"passed": True, "native": invariants, "progress": progress.ProgressEvidence.assess(evidence),
                    "editor": delivery.EditorEvidence.assess(evidence, "cancellation", cancellation=True)}
        except (ValueError, KeyError, OSError, TypeError, StopIteration) as error:
            result = {"passed": False, "reason": str(error)}
            if hasattr(error, "observations"):
                result["observations"] = error.observations
            return result

    @staticmethod
    def read_report(path):
        data = path.read_bytes()
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("responsiveness report exceeds its size bound")
        return json.loads(data)

    @staticmethod
    def report_identity(report):
        if report.get("observationComplete") is not True or report.get("applicationInputsUnchanged") is not True:
            raise ValueError("measurement is incomplete or application inputs changed")
        cleanup = report.get("processCleanup", {})
        if cleanup.get("status") != "verified" or cleanup.get("runs", 0) != cleanup.get("verifiedRuns"):
            raise ValueError("measurement process cleanup was not verified")
        identity = report["identity"]
        if identity.get("frameworkRevision") != observer.REVISION or identity.get("application") != str(observer.APP):
            raise ValueError("measurement application or pinned framework identity changed")
        if identity.get("buildProfile") != "release" or identity.get("producerToolchain") != "nightly-2026-08-19":
            raise ValueError("measurement build or compiler configuration changed")
        if identity["sources"] != observer.Diagnostic.inventory():
            raise ValueError("measurement Rust/Cargo input fingerprints differ from Devlist")
        return identity

    @classmethod
    def baseline(cls):
        """Validate original source/worker and current/generated diagnostic evidence."""
        directory = ROOT / "tools/fixtures/responsiveness"
        source = cls.read_report(directory / "baseline-source.json")
        source_identity = cls.report_identity(source)
        series = {mode: observer.SourceSeries.assess(source["reports"][mode]["sessions"], mode)
                  for mode in observer.MODES}
        current = cls.read_report(directory / "baseline-current.json")
        cls.report_identity(current)
        generated = cls.read_report(directory / "baseline-generated.json")
        cls.report_identity(generated)
        for mode in observer.MODES:
            observer.Diagnostic.source_only_observation(current["reports"][mode]["raw"], "current")
            raw = generated["reports"][mode]["raw"]
            # The early generated diagnostic predates the independent purge
            # barrier. Retain it for transport/preparation attribution only.
            # It must never supply the settled-memory acceptance evidence.
            for row in raw["transport"]:
                if row["method"] != "textDocument/hover":
                    continue
                statuses = [event for event in raw["lifecycle"]
                            if event["method"] == "suprnova-lsp/rustdocStatus"
                            and event["params"].get("workspaceRoot") in {str(observer.APP), observer.APP.as_uri()}
                            and event["receivedNs"] <= row["writtenNs"]]
                if not statuses or statuses[-1]["params"].get("state") != "current":
                    raise ValueError("baseline generated declarations were not published before hover")
            observer.Diagnostic.hover_observation(raw, ["rsp_query", "rsp_without", "rsp_filter"],
                [("Builder<User>",)] * 3, "current")
        slow = series["faster-builds"]["stages"]
        phases = [phase for session in slow for phase in session[0]["preparationPhases"]
                  if phase["phase"] == "saved file materialization"]
        if len(phases) != 20 or observer.Diagnostic.percentile([phase["durationNs"] for phase in phases], 50) < 200_000_000:
            raise ValueError("saved-body change lacks supporting measured materialization delay")
        return source_identity

    @classmethod
    def matrix(cls, candidate_identity):
        """Recheck raw measurements selected by a declared, committed input."""
        manifest = cls.read_report(EVIDENCE)
        selection = manifest.get("matrix")
        if manifest.get("schema") != 1 or not isinstance(selection, dict):
            raise ValueError("complete latency matrix evidence has not been selected")
        owned = (ROOT / "target/agent-debug").resolve()
        path = (ROOT / selection["path"]).resolve(strict=True)
        if not path.is_relative_to(owned) or path.name != "report.json":
            raise ValueError("matrix report escapes the owned artifact root")
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("matrix report exceeds its size bound")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != selection["sha256"]:
            raise ValueError("selected latency matrix report changed")
        report = json.loads(data)
        identity = cls.report_identity(report)
        if (report.get("binaryUnchanged") is not True or report.get("runtimeSourcesUnchanged") is not True
            or identity.get("runtimeSourcesSha256") != observer.Diagnostic.runtime_fingerprint()
            or identity.get("purpose") != observer.AcceptanceMatrix.purpose
            or identity.get("workload") != "generated-captured"):
            raise ValueError("latency matrix does not describe the complete current native workload")
        for field in ("sources", "metadataSha256", "buildCompiler", "producerCompiler", "binarySha256", "runtimeSourcesSha256"):
            if identity[field] != candidate_identity[field]:
                raise ValueError("latency matrix/current candidate identities differ: " + field)
        if identity["openFileLimits"]["effective"] != candidate_identity["openFileLimits"]["effective"]:
            raise ValueError("latency matrix/current candidate effective open-file limits differ")
        # An archive and a fresh build can have different paths. Both actual
        # files must match the shared executable digest inside the owned root.
        binaries = (Path(identity["binary"]).resolve(strict=True), Path(candidate_identity["binary"]).resolve(strict=True))
        if any(not binary.is_relative_to(owned)
               or hashlib.sha256(binary.read_bytes()).hexdigest() != identity["binarySha256"] for binary in binaries):
            raise ValueError("latency matrix native binary changed or escaped the owned root")
        observers = {name: hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest()
                     for name in ("lsp-query.py", "sudus-responsiveness.py", "agent-debug.py")}
        if identity.get("observers") != observers:
            raise ValueError("latency matrix observer implementation changed")
        export = (path.parent / "directory.json").resolve(strict=True)
        if not export.is_relative_to(path.parent) or hashlib.sha256(export.read_bytes()).hexdigest() != identity["capturedExportSha256"]:
            raise ValueError("latency matrix captured declaration export changed")
        cache = identity.get("compilerCache")
        if cache is not None:
            provenance = Path(cache["manifest"]).resolve(strict=True)
            if not provenance.is_relative_to(owned) or provenance.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("compiler cache provenance escapes the owned root")
            prime = cls.read_report(provenance)
            if (hashlib.sha256(provenance.read_bytes()).hexdigest() != cache["manifestSha256"]
                or cache["observerSha256"] != hashlib.sha256((ROOT / "tools/responsiveness-cargo-cache.py").read_bytes()).hexdigest()
                or report.get("compilerSeedUnchanged") is not True
                or prime.get("observationComplete") is not True or prime.get("applicationInputsUnchanged") is not True
                or prime.get("sources") != identity["sources"] or prime.get("toolchain") != identity["producerToolchain"]
                or prime.get("target") != "x86_64-unknown-linux-gnu"
                or [command.get("phase") for command in prime.get("commands", [])] != ["copy-owned-cache", "prime-lib", "prime-directory", "prime-console"]
                or any(command.get("code") != 0 or command.get("cleanup", {}).get("verifiedEmpty") is not True for command in prime["commands"])):
                raise ValueError("latency matrix compiler cache provenance changed or is incomplete")
        commands = report["commands"]
        cleanup = report["processCleanup"]
        if (cleanup.get("runs") != len(commands) or not commands
            or any(command.get("code") != 0 or command.get("timedOut") is not False
                   or command.get("cleanup", {}).get("verifiedEmpty") is not True
                   or command.get("cleanup", {}).get("remainingPids") != [] for command in commands)):
            raise ValueError("latency matrix command cleanup or completion is incomplete")
        # Stored summaries are convenient progress reports. Acceptance always
        # recomputes counts, signatures, readiness, windows and p95 from sessions.
        return observer.AcceptanceMatrix.validate_series(report["reports"], commands, path.parent)

    @classmethod
    async def run(cls):
        runner = observer.helpers.module("rsp_check_runner", ROOT / "tools/agent-debug.py")
        directory = runner.create_run_directory("rsp-check")
        results = {}
        try:
            baseline = cls.baseline()
            print("Frozen baseline readiness, transport, stages and input identities verified.", flush=True)
            # This collector builds the actual current source and owns every
            # compiler/LSP process. A report from an earlier binary cannot stand
            # in for the candidate after the implementation changes.
            candidate_path = await observer.SourceSeries().run(observer.MODES, False, 4096)
            candidate = cls.read_report(candidate_path)
            identity = cls.report_identity(candidate)
            for field in ("sources", "metadataSha256", "buildCompiler", "producerCompiler", "cacheState"):
                if identity[field] != baseline[field]:
                    raise ValueError("baseline/candidate measurement conditions differ: " + field)
            # The collector sets the measured processes' limit before spawning
            # them. Keep the parent-shell limit as provenance, but compare the
            # effective limit that the compiler and server actually inherit.
            if identity["openFileLimits"]["effective"] != baseline["openFileLimits"]["effective"]:
                raise ValueError("baseline/candidate effective open-file limits differ")
            if candidate.get("binaryUnchanged") is not True:
                raise ValueError("candidate binary changed during observation")
            if candidate.get("runtimeSourcesUnchanged") is not True or identity.get("runtimeSourcesSha256") != observer.Diagnostic.runtime_fingerprint():
                raise ValueError("candidate measurements do not describe current native source")
            results["RSP-001"] = {"passed": True, "baseline": "tools/fixtures/responsiveness/baseline-source.json",
                "candidate": str(candidate_path), "binarySha256": identity["binarySha256"]}
            source = {mode: observer.SourceSeries.assess(candidate["reports"][mode]["sessions"], mode)
                      for mode in observer.MODES}
            slow = [f"{mode}/{cohort}: {series[cohort]['p95Ns']} ns"
                    for mode, series in source.items() for cohort in ("first", "repeated")
                    if not series[cohort]["belowTarget"]]
            try:
                # A measured source violation is sufficient to fail acceptance.
                # Missing matrix evidence must not hide the cause of that failure.
                if slow:
                    raise ValueError("cohort exceeds 200 ms: " + ", ".join(slow))
                matrix = cls.matrix(identity)
                slow.extend(f"{cell}/{cohort}: {summary[cohort]['p95Ns']} ns"
                            for cell, summary in matrix.items() for cohort in ("first", "repeated")
                            if not summary[cohort]["belowTarget"])
                results["RSP-002"] = {"passed": not slow, "sourceCohorts": source, "matrix": matrix,
                    "reason": "cohort exceeds 200 ms: " + ", ".join(slow) if slow else "all separate latency cohorts pass"}
            except (ValueError, KeyError, OSError, TypeError) as error:
                results["RSP-002"] = {"passed": False, "sourceCohorts": source, "reason": str(error)}
        except (ValueError, KeyError, OSError) as error:
            results["RSP-001"] = {"passed": False, "reason": str(error)}
        results["RSP-003"] = cls.invariant_evidence("RSP-003")
        results["RSP-004"] = cls.invariant_evidence("RSP-004")
        results["RSP-005"] = cls.invariant_evidence("RSP-005")
        results["RSP-006"] = cls.invariant_evidence("RSP-006")
        (directory / "observations.json").write_text(json.dumps(results, indent=2) + "\n")
        for requirement, result in results.items():
            print(json.dumps({"requirement": requirement, **result}), flush=True)
            print(f"sudus: {requirement}: {'pass' if result['passed'] else 'fail'}", flush=True)
        print(f"Responsiveness check artifacts: {directory}", flush=True)
        return 0 if all(result["passed"] for result in results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(ResponsivenessCheck.run()))
