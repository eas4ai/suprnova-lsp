"""Require every selected native invariant to start and reach an actual terminal outcome."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rsp_native_test", ROOT / "tools/responsiveness-native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeEvidenceIntegrity(unittest.TestCase):
    def setUp(self):
        suites, self.events = {}, []
        for groups in native.NativeEvidence.cases.values():
            for package, cases in groups.items():
                suite = suites.setdefault(package, {"package-name": package, "binary-id": package,
                                                   "kind": "lib", "testcases": {}})
                for case in cases:
                    suite["testcases"][case] = {"ignored": False, "filter-match": {"status": "matches"}}
                    self.events.extend({"type": "test", "name": package + "::" + package + "$" + case,
                                        "event": state} for state in ("started", "ok"))
        self.discovery = {"rust-suites": suites}

    def assess(self, discovery, events, code=0):
        return native.NativeEvidence.assess(json.dumps(discovery), "\n".join(json.dumps(event) for event in events), code)

    def test_preserves_each_case_and_failed_outcome(self):
        observed = self.assess(self.discovery, self.events)
        self.assertEqual(sum(len(cases) for cases in observed.values()), 45)
        self.assertTrue(all(result for cases in observed.values() for result in cases.values()))
        self.events[1]["event"] = "failed"
        failed = self.assess(self.discovery, self.events, 100)
        self.assertEqual(sum(not result for cases in failed.values() for result in cases.values()), 1)
        with self.assertRaisesRegex(ValueError, "exit status"):
            self.assess(self.discovery, self.events)

    def test_missing_filtered_ignored_duplicate_or_unfinished_case_is_not_a_pass(self):
        for change in ("missing", "filtered", "ignored", "unfinished", "duplicate"):
            discovery, events = copy.deepcopy(self.discovery), copy.deepcopy(self.events)
            first = next(iter(discovery["rust-suites"].values()))["testcases"]
            case = next(iter(first))
            if change == "missing":
                del first[case]
            elif change == "filtered":
                first[case]["filter-match"]["status"] = "mismatch"
            elif change == "ignored":
                first[case]["ignored"] = True
            elif change == "unfinished":
                events.pop()
            else:
                events.append(events[0])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.assess(discovery, events)


if __name__ == "__main__":
    unittest.main()
