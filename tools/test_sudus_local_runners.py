"""Controls for live registration and smoke evidence; no remote mutations."""

import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("local_runners", Path(__file__).with_name("sudus-local-runners.py"))
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
Acceptance = observer.RunnerAcceptance


class RunnerControls(unittest.TestCase):
    def setUp(self):
        self.runners = [{"name": name, "status": "online", "labels": [{"name": label} for label in observer.LABELS[role]]}
                        for role, name in observer.NAMES.items()]
        self.run = {"id": 123, "status": "completed", "conclusion": "success", "event": "workflow_dispatch",
                    "actor": {"login": "eas4ai"}, "triggering_actor": {"login": "eas4ai"}, "head_sha": "abc"}
        self.jobs = [{"runner_name": name, "conclusion": "success", "labels": observer.LABELS[role],
                      "steps": [{"name": name, "conclusion": "success"} for name in ("Compile and test Rust probe", "Upload runner observation")]}
                     for role, name in observer.NAMES.items()]
        self.artifacts = [{"name": "runner-smoke-" + role, "expired": False} for role in observer.NAMES]
        self.observations = {role: {"runId": "123", "sha": "abc", "runnerName": name, "role": role,
                             "host": observer.TARGETS[role], "toolchain": "1.98.1", "cargoTestExit": 0, "sourceDigest": "digest"}
                             for role, name in observer.NAMES.items()}

    def test_complete_registration_is_accepted(self):
        Acceptance.registrations({"enabled": True}, self.runners)

    def test_disabled_actions_missing_runner_and_wrong_arch_are_rejected(self):
        for settings, runners in [({"enabled": False}, self.runners), ({"enabled": True}, self.runners[:-1])]:
            with self.assertRaises(ValueError):
                Acceptance.registrations(settings, runners)
        self.runners[1]["labels"] = [{"name": "X64"}]
        with self.assertRaises(ValueError):
            Acceptance.registrations({"enabled": True}, self.runners)

    def test_actual_smoke_shape_is_accepted(self):
        Acceptance.smoke(self.run, self.jobs, self.artifacts, self.observations, "digest")

    def test_nonowner_rerun_and_expired_artifact_are_rejected(self):
        self.run["triggering_actor"]["login"] = "outsider"
        with self.assertRaises(ValueError):
            Acceptance.smoke(self.run, self.jobs, self.artifacts, self.observations, "digest")
        self.run["triggering_actor"]["login"] = "eas4ai"
        self.artifacts[0]["expired"] = True
        with self.assertRaises(ValueError):
            Acceptance.smoke(self.run, self.jobs, self.artifacts, self.observations, "digest")

    def test_failed_or_skipped_probe_and_stale_source_are_rejected(self):
        self.jobs[0]["steps"][0]["conclusion"] = "skipped"
        with self.assertRaises(ValueError):
            Acceptance.smoke(self.run, self.jobs, self.artifacts, self.observations, "digest")
        self.jobs[0]["steps"][0]["conclusion"] = "success"
        with self.assertRaises(ValueError):
            Acceptance.smoke(self.run, self.jobs, self.artifacts, self.observations, "changed")

    def test_artifact_cannot_claim_another_host_or_run(self):
        for key, value in [("host", "x86_64-apple-darwin"), ("sha", "old"), ("runId", "456"), ("cargoTestExit", 1)]:
            observations = copy.deepcopy(self.observations)
            observations["macos"][key] = value
            with self.assertRaises(ValueError):
                Acceptance.smoke(self.run, self.jobs, self.artifacts, observations, "digest")

    def test_pr_and_nonowner_routing_controls_are_rejected(self):
        workflows = Acceptance.workflows(observer.ROOT)
        Acceptance.routing(workflows)
        for guard in ("github.event_name == 'push' || github.event_name == 'workflow_dispatch'", "github.triggering_actor == github.repository_owner", "github.actor == github.repository_owner"):
            changed = copy.deepcopy(workflows)
            expression = changed["ci.yml"]["jobs"]["tests"]["runs-on"]
            changed["ci.yml"]["jobs"]["tests"]["runs-on"] = expression.replace(guard, "true")
            with self.assertRaises(ValueError):
                Acceptance.routing(changed)

    def test_unsupported_release_architecture_and_unreviewed_job_are_rejected(self):
        workflows = Acceptance.workflows(observer.ROOT)
        changed = copy.deepcopy(workflows)
        matrix = changed["github-release.yml"]["jobs"]["packages"]["strategy"]["matrix"]["include"]
        next(row for row in matrix if row["vscode_target"] == "darwin-x64")["local_runner"] = '["self-hosted","macOS","ARM64","rust-ci"]'
        with self.assertRaises(ValueError):
            Acceptance.routing(changed)
        changed = copy.deepcopy(workflows)
        changed["ci.yml"]["jobs"]["unexpected"] = {"runs-on": ["self-hosted", "Linux", "X64", "rust-ci"]}
        with self.assertRaises(ValueError):
            Acceptance.routing(changed)


if __name__ == "__main__":
    unittest.main()
