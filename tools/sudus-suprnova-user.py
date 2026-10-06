#!/usr/bin/env python3
"""Observe the captured Devlist User contract with bounded application processes."""

import asyncio
import contextlib
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "crates/engine/rustdoc/fixtures/devlist-user"
APP = Path("/home/shawn/workspace2/devlist.app")
PROTECTED = Path("/home/shawn/workspace2/suprnova")
TOOLCHAIN = "nightly-2026-08-19"
TARGET = "x86_64-unknown-linux-gnu"
REVISION = "3229aa9af542c991196274fa3c235cdce88a68e2"
WRONG_TARGET_TEST = "indexing::compiler::tests::sup_004_rejects_wrong_application_target"


def producer_configuration():
    """Hash compiler overrides and Cargo configuration without storing credential-bearing values."""
    names = ["CARGO_BUILD_TARGET", "CARGO_ENCODED_RUSTFLAGS", "RUSTFLAGS", "RUSTDOCFLAGS",
             "CARGO_ENCODED_RUSTDOCFLAGS", "RUSTC", "RUSTDOC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER"]
    environment = {name: digest(os.environ[name].encode()) if name in os.environ else None for name in names}
    candidates = {base / ".cargo" / name for base in [ROOT, *ROOT.parents, APP]
                  for name in ["config", "config.toml"]}
    cargo_home = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")))
    candidates.update(cargo_home / name for name in ["config", "config.toml"])
    files = {str(path): digest(path.read_bytes()) for path in sorted(candidates) if path.is_file()}
    return {"environment": environment, "cargoConfig": files}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_inventory(metadata, sysroot):
    """Record Rust and Cargo source inputs; never inspect application secrets or runtime data."""
    roots = {Path(package["manifest_path"]).parent.resolve() for package in metadata["packages"]}
    roots.add(Path(sysroot).resolve())
    files = {}
    for root in sorted(roots):
        if root == PROTECTED or PROTECTED in root.parents:
            raise ValueError("metadata reached the protected framework checkout")
        for directory, children, names in os.walk(root, followlinks=False):
            children[:] = sorted(name for name in children if name not in {".git", "target", "node_modules"})
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix == ".rs" or path.name in {"Cargo.toml", "Cargo.lock"}:
                    resolved = path.resolve()
                    if resolved == PROTECTED or PROTECTED in resolved.parents:
                        raise ValueError("source input reached the protected checkout")
                    files[str(path)] = digest(path.read_bytes())
    for path in [APP / ".cargo/config.toml", APP / ".cargo/config"]:
        if path.exists():
            files[str(path)] = digest(path.read_bytes())
    return files


def package_identity(metadata):
    resolve = metadata["resolve"]
    return {
        "members": sorted(metadata["workspace_members"]),
        "packages": sorted((package["id"], package["manifest_path"]) for package in metadata["packages"]),
        "features": sorted((node["id"], sorted(node["features"])) for node in resolve["nodes"]),
    }


def validate_capture(producer, metadata, sysroot, compiler, cfg, export):
    if producer["schema"] != 1 or producer["frameworkRevision"] != REVISION:
        raise ValueError("capture identity changed")
    if producer["compiler"] != compiler or producer["targetCfg"] != cfg:
        raise ValueError("compiler or target configuration differs from capture")
    if producer["packages"] != json.loads(json.dumps(package_identity(metadata))):
        raise ValueError("Cargo package or feature resolution differs from capture")
    if producer["sources"] != source_inventory(metadata, sysroot):
        raise ValueError("captured application, dependency or sysroot source changed")
    if producer["exportSha256"] != digest(export):
        raise ValueError("compiler export digest differs from capture")
    if producer["rustdocArgs"] != rustdoc_args():
        raise ValueError("recorded producer invocation changed")
    if producer["configuration"] != producer_configuration():
        raise ValueError("compiler overrides or Cargo configuration differs from capture")


def rustdoc_args():
    return ["rustdoc", "--manifest-path", str(APP / "Cargo.toml"), "--lib", "--locked",
            "--target-dir", str(ROOT / "target/agent-debug/devlist-target"), "--",
            "-Z", "unstable-options", "--output-format", "json",
            "--document-private-items", "--document-hidden-items"]


def observation(text, mode, code):
    entries = [line.removeprefix("suprnova-observation: ") for line in text.splitlines()
               if line.startswith("suprnova-observation: ")]
    if code != 0 or len(entries) != 1:
        raise ValueError(f"{mode}: missing, duplicate or unsuccessful application observation")
    value = json.loads(entries[0])
    if not isinstance(value, dict) or value.get("mode") != mode:
        raise ValueError(f"{mode}: observation belongs to another run")
    return value


def all_true(value, names):
    return isinstance(value, dict) and all(value.get(name) is True for name in names)


def target_test_outcome(text, code):
    """Count the exact ignored test's start and terminal event; a failing build is no observation."""
    started, terminal = 0, []
    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"(?m)^\{", text):
        event, _ = decoder.raw_decode(text, match.start())
        if event.get("type") != "test" or not event.get("name", "").endswith("$" + WRONG_TARGET_TEST):
            continue
        if event.get("event") == "started":
            started += 1
        elif event.get("event") in {"ok", "failed"}:
            terminal.append(event["event"])
        else:
            raise ValueError("target test did not complete normally")
    if started != 1 or len(terminal) != 1 or code != (0 if terminal == ["ok"] else 100):
        raise ValueError("target test missing, duplicated or inconsistent with exit status")
    return terminal == ["ok"]


def compact_layout_ok(root=ROOT):
    """Guard the inspected retained roots, independently of process RSS or allocator page reuse."""
    compiler = (root / "crates/engine/project/src/indexing/compiler/mod.rs").read_text()
    declaration = (root / "crates/engine/item-tree/src/compiler/mod.rs").read_text()
    retained = re.search(r"struct CompilerImports\s*\{([^}]+)\}", compiler)
    if retained is None or "RustdocExport" in retained[1] or "memsize(skip)" in retained[1]:
        return False
    if "CompilerTypeDeclarations" not in retained[1] or "rustdoc_types" in declaration:
        return False
    compact = re.sub(r"\s+", "", retained[1])
    if compact != "declarations:Arc<Vec<(CrateRef,CompilerTypeDeclarations)>>,affected_packages:Vec<PackageSlot>,":
        return False
    for path in (root / "crates/engine/project/src").rglob("*.rs"):
        if "tests" in path.parts:
            continue
        text = path.read_text()
        if re.search(r"^\s*(?:pub(?:\([^)]*\))?\s+)?\w+\s*:\s*[^\n]*(?:RustdocExport|rustdoc_types|rd::Crate)", text, re.M):
            return False
    return True


def assess(reports, wrong_target_passed, traces, retained_layout):
    lower, initial, batched, source = (reports.get(mode, {}) for mode in ["lower", "initial", "batched", "source"])
    result = {}
    # A genuine valid input that cannot construct an imported project cannot supply the required API.
    result["SUP-001"] = lower.get("selected") is True and lower.get("lowered", {}).get("directOwnership") is True
    result["SUP-002"] = all(all_true(report.get("queries"), ["query"]) for report in [initial, batched])
    result["SUP-003"] = lower.get("lowered", {}).get("filterBounds") == [
        "suprnova::eloquent::builder::IntoColumn", "suprnova::eloquent::builder::IntoVal"] and all(
        all_true(report.get("queries"), ["without", "filter", "key", "entity", "column"]) for report in [initial, batched])
    if wrong_target_passed is False:
        result["SUP-004"] = False
    elif "queries" in initial:
        result["SUP-004"] = wrong_target_passed is True and all_true(initial["queries"], ["source", "ownerIsolation", "query"]) and all_true(
            initial.get("candidates"), ["targetRejected", "referenceRejected", "previousPreserved"])
    # An unreached preservation check stays unverified; a forbidden launch is a violation even then.
    if any(trace["forbidden"] for trace in traces):
        result["SUP-005"] = False
    elif sorted(trace.get("mode", "") for trace in traces) == ["batched", "initial", "source"] and all("queries" in report for report in [initial, batched, source]):
        result["SUP-005"] = True
    comparison = source.get("comparison")
    same_configuration = isinstance(comparison, dict) and comparison.get("allocator") == "mimalloc" and comparison.get("residency") == "workspace" and comparison.get("queryWorkload") == [
        "column", "entity", "filter", "key", "ownerIsolation", "query", "source", "without"] and bool(comparison.get("sysroot")) and bool(comparison.get("targetCfg")) and bool(comparison.get("residentPackages")) and all(
        report.get("comparison") == comparison for report in [initial, batched])
    result["SUP-006"] = retained_layout and same_configuration and all(
        report.get("indexingComplete") is True and isinstance(report.get("idleResidentPages"), list)
        and len(report["idleResidentPages"]) == 5
        and all(type(sample) is int and sample > 0 for sample in report["idleResidentPages"])
        for report in [initial, batched, source])
    return result


async def main():
    runner = module("agent_debug", ROOT / "tools/agent-debug.py")
    engine = module("engine_import", ROOT / "tools/sudus-engine-import.py")
    runner.install_signal_handlers()
    directory = runner.create_run_directory("suprnova-user")
    environment = dict(os.environ)
    environment.update(RUSTUP_TOOLCHAIN=TOOLCHAIN, CARGO_BUILD_JOBS="2", RAYON_NUM_THREADS="2", RUST_MIN_STACK="16777216",
                       CARGO_TARGET_DIR=str(ROOT / "target/agent-debug/devlist-target"))
    commands, reports, traces = [], {}, []
    configuration = producer_configuration()

    async def run(label, command, args, timeout=20 * 60_000, env=None, measure=False):
        output = directory / label
        output.mkdir(parents=True, exist_ok=True)
        print(f"observing {label}", flush=True)
        # Keep full Cargo metadata and build chatter in bounded logs, not the conversation.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = await runner.run_supervised(runner.CommandSpec(command, args), ROOT,
                env or environment, output, timeout, measure=measure)
        result["phase"] = label
        commands.append(result)
        if result.get("timedOut") or result.get("spawnError") or result.get("signal") or not result["cleanup"].get("verifiedEmpty"):
            raise ValueError(f"{label}: run failed or process cleanup was not verified")
        return result["code"], (output / "stdout.log").read_text()

    try:
        code, compiler = await run("compiler", "rustc", ["-vV"], timeout=30_000)
        if code != 0 or f"host: {TARGET}" not in compiler:
            raise ValueError("recorded compiler host unavailable")
        code, cfg = await run("cfg", "rustc", ["--print", "cfg", "--target", TARGET], timeout=30_000)
        if code != 0:
            raise ValueError("target cfg probe failed")
        code, sysroot_output = await run("sysroot", "rustc", ["--print", "sysroot"], timeout=30_000)
        if code != 0:
            raise ValueError("sysroot probe failed")
        sysroot = Path(sysroot_output.strip()) / "lib/rustlib/src/rust/library"
        if not sysroot.is_dir():
            raise ValueError("recorded rust-src required")
        code, text = await run("metadata", "cargo", ["metadata", "--manifest-path", str(APP / "Cargo.toml"),
            "--locked", "--offline", "--format-version", "1", "--filter-platform", TARGET])
        if code != 0:
            raise ValueError("locked application metadata failed")
        metadata = json.loads(text)
        package_sources = [package.get("source", "") or "" for package in metadata["packages"] if package["name"] == "suprnova"]
        if len(package_sources) != 1 or not package_sources[0].endswith("#" + REVISION):
            raise ValueError("application did not resolve the pinned Git framework")
        if len(sys.argv) == 2 and sys.argv[1] == "capture":
            before = source_inventory(metadata, sysroot)
            code, _ = await run("export", "cargo", rustdoc_args(), measure=True)
            if code != 0 or before != source_inventory(metadata, sysroot) or configuration != producer_configuration():
                raise ValueError("export failed or source changed during capture")
            export = (ROOT / "target/agent-debug/devlist-target/doc/directory.json").read_bytes()
            data = json.loads(export)
            if data["format_version"] != 61 or data["includes_private"] is not True:
                raise ValueError("capture lacks required compiler coverage")
            producer = {"schema": 1, "frameworkRevision": REVISION, "compiler": compiler, "targetCfg": cfg,
                "rustdocArgs": rustdoc_args(), "packages": package_identity(metadata), "sources": before,
                "exportSha256": digest(export), "exportBytes": len(export), "compilerRun": str(directory.relative_to(ROOT)),
                "compilerPeakRssBytes": commands[-1].get("metrics", {}).get("peakRssBytes"), "configuration": configuration}
            CAPTURE.mkdir(parents=True, exist_ok=True)
            (CAPTURE / "export.json.gz").write_bytes(gzip.compress(export, mtime=0))
            (CAPTURE / "producer.json").write_text(json.dumps(producer, indent=2, sort_keys=True) + "\n")
            print(f"captured {len(before)} source inputs and {len(export)} compiler export bytes")
            return 0
        if len(sys.argv) != 1:
            raise ValueError("usage: sudus-suprnova-user.py [capture]")
        producer = json.loads((CAPTURE / "producer.json").read_text())
        with gzip.open(CAPTURE / "export.json.gz", "rb") as stream:
            export = stream.read(256 * 1024 * 1024 + 1)
        if len(export) > 256 * 1024 * 1024:
            raise ValueError("export exceeds reader limit")
        try:
            validate_capture(producer, metadata, sysroot, compiler, cfg, export)
        except ValueError as error:
            print(f"provenance violation: {error}")
            print("sudus: SUP-001: fail")
            return 1
        export_path = directory / "directory.json"
        export_path.write_bytes(export)
        del export
        metadata_path = directory / "metadata.json"
        metadata_path.write_text(json.dumps(metadata))
        plan = {"metadata": str(metadata_path), "export": str(export_path), "manifest": str(APP / "Cargo.toml"),
            "model_source": str(APP / "src/models/user.rs"), "sysroot": str(sysroot), "target_cfg": cfg}
        plan_path = directory / "plan.json"
        plan_path.write_text(json.dumps(plan))
        build_env = dict(environment, RUSTUP_TOOLCHAIN="1.98.1", CARGO_TARGET_DIR=str(ROOT / "target"))
        code, _ = await run("integrity", sys.executable, [str(ROOT / "tools/test_sudus_suprnova_user.py")], timeout=60_000)
        if code != 0:
            raise ValueError("mechanism integrity tests failed")
        code, _ = await run("build", "cargo", ["build", "--locked", "--offline", "-p", "suprnova-lsp", "--example", "suprnova_user"], env=build_env)
        if code != 0:
            raise ValueError("acceptance executable failed to compile")
        test_env = dict(build_env, RG_SUPRNOVA_PLAN=str(plan_path), NEXTEST_EXPERIMENTAL_LIBTEST_JSON="1")
        code, test_output = await run("wrong-target", "cargo", ["nextest", "run", "--offline", "-p", "rg_project", "--lib",
            "--run-ignored", "only", "--no-tests", "fail", "--retries", "0", "--message-format", "libtest-json",
            "--failure-output", "never", "--success-output", "never", "-E", f"test(={WRONG_TARGET_TEST})"], env=test_env)
        wrong_target_passed = target_test_outcome(test_output, code)
        executable = str(ROOT / "target/debug/examples/suprnova_user")
        for mode in ["lower", "initial", "batched", "source"]:
            if mode == "lower":
                code, text = await run(mode, executable, [str(plan_path), mode])
            else:
                trace_path = directory / f"{mode}.exec"
                code, text = await run(mode, "strace", ["-f", "-s", "4096", "-e", "trace=execve,execveat",
                    "-o", str(trace_path), executable, str(plan_path), mode], measure=True)
                forbidden = engine.forbidden_execs(trace_path.read_text())
                traces.append({"mode": mode, "forbidden": forbidden})
            reports[mode] = observation(text, mode, code)
            brief = {key: value for key, value in reports[mode].items() if key != "comparison"}
            print(f"{mode}: {json.dumps(brief, sort_keys=True)}")
        # The same immutable input and requested policy are used by all three indexing runs.
        validate_capture(producer, metadata, sysroot, compiler, cfg, export_path.read_bytes())
        results = assess(reports, wrong_target_passed, traces, compact_layout_ok())
        results["SUP-006"] &= type(producer.get("compilerPeakRssBytes")) is int and producer["compilerPeakRssBytes"] > 0 and all(
            any(command.get("phase") == mode and type(command.get("metrics", {}).get("peakRssBytes")) is int
                and command["metrics"]["peakRssBytes"] > 0 for command in commands)
            for mode in ["initial", "batched", "source"])
        memory = {mode: {"idleRssBytes": [sample * os.sysconf("SC_PAGE_SIZE") for sample in report.get("idleResidentPages", [])],
                         "retainedBytes": report.get("retainedBytes"), "comparison": report.get("comparison"),
                         "indexingPeakRssBytes": next((command.get("metrics", {}).get("peakRssBytes") for command in commands if command.get("phase") == mode), None)}
                  for mode, report in reports.items() if mode != "lower"}
        memory["compilerPreparation"] = {"peakRssBytes": producer.get("compilerPeakRssBytes"), "run": producer["compilerRun"]}
        (directory / "memory.json").write_text(json.dumps(memory, indent=2) + "\n")
        for req, passed in results.items():
            print(f"sudus: {req}: {'pass' if passed else 'fail'}")
        return 0 if len(results) == 6 and all(results.values()) else 1
    finally:
        (directory / "summary.json").write_text(json.dumps({"commands": commands, "reports": reports, "traces": traces,
            "processCleanup": runner.summarize_cleanup(commands)}, indent=2) + "\n")
        print(f"artifacts: {directory}")


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"application mechanism unverified: {error}", file=sys.stderr)
        sys.exit(2)
