#!/usr/bin/env python3
"""Check explicit network preparation and platform-specific Cargo controls."""

import asyncio
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "target/agent-debug/release-readiness"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
# Select the native event loop before dispatch tests override sys.platform.
asyncio.run(asyncio.sleep(0))


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


prepare = module("fixture_prepare_test", "prepare-automatic-editor-fixture.py")
proxy = module("fixture_proxy_test", "automatic-rustdoc-cargo.py")
windows = module("fixture_windows_test", "editor-fixture-windows.py")


class Preparation(unittest.TestCase):
    def observe(self, arguments, failure=None):
        with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
            directory = Path(temporary)
            commands = []

            async def supervised(spec, cwd, environment, output, timeout):
                commands.append((spec.args, environment.copy()))
                return {"code": failure if failure is not None else 0,
                        "cleanup": {"verifiedEmpty": True}}

            output = io.StringIO()
            with mock.patch.object(sys, "argv", ["prepare", *arguments]), \
                    mock.patch.object(sys, "platform", "linux"), \
                    mock.patch.object(prepare.shutil, "which", return_value=sys.executable), \
                    mock.patch.object(prepare.runner, "create_run_directory", return_value=directory), \
                    mock.patch.object(prepare.runner, "run_supervised", side_effect=supervised), \
                    mock.patch.object(prepare.runner, "install_signal_handlers"), \
                    mock.patch.object(Path, "symlink_to"), \
                    contextlib.redirect_stdout(output):
                if failure is not None:
                    with self.assertRaisesRegex(ValueError, "preparation failed"):
                        asyncio.run(prepare.main())
                    self.assertEqual(output.getvalue(), "")
                    return commands, None
                asyncio.run(prepare.main())
            return commands, json.loads(output.getvalue())

    def test_fetch_is_explicit_and_returns_offline_environment(self):
        commands, environment = self.observe(["--fetch"])
        self.assertEqual(commands[0][1]["CARGO_NET_OFFLINE"], "false")
        self.assertNotIn("--offline", commands[0][0])
        self.assertEqual(commands[1][0][:2], ["fetch", "--locked"])
        self.assertEqual(commands[-1][0][:3], ["fetch", "--locked", "--offline"])
        self.assertEqual(environment["CARGO_NET_OFFLINE"], "true")
        self.assertEqual(environment["RUSTUP_TOOLCHAIN"], "nightly-2026-08-19")

    def test_default_never_enables_network(self):
        commands, environment = self.observe([])
        self.assertTrue(all(env["CARGO_NET_OFFLINE"] == "true" for _, env in commands))
        self.assertTrue(all("--offline" in args for args, _ in commands))
        self.assertEqual(environment["CARGO_NET_OFFLINE"], "true")

    def test_failure_does_not_publish_a_fixture(self):
        commands, _ = self.observe(["--fetch"], failure=42)
        self.assertEqual(len(commands), 1)

    def test_unknown_option_is_rejected_before_creating_artifacts(self):
        with mock.patch.object(sys, "argv", ["prepare", "--typo"]), \
                mock.patch.object(prepare.runner, "create_run_directory") as create, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                asyncio.run(prepare.main())
            self.assertEqual(result.exception.code, 2)
            create.assert_not_called()

    def test_windows_dispatch_owns_commands_and_builds_discoverable_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            supervisor = mock.Mock(return_value={"code": 0, "cleanup": {"verifiedEmpty": True}})
            output = io.StringIO()
            with mock.patch.object(sys, "argv", ["prepare"]), \
                    mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.object(prepare.shutil, "which", return_value=sys.executable), \
                    mock.patch.object(prepare.runner, "create_run_directory", return_value=directory), \
                    mock.patch.object(prepare.helpers, "module", return_value=SimpleNamespace(run_supervised=supervisor)), \
                    mock.patch.object(prepare.runner, "install_signal_handlers") as signals, \
                    contextlib.redirect_stdout(output):
                asyncio.run(prepare.main())
            self.assertEqual(supervisor.call_count, 3)
            signals.assert_not_called()
            compile_command = supervisor.call_args.args[0]
            self.assertIn("automatic-rustdoc-cargo-shim.rs", " ".join(compile_command.args))
            self.assertEqual(compile_command.args[-1], str(directory / "proxy-tools/cargo.exe"))
            self.assertEqual(supervisor.call_args.args[2]["RUSTUP_TOOLCHAIN"], "1.98.1")
            environment = json.loads(output.getvalue())
            self.assertTrue(environment["PATH"].startswith(str(directory / "proxy-tools") + os.pathsep))
            self.assertTrue(Path(environment["AUTOMATIC_RUSTDOC_REAL_CARGO"]).is_absolute())

    def test_unverified_cleanup_does_not_publish_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(sys, "argv", ["prepare"]), \
                    mock.patch.object(sys, "platform", "linux"), \
                    mock.patch.object(prepare.shutil, "which", return_value=sys.executable), \
                    mock.patch.object(prepare.runner, "create_run_directory", return_value=Path(temporary)), \
                    mock.patch.object(prepare.runner, "install_signal_handlers"), \
                    mock.patch.object(prepare.runner, "run_supervised", new=mock.AsyncMock(return_value={
                        "code": 0, "cleanup": {"verifiedEmpty": False}})), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(ValueError, "preparation failed"):
                    asyncio.run(prepare.main())
                self.assertEqual(output.getvalue(), "")


class Proxy(unittest.TestCase):
    def test_rust_command_discovers_proxy_and_preserves_native_arguments(self):
        with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
            root = Path(temporary)
            tools = root / "proxy-tools"
            tools.mkdir()
            extension = ".exe" if sys.platform == "win32" else ""
            driver_source = root / "driver.rs"
            driver_source.write_text('fn main() { let status = std::process::Command::new("cargo")'
                '.args(std::env::args_os().skip(1)).status().unwrap(); '
                'std::process::exit(status.code().unwrap_or(1)); }\n')
            environment = dict(os.environ, RUSTUP_TOOLCHAIN="1.98.1", RUSTUP_AUTO_INSTALL="0",
                               CARGO_NET_OFFLINE="true")

            def supervised(command, args, label, env=environment):
                spec = prepare.runner.CommandSpec(command, args)
                if sys.platform == "win32":
                    return windows.run_supervised(spec, root, env, root / label, 120_000)
                return asyncio.run(prepare.runner.run_supervised(spec, root, env, root / label, 120_000))

            for source, executable, label in [
                    (ROOT / "tools/automatic-rustdoc-cargo-shim.rs", tools / ("cargo" + extension), "shim"),
                    (driver_source, root / ("driver" + extension), "compile-driver")]:
                result = supervised("rustc", [str(source), "--edition=2024", "-o", str(executable)], label)
                self.assertEqual(result.get("code"), 0, result)
                self.assertTrue(result["cleanup"]["verifiedEmpty"])
            control = root / "control.json"
            events = root / "events.jsonl"
            control.write_text(json.dumps({"mode": "real", "artifactRoot": str(root / "artifacts")}))
            environment.update(PATH=str(tools) + os.pathsep + os.environ["PATH"],
                AUTOMATIC_RUSTDOC_PYTHON=sys.executable,
                AUTOMATIC_RUSTDOC_PROXY=str(ROOT / "tools/automatic-rustdoc-cargo.py"),
                AUTOMATIC_RUSTDOC_REAL_CARGO=sys.executable,
                AUTOMATIC_RUSTDOC_CONTROL=str(control), AUTOMATIC_RUSTDOC_EVENTS=str(events))
            observed = root / "arguments.json"
            arguments = ["rustdoc", "a spaced path", "quoted\"argument", "percent%bang!amp&pipe|",
                         "trailing\\", "", "unicode-λ"]
            script = "import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))"
            result = supervised(str(root / ("driver" + extension)),
                                ["-c", script, str(observed), *arguments], "forward", environment)
            self.assertEqual(result.get("code"), 0, result)
            self.assertTrue(result["cleanup"]["verifiedEmpty"])
            self.assertEqual(json.loads(observed.read_text()), arguments)
            self.assertEqual(json.loads(events.read_text().splitlines()[-1])["event"], "finished")
            control.write_text(json.dumps({"mode": "failure"}))
            result = supervised(str(root / ("driver" + extension)), ["rustdoc"], "failure", environment)
            self.assertEqual(result.get("code"), 42, result)
            self.assertTrue(result["cleanup"]["verifiedEmpty"])

    def test_record_does_not_require_posix_process_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.dict(os.environ, {"AUTOMATIC_RUSTDOC_CONTROL": str(root / "control"),
                    "AUTOMATIC_RUSTDOC_EVENTS": str(root / "events"),
                    "AUTOMATIC_RUSTDOC_REAL_CARGO": sys.executable}), \
                    mock.patch.object(sys, "argv", ["cargo", "rustdoc"]), \
                    mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.object(proxy.os, "getpgrp", create=True, side_effect=AttributeError("Windows")):
                proxy.CargoProbe().record("started")
            event = json.loads((root / "events").read_text())
            self.assertEqual(event["pid"], os.getpid())
            self.assertIn("platform", event)

    def test_measurement_dispatch_and_exact_cargo_arguments(self):
        value = proxy.CargoProbe.__new__(proxy.CargoProbe)
        value.real_cargo = "C:/Program Files/Rust/cargo.exe"
        value.args = ["rustdoc", "--manifest-path", "a spaced path/Cargo.toml", "--", "-Zunstable-options"]
        for platform, flags in [("darwin", ["-l", "-p"]), ("linux", ["-v"]), ("win32", [])]:
            with self.subTest(platform=platform), mock.patch.object(sys, "platform", platform), \
                    mock.patch.object(Path, "exists", return_value=True):
                command = value.compiler_command(Path("metrics"))
                if platform == "win32":
                    self.assertEqual(command, [value.real_cargo, *value.args])
                else:
                    self.assertEqual(command[:1 + len(flags)], [str(Path("/usr/bin/time")), *flags])
                    self.assertEqual(command[-1 - len(value.args):], [value.real_cargo, *value.args])

    def test_prior_child_liveness_is_observed_without_procfs(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        try:
            self.assertTrue(proxy.CargoProbe.alive(child.pid))
        finally:
            child.kill()
            child.wait(timeout=5)
        self.assertFalse(proxy.CargoProbe.alive(child.pid))

    def test_real_proxy_launch_preserves_arguments_and_records_compiler_outcome(self):
        with tempfile.TemporaryDirectory(dir=ARTIFACTS) as temporary:
            root = Path(temporary)
            artifact = root / "artifacts/doc/model.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(json.dumps({"format_version": 1}))
            control = root / "control.json"
            control.write_text(json.dumps({"mode": "real", "artifactRoot": str(root / "artifacts")}))
            observed = root / "arguments.json"
            arguments = ["rustdoc", "a spaced path", "trailing\\"]
            script = "import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))"
            environment = dict(os.environ, AUTOMATIC_RUSTDOC_CONTROL=str(control),
                AUTOMATIC_RUSTDOC_EVENTS=str(root / "events.jsonl"), AUTOMATIC_RUSTDOC_REAL_CARGO=sys.executable)
            result = subprocess.run([sys.executable, str(ROOT / "tools/automatic-rustdoc-cargo.py"),
                "-c", script, str(observed), *arguments], env=environment, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(observed.read_text()), arguments)
            event = json.loads((root / "events.jsonl").read_text().splitlines()[-1])
            self.assertEqual(event["code"], 0)
            self.assertEqual(event["exports"][0]["path"], str(artifact))
            if sys.platform == "win32":
                self.assertIsNone(event["metrics"])
            else:
                self.assertTrue(Path(event["metrics"]).is_file())


class WindowsSupervision(unittest.TestCase):
    def test_unverified_job_drain_remains_unverified_and_closes_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            child = mock.Mock(pid=123)
            child.wait.return_value = 0
            job = mock.Mock()
            job.spawn.return_value = child
            job.drain.side_effect = OSError("accounting unavailable")
            with mock.patch.object(windows, "WindowsJob", return_value=job):
                result = windows.run_supervised(SimpleNamespace(command="cargo", args=["fetch", "--locked"]),
                                                Path(temporary), {}, Path(temporary), 1000)
            self.assertFalse(result["cleanup"]["verifiedEmpty"])
            self.assertIn("accounting unavailable", result["cleanup"]["error"])
            job.close.assert_called_once()

    @unittest.skipUnless(sys.platform == "win32", "requires native Windows Job APIs")
    def test_native_job_success_failure_timeout_and_descendant_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for code in [0, 42]:
                result = windows.run_supervised(SimpleNamespace(command=sys.executable,
                    args=["-c", f"raise SystemExit({code})"]), root, dict(os.environ), root / str(code), 5000)
                self.assertEqual(result["code"], code)
                self.assertTrue(result["cleanup"]["verifiedEmpty"])
            ready = root / "descendant.json"
            script = (
                "import subprocess,sys,time,json,pathlib; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "pathlib.Path(sys.argv[1]).write_text(json.dumps(child.pid)); time.sleep(60)"
            )
            result = windows.run_supervised(SimpleNamespace(command=sys.executable,
                args=["-c", script, str(ready)]), root, dict(os.environ), root / "timeout", 2000)
            self.assertTrue(result["timedOut"])
            self.assertTrue(result["cleanup"]["verifiedEmpty"])
            self.assertFalse(proxy.CargoProbe.alive(json.loads(ready.read_text())))
            result = windows.run_supervised(SimpleNamespace(command=str(root / "missing.exe"), args=[]),
                                            root, dict(os.environ), root / "missing", 5000)
            self.assertIn("spawnError", result)
            self.assertTrue(result["cleanup"]["verifiedEmpty"])
            control = root / "control.json"
            events = root / "events.jsonl"
            control.write_text(json.dumps({"mode": "hold"}))
            environment = dict(os.environ, AUTOMATIC_RUSTDOC_CONTROL=str(control),
                AUTOMATIC_RUSTDOC_EVENTS=str(events), AUTOMATIC_RUSTDOC_REAL_CARGO=sys.executable)
            result = windows.run_supervised(SimpleNamespace(command=sys.executable,
                args=[str(ROOT / "tools/automatic-rustdoc-cargo.py"), "rustdoc"]),
                root, environment, root / "held-proxy", 5000)
            self.assertTrue(result["timedOut"])
            self.assertTrue(result["cleanup"]["verifiedEmpty"])
            child_events = [json.loads(line) for line in events.read_text().splitlines()
                            if json.loads(line)["event"] == "child"]
            self.assertEqual(len(child_events), 1)
            self.assertFalse(proxy.CargoProbe.alive(child_events[0]["pid"]))
            self.assertFalse(proxy.CargoProbe.alive(child_events[0]["childPid"]))


if __name__ == "__main__":
    unittest.main()
