"""Recheck genuine Devlist User methods and ownership from recorded observations."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SemanticEvidence:
    @staticmethod
    def assess(evidence):
        path, report = evidence.bound("semantic")
        cleanup_path, cleanup = evidence.bound("semanticCleanup")
        if cleanup_path.parent != path.parent:
            raise ValueError("semantic process cleanup belongs to another run")
        commands = evidence.cleanup(cleanup)
        identity = report["identity"]
        observer = evidence.observer
        if (identity.get("sources") != observer.Diagnostic.inventory()
            or identity.get("runtimeSourcesSha256") != observer.Diagnostic.runtime_fingerprint()
            or not all(identity.get(key) is True for key in
                       ("applicationInputsUnchanged", "runtimeSourcesUnchanged", "observersUnchanged", "binaryUnchanged"))
            or any(value != hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest()
                   for name, value in identity["observers"].items())):
            raise ValueError("semantic evidence belongs to another source or observer")
        binary = Path(identity["binary"]).resolve(strict=True)
        if not binary.is_relative_to((ROOT / "target").resolve()) or hashlib.sha256(binary.read_bytes()).hexdigest() != identity["binarySha256"]:
            raise ValueError("semantic native binary changed or escaped the build root")
        outputs = {name: evidence.artifacts.read(path.parent, report["artifacts"], name)
                   for name in report["artifacts"] if name != "directory.json"}
        with (path.parent / "directory.json").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != report["artifacts"]["directory.json"]:
                raise ValueError("semantic generated declarations changed after observation")
        phases = {command["phase"]: command for command in commands}
        helpers = observer.helpers
        wanted = helpers.discover(outputs["discovery/stdout.log"], helpers.CASES)
        tests = helpers.outcomes(outputs["semantic/stdout.log"], wanted, phases["semantic"]["code"])
        editor = helpers.editor_outcome(outputs["editor-results.json"], phases["editor"]["code"])
        if tests != report["tests"] or not all(tests.values()) or editor is not True or report["editor"] is not True:
            raise ValueError("semantic/editor invariant failed or contradicts its terminal events")
        SemanticEvidence.queries(evidence, path, report, outputs, phases)
        return {"report": str(path), "cases": tests, "modes": list(helpers.MODES)}

    @staticmethod
    def queries(evidence, path, report, outputs, phases):
        helpers = evidence.observer.helpers
        for mode in helpers.MODES:
            raw = report["reports"][mode]
            plan = json.loads(outputs[mode + "-plan.json"])
            query_hashes = json.loads(evidence.manifest.read_text())["semantic"]["queryLogs"]
            terminal = evidence.artifacts.read(path.parent, query_hashes, mode + "/stdout.log")
            actual = helpers.lsp_observation(terminal, phases[mode]["code"])
            if actual["results"] != raw["results"] or actual["barriers"] != raw["barriers"]:
                raise ValueError("semantic query summary contradicts the actual transport replies")
            facts = helpers.query_facts(raw, plan["text"])
            expected = ("source", "isolation", "sourceOnly") if mode.startswith("source-") else ("hover", "inlay", "completion", "source", "isolation")
            if facts != raw["facts"] or not all(facts[key] for key in expected):
                raise ValueError("genuine User/source/owner observation failed: " + mode)
