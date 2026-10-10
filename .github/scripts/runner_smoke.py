#!/usr/bin/env python3
"""Compile a dependency-free probe and record the actual runner's identity."""

import hashlib
import json
import os
from pathlib import Path
import subprocess


SOURCES = [".github/workflows/ci.yml", ".github/workflows/build-server.yml",
           ".github/workflows/platform-checks.yml", ".github/workflows/github-release.yml",
           ".github/workflows/runner-smoke.yml", ".github/actions/setup-rust/action.yml",
           ".github/actions/cargo-cache/action.yml", ".github/scripts/runner_smoke.py"]


def main():
    toolchain = os.environ["RUSTUP_TOOLCHAIN"]
    role = os.environ["SMOKE_ROLE"]
    expected = {"linux": "x86_64-unknown-linux-gnu", "macos": "aarch64-apple-darwin", "windows": "x86_64-pc-windows-msvc"}
    assert toolchain == "1.98.1" and role in expected, "Unexpected smoke configuration"
    rustc = subprocess.check_output(["rustc", "+" + toolchain, "-vV"], text=True, timeout=30)
    host = next(line.removeprefix("host: ") for line in rustc.splitlines() if line.startswith("host: "))
    assert host == expected[role], "Compiler host differs from expected runner architecture"
    probe = Path(os.environ["RUNNER_WORKSPACE"]) / "suprnova-lsp-runner-probe"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "src").mkdir(exist_ok=True)
    (probe / "Cargo.toml").write_text('[package]\nname = "suprnova-runner-probe"\nversion = "0.0.0"\nedition = "2024"\n')
    (probe / "src/lib.rs").write_text('#[test]\nfn rust_probe_runs() { assert_eq!(2 + 2, 4); }\n')
    result = subprocess.run(["cargo", "+" + toolchain, "test", "--offline", "--manifest-path", str(probe / "Cargo.toml"),
                             "--target-dir", str(probe / "target")], timeout=180)
    if result.returncode:
        raise RuntimeError("Rust probe tests failed")
    # Resolve this repository's manifest as well; no application dependencies are compiled.
    subprocess.run(["cargo", "+" + toolchain, "metadata", "--locked", "--offline", "--no-deps", "--format-version", "1"],
                   stdout=subprocess.DEVNULL, check=True, timeout=30)
    digest = hashlib.sha256(json.dumps({name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                            for name in SOURCES}, sort_keys=True).encode()).hexdigest()
    observation = {"role": role, "host": host, "runnerName": os.environ["RUNNER_NAME"],
                   "runId": os.environ["GITHUB_RUN_ID"], "sha": os.environ["GITHUB_SHA"],
                   "toolchain": toolchain, "cargoTestExit": result.returncode, "sourceDigest": digest,
                   "cargoBuildJobs": os.environ.get("CARGO_BUILD_JOBS"), "cargoHomeConfigured": bool(os.environ.get("CARGO_HOME"))}
    output = Path(".ci/runner-smoke") / (role + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(observation, indent=2) + "\n")
    print(json.dumps(observation))


if __name__ == "__main__":
    main()
