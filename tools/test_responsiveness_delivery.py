"""Reject editor evidence with stale replies, missing overlap, or skipped delivery."""

import importlib.util
import json
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("delivery_test", Path(__file__).with_name("responsiveness-delivery.py"))
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def trace_snapshot(events):
    recorded = [{"isLSPMessage": True, **event} for event in events]
    return {"output": "\n".join(json.dumps(event, separators=(",", ":")) for event in recorded), "events": recorded}


class EditorDeliveryIntegrity(unittest.TestCase):
    def setUp(self):
        self.root = "/recorded/devlist.app"
        self.uri = "file:///recorded/devlist.app/src/models/user.rs"
        self.events = [
            {"type": "send-request", "timestamp": 1, "message": {"id": 0, "method": "initialize"}},
            {"type": "receive-notification", "timestamp": 10, "message": {"method": "suprnova-lsp/deferredIndexingStarted", "params": {"root": self.root}}},
            {"type": "send-request", "timestamp": 20, "message": {"id": 1, "method": "textDocument/hover", "params": {"textDocument": {"uri": self.uri}}}},
            {"type": "receive-response", "timestamp": 23, "message": {"id": 1, "result": {"contents": "Builder<User>"}}},
            {"type": "send-request", "timestamp": 25, "message": {"id": 2, "method": "textDocument/hover", "params": {"textDocument": {"uri": self.uri}}}},
            {"type": "send-notification", "timestamp": 30, "message": {"method": "textDocument/didChange", "params": {"textDocument": {"uri": self.uri, "version": 3}}}},
            {"type": "send-request", "timestamp": 31, "message": {"id": 3, "method": "textDocument/hover", "params": {"textDocument": {"uri": self.uri}}}},
            {"type": "receive-response", "timestamp": 32, "message": {"id": 2, "error": {"code": -32801}}},
            {"type": "receive-response", "timestamp": 35, "message": {"id": 3, "result": {"contents": "u32"}}},
        ]
        self.terminal = {"stats": {"tests": 1, "passes": 1, "pending": 0, "failures": 0},
                         "tests": [{"fullTitle": delivery.EditorDelivery.case}],
                         "passes": [{"fullTitle": delivery.EditorDelivery.case}], "pending": [], "failures": []}
        self.observed = {"application": self.root, "generated": {"text": "Builder<User>"},
                         "beforeGenerated": {"output": "configured rustdoc declarations published generation=1 root=" + self.root + "\ndeferred indexing background finish started generation=1\neditor document analysis route published ready=true path=" + self.root + "/src/models/user.rs"},
                         "edit": {"requestId": 2, "oldVersion": 2, "newVersion": 3, "oldOutcome": {"text": ""}, "newText": "u32"}}

    def assess(self):
        self.observed["final"] = trace_snapshot(self.events)
        return delivery.EditorDelivery.assess(json.dumps(self.terminal), json.dumps(self.observed), self.root)

    def test_preserves_actual_unsaved_generated_and_new_revision_results(self):
        self.assertEqual(self.assess()["overtakenRequestId"], 2)

    def test_rejects_success_after_newer_edit_wrong_version_or_missing_overlap(self):
        for violation in ("success", "late-edit", "wrong-version", "finished", "missing-readiness", "skipped", "old-provider"):
            self.setUp()
            if violation == "success":
                self.events[7]["message"] = {"id": 2, "result": "Builder<User>"}
            elif violation == "late-edit":
                self.events[5], self.events[7] = self.events[7], self.events[5]
            elif violation == "wrong-version":
                self.events[5]["message"]["params"]["textDocument"]["version"] = 2
            elif violation == "finished":
                self.events.insert(2, {"type": "receive-notification", "timestamp": 11, "message": {"method": "suprnova-lsp/deferredIndexingFinished", "params": {"root": self.root}}})
            elif violation == "missing-readiness":
                self.observed["beforeGenerated"]["output"] = ""
            elif violation == "skipped":
                self.terminal["stats"]["pending"] = 1
            else:
                self.observed["edit"]["oldOutcome"]["text"] = "Builder<User>"
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                self.assess()


class EditorCancellationIntegrity(unittest.TestCase):
    def setUp(self):
        self.root = "/recorded/devlist.app"
        uri = self.root + "/src/models/user.rs"
        self.events = [
            {"type": "send-request", "timestamp": 1, "message": {"method": "initialize", "id": 0}},
            {"type": "send-request", "timestamp": 2, "message": {"method": "textDocument/hover", "id": 1, "params": {"textDocument": {"uri": "file://" + uri}}}},
            {"type": "send-notification", "timestamp": 3, "message": {"method": "$/cancelRequest", "params": {"id": 1}}},
            {"type": "receive-response", "timestamp": 4, "message": {"id": 1, "error": {"code": -32800}}},
            {"type": "send-request", "timestamp": 5, "message": {"method": "textDocument/hover", "id": 2, "params": {"textDocument": {"uri": "file://" + uri}}}},
            {"type": "receive-response", "timestamp": 6, "message": {"id": 2, "result": {"contents": "Builder<User>"}}},
        ]
        self.observed = {"application": self.root, "requestId": 1, "hidden": {"visible": False},
                         "following": {"text": "Builder<User>"}, "visible": {"visible": True, "text": "Builder<User>"}, "visibleObservedAt": 7}
        self.terminal = {"stats": {"tests": 1, "passes": 1, "pending": 0, "failures": 0},
                         "tests": [{"fullTitle": delivery.EditorCancellation.case}],
                         "passes": [{"fullTitle": delivery.EditorCancellation.case}], "pending": [], "failures": []}

    def assess(self):
        self.observed.update(final=trace_snapshot(self.events), visibleSnapshot=trace_snapshot(self.events))
        return delivery.EditorCancellation.assess(json.dumps(self.terminal), json.dumps(self.observed), self.root)

    def test_preserves_real_cancellation_and_following_render(self):
        self.assertEqual(self.assess()["cancelledCode"], -32800)

    def test_rejects_unmatched_cancel_obsolete_success_or_unproved_following_tooltip(self):
        for violation in ("wrong-id", "success", "late-cancel", "no-following", "wrong-type", "premature-render", "saved"):
            self.setUp()
            if violation == "wrong-id":
                self.events[2]["message"]["params"]["id"] = 3
            elif violation == "success":
                self.events[3]["message"] = {"id": 1, "result": "Builder<User>"}
            elif violation == "late-cancel":
                self.events[2], self.events[3] = self.events[3], self.events[2]
            elif violation == "no-following":
                self.events = self.events[:4]
            elif violation == "wrong-type":
                self.events[5]["message"]["result"] = "Builder<Wrong>"
            elif violation == "premature-render":
                self.observed["visibleObservedAt"] = 5
            else:
                self.events.append({"type": "send-notification", "timestamp": 8, "message": {"method": "textDocument/didSave"}})
            with self.subTest(violation=violation), self.assertRaises(ValueError):
                self.assess()


if __name__ == "__main__":
    unittest.main()
