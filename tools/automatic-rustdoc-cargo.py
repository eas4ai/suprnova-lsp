#!/usr/bin/env python3
"""Observe Cargo argv and provide explicitly selected compiler-process controls."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class CargoProbe:
    def __init__(self):
        self.control = Path(os.environ["AUTOMATIC_RUSTDOC_CONTROL"])
        self.events = Path(os.environ["AUTOMATIC_RUSTDOC_EVENTS"])
        self.real_cargo = os.environ["AUTOMATIC_RUSTDOC_REAL_CARGO"]
        self.args = sys.argv[1:]

    def record(self, event, **values):
        record = {"event": event, "monotonicNs": time.monotonic_ns(),
                  "pid": os.getpid(), "platform": sys.platform, "cwd": os.getcwd(),
                  "args": self.args, "toolchain": os.environ.get("RUSTUP_TOOLCHAIN"),
                  "buildDirectory": os.environ.get("CARGO_BUILD_BUILD_DIR"), **values}
        if sys.platform != "win32":
            try:
                record.update(pgid=os.getpgrp(), sessionId=os.getsid(0))
            except AttributeError:
                pass
        with self.events.open("a") as stream:
            stream.write(json.dumps(record) + "\n")

    @staticmethod
    def alive(pid):
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, read-only observation.
            if not handle:
                if ctypes.get_last_error() == 87:  # PID no longer exists.
                    return False
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                state = kernel.WaitForSingleObject(handle, 0)
                if state == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                return state == 258  # WAIT_TIMEOUT means the process has not exited.
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def compiler_command(self, metrics):
        if sys.platform == "win32":
            return [self.real_cargo, *self.args]
        # Reuse the runner's platform-specific time flags rather than assuming GNU time.
        path = Path(__file__).resolve().with_name("agent-debug.py")
        spec = importlib.util.spec_from_file_location("automatic_cargo_measurement", path)
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)
        measured = runner.measurement_command(self.real_cargo, self.args, metrics)
        return [measured.command, *measured.args]

    def run(self):
        if "rustdoc" not in self.args:
            if "metadata" in self.args:
                for flag in ["--locked", "--offline"]:
                    if flag not in self.args:
                        self.args.append(flag)
            os.execv(self.real_cargo, ["cargo", *self.args])

        config = json.loads(self.control.read_text())
        mode = config.get("mode", "real")
        prior = [json.loads(line) for line in self.events.read_text().splitlines()] if self.events.exists() else []
        live = [entry["childPid"] for entry in prior if entry["event"] == "child"
                and self.alive(entry["childPid"])]
        self.record("started", mode=mode, livePriorChildren=live)
        if mode in {"hold", "late-failure"}:
            ready = self.events.parent / f"child-ready-{os.getpid()}"
            child = subprocess.Popen([sys.executable, __file__, "--owned-child", str(ready)])
            deadline = time.monotonic() + 5
            while not ready.exists():
                if child.poll() is not None or time.monotonic() >= deadline:
                    child.kill()
                    child.wait(timeout=5)
                    raise RuntimeError("controlled compiler child failed to become ready")
                time.sleep(0.01)
            if mode == "hold":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            self.record("child", childPid=child.pid)
            if mode == "hold":
                # Both processes resist graceful termination, so killing Cargo alone cannot
                # accidentally satisfy the descendant-cleanup assertion.
                while True:
                    time.sleep(1)
            time.sleep(6)
            child.kill()
            child.wait(timeout=5)
            self.record("finished", code=42)
            return 42
        if mode == "failure":
            print("controlled rustdoc compile failure", file=sys.stderr)
            self.record("finished", code=42)
            return 42
        if mode == "flood":
            for _ in range(128):
                os.write(2, b"compiler diagnostic control\n" * 1024)
            self.record("finished", code=42)
            return 42

        metrics = self.events.parent / f"compiler-{os.getpid()}.time.txt"
        child = subprocess.Popen(self.compiler_command(metrics))
        self.record("compiler", childPid=child.pid)
        code = child.wait()
        exports = []
        mutations = []
        artifact_root = Path(config["artifactRoot"])
        if code == 0:
            for path in artifact_root.rglob("*.json"):
                if path.parent.name != "doc":
                    continue
                data = json.loads(path.read_text())
                if not isinstance(data, dict) or "format_version" not in data:
                    continue
                if mode == "invalid-schema":
                    data["format_version"] = -1
                    path.write_text(json.dumps(data))
                    mutations.append({"path": str(path), "kind": "schema"})
                elif mode == "invalid-reference":
                    if not any(entry["path"] == ["automatic_models", "Post"]
                               for entry in data["paths"].values()):
                        continue
                    owners = [i for i, entry in data["paths"].items()
                              if entry["path"] == ["automatic_models", "Post"]]
                    if len(owners) != 1:
                        raise ValueError("compiler control could not identify Post")
                    owner = data["index"][owners[0]]
                    changed = 0
                    for impl_id in owner["inner"]["struct"]["impls"]:
                        implementation = data["index"][str(impl_id)]["inner"]["impl"]
                        for method_id in implementation["items"]:
                            method = data["index"][str(method_id)]
                            if method.get("name") == "filter":
                                changed += 1
                                method["inner"]["function"]["sig"]["output"] = {
                                    "resolved_path": {"path": "Missing", "id": 4294967295, "args": None}}
                    if changed != 1:
                        raise ValueError(f"reference control changed {changed} methods, expected one")
                    path.write_text(json.dumps(data))
                    mutations.append({"path": str(path), "kind": "reference"})
                exports.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if config.get("releaseFile"):
            self.record("compiled", code=code)
            deadline = time.monotonic() + 30
            while not Path(config["releaseFile"]).exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("controlled compiler release deadline exceeded")
                time.sleep(0.02)
        self.record("finished", code=code, exports=exports, mutations=mutations,
                    metrics=str(metrics) if sys.platform != "win32" else None)
        return code


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--owned-child":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        Path(sys.argv[2]).write_text("ready\n")
        while True:
            time.sleep(1)
    sys.exit(CargoProbe().run())
