#!/usr/bin/env python3
"""Collect bounded Devlist diagnostic evidence before choosing a responsiveness fix."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from importlib.util import module_from_spec, spec_from_file_location


ROOT = Path(__file__).resolve().parent.parent
APP = Path("/home/shawn/workspace2/devlist.app")
PROTECTED = Path("/home/shawn/workspace2/suprnova")
REVISION = "3229aa9af542c991196274fa3c235cdce88a68e2"
MODES = ("faster-builds", "lower-peak-memory")
spec = spec_from_file_location("responsiveness_helpers", ROOT / "tools/sudus-editor-import.py")
helpers = module_from_spec(spec)
sys.modules[spec.name] = helpers
spec.loader.exec_module(helpers)
lsp = helpers.module("responsiveness_lsp", ROOT / "tools/lsp-query.py")


class Diagnostic:
    @staticmethod
    def percentile(values, percentile):
        if not values or any(type(value) is not int or value < 0 for value in values):
            raise ValueError("timings require a nonempty list of integer nanoseconds")
        if type(percentile) is not int or not 1 <= percentile <= 100:
            raise ValueError("percentile must be an integer from 1 to 100")
        return sorted(values)[(len(values) * percentile + 99) // 100 - 1]

    @staticmethod
    def inventory():
        """Hash application Rust/Cargo inputs without reading secrets or changing Git state."""
        if APP.resolve() == PROTECTED or PROTECTED in APP.resolve().parents:
            raise ValueError("application points at the protected framework checkout")
        files = {}
        for directory, children, names in os.walk(APP, followlinks=False):
            children[:] = sorted(name for name in children if name not in {".git", "target", "node_modules"})
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix == ".rs" or name in {"Cargo.toml", "Cargo.lock"}:
                    resolved = path.resolve()
                    if resolved == PROTECTED or PROTECTED in resolved.parents:
                        raise ValueError("application source points at the protected checkout")
                    files[str(path.relative_to(APP))] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name in (".cargo/config.toml", ".cargo/config"):
            path = APP / name
            if path.exists():
                if not path.resolve().is_relative_to(APP.resolve()):
                    raise ValueError("application Cargo configuration escapes its checkout")
                files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return files

    @classmethod
    def source_observation(cls, report, source="saved_exact"):
        return cls.hover_observation(report, [f"source-{n}" for n in range(3)],
            [("verify_password", "Result<bool, FrameworkError>")] * 3, source)

    @classmethod
    def source_only_observation(cls, report, source="saved_exact"):
        if any(event.get("method") == "suprnova-lsp/rustdocStatus" for event in report.get("lifecycle", [])):
            raise ValueError("automatic-disabled control observed worker activity")
        summary = cls.source_observation(report, source)
        summary["idleRssBytes"] = cls.idle_observation(report)
        return summary

    @staticmethod
    def idle_observation(report):
        if not helpers.memory_ok(report) or report.get("barriers", {}).get("hoverCleanup") is not True:
            raise ValueError("settled memory lacks independent hover cleanup evidence")
        observations = lsp.TransportObservations()
        observations.requests = {row["id"]: row for row in report.get("transport", [])}
        observations.stages = report.get("stages", [])
        if not observations.hover_cleanup_complete():
            raise ValueError("settled memory precedes hover cleanup")
        released = [event["observedNs"] for event in observations.stages
                    if event["message"] == "memory report" and event["fields"].get("label") == "hover"]
        previous = max(released)
        samples = report["idleMemory"]["samples"]
        for sample in samples:
            timestamp = sample.get("observedNs")
            if type(timestamp) is not int or timestamp < previous:
                raise ValueError("idle memory timestamp is missing or precedes request release")
            previous = timestamp
        return [sample["aggregateRssBytes"] for sample in samples]

    @classmethod
    def hover_observation(cls, report, labels, signatures, source):
        # Match replies to the entire sent hover ledger, so removing a slow/error
        # result cannot improve a distribution by changing only the results array.
        results = report.get("results", [])
        ledger = report.get("transport", [])
        sent = [row for row in ledger if row.get("method") == "textDocument/hover"]
        if len(results) != len(labels) or len(sent) != len(labels) or len(signatures) != len(labels):
            raise ValueError("diagnostic requires every planned hover and reply")
        if [row.get("label") for row in results] != labels:
            raise ValueError("hover workload is missing, reordered or duplicated")
        ids = [row.get("id") for row in sent]
        if any(type(value) is not int for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("hover request identities are invalid or duplicated")
        previous = None
        durations = []
        for result, row, expected in zip(results, sent, signatures):
            if result.get("kind") != "hover" or result.get("transport") != row or row.get("status") != "success":
                raise ValueError("hover result and transport observation disagree")
            written, received = row.get("writtenNs"), row.get("receivedNs")
            if type(written) is not int or type(received) is not int or written < 0 or received < written:
                raise ValueError("hover timestamps are absent or non-monotonic")
            if row.get("durationNs") != received - written or type(row.get("durationNs")) is not int:
                raise ValueError("hover duration contradicts its integer timestamps")
            if previous is not None and written < previous:
                raise ValueError("sequential hover requests overlap or changed order")
            previous = received
            text = result.get("text")
            if not isinstance(text, str) or any(signature not in text for signature in expected):
                raise ValueError("hover is empty or has the wrong signature")
            ready = [event for event in report.get("lifecycle", [])
                     if event.get("method") == "suprnova-lsp/activeWorkspaceChanged"
                     and event.get("params", {}).get("state") == "ready"
                     and event.get("params", {}).get("root") == str(APP)
                     and type(event.get("receivedNs")) is int and event["receivedNs"] <= written]
            if not ready:
                raise ValueError("source declaration readiness was not observed before hover")
            routes = [event for event in report.get("stages", [])
                      if event.get("message") == "editor document analysis route published"
                      and event.get("fields", {}).get("path") == str(APP / "src/models/user.rs")
                      and event.get("fields", {}).get("ready") is True
                      and type(event.get("observedNs")) is int and event["observedNs"] <= written]
            if not routes:
                raise ValueError("document route readiness was not observed before hover")
            durations.append(row["durationNs"])
        return {"firstNs": durations[0], "repeatedNs": durations[1:],
                "stages": cls.stage_observations(report, sent, source),
                "diagnosticP95Ns": cls.percentile(durations, 95), "maxNs": max(durations),
                "acceptance": "unverified: diagnostic counts are below the RSP minimum"}

    @staticmethod
    def stage_observations(report, sent, source):
        """Join native stage durations to this strictly sequential diagnostic workload."""
        completed = [event["fields"] for event in report.get("stages", [])
                     if event.get("message") == "analysis query completed" and event.get("fields", {}).get("query") == "hover"]
        prepared = [event["fields"] for event in report.get("stages", [])
                    if event.get("message") == "document analysis prepared" and event.get("fields", {}).get("query") == "hover"]
        if len(completed) != len(sent) or len(prepared) != len(sent):
            raise ValueError("sequential hover stage attribution is missing or duplicated")
        stages = []
        # Native work is serialized and this driver sends one hover at a time.
        # Group phase events at the matching completion rather than by phase name,
        # which repeats for several Cargo interpretations and requests.
        phase_groups, phases = [], []
        for event in report.get("stages", []):
            fields = event.get("fields", {})
            if event.get("message") == "document analysis phase" and fields.get("query") == "hover":
                elapsed = fields.get("elapsed_us")
                if type(elapsed) not in {str, int} or not str(elapsed).isdigit() or not isinstance(fields.get("phase"), str):
                    raise ValueError("preparation phase is malformed")
                phases.append({"phase": fields["phase"], "durationNs": int(elapsed) * 1000})
            if event.get("message") == "analysis query completed" and fields.get("query") == "hover":
                phase_groups.append(phases)
                phases = []
        if phases:
            raise ValueError("preparation phases lack an analysis completion")
        for row, execution, preparation in zip(sent, completed, prepared):
            if execution.get("status") != "ok" or preparation.get("source") != source:
                raise ValueError("stage attribution describes a different document source")
            fields = [execution.get("queued_ms"), execution.get("elapsed_ms"), preparation.get("elapsed_us")]
            if any(type(value) not in {str, int} or not str(value).isdigit() for value in fields):
                raise ValueError("stage durations are absent or malformed")
            stages.append({"id": row["id"], "transportNs": row["durationNs"], "queuedNs": int(fields[0]) * 1_000_000,
                           "executionNs": int(fields[1]) * 1_000_000, "preparationNs": int(fields[2]) * 1000,
                           "preparationPhases": phase_groups[len(stages)],
                           "correlation": "one outstanding hover; all three lifecycle/preparation observations in request order"})
        return stages

    @staticmethod
    def workload_plan(mode, directory, workload):
        plan = {"file": "src/models/user.rs", "format": "json", "readinessBarrier": "ready", "documentReadinessBarrier": True,
            "deferredBarrier": "after-queries", "idleMemory": False,
            "queries": [{"kind": "hover", "label": f"source-{n}", "marker": "pub fn verify_password", "delta": 7} for n in range(3)],
            "initializationOptions": {"cfg": {"test": False}, "cache": {"packageResidency": "workspace"},
                "indexing": {"performancePreference": mode}, "rustdoc": {"automatic": {
                    "artifactRoot": str(directory / mode / "compiler"), "toolchain": "nightly-2026-08-19", "jobs": 2}}}}
        if workload in {"source-only", "source-current"}:
            plan["initializationOptions"]["rustdoc"]["automatic"]["enabled"] = False
            plan["idleMemory"] = True
            plan["hoverCleanupBarrier"] = True
        if workload == "source-current":
            original = (APP / plan["file"]).read_text()
            signature = "pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {"
            if original.count(signature) != 1:
                raise ValueError("Devlist source method changed")
            plan["text"] = original.replace(signature, signature + "\n")
        if workload == "generated-settled":
            original = (APP / plan["file"]).read_text()
            signature = "pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {"
            if original.count(signature) != 1:
                raise ValueError("Devlist source method changed")
            plan["text"] = original.replace(signature, signature + '\n        use suprnova::eloquent::Model as _;\n'
                '        let rsp_query = User::query();\n        let rsp_without = User::without_global_scopes();\n'
                '        let rsp_filter = User::filter("email", "member@example.test");\n')
            plan.update(rustdocBarrier="before-queries", rustdocTimeoutMs=900000, deferredBarrier="before-queries", idleMemory=True, hoverCleanupBarrier=True,
                queries=[{"kind": "hover", "label": marker, "marker": marker} for marker in ("rsp_query", "rsp_without", "rsp_filter")])
        return plan

    @classmethod
    def generated_observation(cls, report):
        sent = [row for row in report.get("transport", []) if row.get("method") == "textDocument/hover"]
        for row in sent:
            events = [event["params"] for event in report.get("lifecycle", [])
                      if event.get("method") == "suprnova-lsp/rustdocStatus"
                      and event.get("params", {}).get("workspaceRoot") in {str(APP), APP.as_uri()}
                      and type(event.get("receivedNs")) is int and event["receivedNs"] <= row.get("writtenNs", -1)]
            if not events or any(type(event.get("generation")) is not int for event in events):
                raise ValueError("generated readiness was not independently observed before hover")
            generation = max(event["generation"] for event in events)
            latest = next(event for event in reversed(events) if event["generation"] == generation)
            if latest.get("state") != "current":
                raise ValueError("latest generated declarations were not current before hover")
        summary = cls.hover_observation(report, ["rsp_query", "rsp_without", "rsp_filter"], [("Builder<User>",)] * 3, "current")
        summary["idleRssBytes"] = cls.idle_observation(report)
        return summary

    @staticmethod
    def configure_limits(nofile_soft):
        """Apply only explicitly requested Linux process limits and record both values."""
        if not sys.platform.startswith("linux"):
            raise ValueError("this diagnostic requires Linux for its owned-process RSS observations")
        import resource
        limits = resource.getrlimit(resource.RLIMIT_NOFILE)
        if nofile_soft is not None:
            if not 1024 <= nofile_soft <= limits[1]:
                raise ValueError("requested open-file limit is outside the inherited hard limit")
            resource.setrlimit(resource.RLIMIT_NOFILE, (nofile_soft, limits[1]))
        return {"inherited": list(limits), "effective": list(resource.getrlimit(resource.RLIMIT_NOFILE))}

    async def observe_mode(self, mode, directory, workload, command):
        """Run one immutable workload through the bounded stdio observer."""
        plan = self.workload_plan(mode, directory, workload)
        plan_path = directory / f"{mode}-plan.json"
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
        runtime_minutes = 17 if workload == "generated-settled" else 5
        text = await command(mode, "just", ["agent-debug", "--no-build", "--timeout", f"{runtime_minutes}m", "--measure",
            "--log", "rg_lsp_engine=trace,rg_lsp_server=debug", "lsp-query", "--workspace-root", str(APP),
            "--query-file", str(plan_path), "--timeout-ms", "300000", "--json"], timeout=(runtime_minutes + 1) * 60_000)
        # The supervisor appends its own summary. The first JSON document is
        # the query result; preserve the entire stdout separately as evidence.
        start = text.index('{\n  "file"')
        report, _ = json.JSONDecoder().raw_decode(text[start:])
        observe = {"source": self.source_observation, "source-only": self.source_only_observation,
                   "source-current": self.source_only_observation,
                   "generated-settled": self.generated_observation}[workload]
        summary = observe(report, "current") if workload == "source-current" else observe(report)
        return {"raw": report, "summary": summary, "plan": plan}

    async def run(self, modes, no_build, nofile_soft=None, workload="source"):
        limits = self.configure_limits(nofile_soft)
        runner = helpers.module("responsiveness_runner", ROOT / "tools/agent-debug.py")
        runner.install_signal_handlers()
        directory = runner.create_run_directory("rsp-diagnostic")
        commands, reports = [], {}
        original = self.inventory()
        build_environment = dict(os.environ, RUSTUP_TOOLCHAIN="1.98.1", CARGO_BUILD_JOBS="2",
                                 CARGO_NET_OFFLINE="true", CARGO_TARGET_DIR=str(runner.BUILD_ROOT),
                                 CARGO_BUILD_BUILD_DIR=str(ROOT / "target/agent-debug/build-intermediates"))
        environment = dict(build_environment, RUSTUP_TOOLCHAIN="nightly-2026-08-19",
            CARGO_TARGET_DIR=str(ROOT / "target/agent-debug/devlist-target"),
            CARGO_BUILD_BUILD_DIR=str(ROOT / "target/agent-debug/devlist-build"))

        async def command(label, program, args, env=environment, timeout=60_000):
            output = directory / label
            print(f"observing {label}: {output}", flush=True)
            result, text = await runner.observe_command(runner.CommandSpec(program, args), ROOT, env, output, timeout)
            result["phase"] = label
            commands.append(result)
            if result["code"] != 0:
                raise ValueError(f"{label}: observation failed; inspect {output}")
            return text

        identity = {"purpose": "small diagnostic, not RSP acceptance", "workload": workload, "application": str(APP),
                    "openFileLimits": limits,
                    "sources": original, "frameworkRevision": REVISION, "buildProfile": "release",
                    "producerToolchain": "nightly-2026-08-19",
                    "cacheState": "existing LSP/compiler caches; fresh owned export artifact root",
                    "observers": {name: hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest()
                                  for name in ("lsp-query.py", "sudus-responsiveness.py", "agent-debug.py")}}
        complete = False
        try:
            metadata = json.loads(await command("metadata", "cargo", ["metadata", "--manifest-path", str(APP / "Cargo.toml"),
                "--locked", "--offline", "--format-version", "1", "--filter-platform", runner.host_target()]))
            framework = [package for package in metadata["packages"] if package["name"] == "suprnova"]
            if len(framework) != 1 or not (framework[0].get("source") or "").endswith("#" + REVISION):
                raise ValueError("application framework is not the approved pinned dependency")
            for package in metadata["packages"]:
                path = Path(package["manifest_path"]).resolve()
                if path == PROTECTED or PROTECTED in path.parents:
                    raise ValueError("application metadata reached the protected framework checkout")
            identity["metadataSha256"] = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
            identity["buildCompiler"] = await command("build-compiler", "rustc", ["-vV"], env=build_environment)
            identity["producerCompiler"] = await command("producer-compiler", "rustc", ["-vV"])
            identity["commit"] = (await command("commit", "git", ["rev-parse", "HEAD"])).strip()
            await command("runtime-inputs", "git", ["diff", "--exit-code", "HEAD", "--",
                "crates", "Cargo.lock", "Cargo.toml", "rust-toolchain.toml"])
            if not no_build:
                build = runner.build_spec(runner.RunnerOptions(build_profile="release"))
                await command("build", build.command, [*build.args, "--locked", "--offline"], env=build_environment, timeout=20 * 60_000)
            binary = runner.rust_glancer_binary("release")
            identity["binary"] = str(binary)
            identity["binarySha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
            for mode in modes:
                reports[mode] = await self.observe_mode(mode, directory, workload, command)
            complete = True
        finally:
            after = self.inventory()
            result = {"identity": identity, "reports": reports, "commands": commands,
                      "observationComplete": complete,
                      "applicationInputsUnchanged": original == after,
                      "processCleanup": runner.summarize_cleanup(commands),
                      "requirements": {f"RSP-{n:03}": "unverified" for n in range(1, 7)}}
            if not complete:
                result["processCleanup"]["status"] = "unverified"
                result["processCleanup"]["reason"] = "incomplete observation; inspect the individual supervisor artifacts"
            (directory / "report.json").write_text(json.dumps(result, indent=2) + "\n")
            print(f"Diagnostic report: {directory / 'report.json'}", flush=True)
            if original != after:
                raise ValueError("application Rust/Cargo inputs changed during observation")
        for mode, report in reports.items():
            print(mode, json.dumps(report["summary"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", action="store_true", required=True,
                        help="small source workload; emits no passing Sudus requirement verdicts")
    parser.add_argument("--mode", choices=MODES, action="append")
    parser.add_argument("--no-build", action="store_true", help="intentionally use an existing managed optimized binary")
    parser.add_argument("--workload", choices=("source", "source-only", "source-current", "generated-settled"), default="source")
    parser.add_argument("--nofile-soft", type=int, help="explicit open-file limit for this diagnostic process and its children only")
    options = parser.parse_args()
    try:
        asyncio.run(Diagnostic().run(options.mode or MODES, options.no_build, options.nofile_soft, options.workload))
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f"responsiveness observation incomplete: {error}", file=sys.stderr)
        sys.exit(2)
