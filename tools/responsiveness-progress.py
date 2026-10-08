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
        summary["idleRssBytes"] = cls.idle_observation(raw)
        return {"summary": summary, "progress": progress, "finished": finished}

    async def observe_mode(self, mode, directory, workload, command):
        plan = self.workload_plan(mode, directory, workload)
        plan.update(deferredBarrier="after-queries", recordSession=True)
        plan["queries"] = [{"kind": "hover", "label": f"stream-{n}", "marker": "rsp_filter"} for n in range(20)]
        raw = await self.observe_plan("stream-" + mode, plan, directory, command)
        return {"raw": raw, "plan": plan, **self.assess(raw)}

if __name__ == "__main__":
    asyncio.run(Progress().run(("faster-builds",), True, 4096, "generated-captured"))
