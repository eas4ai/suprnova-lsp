#!/usr/bin/env python3
"""Observe explicit compiler inputs through configuration, the editor and stdio LSP."""

import asyncio
import contextlib
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import sys

from importlib.util import module_from_spec, spec_from_file_location


ROOT = Path(__file__).resolve().parent.parent


def module(name, path):
    spec = spec_from_file_location(name, path)
    value = module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


CASES = {
    "EDT-001": {
        "rg_lsp_proto": ["config::tests::edt_001_preserves_explicit_input_identity",
                         "config::tests::edt_001_rejects_malformed_entries_instead_of_dropping_them"],
        "rg_lsp_server": ["config::tests::edt_001_routes_inputs_only_to_the_selected_root",
                          "config::tests::edt_001_rejects_ambiguous_relative_roots"],
    },
    "EDT-004": {"rg_lsp_engine": [
        "tests::utils::rustdoc_import::edt_004_rejects_invalid_startup_without_replacing_the_previous_project",
        "tests::utils::rustdoc_import::edt_004_saved_body_and_reindex_keep_the_imported_api"]},
    "EDT-006": {"rg_lsp_engine": [
        "tests::utils::rustdoc_import::edt_006_keeps_captured_facts_after_the_external_file_changes",
        "tests::utils::rustdoc_import::edt_006_unconfigured_startup_does_not_reuse_an_imported_generation"]},
}
EDITOR_CASE = "Rust Glancer extension EDT-001 sends a configured compiler model through the editor client"
LABELS = {"query", "without", "filter", "source", "methods", "sourceMethods", "otherMethods", "types"}
METHODS = {"query", "without_global_scopes", "filter"}
MODES = {"initial": "faster-builds", "batched": "lower-peak-memory",
         "source-initial": "faster-builds", "source-batched": "lower-peak-memory"}


def json_objects(text):
    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"(?m)^\s*\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.end() - 1)
        except json.JSONDecodeError:
            continue
        yield value


def discover(text):
    data = json.loads(text)
    wanted = {}
    for packages in CASES.values():
        for package, cases in packages.items():
            suites = [suite for suite in data["rust-suites"].values()
                      if suite["package-name"] == package and suite["kind"] == "lib"]
            if len(suites) != 1:
                raise ValueError(f"missing or ambiguous suite: {package}")
            suite = suites[0]
            for case in cases:
                entry = suite["testcases"].get(case)
                if entry is None or entry.get("ignored") is not False or entry.get("filter-match", {}).get("status") != "matches":
                    raise ValueError(f"required test missing, ignored or filtered: {case}")
                wanted[f"{package}::{suite['binary-id']}${case}"] = case
    return wanted


def outcomes(text, wanted, code):
    started, terminal = set(), {}
    for event in json_objects(text):
        if event.get("type") != "test" or event.get("name") not in wanted:
            continue
        name, state = event["name"], event.get("event")
        if state == "started" and name not in started:
            started.add(name)
        elif state in {"ok", "failed"} and name in started and name not in terminal:
            terminal[name] = state
        else:
            raise ValueError(f"duplicate, skipped or invalid test event: {name}: {state}")
    if set(terminal) != set(wanted) or code != (100 if "failed" in terminal.values() else 0):
        raise ValueError("test observations incomplete or inconsistent with exit status")
    return {wanted[name]: state == "ok" for name, state in terminal.items()}


def editor_outcome(text, code):
    reports = [value for value in json_objects(text) if "stats" in value and "tests" in value]
    if len(reports) != 1:
        raise ValueError("missing or duplicate Mocha report")
    report = reports[0]
    tests = report["tests"]
    if len(tests) != 1 or tests[0].get("fullTitle") != EDITOR_CASE or report["stats"].get("pending") != 0:
        raise ValueError("editor test missing, skipped or filtered incorrectly")
    passes, failures = report.get("passes", []), report.get("failures", [])
    if len(passes) + len(failures) != 1 or code != (1 if failures else 0):
        raise ValueError("editor result inconsistent with exit status")
    if (passes or failures)[0].get("fullTitle") != EDITOR_CASE:
        raise ValueError("editor terminal result belongs to another case")
    return bool(passes)


def lsp_observation(text, code):
    values = [value for value in json_objects(text) if "results" in value and "barriers" in value]
    if code != 0 or len(values) != 1:
        raise ValueError("stdio LSP queries did not finish normally")
    value = values[0]
    results = value["results"]
    if len(results) != len(LABELS) or {result.get("label") for result in results} != LABELS:
        raise ValueError("missing or duplicate LSP query")
    if value["barriers"] != {"readiness": "ready", "deferred": "before-queries"}:
        raise ValueError("LSP observation did not wait for deferred completion")
    for result in results:
        if result.get("kind") == "completion" and (result.get("truncated") or result.get("isIncomplete")):
            raise ValueError("completion observation is incomplete")
        if result.get("kind") == "inlay" and result.get("truncated"):
            raise ValueError("inlay observation is incomplete")
    return value


def query_facts(report, text):
    by_label = {result["label"]: result for result in report["results"]}
    hover = {name: "Builder<User>" in (by_label[name].get("text") or "") for name in ["query", "without", "filter"]}
    source = by_label["source"].get("text") or ""
    source_ok = "Result<bool, FrameworkError>" in source
    labels = lambda name: [re.split(r"[<(]", item["label"])[0].strip() for item in by_label[name]["items"]]
    methods, other, source_methods = labels("methods"), labels("otherMethods"), labels("sourceMethods")
    completions = all(methods.count(name) == 1 for name in METHODS)
    isolation = all(name not in other for name in METHODS)
    preserved = source_ok and source_methods.count("verify_password") == 1
    hints = by_label["types"].get("raw") or []
    inlay = {}
    for name in hover:
        # Associate the hint with its binding line, not another Builder expression nearby.
        offset = text.index(f"let edt_{name}")
        line = text[:offset].count("\n")
        matching = [hint for hint in hints if hint.get("position", {}).get("line") == line]
        rendered = []
        for hint in matching:
            label = hint.get("label")
            rendered.append(label if isinstance(label, str) else "".join(part["value"] for part in label))
        inlay[name] = any("Builder<User>" in value for value in rendered)
    return {"hover": all(hover.values()), "inlay": all(inlay.values()), "completion": completions,
            "source": preserved, "isolation": isolation,
            "sourceOnly": not any(hover.values()) and all(name not in methods for name in METHODS)}


def memory_ok(report):
    memory = report.get("idleMemory") or {}
    samples = memory.get("samples")
    if memory.get("indexingComplete") is not True or memory.get("metric") != "sum-of-process-RSS" or not isinstance(samples, list) or len(samples) != 5:
        return False
    if type(memory.get("indexingPeakRssBytes")) is not int or memory["indexingPeakRssBytes"] <= 0 or type(memory.get("indexingSamples")) is not int or memory["indexingSamples"] < 5 or memory.get("samplingIntervalMs") != 100:
        return False
    identity = None
    for sample in samples:
        processes = sample.get("processRssBytes")
        if not isinstance(processes, dict) or len(processes) < 2 or any(type(value) is not int or value <= 0 for value in processes.values()):
            return False
        if sample.get("aggregateRssBytes") != sum(processes.values()):
            return False
        current = sorted(processes)
        if identity is not None and identity != current:
            return False
        identity = current
    return True


def setting_description_ok():
    package = json.loads((ROOT / "editors/code/package.json").read_text())
    setting = package["contributes"]["configuration"]["properties"].get("rust-glancer.rustdoc.inputs", {})
    description = setting.get("markdownDescription", setting.get("description", "")).lower()
    return setting.get("default") == [] and all(word in description for word in ["prepared", "restart", "not automatically"])


def assess(tests, editor, reports, traces, retained, description):
    result = {req: all(tests[case] for cases in packages.values() for case in cases) for req, packages in CASES.items()}
    imported = [reports[mode]["facts"] for mode in ["initial", "batched"]]
    result["EDT-001"] &= editor and all(value["hover"] for value in imported)
    result["EDT-002"] = all(value["hover"] and value["inlay"] for value in imported)
    result["EDT-003"] = all(value["completion"] and value["source"] and value["isolation"] for value in imported)
    comparisons = all(reports[mode]["comparison"] == reports[f"source-{mode}"]["comparison"] for mode in ["initial", "batched"])
    result["EDT-005"] = retained and comparisons and set(traces) == set(MODES) and all(not value for value in traces.values()) and all(
        memory_ok(report) and report.get("indexingPeakRssBytes") == report["idleMemory"].get("indexingPeakRssBytes") for report in reports.values())
    result["EDT-006"] &= description and all(reports[mode]["facts"]["sourceOnly"] for mode in ["source-initial", "source-batched"])
    return result


async def main():
    user = module("suprnova_user", ROOT / "tools/sudus-suprnova-user.py")
    engine = module("engine_import", ROOT / "tools/sudus-engine-import.py")
    runner = module("agent_debug", ROOT / "tools/agent-debug.py")
    runner.install_signal_handlers()
    directory = runner.create_run_directory("editor-import")
    environment = dict(os.environ, RUSTUP_TOOLCHAIN=user.TOOLCHAIN, CARGO_BUILD_JOBS="2", RAYON_NUM_THREADS="2",
                       RUST_MIN_STACK="16777216", CARGO_TARGET_DIR=str(ROOT / "target/agent-debug/devlist-target"), CARGO_NET_OFFLINE="true")
    build_env = dict(environment, RUSTUP_TOOLCHAIN="1.98.1", CARGO_TARGET_DIR=str(ROOT / "target"), NEXTEST_EXPERIMENTAL_LIBTEST_JSON="1")
    commands, reports, traces = [], {}, {}

    async def run(label, command, args, env=None, cwd=ROOT, timeout=20 * 60_000):
        output = directory / label
        output.mkdir(parents=True, exist_ok=True)
        print(f"observing {label}", flush=True)
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = await runner.run_supervised(runner.CommandSpec(command, args), cwd, env or environment, output, timeout)
        result["phase"] = label
        commands.append(result)
        if result.get("timedOut") or result.get("spawnError") or result.get("signal") or not result["cleanup"].get("verifiedEmpty"):
            raise ValueError(f"{label}: command or owned process cleanup incomplete")
        return result["code"], (output / "stdout.log").read_text()

    try:
        code, _ = await run("integrity", sys.executable, [str(ROOT / "tools/test_sudus_editor_import.py")], timeout=60_000)
        if code != 0:
            raise ValueError("mechanism integrity checks failed")
        code, compiler = await run("compiler", "rustc", ["-vV"], timeout=30_000)
        if code != 0:
            raise ValueError("compiler probe failed")
        code, cfg = await run("cfg", "rustc", ["--print", "cfg", "--target", user.TARGET], timeout=30_000)
        if code != 0:
            raise ValueError("cfg probe failed")
        code, sysroot_text = await run("sysroot", "rustc", ["--print", "sysroot"], timeout=30_000)
        if code != 0:
            raise ValueError("sysroot probe failed")
        sysroot = Path(sysroot_text.strip()) / "lib/rustlib/src/rust/library"
        code, text = await run("metadata", "cargo", ["metadata", "--manifest-path", str(user.APP / "Cargo.toml"),
            "--locked", "--offline", "--format-version", "1", "--filter-platform", user.TARGET])
        if code != 0:
            raise ValueError("locked application metadata failed")
        metadata = json.loads(text)
        producer = json.loads((user.CAPTURE / "producer.json").read_text())
        with gzip.open(user.CAPTURE / "export.json.gz", "rb") as stream:
            export = stream.read(256 * 1024 * 1024 + 1)
        if len(export) > 256 * 1024 * 1024:
            raise ValueError("export exceeds reader bound")
        user.validate_capture(producer, metadata, sysroot, compiler, cfg, export)
        export_path = directory / "directory.json"
        export_path.write_bytes(export)
        del export

        selection = ["--locked", "--offline", "--lib", "-p", "rg_lsp_proto", "-p", "rg_lsp_server", "-p", "rg_lsp_engine", "-E", "test(edt_)"]
        code, text = await run("discovery", "cargo", ["nextest", "list", *selection, "--message-format", "json"], env=build_env)
        if code != 0:
            raise ValueError("test discovery or compilation failed")
        wanted = discover(text)
        code, text = await run("semantic", "cargo", ["nextest", "run", *selection, "--message-format", "libtest-json",
            "--no-fail-fast", "--retries", "0", "--no-tests", "fail", "--test-threads", "2", "--failure-output", "never", "--success-output", "never"], env=build_env)
        tests = outcomes(text, wanted, code)
        build = runner.build_spec(runner.RunnerOptions(build_profile="debug"))
        code, _ = await run("build", build.command, [*build.args, "--locked", "--offline"], env=build_env)
        if code != 0:
            raise ValueError("current LSP executable failed to compile")
        binary = runner.rust_glancer_binary("debug")
        editor_report = directory / "editor-results.json"
        editor_env = dict(build_env, RUST_GLANCER_TEST_SERVER=str(binary), RUST_GLANCER_EXTENSION_TEST_GREP="EDT-001 sends",
                          RUST_GLANCER_EXTENSION_TEST_REPORT=str(editor_report))
        code, text = await run("editor", "xvfb-run", ["-a", "npm", "run", "test:e2e:prebuilt"], env=editor_env, cwd=ROOT / "editors/code", timeout=5 * 60_000)
        editor = editor_outcome(editor_report.read_text(), code)

        # Force locked offline metadata in the LSP process tree without altering the app.
        tools = directory / "query-tools"
        tools.mkdir()
        cargo = shutil.which("cargo")
        if not cargo:
            raise ValueError("Cargo executable missing")
        shim = tools / "cargo"
        shim.write_text(f"#!{sys.executable}\nimport os, sys, subprocess\nsubprocess.run(['rustdoc', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)\nargs = sys.argv[1:]\nif args and args[0] == 'metadata':\n    args += ['--locked', '--offline']\nos.execv({cargo!r}, ['cargo', *args])\n")
        shim.chmod(0o755)
        query_env = dict(environment, PATH=str(tools) + os.pathsep + environment["PATH"])
        original = (user.APP / "src/models/user.rs").read_text()
        signature = "pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {"
        if original.count(signature) != 1:
            raise ValueError("Devlist source method changed")
        probe = """
        use suprnova::eloquent::Model as _;
        let edt_query = User::query();
        let edt_without = User::without_global_scopes();
        let edt_filter = User::filter("email", "member@example.test");
        let edt_source = self.verify_password(password);
        { struct User; let edt_other = User::without_global_scopes(); }
"""
        overlay = original.replace(signature, signature + probe)
        queries = [{"kind": "hover", "label": name, "marker": f"edt_{name}"} for name in ["query", "without", "filter", "source"]]
        queries += [{"kind": "completion", "label": "methods", "marker": "let edt_query = User::", "delta": len("let edt_query = User::")},
                    {"kind": "completion", "label": "sourceMethods", "marker": "self.verify_password", "delta": 5},
                    {"kind": "completion", "label": "otherMethods", "marker": "let edt_other = User::", "delta": len("let edt_other = User::")},
                    {"kind": "inlay", "label": "types", "range": {"startMarker": "let edt_query", "endMarker": "let edt_source"}}]
        input_entry = {"workspaceRoot": str(user.APP), "manifestPath": str(user.APP / "Cargo.toml"),
                       "targetName": "directory", "targetKind": "lib", "exportPath": str(export_path),
                       "itemPath": "directory::models::user::User"}
        for mode, preference in MODES.items():
            plan = {"file": "src/models/user.rs", "text": overlay, "format": "json", "readinessBarrier": "ready",
                    "deferredBarrier": "before-queries", "idleMemory": True, "queries": queries,
                    "initializationOptions": {"cfg": {"test": False}, "cache": {"packageResidency": "workspace"},
                          "indexing": {"performancePreference": preference}, "rustdoc": {
                              "automatic": {"enabled": False}, "inputs": [] if mode.startswith("source-") else [input_entry]}}}
            plan_path = directory / f"{mode}-plan.json"
            plan_path.write_text(json.dumps(plan))
            trace_path = directory / f"{mode}.exec"
            code, text = await run(mode, "strace", ["-f", "-s", "4096", "-e", "trace=execve,execveat", "-o", str(trace_path),
                "just", "agent-debug", "--no-build", "--build-profile", "debug", "--measure", "--timeout", "5m", "lsp-query",
                "--workspace-root", str(user.APP), "--query-file", str(plan_path), "--timeout-ms", "300000", "--max-completions", "1000", "--json"], env=query_env, timeout=6 * 60_000)
            traces[mode] = engine.forbidden_execs(trace_path.read_text())
            report = lsp_observation(text, code)
            runner_log = (directory / mode / "stderr.log").read_text()
            paths = re.findall(r"agent-debug: run artifacts: (.+)", runner_log)
            if len(paths) != 1:
                raise ValueError("missing managed LSP artifacts")
            summary = json.loads((Path(paths[0]) / "summary.json").read_text())
            if summary["processCleanup"].get("status") != "verified":
                raise ValueError("LSP subprocess cleanup unverified")
            report["runnerPeakRssBytes"] = summary["results"][0]["metrics"]["peakRssBytes"]
            report["indexingPeakRssBytes"] = report["idleMemory"]["indexingPeakRssBytes"]
            report["facts"] = query_facts(report, overlay)
            report["comparison"] = {"packages": user.package_identity(metadata), "sources": producer["sources"],
                "sysroot": str(sysroot), "targetCfg": cfg, "residency": "workspace", "indexingPreference": preference,
                "queryWorkload": queries, "sourceTextSha256": user.digest(overlay.encode()), "compiler": compiler,
                "configuration": user.producer_configuration()}
            reports[mode] = report
            print(f"{mode}: {json.dumps(report['facts'], sort_keys=True)}", flush=True)
        user.validate_capture(producer, metadata, sysroot, compiler, cfg, export_path.read_bytes())
        retained = user.compact_layout_ok()
        # The editor adds another saved-state boundary. Check its typed fields as well as
        # the engine's retained roots; paths/configuration must not retain compiler graphs.
        for path in (ROOT / "crates/lsp").rglob("*.rs"):
            if "tests" in path.parts:
                continue
            if re.search(r"^\s*(?:pub(?:\([^)]*\))?\s+)?\w+\s*:\s*[^\n]*(?:RustdocExport|rustdoc_types|rd::Crate)", path.read_text(), re.M):
                retained = False
        results = assess(tests, editor, reports, traces, retained, setting_description_ok())
        runner.write_json(directory / "observations.json", {"tests": tests, "editor": editor, "reports": reports, "traces": traces, "results": results})
        for req in sorted(results):
            print(f"sudus: {req}: {'pass' if results[req] else 'fail'}", flush=True)
        return 0 if all(results.values()) else 1
    finally:
        runner.write_json(directory / "summary.json", {"commands": commands, "processCleanup": runner.summarize_cleanup(commands)})
        print(f"editor-import mechanism artifacts: {directory}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"editor-import observation incomplete: {error}", file=sys.stderr)
        sys.exit(2)
