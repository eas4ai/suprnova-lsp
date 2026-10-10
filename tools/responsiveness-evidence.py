"""Recheck selected native invariant evidence from its recorded terminal events."""

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rsp_bound_native", ROOT / "tools/responsiveness-native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class RecordedArtifact:
    @staticmethod
    def read(directory, hashes, name):
        path = (directory / name).resolve(strict=True)
        if not path.is_relative_to(directory.resolve()) or path.stat().st_size > 512 * 1024 * 1024:
            raise ValueError("selected artifact escaped its run or exceeds its report bound")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != hashes[name]:
            raise ValueError("selected artifact changed: " + name)
        return data.decode()


class Evidence:
    observer = native.observer
    artifacts = RecordedArtifact

    def __init__(self, manifest):
        self.manifest = manifest

    def bound(self, name):
        selection = json.loads(self.manifest.read_text())
        entry = selection.get(name)
        if selection.get("schema") != 1 or not isinstance(entry, dict):
            raise ValueError(name + " evidence has not been selected")
        owned = (ROOT / "target/agent-debug").resolve()
        path = (ROOT / entry["path"]).resolve(strict=True)
        if not path.is_relative_to(owned) or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError(name + " evidence escaped the owned root or exceeds its report bound")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(name + " evidence changed after selection")
        return path, json.loads(data)

    @staticmethod
    def cleanup(report):
        commands = report.get("commands", [])
        cleanup = report.get("processCleanup", {})
        if (not commands or cleanup.get("status") != "verified"
            or cleanup.get("runs") != len(commands) or cleanup.get("verifiedRuns") != len(commands)
            or any(type(command.get("code")) is not int or command.get("timedOut") is not False
                   or command.get("signal") or command.get("spawnError")
                   or command.get("cleanup", {}).get("verifiedEmpty") is not True
                   or command.get("cleanup", {}).get("remainingPids") != [] for command in commands)):
            raise ValueError("selected evidence lacks complete supervised process cleanup")
        return commands

    def native(self, requirement):
        path, report = self.bound("native")
        commands = self.cleanup(report)
        if ([command.get("phase") for command in commands] != ["discovery", "native"]
            or commands[0]["code"] != 0 or report.get("observationComplete") is not True
            or report.get("runtimeSourcesUnchanged") is not True
            or report.get("runtimeSourcesSha256") != native.observer.Diagnostic.runtime_fingerprint()
            or report.get("observerUnchanged") is not True
            or report.get("observerSha256") != hashlib.sha256((ROOT / "tools/responsiveness-native.py").read_bytes()).hexdigest()):
            raise ValueError("native evidence is incomplete or belongs to another implementation")
        outputs = []
        for label in ("discovery", "native"):
            artifact = (path.parent / label / "stdout.log").resolve(strict=True)
            if not artifact.is_relative_to(path.parent) or artifact.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("native terminal events escaped their run or exceed the report bound")
            data = artifact.read_bytes()
            if hashlib.sha256(data).hexdigest() != report["artifacts"][label]:
                raise ValueError("native terminal events changed after observation")
            outputs.append(data.decode())
        # A failed case remains evidence of failure. Compilation, discovery and
        # cleanup failures are rejected before they can stand in for that case.
        observed = native.NativeEvidence.assess(*outputs, commands[-1]["code"])
        if observed != report["tests"]:
            raise ValueError("native summary contradicts the actual terminal events")
        failures = [case for case, passed in observed[requirement].items() if not passed]
        if failures:
            raise ValueError(requirement + " native invariant failed: " + ", ".join(failures))
        return {"report": str(path), "cases": observed[requirement]}
