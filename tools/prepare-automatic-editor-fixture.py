#!/usr/bin/env python3
"""Prepare the genuine editor fixture; network access requires an explicit fetch option."""

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="resolve and fetch dependencies online before checking offline readiness")
    options = parser.parse_args()
    if sys.platform != "win32":
        runner.install_signal_handlers()
    directory = runner.create_run_directory("automatic-editor-fixture")
    fixture = directory / "automatic-models"
    shutil.copytree(ROOT / "crates/engine/rustdoc/fixtures/automatic-models", fixture)
    environment = dict(os.environ, RUSTUP_TOOLCHAIN="1.98.1", CARGO_NET_OFFLINE="true",
                       CARGO_BUILD_JOBS="2", RUSTUP_AUTO_INSTALL="0",
                       CARGO_TARGET_DIR=str(directory / "metadata-target"))
    real_cargo = shutil.which("cargo")
    if not real_cargo:
        raise ValueError("Cargo executable unavailable")
    # Resolve the directory, preserving Cargo's rustup shim basename for toolchain dispatch.
    real_cargo = str(Path(real_cargo).absolute().parent.resolve() / Path(real_cargo).name)
    commands = []
    phases = [("lock", ["generate-lockfile", *([] if options.fetch else ["--offline"])], options.fetch)]
    if options.fetch:
        phases.append(("fetch", ["fetch", "--locked"], True))
    phases.append(("offline", ["fetch", "--locked", "--offline"], False))
    for label, arguments, online in phases:
        command = runner.CommandSpec(real_cargo, [*arguments, "--manifest-path", str(fixture / "Cargo.toml")])
        phase_environment = dict(environment, CARGO_NET_OFFLINE="false" if online else "true")
        output = directory / label
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            if sys.platform == "win32":
                windows = helpers.module("editor_fixture_windows", ROOT / "tools/editor-fixture-windows.py")
                result = windows.run_supervised(command, fixture, phase_environment, output, 120_000)
            else:
                result = await runner.run_supervised(command, fixture, phase_environment, output, 120_000)
        result["phase"] = label
        commands.append(result)
        runner.write_json(directory / "summary.json", {"commands": commands,
            "processCleanup": runner.summarize_cleanup(commands)})
        if result.get("code") != 0 or not result["cleanup"].get("verifiedEmpty"):
            raise ValueError(f"{label} fixture preparation failed; inspect {output / 'stderr.log'}")
    tools = directory / "proxy-tools"
    tools.mkdir()
    proxy = ROOT / "tools/automatic-rustdoc-cargo.py"
    if sys.platform == "win32":
        # std::process::Command finds cargo.exe; a batch file would bypass the probe.
        rustc = shutil.which("rustc")
        if not rustc:
            raise ValueError("Rust compiler executable unavailable for Cargo proxy preparation")
        command = runner.CommandSpec(rustc, [str(ROOT / "tools/automatic-rustdoc-cargo-shim.rs"),
            "--edition=2024", "--crate-name", "automatic_rustdoc_cargo", "-o", str(tools / "cargo.exe")])
        result = windows.run_supervised(command, fixture, environment, directory / "proxy", 120_000)
        result["phase"] = "proxy"
        commands.append(result)
        runner.write_json(directory / "summary.json", {"commands": commands,
            "processCleanup": runner.summarize_cleanup(commands)})
        if result.get("code") != 0 or not result["cleanup"].get("verifiedEmpty"):
            raise ValueError(f"proxy fixture preparation failed; inspect {directory / 'proxy/stderr.log'}")
    else:
        (tools / "cargo").symlink_to(proxy)
    artifacts = directory / "compiler-artifacts"
    artifacts.mkdir()
    control = directory / "control.json"
    control.write_text(json.dumps({"mode": "real", "artifactRoot": str(artifacts)}))
    print(json.dumps({"SUPRNOVA_LSP_AUTOMATIC_RUSTDOC_FIXTURE": str(fixture),
        "SUPRNOVA_LSP_AUTOMATIC_RUSTDOC_ARTIFACTS": str(artifacts),
        "AUTOMATIC_RUSTDOC_CONTROL": str(control), "AUTOMATIC_RUSTDOC_EVENTS": str(directory / "events.jsonl"),
        "AUTOMATIC_RUSTDOC_PYTHON": str(Path(sys.executable).resolve()), "AUTOMATIC_RUSTDOC_PROXY": str(proxy),
        "AUTOMATIC_RUSTDOC_REAL_CARGO": real_cargo, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "CARGO_TARGET_DIR": str(directory / "consumer-target"), "CARGO_NET_OFFLINE": "true",
        "CARGO_BUILD_JOBS": "2", "RUSTUP_TOOLCHAIN": "nightly-2026-08-19", "RUSTUP_AUTO_INSTALL": "0"}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
