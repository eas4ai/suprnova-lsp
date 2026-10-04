#!/usr/bin/env python3
"""Observe the engine-import contract through bounded semantic tests and exec tracing."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys


ROOT = Path(__file__).resolve().parent.parent
PREFIX = "tests::rustdoc_import::"
CASES = {
    "MAC-001": ["mac_001_initial_query", "mac_001_batched_query", "mac_001_trait_default", "mac_001_batched_trait_default"],
    "MAC-002": ["mac_002_generic_and_associated_type"],
    "MAC-003": ["mac_003_owner_namespaces_and_overlap"],
    "MAC-004": ["mac_004_source_artifact_before_import", "mac_004_import_does_not_leak_into_source_artifact"],
    "MAC-005": ["mac_005_invalid_candidate_preserves_previous_project", "mac_005_previous_import_survives_failed_candidate"],
    "MAC-006": ["mac_006_query_without_compiler_servers"],
}


def discover(text):
    """Reject missing or ignored acceptance cases before executing the mechanism."""
    data = json.loads(text)
    suites = [suite for suite in data["rust-suites"].values() if suite["package-name"] == "rg_project" and suite["kind"] == "lib"]
    if len(suites) != 1:
        raise ValueError("expected exactly one rg_project library test suite")
    suite = suites[0]
    for cases in CASES.values():
        for case in cases:
            entry = suite["testcases"].get(PREFIX + case)
            if entry is None or entry.get("ignored") is not False or entry.get("filter-match", {}).get("status") != "matches":
                raise ValueError(f"required test missing, ignored, or filtered: {case}")
    return suite["binary-id"]


def outcomes(text, binary_id, cases, exit_code):
    """A case counts only when its exact test was started and finished once."""
    wanted = {f"rg_project::{binary_id}${PREFIX}{case}": case for case in cases}
    started = set()
    terminal = {}
    # Nextest tracer mode emits literal newlines inside its uncaptured-output field.
    decoder = json.JSONDecoder(strict=False)
    for match in re.finditer(r"(?m)^\{", text):
        event, _ = decoder.raw_decode(text, match.start())
        if event.get("type") != "test" or event.get("name") not in wanted:
            continue
        case = wanted[event["name"]]
        state = event.get("event")
        if state == "started":
            if case in started:
                raise ValueError(f"duplicate test start: {case}")
            started.add(case)
        elif state in {"ok", "failed"}:
            if case not in started or case in terminal:
                raise ValueError(f"invalid test completion: {case}")
            terminal[case] = state
        else:
            raise ValueError(f"required test did not complete normally: {case}: {state}")
    if set(terminal) != set(cases):
        raise ValueError(f"incomplete required test results: {sorted(set(cases) - set(terminal))}")
    expected_exit = 100 if "failed" in terminal.values() else 0
    if exit_code != expected_exit:
        raise ValueError(f"test exit {exit_code} disagrees with observed results {expected_exit}")
    return {case: state == "ok" for case, state in terminal.items()}


def forbidden_execs(text):
    """Trace absolute paths and rustup proxy arguments, including short-lived children."""
    exec_lines = [line for line in text.splitlines() if re.search(r"\bexecve(?:at)?\(", line)]
    if not exec_lines or not any(re.search(r"= 0\s*$", line) for line in exec_lines):
        raise ValueError("trace has no successful exec observation")
    forbidden = []
    for line in exec_lines:
        arguments = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
        if any(Path(argument).name in {"rustdoc", "rust-analyzer"} for argument in arguments):
            forbidden.append(line)
    return forbidden


async def main():
    if not sys.platform.startswith("linux"):
        raise ValueError("the compiler-launch mechanism requires Linux exec tracing")
    # Reuse the repository runner's owned process groups, deadlines, logs, and cleanup.
    spec = importlib.util.spec_from_file_location("agent_debug", ROOT / "tools/agent-debug.py")
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    runner.install_signal_handlers()
    directory = runner.create_run_directory("test")
    environment = dict(os.environ)
    environment.update(RUSTUP_TOOLCHAIN="1.98.1", CARGO_BUILD_JOBS="2", NEXTEST_EXPERIMENTAL_LIBTEST_JSON="1")
    commands = []

    async def run(label, arguments, command="cargo"):
        output = directory / label
        argv = ["nextest"] + arguments if command == "cargo" else arguments
        result = await runner.run_supervised(runner.CommandSpec(command, argv), ROOT,
            environment, output, 5 * 60_000)
        commands.append(result)
        if result.get("timedOut") or result.get("spawnError") or result.get("signal") or not result["cleanup"].get("verifiedEmpty"):
            raise ValueError(f"{label}: command did not finish with verified process cleanup")
        return result["code"], (output / "stdout.log").read_text()

    try:
        code, _ = await run("integrity", [str(ROOT / "tools/test_sudus_engine_import.py")], command=sys.executable)
        if code != 0:
            raise ValueError("mechanism integrity checks failed")
        selection = ["-p", "rg_project", "--lib", "--offline"]
        code, text = await run("discovery", ["list"] + selection + ["--message-format", "json"])
        if code != 0:
            raise ValueError("acceptance test discovery or compilation failed")
        binary_id = discover(text)
        normal = [case for req, cases in CASES.items() if req != "MAC-006" for case in cases]
        report_flags = ["--message-format", "libtest-json", "--failure-output", "never", "--success-output", "never", "--no-fail-fast", "--retries", "0", "--no-tests", "fail", "--test-threads", "2"]
        code, text = await run("semantic", ["run"] + selection + report_flags + ["-E", "test(rustdoc_import) & not(test(mac_006_))"])
        observed = outcomes(text, binary_id, normal, code)

        # Only the test executable is traced. Cargo compilation precedes this observation window.
        strace = shutil.which("strace")
        env_command = shutil.which("env")
        if not strace or not env_command:
            raise ValueError("strace and env are required for compiler-launch observations")
        allowed_tools = directory / "query-tools"
        allowed_tools.mkdir()
        for name in ["cargo", "rustc", "rustup"]:
            tool = shutil.which(name)
            if not tool:
                raise ValueError(f"query metadata tool missing: {name}")
            (allowed_tools / name).symlink_to(tool)
        trace = directory / "query-exec.trace"
        tracer = shlex.join([strace, "-f", "-s", "4096", "-e", "trace=execve,execveat", "-o", str(trace), env_command, f"PATH={allowed_tools}"])
        code, text = await run("query-trace", ["run"] + selection + report_flags + ["--tracer", tracer, "--", PREFIX + CASES["MAC-006"][0], "--exact"])
        traced = outcomes(text, binary_id, CASES["MAC-006"], code)
        violations = forbidden_execs(trace.read_text())
        for violation in violations:
            print(f"forbidden compiler launch: {violation}", flush=True)
        observed.update(traced)
        results = {req: all(observed[case] for case in cases) and (req != "MAC-006" or not violations) for req, cases in CASES.items()}
        for req, passed in results.items():
            print(f"sudus: {req}: {'pass' if passed else 'fail'}", flush=True)
        return 0 if all(results.values()) else 1
    finally:
        runner.write_json(directory / "summary.json", {"commands": commands, "processCleanup": runner.summarize_cleanup(commands)})
        print(f"engine-import mechanism artifacts: {directory}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Emit no pass/fail claim for incomplete observations; Sudus records them as unverified.
        print(f"engine-import observation incomplete: {error}", file=sys.stderr)
        sys.exit(2)
