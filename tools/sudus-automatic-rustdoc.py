#!/usr/bin/env python3
"""Observe automatic production, live queries and process ownership without prepared inputs."""

import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTO = ["config::tests::aut_001_defaults_to_automatic_discovery_without_explicit_inputs",
         "config::tests::aut_002_preserves_worker_policy_and_explicit_disable",
         "config::tests::aut_003_rejects_malformed_worker_policy_instead_of_ignoring_it"]
LIFECYCLE = {"discovery", "producer", "debounce", "unsaved-duplicate", "live-refresh",
             "held-query-cleanup", "native-inputs", "models", "targets", "stale-disk",
             "obsolete-failure", "invalid-schema", "invalid-reference", "failed-recovery",
             "bounded-output", "global-serial"}
EXPECTED = {"initial": LIFECYCLE, "batched": LIFECYCLE,
            "disabled": {"no-worker"}, "unrelated": {"no-worker"}, "prepared": {"prepared"},
            "timeout": {"timeout-cleanup"}, "shutdown": {"shutdown-cleanup"},
              "missing-producer": {"missing-producer"}, "artifact-failure": {"artifact-recovery"},
              "user-initial": {"genuine-user"},
            "user-batched": {"genuine-user"}, "source-initial": {"source-user"},
            "source-batched": {"source-user"}}
EDITOR_CASE = "Suprnova LSP extension AUT-001 sends automatic worker policy through the editor client"


spec = importlib.util.spec_from_file_location("automatic_editor_mechanism", ROOT / "tools/sudus-editor-import.py")
editor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = editor
spec.loader.exec_module(editor)
user = editor.module("automatic_user_mechanism", ROOT / "tools/sudus-suprnova-user.py")
runner = editor.module("automatic_debug_runner", ROOT / "tools/agent-debug.py")
engine = editor.module("automatic_engine_mechanism", ROOT / "tools/sudus-engine-import.py")


def discover(text):
    data = json.loads(text)
    suites = [suite for suite in data["rust-suites"].values()
              if suite["package-name"] == "rg_lsp_proto" and suite["kind"] == "lib"]
    if len(suites) != 1:
        raise ValueError("required protocol suite missing or ambiguous")
    suite = suites[0]
    for name in PROTO:
        case = suite["testcases"].get(name, {})
        if case.get("ignored") is not False or case.get("filter-match", {}).get("status") != "matches":
            raise ValueError(f"required case missing, ignored or filtered: {name}")
    return {f"rg_lsp_proto::{suite['binary-id']}${name}": name for name in PROTO}


def observation(report, mode, code):
    cases = report.get("cases")
    if not isinstance(cases, dict) or set(cases) != EXPECTED[mode] or "error" in report:
        raise ValueError(f"{mode}: missing, unexpected or aborted observations")
    for name, case in cases.items():
        if case.get("attempted") is not True or type(case.get("passed")) is not bool:
            raise ValueError(f"{mode}: skipped or malformed case {name}")
        if case["passed"] and not isinstance(case.get("evidence"), dict):
            raise ValueError(f"{mode}: passing case lacks evidence {name}")
    if code != (0 if all(case["passed"] for case in cases.values()) else 1):
        raise ValueError(f"{mode}: report contradicts exit status")
    return report


def traced(text):
    launches = engine.forbidden_execs(text)
    analyzer = [line for line in launches if any(Path(arg).name == "rust-analyzer"
                for arg in re.findall(r'"((?:[^"\\]|\\.)*)"', line))]
    return {"successfulExecObserved": True, "rustAnalyzer": analyzer,
            "rustdoc": [line for line in launches if line not in analyzer]}


def peak(metrics):
    matches = re.findall(r"Maximum resident set size \(kbytes\):\s*(\d+)", metrics)
    if len(matches) != 1 or int(matches[0]) <= 0:
        raise ValueError("compiler peak RSS observation missing")
    return int(matches[0]) * 1024


def settings_ok():
    package = json.loads((ROOT / "editors/code/package.json").read_text())
    setting = package["contributes"]["configuration"]["properties"].get("suprnova-lsp.rustdoc.automatic", {})
    defaults = setting.get("default", {})
    description = setting.get("markdownDescription", setting.get("description", "")).lower()
    return (isinstance(defaults, dict) and defaults.get("enabled") is True
            and defaults.get("debounceMs") == 2000 and defaults.get("jobs") == 2
            and all(word in description for word in ["saved", "restart", "toolchain", "stale"]))


def assess(tests, editor_passed, reports, traces, retained, described):
    def both(*names):
        return all(reports[mode]["cases"][name]["passed"] for mode in ["initial", "batched"] for name in names)

    def one(mode):
        return all(case["passed"] for case in reports[mode]["cases"].values())

    results = {
        "AUT-001": tests[PROTO[0]] and editor_passed and both("discovery")
                   and all(one(mode) for mode in ["disabled", "unrelated", "prepared"]),
        "AUT-002": tests[PROTO[1]] and both("debounce", "unsaved-duplicate", "native-inputs"),
        "AUT-003": tests[PROTO[2]] and both("producer", "invalid-schema", "invalid-reference")
                   and one("missing-producer"),
        "AUT-004": both("held-query-cleanup", "global-serial", "bounded-output")
                   and one("timeout") and one("shutdown"),
        "AUT-005": both("stale-disk", "obsolete-failure", "targets"),
        "AUT-006": both("live-refresh", "models", "targets", "invalid-schema", "invalid-reference", "failed-recovery"),
        "AUT-007": described and editor_passed and both("failed-recovery", "invalid-schema", "bounded-output")
                     and one("missing-producer") and one("timeout") and one("artifact-failure"),
        "AUT-008": both("live-refresh") and one("user-initial") and one("user-batched"),
    }
    memory = retained and set(traces) == set(EXPECTED) and all(
        trace.get("successfulExecObserved") is True and trace.get("rustAnalyzer") == [] for trace in traces.values())
    for mode in ["initial", "batched"]:
        automatic, source = reports[f"user-{mode}"], reports[f"source-{mode}"]
        memory &= one(f"user-{mode}") and one(f"source-{mode}")
        memory &= isinstance(automatic.get("comparison"), dict) and automatic["comparison"] == source.get("comparison")
        workload = automatic.get("evidence", {}).get("queryWorkload")
        memory &= isinstance(workload, list) and [entry.get("method") if isinstance(entry, dict) else None
                    for entry in workload] == (["textDocument/hover"] * 4
                        + ["textDocument/completion"] * 3 + ["textDocument/inlayHint"])
        memory &= workload == source.get("evidence", {}).get("queryWorkload")
        memory &= bool(traces.get(f"user-{mode}", {}).get("rustdoc"))
        memory &= traces.get(f"source-{mode}", {}).get("rustdoc") == []
        for report, case_name in [(automatic, "genuine-user"), (source, "source-user")]:
            case = report["cases"][case_name]
            value = case.get("evidence", {})
            idle = dict(value.get("idleMemory", {}), **report.get("evidence", {}).get("indexingMemory", {}))
            memory &= editor.memory_ok({"idleMemory": idle})
        finishes = [event for event in automatic.get("evidence", {}).get("events", [])
                    if event["event"] == "finished" and event.get("code") == 0]
        memory &= bool(finishes) and all(type(event.get("compilerPeakRssBytes")) is int
                                        and event["compilerPeakRssBytes"] > 0 for event in finishes)
    results["AUT-009"] = bool(memory) and both("unsaved-duplicate", "held-query-cleanup")
    return results


def retained_layout_ok():
    if not user.compact_layout_ok():
        return False
    for source in (ROOT / "crates/lsp").rglob("*.rs"):
        if "tests" not in source.parts and re.search(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?\w+\s*:\s*[^\n]*(?:RustdocExport|rustdoc_types|rd::Crate)",
            source.read_text(), re.M):
            return False
    return True


def unowned_outputs(paths):
    # Include empty directories as well as file contents when comparing Cargo writes.
    return {str(path): user.digest(path.read_bytes()) if path.is_file() else "directory"
            for directory in paths for path in directory.rglob("*")}


async def main():
    runner.install_signal_handlers()
    directory = runner.create_run_directory("automatic-rustdoc")
    environment = dict(os.environ, RUSTUP_TOOLCHAIN=user.TOOLCHAIN, CARGO_BUILD_JOBS="2",
        RAYON_NUM_THREADS="2", RUST_MIN_STACK="16777216", CARGO_NET_OFFLINE="true",
        CARGO_TARGET_DIR=str(ROOT / "target/agent-debug/automatic-consumer-target"), RUSTUP_AUTO_INSTALL="0",
        # Metadata and lockfile setup otherwise write version caches before the worker starts.
        CARGO_CACHE_RUSTC_INFO="0")
    build_env = dict(environment, RUSTUP_TOOLCHAIN="1.98.1", CARGO_TARGET_DIR=str(ROOT / "target"),
                     NEXTEST_EXPERIMENTAL_LIBTEST_JSON="1")
    commands, reports, traces = [], {}, {}

    async def run(label, command, args, env=None, cwd=ROOT, timeout=20 * 60_000):
        output = directory / label
        print(f"observing {label}", flush=True)
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = await runner.run_supervised(runner.CommandSpec(command, args), cwd, env or environment, output, timeout)
        result["phase"] = label
        commands.append(result)
        if result.get("timedOut") or result.get("spawnError") or result.get("signal") or not result["cleanup"].get("verifiedEmpty"):
            raise ValueError(f"{label}: command or process cleanup incomplete")
        return result["code"], (output / "stdout.log").read_text()

    async def fixture(name):
        target = directory / name / "fixture"
        shutil.copytree(ROOT / "crates/engine/rustdoc/fixtures/automatic-models", target)
        (target / ".ignore").write_text("src/lib.rs\n")
        ordinary = target.parent / "ordinary-build"
        ordinary.mkdir()
        (ordinary / "unowned-sentinel").write_text("preserve this file\n")
        (target / ".cargo").mkdir()
        (target / ".cargo/config.toml").write_text(
            "[build]\nbuild-dir = " + json.dumps(str(ordinary)) + "\n")
        code, _ = await run(name + "-lock", "cargo", ["generate-lockfile", "--offline",
                           "--manifest-path", str(target / "Cargo.toml")], cwd=target)
        if code != 0:
            raise ValueError("owned genuine fixture lock resolution failed")
        return target

    async def probe(mode, root, scenario, preference="faster-builds", **options):
        work = directory / mode
        work.mkdir(exist_ok=True, parents=True)
        artifacts = work / "compiler-artifacts"
        artifacts.mkdir()
        sentinel = artifacts / "unowned-sentinel"
        sentinel.write_text("preserve this file\n")
        unowned = list(directory.glob("*-fixture/ordinary-build"))
        inherited = work / "inherited-build"
        inherited.mkdir()
        (inherited / "unowned-sentinel").write_text("preserve this file\n")
        unowned.append(inherited)
        plan = {"root": str(root), "binary": str(binary), "scenario": scenario,
            "preference": preference, "artifactRoot": str(artifacts), "control": str(work / "control.json"),
            "events": str(work / "cargo-events.jsonl"), "report": str(work / "report.json"),
            "documents": [{"file": "src/lib.rs"}, {"file": "src/main.rs"}],
            "unownedBuildDirectories": [str(path) for path in unowned], **options}
        path = work / "plan.json"
        path.write_text(json.dumps(plan))
        env = dict(environment, PATH=str(proxy_tools) + os.pathsep + environment["PATH"],
            AUTOMATIC_RUSTDOC_CONTROL=plan["control"], AUTOMATIC_RUSTDOC_EVENTS=plan["events"],
            AUTOMATIC_RUSTDOC_REAL_CARGO=real_cargo)
        if scenario == "lifecycle" and preference == "lower-peak-memory":
            env["CARGO_BUILD_BUILD_DIR"] = str(inherited)
        unowned_before = unowned_outputs(unowned)
        before = {str(p): user.digest(p.read_bytes()) for p in root.rglob("*")
                  if p.is_file() and (p.suffix == ".rs" or p.name in {"Cargo.toml", "Cargo.lock"})
                  and "target" not in p.relative_to(root).parts}
        trace = work / "process.exec"
        # Sixteen lifecycle controls include repeated genuine source/project rebuilds.
        # Their aggregate budget is separate from each bounded worker/query operation.
        timeout = 60 * 60_000 if scenario == "lifecycle" else 30 * 60_000
        if scenario == "devlist":
            timeout = (plan["workerWaitSeconds"] + 600) * 1000
        # Filter in the kernel so unrelated compiler syscalls do not stop at
        # the tracer. Keep following every descendant and observing each exec.
        code, _ = await run(mode, "strace", ["-f", "--seccomp-bpf", "-s", "4096", "-e", "trace=execve,execveat",
            "-o", str(trace), sys.executable, str(ROOT / "tools/automatic-rustdoc-probe.py"), str(path)],
            env=env, timeout=timeout)
        report = observation(json.loads(Path(plan["report"]).read_text()), mode, code)
        unowned_after = unowned_outputs(unowned)
        if unowned_before != unowned_after:
            raise ValueError(f"{mode}: worker changed unowned Cargo intermediate output")
        after = {str(p): user.digest(p.read_bytes()) for p in root.rglob("*")
                 if p.is_file() and (p.suffix == ".rs" or p.name in {"Cargo.toml", "Cargo.lock"})
                 and "target" not in p.relative_to(root).parts}
        if before != after or not sentinel.exists():
            raise ValueError(f"{mode}: source/lock or unowned artifact changed")
        traces[mode] = traced(trace.read_text())
        for event in report.get("evidence", {}).get("events", []):
            if event["event"] == "finished" and event.get("code") == 0:
                event["compilerPeakRssBytes"] = peak(Path(event["metrics"]).read_text())
        reports[mode] = report
        print(f"{mode}: {sum(c['passed'] for c in report['cases'].values())}/{len(report['cases'])} observations passed", flush=True)
        return report

    try:
        code, _ = await run("integrity", sys.executable, [str(ROOT / "tools/test_sudus_automatic_rustdoc.py")], timeout=60_000)
        if code != 0:
            raise ValueError("mechanism integrity checks failed")
        selection = ["--locked", "--offline", "--lib", "-p", "rg_lsp_proto", "-E", "test(aut_)"]
        code, text = await run("discovery", "cargo", ["nextest", "list", *selection, "--message-format", "json"], env=build_env)
        if code != 0:
            raise ValueError("test discovery or compilation failed")
        wanted = discover(text)
        code, text = await run("semantic", "cargo", ["nextest", "run", *selection, "--message-format", "libtest-json",
            "--no-fail-fast", "--retries", "0", "--no-tests", "fail", "--test-threads", "2",
            "--failure-output", "never", "--success-output", "never"], env=build_env)
        tests = editor.outcomes(text, wanted, code)
        build = runner.build_spec(runner.RunnerOptions(build_profile="debug"))
        code, _ = await run("build", build.command, [*build.args, "--locked", "--offline"], env=build_env)
        if code != 0:
            raise ValueError("current LSP executable failed to compile")
        binary = runner.rust_glancer_binary("debug")
        real_cargo = shutil.which("cargo")
        if not real_cargo:
            raise ValueError("Cargo executable unavailable")
        proxy_tools = directory / "proxy-tools"
        proxy_tools.mkdir()
        cargo_proxy = proxy_tools / "cargo"
        cargo_proxy.symlink_to(ROOT / "tools/automatic-rustdoc-cargo.py")

        initial = await fixture("initial-fixture")
        second = await fixture("second-fixture")
        # Prepared precedence uses a genuine separately staged export. It never supplies
        # bytes to an automatic run, including the mandatory real Devlist acceptance.
        prepare_env = dict(environment, CARGO_TARGET_DIR=str(ROOT / "target/agent-debug/automatic-fixture-target"),
                           CARGO_BUILD_BUILD_DIR=str(ROOT / "target/agent-debug/automatic-fixture-target"))
        code, _ = await run("prepared-export", "cargo", ["rustdoc", "--manifest-path", str(initial / "Cargo.toml"),
            "--locked", "--offline", "--lib", "--jobs", "2", "--", "-Z", "unstable-options",
            "--output-format", "json", "--document-private-items", "--document-hidden-items"], env=prepare_env, cwd=initial)
        if code != 0:
            raise ValueError("genuine compiler control failed")
        export = directory / "prepared.json"
        shutil.copyfile(Path(prepare_env["CARGO_TARGET_DIR"]) / "doc/automatic_models.json", export)
        code, _ = await run("binary-export-control", "cargo", ["rustdoc", "--manifest-path", str(initial / "Cargo.toml"),
            "--locked", "--offline", "--bin", "automatic_models", "--jobs", "2", "--", "-Z", "unstable-options",
            "--output-format", "json", "--document-private-items", "--document-hidden-items"], env=prepare_env, cwd=initial)
        if code != 0:
            raise ValueError("genuine binary compiler control failed")
        code, _ = await run("editor-compile", "npm", ["run", "compile"], env=build_env, cwd=ROOT / "editors/code")
        if code != 0:
            raise ValueError("editor acceptance failed to compile")
        editor_work = directory / "editor-run"
        editor_work.mkdir()
        editor_report = editor_work / "results.json"
        editor_control = editor_work / "control.json"
        editor_control.write_text(json.dumps({"mode": "real", "artifactRoot": str(editor_work / "artifacts")}))
        editor_env = dict(environment, PATH=str(proxy_tools) + os.pathsep + environment["PATH"],
            AUTOMATIC_RUSTDOC_CONTROL=str(editor_control), AUTOMATIC_RUSTDOC_EVENTS=str(editor_work / "events.jsonl"),
            AUTOMATIC_RUSTDOC_REAL_CARGO=real_cargo, SUPRNOVA_LSP_TEST_SERVER=str(binary),
            SUPRNOVA_LSP_EXTENSION_TEST_GREP="AUT-001 sends", SUPRNOVA_LSP_EXTENSION_TEST_REPORT=str(editor_report),
            SUPRNOVA_LSP_AUTOMATIC_RUSTDOC_FIXTURE=str(initial),
            SUPRNOVA_LSP_AUTOMATIC_RUSTDOC_ARTIFACTS=str(editor_work / "artifacts"))
        code, _ = await run("editor", "xvfb-run", ["-a", "npm", "run", "test:e2e:prebuilt"],
                            env=editor_env, cwd=ROOT / "editors/code", timeout=20 * 60_000)
        previous = editor.EDITOR_CASE
        try:
            editor.EDITOR_CASE = EDITOR_CASE
            editor_passed = editor.editor_outcome(editor_report.read_text(), code)
        finally:
            editor.EDITOR_CASE = previous

        for mode, preference in [("initial", "faster-builds"), ("batched", "lower-peak-memory")]:
            await probe(mode, initial, "lifecycle", preference, extraRoots=[str(second)],
                        cargo={"target": user.TARGET, "features": ["extra"]})
        await probe("disabled", initial, "disabled", automatic={"enabled": False})
        await probe("prepared", initial, "prepared", inputs=[{"workspaceRoot": str(initial),
            "manifestPath": "Cargo.toml", "targetKind": "lib", "targetName": "automatic_models",
            "exportPath": str(export), "itemPath": "automatic_models::Post"}])
        unrelated = directory / "unrelated-fixture"
        (unrelated / "src").mkdir(parents=True)
        (unrelated / "Cargo.toml").write_text('[package]\nname="unrelated"\nversion="0.1.0"\nedition="2024"\n[workspace]\n')
        (unrelated / "src/lib.rs").write_text("pub fn value() -> u64 { 7 }\n")
        code, _ = await run("unrelated-lock", "cargo", ["generate-lockfile", "--offline", "--manifest-path", str(unrelated / "Cargo.toml")])
        if code != 0:
            raise ValueError("unrelated fixture preparation failed")
        await probe("unrelated", unrelated, "disabled", documents=[{"file": "src/lib.rs"}])
        for mode in ["timeout", "shutdown"]:
            await probe(mode, initial, mode, controlMode="hold", timeoutMs=5000 if mode == "timeout" else 900000)
        await probe("missing-producer", initial, "missing-producer", automatic={"toolchain": "missing-automatic-rustdoc-producer"})
        await probe("artifact-failure", initial, "artifact-failure")

        original = (user.APP / "src/models/user.rs").read_text()
        signature = "pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {"
        if original.count(signature) != 1:
            raise ValueError("Devlist source method changed")
        overlay = original.replace(signature, signature + '\n        use suprnova::eloquent::Model as _;\n'
            '        let edt_query = User::query();\n        let edt_without = User::without_global_scopes();\n'
            '        let edt_filter = User::filter("email", "member@example.test");\n'
            '        let edt_source = self.verify_password(password);\n'
            '        { struct User; let edt_other = User::without_global_scopes(); }\n')
        code, metadata_text = await run("devlist-metadata", "cargo", ["metadata", "--manifest-path", str(user.APP / "Cargo.toml"),
            "--locked", "--offline", "--format-version", "1", "--filter-platform", user.TARGET])
        if code != 0:
            raise ValueError("locked Devlist metadata failed")
        metadata = json.loads(metadata_text)
        framework = [p for p in metadata["packages"] if p["name"] == "suprnova"]
        if len(framework) != 1 or not framework[0]["source"].endswith("#" + user.REVISION):
            raise ValueError("Devlist framework is not its pinned dependency")
        code, compiler = await run("producer-identity", "rustc", ["-vV"], timeout=30_000)
        if code != 0:
            raise ValueError("producer identity unavailable")
        code, cfg = await run("target-cfg", "rustc", ["--print", "cfg", "--target", user.TARGET], timeout=30_000)
        if code != 0:
            raise ValueError("target configuration unavailable")
        code, sysroot = await run("sysroot", "rustc", ["--print", "sysroot"], timeout=30_000)
        if code != 0:
            raise ValueError("sysroot unavailable")
        sources = user.source_inventory(metadata, Path(sysroot.strip()) / "lib/rustlib/src/rust/library")
        # Give the real application's cold dependency builds a larger explicit deadline.
        # It applies to each serial command; preparation and replacement indexing
        # also need time after the selected targets have been exported.
        devlist_timeout_ms = 30 * 60_000
        workspace_target_count = sum(
            bool(set(target["kind"]) & {"lib", "rlib", "dylib", "staticlib", "cdylib", "bin"})
            for package in metadata["packages"] if package["id"] in metadata["workspace_members"]
            for target in package["targets"])
        worker_wait_seconds = (devlist_timeout_ms // 1000) * (workspace_target_count + 1)
        for mode, preference in [("initial", "faster-builds"), ("batched", "lower-peak-memory")]:
            comparison = {"packages": user.package_identity(metadata), "sources": sources,
                "sourceTextSha256": user.digest(overlay.encode()), "compiler": compiler, "targetCfg": cfg,
                "sysroot": sysroot.strip(), "residency": "workspace", "indexingPreference": preference,
                  "configuration": dict(user.producer_configuration(),
                                        cargoCacheRustcInfo=environment["CARGO_CACHE_RUSTC_INFO"]),
                  "workerTimeoutMs": devlist_timeout_ms}
            for prefix, scenario, automatic in [("user", "devlist", {}), ("source", "devlist-source", {"enabled": False})]:
                report = await probe(f"{prefix}-{mode}", user.APP, scenario, preference,
                    documents=[{"file": "src/models/user.rs", "text": overlay}], automatic=automatic,
                    cargo={"target": user.TARGET}, timeoutMs=devlist_timeout_ms,
                    workerWaitSeconds=worker_wait_seconds)
                report["comparison"] = comparison
        if sources != user.source_inventory(metadata, Path(sysroot.strip()) / "lib/rustlib/src/rust/library"):
            raise ValueError("application, dependency or sysroot inputs changed")
        results = assess(tests, editor_passed, reports, traces, retained_layout_ok(), settings_ok())
        runner.write_json(directory / "observations.json", {"tests": tests, "editor": editor_passed,
            "reports": reports, "traces": traces, "results": results})
        for req, passed in results.items():
            print(f"sudus: {req}: {'pass' if passed else 'fail'}", flush=True)
        return 0 if all(results.values()) else 1
    finally:
        runner.write_json(directory / "summary.json", {"commands": commands, "processCleanup": runner.summarize_cleanup(commands)})
        print(f"automatic-rustdoc artifacts: {directory}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"automatic-rustdoc observation incomplete: {error}", file=sys.stderr)
        sys.exit(2)
