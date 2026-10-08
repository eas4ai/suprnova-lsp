"""Reject editor evidence with stale replies, missing overlap, or skipped delivery."""

import importlib.util
import json
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("delivery_test", Path(__file__).with_name("responsiveness-delivery.py"))
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


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
        events = [{"isLSPMessage": True, **event} for event in self.events]
        self.observed["final"] = {"output": "\n".join(json.dumps(event, separators=(",", ":")) for event in events), "events": events}
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


if __name__ == "__main__":
    unittest.main()
