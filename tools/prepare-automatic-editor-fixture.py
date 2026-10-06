#!/usr/bin/env python3
"""Prepare the genuine editor fixture with owned artifacts and supervised offline resolution."""

import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("editor_fixture_helpers", ROOT / "tools/sudus-editor-import.py")
helpers = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helpers
spec.loader.exec_module(helpers)
runner = helpers.module("editor_fixture_runner", ROOT / "tools/agent-debug.py")


async def main():
    runner.install_signal_handlers()
    directory = runner.create_run_directory("automatic-editor-fixture")
    fixture = directory / "automatic-models"
    shutil.copytree(ROOT / "crates/engine/rustdoc/fixtures/automatic-models", fixture)
    environment = dict(os.environ, RUSTUP_TOOLCHAIN="1.98.1", CARGO_NET_OFFLINE="true",
                       CARGO_BUILD_JOBS="2", RUSTUP_AUTO_INSTALL="0",
                       CARGO_TARGET_DIR=str(directory / "metadata-target"))
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        result = await runner.run_supervised(runner.CommandSpec("cargo", ["generate-lockfile", "--offline",
            "--manifest-path", str(fixture / "Cargo.toml")]), fixture, environment, directory / "lock", 120_000)
    runner.write_json(directory / "summary.json", {"commands": [result], "processCleanup": runner.summarize_cleanup([result])})
    if result.get("code") != 0 or not result["cleanup"].get("verifiedEmpty"):
        raise ValueError(f"offline fixture preparation failed; inspect {directory / 'lock/stderr.log'}")
    real_cargo = shutil.which("cargo")
    if not real_cargo:
        raise ValueError("Cargo executable unavailable")
    tools = directory / "proxy-tools"
    tools.mkdir()
    (tools / "cargo").symlink_to(ROOT / "tools/automatic-rustdoc-cargo.py")
    artifacts = directory / "compiler-artifacts"
    artifacts.mkdir()
    control = directory / "control.json"
    control.write_text(json.dumps({"mode": "real", "artifactRoot": str(artifacts)}))
    print(json.dumps({"RUST_GLANCER_AUTOMATIC_RUSTDOC_FIXTURE": str(fixture),
        "RUST_GLANCER_AUTOMATIC_RUSTDOC_ARTIFACTS": str(artifacts),
        "AUTOMATIC_RUSTDOC_CONTROL": str(control), "AUTOMATIC_RUSTDOC_EVENTS": str(directory / "events.jsonl"),
        "AUTOMATIC_RUSTDOC_REAL_CARGO": real_cargo, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "CARGO_TARGET_DIR": str(directory / "consumer-target"), "CARGO_NET_OFFLINE": "true",
        "CARGO_BUILD_JOBS": "2", "RUSTUP_TOOLCHAIN": "nightly-2026-08-19", "RUSTUP_AUTO_INSTALL": "0"}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
