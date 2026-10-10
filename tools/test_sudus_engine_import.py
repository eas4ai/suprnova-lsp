"""Check that incomplete or misleading observations cannot produce passing receipts."""

import importlib.util
import json
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("engine_import", Path(__file__).with_name("sudus-engine-import.py"))
mechanism = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mechanism)


class MechanismIntegrity(unittest.TestCase):
    def test_discovery_rejects_missing_and_ignored_cases(self):
        cases = {mechanism.PREFIX + case: {"ignored": False, "filter-match": {"status": "matches"}}
                 for names in mechanism.CASES.values() for case in names}
        suite = {"package-name": "rg_project", "kind": "lib", "binary-id": "rg_project", "testcases": cases}
        data = {"rust-suites": {"rg_project": suite}}
        self.assertEqual(mechanism.discover(json.dumps(data)), "rg_project")
        case = next(iter(cases))
        cases[case]["ignored"] = True
        with self.assertRaises(ValueError):
            mechanism.discover(json.dumps(data))
        del cases[case]
        with self.assertRaises(ValueError):
            mechanism.discover(json.dumps(data))

    def test_results_require_exact_start_completion_and_matching_exit(self):
        case = "mac_001_initial_query"
        name = f"rg_project::rg_project${mechanism.PREFIX}{case}"
        start = json.dumps({"type": "test", "event": "started", "name": name})
        finish = json.dumps({"type": "test", "event": "ok", "name": name})
        text = start + "\n" + finish
        self.assertEqual(mechanism.outcomes(text, "rg_project", [case], 0), {case: True})
        for invalid, code in [(finish, 0), (text + "\n" + finish, 0), (text, 100), ("", 0)]:
            with self.assertRaises(ValueError):
                mechanism.outcomes(invalid, "rg_project", [case], code)

    def test_failed_and_multiline_tracer_results_remain_failures(self):
        case = "mac_006_query_without_compiler_servers"
        name = f"rg_project::rg_project${mechanism.PREFIX}{case}"
        text = json.dumps({"type": "test", "event": "started", "name": name}) + "\n"
        # Match Nextest's literal newline inside its tracer-output JSON string.
        text += json.dumps({"type": "test", "event": "failed", "name": name, "stdout": "not captured\n"}).replace("\\n", "\n")
        self.assertEqual(mechanism.outcomes(text, "rg_project", [case], 100), {case: False})

    def test_trace_rejects_absent_observation_and_detects_absolute_and_proxy_launches(self):
        harmless = '100 execve("/tmp/query", ["query"], 0x1) = 0\n'
        self.assertEqual(mechanism.forbidden_execs(harmless), [])
        for launch in ['execve("/opt/bin/rustdoc", ["rustdoc"], 0x1) = 0',
                       'execveat(AT_FDCWD, "rust-analyzer", [], 0x1, 0) = 0',
                       'execve("/opt/bin/rustup", ["rustup", "run", "nightly", "rustdoc"], 0x1) = 0']:
            self.assertEqual(mechanism.forbidden_execs(harmless + launch), [launch])
        with self.assertRaises(ValueError):
            mechanism.forbidden_execs("")


if __name__ == "__main__":
    unittest.main()
