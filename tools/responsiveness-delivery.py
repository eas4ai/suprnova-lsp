"""Check editor delivery against the actual client wire trace."""

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent


class EditorCase:
    @staticmethod
    def require_pass(terminal, expected):
        report = json.loads(terminal)
        if (report["stats"].get("tests") != 1 or report["stats"].get("passes") != 1
            or report["stats"].get("pending") != 0 or report["stats"].get("failures") != 0
            or len(report.get("tests", [])) != 1 or len(report.get("passes", [])) != 1
            or report.get("failures") or report.get("pending")
            or report["tests"][0].get("fullTitle") != expected
            or report["passes"][0].get("fullTitle") != expected):
            raise ValueError("genuine editor case did not pass exactly once")


class EditorDelivery:
    case = "Suprnova LSP responsiveness RSP-003 delivers generated hover during indexing and rejects an overtaken revision"

    @staticmethod
    def events(output):
        events = []
        for line in output.splitlines():
            offset = line.find('{"isLSPMessage":true')
            if offset >= 0:
                event = json.loads(line[offset:])
                if type(event.get("timestamp")) is not int:
                    raise ValueError("editor trace lacks an actual client timestamp")
                if event["type"] == "send-request" and event["message"].get("method") == "initialize":
                    events.clear()
                events.append(event)
        return events

    @classmethod
    def assess(cls, terminal, protocol, root):
        EditorCase.require_pass(terminal, cls.case)
        observed = json.loads(protocol)
        if observed.get("application") != root:
            raise ValueError("editor observed another application")
        first, events, replies = cls.generated_window(observed, root)
        edit = cls.edited_response(observed, first, events, replies)
        return {"case": cls.case, "generatedRequestId": first["message"]["id"],
                "overtakenRequestId": edit["requestId"], "oldVersion": edit["oldVersion"], "newVersion": edit["newVersion"]}

    @classmethod
    def generated_window(cls, observed, root):
        output = observed["final"]["output"]
        events = cls.events(output)
        if events != observed["final"]["events"]:
            raise ValueError("editor event summary differs from the client trace")
        replies = {event["message"]["id"]: event for event in events if event["type"] == "receive-response"}
        requests = [event for event in events if event["type"] == "send-request" and event["message"].get("method") == "textDocument/hover"]
        generated = [request for request in requests if "Builder<User>" in json.dumps(replies.get(request["message"]["id"], {}).get("message", {}).get("result"))]
        if not generated or "Builder<User>" not in observed["generated"]["text"]:
            raise ValueError("genuine unsaved generated hover is absent")
        first = generated[0]
        uri = first["message"]["params"]["textDocument"]["uri"]
        if uri != (Path(root) / "src/models/user.rs").as_uri():
            raise ValueError("generated editor hover belongs to another document owner")
        started = [event for event in events if event["message"].get("method") == "suprnova-lsp/deferredIndexingStarted"
                   and event["message"]["params"].get("root") == root and event["timestamp"] <= first["timestamp"]]
        finished = [event for event in events if event["message"].get("method") == "suprnova-lsp/deferredIndexingFinished"
                    and event["message"]["params"].get("root") == root and event["timestamp"] <= first["timestamp"]]
        declarations = re.findall(r"configured rustdoc declarations published[^\n]*generation=(\d+)[^\n]*root=" + re.escape(root), observed["beforeGenerated"]["output"])
        generations = re.findall(r"deferred indexing background finish started generation=(\d+)", observed["beforeGenerated"]["output"])
        if not started or finished or not declarations or not generations or declarations[-1] != generations[-1]:
            raise ValueError("generated editor hover lacks independent matching active-generation readiness")
        if not any("editor document analysis route published" in line and "ready=true" in line
                   and str(Path(root) / "src/models/user.rs") in line
                   for line in observed["beforeGenerated"]["output"].splitlines()):
            raise ValueError("generated editor document route was not independently ready")
        return first, events, replies

    @staticmethod
    def edited_response(observed, first, events, replies):
        uri = first["message"]["params"]["textDocument"]["uri"]
        requests = [event for event in events if event["type"] == "send-request" and event["message"].get("method") == "textDocument/hover"]
        edit = observed["edit"]
        old = next(request for request in requests if request["message"]["id"] == edit["requestId"])
        changes = [event for event in events if event["message"].get("method") == "textDocument/didChange"
                   and event["message"]["params"]["textDocument"].get("uri") == uri
                   and event["message"]["params"]["textDocument"].get("version") == edit["newVersion"]]
        reply = replies[edit["requestId"]]
        if (len(changes) != 1 or edit["newVersion"] <= edit["oldVersion"]
            or not events.index(old) < events.index(changes[0]) < events.index(reply)
            or reply["message"].get("error", {}).get("code") != -32801):
            raise ValueError("overtaken editor hover was not rejected after the actual didChange")
        following = [request for request in requests if request["timestamp"] > changes[0]["timestamp"]
                     and request["message"]["params"]["textDocument"].get("uri") == uri
                     and re.search(r"\bu32\b", json.dumps(replies.get(request["message"]["id"], {}).get("message", {}).get("result")))]
        if not following or not re.search(r"\bu32\b", edit["newText"]) or edit["oldOutcome"].get("text"):
            raise ValueError("new editor revision is missing or obsolete success reached the provider")
        return edit


class EditorEvidence:
    @staticmethod
    def assess(evidence, name="editor", cancellation=False):
        path, report = evidence.bound(name)
        commands = evidence.cleanup(report)
        if (len(commands) != 1 or commands[0]["code"] != 0
            or report.get("runtimeSourcesSha256") != evidence.observer.Diagnostic.runtime_fingerprint()
            or report.get("sources") != evidence.observer.Diagnostic.inventory()
            or not all(report.get(key) is True for key in
                       ("applicationInputsUnchanged", "runtimeSourcesUnchanged", "binaryUnchanged", "observersUnchanged"))):
            raise ValueError("editor delivery is incomplete or belongs to another implementation")
        for name, digest in report["observers"].items():
            artifact = (ROOT / name).resolve(strict=True)
            if not artifact.is_relative_to(ROOT) or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                raise ValueError("editor observer changed after delivery")
        binary = Path(report["binary"]).resolve(strict=True)
        export = Path(report["export"]).resolve(strict=True)
        owned = (ROOT / "target/agent-debug").resolve()
        if (not binary.is_relative_to(owned) or not export.is_relative_to(owned)
            or hashlib.sha256(binary.read_bytes()).hexdigest() != report["binarySha256"]
            or hashlib.sha256(export.read_bytes()).hexdigest() != report["exportSha256"]):
            raise ValueError("editor binary or generated declarations changed")
        outputs = [evidence.artifacts.read(path.parent, report["artifacts"], name)
                   for name in ("editor-results.json", "editor-results.json.protocol.json")]
        observer = EditorCancellation if cancellation else EditorDelivery
        return {"report": str(path), **observer.assess(*outputs, str(evidence.observer.APP))}


class EditorCancellation:
    case = "Suprnova LSP responsiveness RSP-004 cancels a genuine editor hover and serves the following request"

    @classmethod
    def assess(cls, terminal, protocol, root):
        EditorCase.require_pass(terminal, cls.case)
        observed = json.loads(protocol)
        events = EditorDelivery.events(observed["final"]["output"])
        if observed.get("application") != root or events != observed["final"]["events"]:
            raise ValueError("cancellation trace summary or application differs")
        request_id = observed["requestId"]
        requests = [event for event in events if event["type"] == "send-request" and event["message"].get("id") == request_id]
        cancels = [event for event in events if event["type"] == "send-notification"
                   and event["message"].get("method") == "$/cancelRequest"
                   and event["message"]["params"].get("id") == request_id]
        replies = [event for event in events if event["type"] == "receive-response" and event["message"].get("id") == request_id]
        if (len(requests) != 1 or len(cancels) != 1 or len(replies) != 1
            or not events.index(requests[0]) < events.index(cancels[0]) < events.index(replies[0])
            or requests[0]["message"].get("method") != "textDocument/hover"
            or requests[0]["message"]["params"]["textDocument"].get("uri") != (Path(root) / "src/models/user.rs").as_uri()
            or replies[0]["message"].get("error", {}).get("code") != -32800):
            raise ValueError("actual editor cancellation did not suppress its hover response")
        if (observed["hidden"].get("visible") is not False or observed["visible"].get("visible") is not True
            or "Builder<User>" not in observed["following"]["text"]
            or "Builder<User>" not in observed["visible"]["text"]):
            raise ValueError("cancelled tooltip remained visible or following valid tooltip was absent")
        uri = (Path(root) / "src/models/user.rs").as_uri()
        following = [event for event in events if event["type"] == "send-request"
                     and event["message"].get("method") == "textDocument/hover"
                     and event["message"]["params"]["textDocument"].get("uri") == uri
                     and events.index(event) > events.index(replies[0])]
        responses = {event["message"]["id"]: event for event in events if event["type"] == "receive-response"}
        valid = [responses[event["message"]["id"]] for event in following
                 if "Builder<User>" in json.dumps(responses.get(event["message"]["id"], {}).get("message", {}).get("result"))]
        if (not valid or observed["visibleObservedAt"] < max(event["timestamp"] for event in valid)
            or EditorDelivery.events(observed["visibleSnapshot"]["output"]) != observed["visibleSnapshot"]["events"]
            or events[:len(observed["visibleSnapshot"]["events"])] != observed["visibleSnapshot"]["events"]
            or any(event["message"].get("method") == "textDocument/didSave" for event in events)):
            raise ValueError("following tooltip lacks a subsequent genuine response or the editor saved its probe")
        return {"case": cls.case, "requestId": request_id, "cancelledCode": -32800,
                "followingTooltip": observed["visible"], "visibleObservedAt": observed["visibleObservedAt"]}
