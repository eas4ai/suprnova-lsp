#!/usr/bin/env python3
"""Check the responsiveness contract using frozen baselines and bounded observations."""

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rsp_observer", ROOT / "tools/sudus-responsiveness.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ResponsivenessCheck:
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
            for field in ("sources", "metadataSha256", "buildCompiler", "producerCompiler", "cacheState", "openFileLimits"):
                if identity[field] != baseline[field]:
                    raise ValueError("baseline/candidate measurement conditions differ: " + field)
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
            results["RSP-002"] = {"passed": False, "sourceCohorts": source,
                "reason": "source cohort exceeds 200 ms: " + ", ".join(slow) if slow else
                    "generated, deferred-body and settled cohorts remain unverified"}
        except (ValueError, KeyError, OSError) as error:
            results["RSP-001"] = {"passed": False, "reason": str(error)}
        (directory / "observations.json").write_text(json.dumps(results, indent=2) + "\n")
        for requirement, result in results.items():
            print(json.dumps({"requirement": requirement, **result}), flush=True)
            print(f"sudus: {requirement}: {'pass' if result['passed'] else 'fail'}", flush=True)
        print(f"Responsiveness check artifacts: {directory}", flush=True)
        return 0 if all(result["passed"] for result in results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(ResponsivenessCheck.run()))
