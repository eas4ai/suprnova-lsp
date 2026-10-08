import asyncio
import importlib.util
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rsp_progress", ROOT / "tools/sudus-responsiveness.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)

class Progress(observer.Diagnostic):
    purpose = "finite genuine hover stream and accepted background progress; not latency acceptance"
    run_kind = "rsp-progress"

    async def run(self, modes, no_build, nofile_soft, workload):
        before = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        path = await super().run(modes, no_build, nofile_soft, workload)
        report = json.loads(path.read_text())
        report.update(progressObserverSha256=before,
                      progressObserverUnchanged=before == hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        path.write_text(json.dumps(report, indent=2) + "\n")
        return path

    @classmethod
    def assess(cls, raw):
        summary = cls.hover_observation(raw, [f"stream-{n}" for n in range(20)], [("Builder<User>",)] * 20, "current")
        sent = [row for row in raw["transport"] if row["method"] == "textDocument/hover"]
        generation = observer.AcceptanceMatrix.window_evidence(raw, "deferred", sent[:1])[0]
        progress = [event for event in raw["stages"] if event["message"] == "deferred indexing progress"
                    and event["fields"].get("root") == str(observer.APP) and event["fields"].get("generation") == generation
                    and sent[0]["writtenNs"] < event["observedNs"] <= sent[-1]["receivedNs"]]
        finished = [event for event in raw["stages"] if event["message"] == "deferred indexing lifecycle finished"
                    and event["fields"].get("root") == str(observer.APP) and event["fields"].get("generation") == generation]
        if not progress or len(finished) != 1 or finished[0]["fields"].get("outcome") != "Succeeded":
            raise ValueError("finite hover stream lacks accepted progress or generation completion")
        if (finished[0]["observedNs"] <= progress[-1]["observedNs"]
            or any(sample["observedNs"] < finished[0]["observedNs"] for sample in raw["idleMemory"]["samples"])):
            raise ValueError("background completion or settled memory precedes accepted progress")
        summary["idleRssBytes"] = cls.idle_observation(raw)
        return {"summary": summary, "progress": progress, "finished": finished}

    async def observe_mode(self, mode, directory, workload, command):
        plan = self.workload_plan(mode, directory, workload)
        plan.update(deferredBarrier="after-queries", recordSession=True)
        plan["queries"] = [{"kind": "hover", "label": f"stream-{n}", "marker": "rsp_filter"} for n in range(20)]
        raw = await self.observe_plan("stream-" + mode, plan, directory, command)
        return {"raw": raw, "plan": plan, **self.assess(raw)}


class ProgressEvidence:
    @staticmethod
    def assess(evidence):
        path, report = evidence.bound("progress")
        commands = evidence.cleanup(report)
        identity = report["identity"]
        expected = ["metadata", "build-compiler", "producer-compiler", "producer-cfg", "producer-sysroot",
                    "commit", "runtime-inputs", "stream-faster-builds"]
        if ([command.get("phase") for command in commands] != expected or any(command["code"] != 0 for command in commands)
            or not all(report.get(key) is True for key in ("observationComplete", "applicationInputsUnchanged",
                                                          "binaryUnchanged", "runtimeSourcesUnchanged", "progressObserverUnchanged"))
            or identity.get("sources") != observer.Diagnostic.inventory()
            or identity.get("runtimeSourcesSha256") != observer.Diagnostic.runtime_fingerprint()
            or identity.get("application") != str(observer.APP) or identity.get("frameworkRevision") != observer.REVISION
            or identity.get("workload") != "generated-captured" or identity.get("buildProfile") != "release"
            or identity.get("producerToolchain") != "nightly-2026-08-19"
            or report.get("progressObserverSha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
            raise ValueError("progress observation is incomplete or belongs to another implementation")
        observers = identity["observers"]
        if (set(observers) != {"lsp-query.py", "sudus-responsiveness.py", "agent-debug.py"}
            or any(digest != hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest() for name, digest in observers.items())):
            raise ValueError("progress transport observer changed")
        binary = Path(identity["binary"]).resolve(strict=True)
        if (not binary.is_relative_to((ROOT / "target/agent-debug").resolve())
            or hashlib.sha256(binary.read_bytes()).hexdigest() != identity["binarySha256"]):
            raise ValueError("progress binary changed or escaped its owned root")
        entry = json.loads(evidence.manifest.read_text())["progress"]
        outputs = {name: evidence.artifacts.read(path.parent, entry["artifacts"], name)
                   for name in ("stream-faster-builds/stdout.log", "stream-faster-builds-plan.json")}
        terminal = outputs["stream-faster-builds/stdout.log"]
        actual, _ = json.JSONDecoder().raw_decode(terminal[terminal.index('{\n  "file"'):])
        recorded = report["reports"]["faster-builds"]
        plan = json.loads(outputs["stream-faster-builds-plan.json"])
        if (set(report["reports"]) != {"faster-builds"} or actual != recorded["raw"] or plan != recorded["plan"]
            or plan.get("deferredBarrier") != "after-queries" or plan.get("recordSession") is not True
            or plan["queries"] != [{"kind": "hover", "label": f"stream-{n}", "marker": "rsp_filter"} for n in range(20)]):
            raise ValueError("progress summary differs from the actual workload or transport")
        export = (path.parent / "directory.json").resolve(strict=True)
        if not export.is_relative_to(path.parent):
            raise ValueError("progress declarations escaped the owned run")
        with export.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != identity["capturedExportSha256"]:
                raise ValueError("progress declarations changed")
        first = next(row for row in actual["transport"] if row["method"] == "textDocument/hover")
        published = observer.AcceptanceMatrix.configured_publication(actual, plan, first["writtenNs"])
        observed = Progress.assess(actual)
        if published != observed["progress"][0]["fields"]["generation"]:
            raise ValueError("progress belongs to another saved generation")
        return {"report": str(path), "hoverCount": len(actual["results"]), "generation": published,
                "acceptedProgress": observed["progress"], "finished": observed["finished"],
                "idleRssBytes": observed["summary"]["idleRssBytes"]}


if __name__ == "__main__":
    asyncio.run(Progress().run(("faster-builds",), True, 4096, "generated-captured"))
