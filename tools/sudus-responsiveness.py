#!/usr/bin/env python3
"""Collect bounded Devlist diagnostic evidence before choosing a responsiveness fix."""

import argparse
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
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
    purpose = "small diagnostic, not RSP acceptance"
    run_kind = "rsp-diagnostic"
    log_filter = "rg_lsp_engine=trace,rg_lsp_server=debug,rg_lsp_server::client_status=trace"

    @staticmethod
    def percentile(values, percentile):
        if not values or any(type(value) is not int or value < 0 for value in values):
            raise ValueError("timings require a nonempty list of integer nanoseconds")
        if type(percentile) is not int or not 1 <= percentile <= 100:
            raise ValueError("percentile must be an integer from 1 to 100")
        return sorted(values)[(len(values) * percentile + 99) // 100 - 1]

    @staticmethod
    def duration_ns(value, unit_ns):
        """Native durations may be JSON integers or tracing's decimal strings."""
        if type(value) not in {str, int} or not str(value).isdigit():
            raise ValueError("stage durations are absent or malformed")
        return int(value) * unit_ns

    @staticmethod
    def inventory(root=APP):
        """Hash application Rust/Cargo inputs without reading secrets or changing Git state."""
        if root.resolve() == PROTECTED or PROTECTED in root.resolve().parents:
            raise ValueError("application points at the protected framework checkout")
        files = {}
        for directory, children, names in os.walk(root, followlinks=False):
            children[:] = sorted(name for name in children if name not in {".git", "target", "node_modules"})
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix == ".rs" or name in {"Cargo.toml", "Cargo.lock"}:
                    resolved = path.resolve()
                    if resolved == PROTECTED or PROTECTED in resolved.parents:
                        raise ValueError("application source points at the protected checkout")
                    files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name in (".cargo/config.toml", ".cargo/config"):
            path = root / name
            if path.exists():
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("application Cargo configuration escapes its checkout")
                files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return files

    @classmethod
    def runtime_fingerprint(cls):
        """Fingerprint native source independently of commits that also change observers."""
        sources = cls.inventory(ROOT / "crates")
        for name in ("Cargo.lock", "Cargo.toml", "rust-toolchain.toml"):
            sources[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        return hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()

    @classmethod
    def source_observation(cls, report, source="saved_exact", count=3):
        return cls.hover_observation(report, [f"source-{n}" for n in range(count)],
            [("verify_password", "Result<bool, FrameworkError>")] * count, source)

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

    @classmethod
    def stage_observations(cls, report, sent, source):
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
                if not isinstance(fields.get("phase"), str):
                    raise ValueError("preparation phase is malformed")
                phases.append({"phase": fields["phase"], "durationNs": cls.duration_ns(fields.get("elapsed_us"), 1000)})
            if event.get("message") == "analysis query completed" and fields.get("query") == "hover":
                phase_groups.append(phases)
                phases = []
        if phases:
            raise ValueError("preparation phases lack an analysis completion")
        for row, execution, preparation in zip(sent, completed, prepared):
            if execution.get("status") != "ok" or preparation.get("source") != source:
                raise ValueError("stage attribution describes a different document source")
            stages.append({"id": row["id"], "transportNs": row["durationNs"],
                           "queuedNs": cls.duration_ns(execution.get("queued_ms"), 1_000_000),
                           "executionNs": cls.duration_ns(execution.get("elapsed_ms"), 1_000_000),
                           "preparationNs": cls.duration_ns(preparation.get("elapsed_us"), 1000),
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
        if workload in {"generated-settled", "generated-captured"}:
            original = (APP / plan["file"]).read_text()
            signature = "pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {"
            if original.count(signature) != 1:
                raise ValueError("Devlist source method changed")
            plan["text"] = original.replace(signature, signature + '\n        use suprnova::eloquent::Model as _;\n'
                '        let rsp_query = User::query();\n        let rsp_without = User::without_global_scopes();\n'
                '        let rsp_filter = User::filter("email", "member@example.test");\n')
            plan.update(rustdocBarrier="before-queries", rustdocTimeoutMs=900000, deferredBarrier="before-queries", idleMemory=True, hoverCleanupBarrier=True,
                  queries=[{"kind": "hover", "label": marker, "marker": marker} for marker in ("rsp_query", "rsp_without", "rsp_filter")])
            if workload == "generated-captured":
                plan["rustdocBarrier"] = "none"
                plan["initializationOptions"]["rustdoc"].update(
                    automatic={"enabled": False},
                    inputs=[{"workspaceRoot": str(APP), "manifestPath": str(APP / "Cargo.toml"),
                             "targetName": "directory", "targetKind": "lib",
                             "exportPath": str(directory / "directory.json"),
                             "itemPath": "directory::models::user::User"}],
                )
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

    async def observe_mode(self, mode, directory, workload, command, session_number=None):
        """Run one immutable workload through the bounded stdio observer."""
        plan = self.workload_plan(mode, directory, workload)
        label = mode
        if session_number is not None:
            label = f"{mode}-{session_number:02}"
            plan["queries"] = [dict(plan["queries"][0], label=f"source-{n}") for n in range(6)]
            plan["workerRunningBarrier"] = True
            plan["initializationOptions"]["rustdoc"]["automatic"]["artifactRoot"] = str(directory / label / "compiler")
        report = await self.observe_plan(label, plan, directory, command,
            runtime_minutes=17 if workload == "generated-settled" else 5)
        if workload == "generated-captured":
            summary = self.hover_observation(report, ["rsp_query", "rsp_without", "rsp_filter"], [("Builder<User>",)] * 3, "current")
            summary["idleRssBytes"] = self.idle_observation(report)
        elif session_number is not None:
            summary = self.source_observation(report, count=6)
        else:
            observe = {"source": self.source_observation, "source-only": self.source_only_observation,
                       "source-current": self.source_only_observation,
                       "generated-settled": self.generated_observation}[workload]
            summary = observe(report, "current") if workload == "source-current" else observe(report)
        return {"raw": report, "summary": summary, "plan": plan}

    async def observe_plan(self, label, plan, directory, command, runtime_minutes=5):
        """Keep all workloads on the existing supervised stdio transport."""
        plan_path = directory / f"{label}-plan.json"
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
        binary_args = ["--binary", str(self.binary)] if getattr(self, "binary", None) else []
        environment = {"env": self.compiler_environment} if getattr(self, "compiler_environment", None) else {}
        text = await command(label, "just", ["agent-debug", "--no-build", "--timeout", f"{runtime_minutes}m", "--measure",
              "--log", self.log_filter, "lsp-query", "--workspace-root", str(APP),
              "--query-file", str(plan_path), "--timeout-ms", "300000", "--json", *binary_args],
              timeout=(runtime_minutes + 1) * 60_000, **environment)
        # Successful stdout contains the raw protocol observation followed by the supervisor summary.
        start = text.index('{\n  "file"')
        report, _ = json.JSONDecoder().raw_decode(text[start:])
        return report

    async def run(self, modes, no_build, nofile_soft=None, workload="source"):
        limits = self.configure_limits(nofile_soft)
        runner = helpers.module("responsiveness_runner", ROOT / "tools/agent-debug.py")
        runner.install_signal_handlers()
        directory = runner.create_run_directory(self.run_kind)
        commands, reports = [], {}
        original = self.inventory()
        original_runtime = self.runtime_fingerprint()
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

        identity = {"purpose": self.purpose, "workload": workload, "application": str(APP),
                    "openFileLimits": limits,
                    "sources": original, "frameworkRevision": REVISION, "buildProfile": "release",
                    "runtimeSourcesSha256": original_runtime,
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
            if workload == "generated-captured":
                # Replay only a genuine compiler export whose complete producer
                # inputs still match Devlist. It changes neither the application
                # nor its dependency; the export is owned by this diagnostic.
                user = helpers.module("rsp_captured_user", ROOT / "tools/sudus-suprnova-user.py")
                cfg = await command("producer-cfg", "rustc", ["--print", "cfg", "--target", user.TARGET])
                sysroot = Path((await command("producer-sysroot", "rustc", ["--print", "sysroot"])).strip()) / "lib/rustlib/src/rust/library"
                producer = json.loads((user.CAPTURE / "producer.json").read_text())
                with gzip.open(user.CAPTURE / "export.json.gz", "rb") as stream:
                    export = stream.read(256 * 1024 * 1024 + 1)
                if len(export) > 256 * 1024 * 1024:
                    raise ValueError("captured export exceeds the reader bound")
                user.validate_capture(producer, metadata, sysroot, identity["producerCompiler"], cfg, export)
                (directory / "directory.json").write_bytes(export)
                identity["capturedExportSha256"] = user.digest(export)
                del export
            identity["commit"] = (await command("commit", "git", ["rev-parse", "HEAD"])).strip()
            runtime_diff = await command("runtime-inputs", "git", ["diff", "HEAD", "--",
                "crates", "Cargo.lock", "Cargo.toml", "rust-toolchain.toml"])
            identity["runtimeDiffSha256"] = hashlib.sha256(runtime_diff.encode()).hexdigest()
            if not no_build:
                build = runner.build_spec(runner.RunnerOptions(build_profile="release"))
                await command("build", build.command, [*build.args, "--locked", "--offline"], env=build_environment, timeout=20 * 60_000)
            binary = getattr(self, "binary", None) or runner.suprnova_lsp_binary("release")
            identity["binary"] = str(binary)
            identity["binarySha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
            if getattr(self, "compiler_seed_manifest", None):
                self.configure_compiler_cache(directory, environment, identity)
            for mode in modes:
                reports[mode] = await self.observe_mode(mode, directory, workload, command)
            complete = True
        finally:
            after = self.inventory()
            binary_unchanged = None
            if "binary" in identity:
                binary_unchanged = hashlib.sha256(Path(identity["binary"]).read_bytes()).hexdigest() == identity["binarySha256"]
            cache_unchanged = None
            if "compilerCache" in identity:
                cache_unchanged = self.cache_module.CompilerCache.inventory(self.compiler_seed) == self.compiler_seed_inventory
            result = {"identity": identity, "reports": reports, "commands": commands,
                      "observationComplete": complete,
                        "applicationInputsUnchanged": original == after,
                        "binaryUnchanged": binary_unchanged,
                          "runtimeSourcesUnchanged": original_runtime == self.runtime_fingerprint(),
                        "compilerSeedUnchanged": cache_unchanged,
                      "processCleanup": runner.summarize_cleanup(commands),
                      "requirements": {f"RSP-{n:03}": "unverified" for n in range(1, 7)}}
            if not complete:
                result["processCleanup"]["status"] = "unverified"
                result["processCleanup"]["reason"] = "incomplete observation; inspect the individual supervisor artifacts"
            (directory / "report.json").write_text(json.dumps(result, indent=2) + "\n")
            print(f"Observation report: {directory / 'report.json'}", flush=True)
            if original != after:
                raise ValueError("application Rust/Cargo inputs changed during observation")
            if binary_unchanged is False:
                raise ValueError("managed server binary changed during observation")
            if not result["runtimeSourcesUnchanged"]:
                raise ValueError("native source changed during observation")
            if cache_unchanged is False:
                raise ValueError("compiler cache seed changed during observation")
        for mode, report in reports.items():
            if "summary" in report:
                print(mode, json.dumps({key: value for key, value in report["summary"].items()
                                       if key not in {"stages", "workerGenerations"}}))
            else:
                print(mode, "observed", len(report["series"]), "separate latency series")
        return directory / "report.json"


class SourceSeries(Diagnostic):
    """A complete source/active-worker cohort; other contract windows stay unverified."""

    purpose = "source active-worker baseline series; not complete RSP acceptance"
    run_kind = "rsp-source-series"

    @staticmethod
    def worker_overlap(report):
        events = [event for event in report.get("lifecycle", [])
                  if event.get("method") == "suprnova-lsp/rustdocStatus"
                  and event.get("params", {}).get("workspaceRoot") in {str(APP), APP.as_uri()}]
        if any(type(event.get("receivedNs")) is not int or type(event["params"].get("generation")) is not int
               or event["params"]["generation"] < 0 for event in events):
            raise ValueError("worker overlap has invalid timestamps or generations")
        generations = []
        for row in report["transport"]:
            if row["method"] != "textDocument/hover":
                continue
            before = [event for event in events if event["receivedNs"] <= row["writtenNs"]]
            if not before:
                raise ValueError("worker was not observed before hover")
            generation = max(event["params"]["generation"] for event in before)
            latest = next(event["params"] for event in reversed(before) if event["params"]["generation"] == generation)
            if latest.get("state") != "running":
                raise ValueError("worker was not running before hover")
            during = [event for event in events if row["writtenNs"] < event["receivedNs"] <= row["receivedNs"]
                      and event["params"]["generation"] >= generation]
            if any(event["params"]["generation"] != generation or event["params"].get("state") != "running" for event in during):
                raise ValueError("worker changed state during hover response")
            generations.append(generation)
        return generations

    @classmethod
    def assess(cls, sessions, mode):
        if mode not in MODES or len(sessions) != 20:
            raise ValueError("source series requires 20 independent sessions in one indexing mode")
        identities, first, repeated, stages, generations = set(), [], [], [], []
        for session in sessions:
            raw, plan = session["raw"], session["plan"]
            # Only artifact paths vary between sessions. Every sampled query is
            # the same saved declaration, with no body overlay or hover warm-up.
            expected = cls.workload_plan(mode, ROOT / "target/agent-debug", "source")
            expected["queries"] = [dict(expected["queries"][0], label=f"source-{n}") for n in range(6)]
            expected["workerRunningBarrier"] = True
            actual = json.loads(json.dumps(plan))
            actual["initializationOptions"]["rustdoc"]["automatic"]["artifactRoot"] = expected["initializationOptions"]["rustdoc"]["automatic"]["artifactRoot"]
            if actual != expected:
                raise ValueError("source series mixes modes, symbols or measurement configuration")
            initialized = [row for row in raw["transport"] if row.get("method") == "initialize"]
            pid = raw.get("session", {}).get("serverPid")
            if len(initialized) != 1 or initialized[0].get("status") != "success" or type(pid) is not int or pid <= 0:
                raise ValueError("source series lacks a successful fresh server initialization")
            timestamp = initialized[0].get("receivedNs")
            if type(timestamp) is not int or timestamp < 0:
                raise ValueError("source session initialization timestamp is invalid")
            identity = (pid, timestamp)
            if identity in identities:
                raise ValueError("source series reuses a server session")
            identities.add(identity)
            summary = cls.source_observation(raw, count=6)
            if timestamp >= next(row["writtenNs"] for row in raw["transport"] if row["method"] == "textDocument/hover"):
                raise ValueError("hover precedes its server initialization")
            generations.append(cls.worker_overlap(raw))
            first.append(summary["firstNs"])
            repeated.extend(summary["repeatedNs"])
            stages.append(summary["stages"])
        result = {"mode": mode, "symbols": "source", "window": "automatic-worker-active",
                  "workerGenerations": generations, "stages": stages,
                  "acceptance": "unverified: other required windows and before/after comparisons remain pending"}
        for cohort, samples in (("first", first), ("repeated", repeated)):
            p95 = cls.percentile(samples, 95)
            result[cohort] = {"count": len(samples), "rawNs": samples, "firstResponseNs": samples[0],
                "p50Ns": cls.percentile(samples, 50), "p95Ns": p95, "maxNs": max(samples),
                "failures": 0, "belowTarget": p95 < 200_000_000}
        return result

    async def observe_mode(self, mode, directory, workload, command):
        sessions = []
        for number in range(20):
            sessions.append(await super().observe_mode(mode, directory, workload, command, session_number=number))
            # Preserve successful sessions if a later one fails. Failed commands
            # keep their own transport ledger and supervised cleanup artifacts.
            (directory / f"{mode}-sessions.json").write_text(json.dumps(sessions, indent=2) + "\n")
        return {"sessions": sessions, "summary": self.assess(sessions, mode)}


class AcceptanceMatrix(Diagnostic):
    purpose = "complete latency matrix; semantics, races and memory remain separate gates"
    run_kind = "rsp-matrix"
    symbols = ("source", "rsp_query", "rsp_without", "rsp_filter")
    windows = ("worker", "settled", "deferred")

    def configure_compiler_cache(self, directory, environment, identity):
        """Keep warm worker measurements separate from the original cold observation."""
        manifest_path = self.compiler_seed_manifest.resolve(strict=True)
        owned = ROOT / "target/agent-debug"
        if not manifest_path.is_relative_to(owned) or manifest_path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("compiler cache provenance escapes the owned root or exceeds its bound")
        manifest = json.loads(manifest_path.read_text())
        wanted = ["copy-owned-cache", "prime-lib", "prime-directory", "prime-console"]
        commands = manifest.get("commands", [])
        if (manifest.get("observationComplete") is not True or manifest.get("applicationInputsUnchanged") is not True
            or manifest.get("sources") != self.inventory() or manifest.get("toolchain") != "nightly-2026-08-19"
            or manifest.get("target") != "x86_64-unknown-linux-gnu"
            or [item.get("phase") for item in commands] != wanted
            or any(item.get("code") != 0 or item.get("cleanup", {}).get("verifiedEmpty") is not True for item in commands)):
            raise ValueError("compiler cache lacks complete genuine pinned producer observations")
        self.compiler_seed = Path(manifest["cache"]).resolve(strict=True)
        if not self.compiler_seed.is_relative_to(manifest_path.parent) or not self.compiler_seed.is_relative_to(owned):
            raise ValueError("compiler seed escapes its priming observation")
        self.cache_module = helpers.module("rsp_compiler_cache", ROOT / "tools/responsiveness-cargo-cache.py")
        self.compiler_seed_inventory = self.cache_module.CompilerCache.inventory(self.compiler_seed)
        shim = directory / "compiler-tools"
        shim.mkdir()
        (shim / "cargo").symlink_to(ROOT / "tools/responsiveness-cargo-cache.py")
        config = directory / "compiler-cache-config.json"
        config.write_text(json.dumps({"seed": str(self.compiler_seed), "cargo": shutil.which("cargo"),
                                      "events": str(directory / "compiler-cache-events.jsonl")}) + "\n")
        self.compiler_environment = dict(environment, PATH=str(shim) + os.pathsep + environment["PATH"],
                                         RSP_COMPILER_CACHE_CONFIG=str(config))
        identity["compilerCache"] = {"state": "private copies of a disclosed owned warm cache",
            "manifest": str(manifest_path), "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "seed": str(self.compiler_seed),
            "inventorySha256": hashlib.sha256(json.dumps(self.compiler_seed_inventory, sort_keys=True).encode()).hexdigest(),
            "observerSha256": hashlib.sha256((ROOT / "tools/responsiveness-cargo-cache.py").read_bytes()).hexdigest()}
        identity["cacheState"] = "existing LSP caches; private copied warm compiler caches; genuine Cargo worker exports staged in a fresh owned root"

    @classmethod
    def series_plan(cls, mode, directory, symbol, window):
        if mode not in MODES or symbol not in cls.symbols or window not in cls.windows:
            raise ValueError("unknown latency series")
        if mode == "lower-peak-memory" and window == "deferred":
            raise ValueError("lower-memory initial deferred window is structurally absent")
        workload = "source" if symbol == "source" else "generated-captured"
        plan = cls.workload_plan(mode, directory, workload)
        plan.update(recordSession=True, idleMemory=False, hoverCleanupBarrier=False,
                    deferredBarrier="before-queries" if window == "settled" else "after-queries")
        plan["initializationOptions"]["rustdoc"]["automatic"] = {
            "enabled": window == "worker", "artifactRoot": str(directory / mode / "compiler"),
            "toolchain": "nightly-2026-08-19", "jobs": 2}
        if window == "worker":
            if symbol == "source":
                plan["workerRunningBarrier"] = True
            else:
                plan["initializationOptions"]["rustdoc"].pop("inputs")
                plan["workerReindexBarrier"] = True
                plan["rustdocTimeoutMs"] = 900000
        if window == "deferred":
            plan["deferredWindow"] = True
        query = plan["queries"][0] if symbol == "source" else next(q for q in plan["queries"] if q["marker"] == symbol)
        plan["queries"] = [dict(query, label=f"{symbol}-{n}") for n in range(6)]
        return plan

    @staticmethod
    def configured_publication(raw, plan, written):
        expected = plan["initializationOptions"]["rustdoc"]["inputs"][0]
        fields = {"root": expected["workspaceRoot"], "manifest_path": expected["manifestPath"],
                  "target_name": expected["targetName"], "target_kind": expected["targetKind"],
                  "export_path": expected["exportPath"], "item_path": expected["itemPath"]}
        events = [event for event in raw.get("stages", [])
                  if event.get("message") == "configured rustdoc declarations published"
                  and type(event.get("observedNs")) is int and event["observedNs"] <= written
                  and all(event.get("fields", {}).get(key) == value for key, value in fields.items())]
        if len(events) != 1 or type(events[0]["fields"].get("generation")) is not int or events[0]["fields"]["generation"] < 0:
            raise ValueError("configured generated declarations lack independent publication evidence")
        return events[0]["fields"]["generation"]

    @staticmethod
    def window_evidence(raw, window, sent):
        if window == "worker":
            return SourceSeries.worker_overlap(raw)
        generations = []
        for row in sent:
            if window == "settled":
                events = [event for event in raw.get("lifecycle", [])
                          if event.get("method") == lsp.SERVER_STATUS
                          and type(event.get("receivedNs")) is int and event["receivedNs"] <= row["writtenNs"]]
                if not events or events[-1]["params"].get("health") != "ok" or events[-1]["params"].get("quiescent") is not True:
                    raise ValueError("settled hover preceded independently observed completion")
            events = [event for event in raw.get("stages", [])
                      if event.get("message") in {"deferred indexing lifecycle started", "deferred indexing lifecycle finished"}
                      and event.get("fields", {}).get("root") == str(APP)
                      and type(event.get("observedNs")) is int and event["observedNs"] <= row["writtenNs"]]
            if not events or any(type(event["fields"].get("generation")) is not int or event["fields"]["generation"] < 0 for event in events):
                raise ValueError("hover lacks accepted deferred-generation evidence")
            generation = max(event["fields"]["generation"] for event in events)
            latest = next(event for event in reversed(events) if event["fields"]["generation"] == generation)
            expected = "started" if window == "deferred" else "finished"
            if latest["message"] != "deferred indexing lifecycle " + expected:
                raise ValueError("hover was sent outside its required background window")
            generations.append(generation)
        return generations

    @classmethod
    def assess(cls, sessions, mode, directory, symbol, window, require_counts=True):
        expected = cls.series_plan(mode, directory, symbol, window)
        identities, first, repeated, evidence = set(), [], [], []
        for session in sessions:
            raw, plan = session["raw"], session["plan"]
            actual = json.loads(json.dumps(plan))
            actual["initializationOptions"]["rustdoc"]["automatic"]["artifactRoot"] = expected["initializationOptions"]["rustdoc"]["automatic"]["artifactRoot"]
            if actual != expected:
                raise ValueError("latency series mixes modes, symbols or configuration")
            initialized = [row for row in raw["transport"] if row.get("method") == "initialize"]
            identity = raw.get("session")
            pid = identity.get("serverPid") if isinstance(identity, dict) else None
            if len(initialized) != 1 or initialized[0].get("status") != "success" or type(pid) is not int or pid <= 0:
                raise ValueError("latency series lacks fresh successful server initialization")
            initialized_ns = initialized[0].get("receivedNs")
            if type(initialized_ns) is not int or initialized_ns < 0 or (pid, initialized_ns) in identities:
                raise ValueError("latency series reuses a session or lacks initialization timestamps")
            identities.add((pid, initialized_ns))
            sent = [row for row in raw["transport"] if row.get("method") == "textDocument/hover"]
            count = len(sent)
            if window == "deferred":
                boundary = raw.get("deferredWindow", {})
                if boundary.get("planned") != 6 or boundary.get("sent") != count or not 0 <= count <= 6:
                    raise ValueError("deferred workload ledger contradicts its unsent remainder")
                if count < 6 and boundary.get("closedBeforeNextSend") is not True:
                    raise ValueError("deferred samples disappeared without an observed closed window")
                if count < 6:
                    closed_ns = boundary.get("closedNs")
                    if type(closed_ns) is not int or closed_ns < initialized_ns or (sent and closed_ns < sent[-1]["receivedNs"]):
                        raise ValueError("deferred window closure lacks an ordered observation")
                    cls.window_evidence(raw, "closed", [{"writtenNs": closed_ns}])
                if not count:
                    if raw.get("results"):
                        raise ValueError("unsent deferred session contains fabricated replies")
                    continue
            elif count != 6:
                raise ValueError("latency series dropped a planned hover")
            labels = [f"{symbol}-{n}" for n in range(count)]
            signatures = [("verify_password", "Result<bool, FrameworkError>") if symbol == "source" else ("Builder<User>",)] * count
            summary = cls.hover_observation(raw, labels, signatures, "saved_exact" if symbol == "source" else "current")
            if initialized_ns >= sent[0]["writtenNs"]:
                raise ValueError("hover preceded initialization")
            if symbol != "source" and window == "worker":
                cls.worker_publication(raw, sent)
            elif symbol != "source":
                for row in sent:
                    cls.configured_publication(raw, plan, row["writtenNs"])
            evidence.append(cls.window_evidence(raw, window, sent))
            first.append(summary["firstNs"])
            repeated.extend(summary["repeatedNs"])
        if require_counts and (len(first) < 20 or len(repeated) < 100):
            raise ValueError("latency series is undersampled: at least 20 first sessions and 100 repeats are required")
        result = {"mode": mode, "symbol": symbol, "window": window, "sessions": len(sessions),
                  "backgroundGenerations": evidence, "completeCounts": len(first) >= 20 and len(repeated) >= 100}
        for cohort, samples in (("first", first), ("repeated", repeated)):
            result[cohort] = {"count": len(samples), "rawNs": samples, "failures": 0}
            if samples:
                result[cohort].update(firstResponseNs=samples[0], p50Ns=cls.percentile(samples, 50),
                    p95Ns=cls.percentile(samples, 95), maxNs=max(samples), belowTarget=cls.percentile(samples, 95) < 200_000_000)
        return result

    @staticmethod
    def worker_publication(raw, sent):
        reindex = [row for row in raw["transport"] if row.get("method") == "workspace/executeCommand"]
        if len(reindex) != 1 or reindex[0].get("status") != "success" or type(reindex[0].get("receivedNs")) is not int:
            raise ValueError("generated worker overlap lacks a successful editor reindex")
        initial = [event for event in raw.get("lifecycle", [])
                   if event.get("method") == "suprnova-lsp/rustdocStatus"
                   and event.get("params", {}).get("workspaceRoot") in {str(APP), APP.as_uri()}
                   and type(event.get("receivedNs")) is int and event["receivedNs"] <= reindex[0]["writtenNs"]]
        if not initial or any(type(event["params"].get("generation")) is not int or event["params"]["generation"] < 0 for event in initial):
            raise ValueError("initial generated publication lacks worker-generation evidence")
        generation = max(event["params"]["generation"] for event in initial)
        latest = next(event for event in reversed(initial) if event["params"]["generation"] == generation)
        if latest["params"].get("state") != "current":
            raise ValueError("generated declarations were not current before the editor reindex")
        if reindex[0]["receivedNs"] >= sent[0]["writtenNs"] or any(value <= generation for value in SourceSeries.worker_overlap(raw)):
            raise ValueError("generated hovers did not overlap a subsequent worker generation")

    @staticmethod
    def read_sessions(references):
        sessions = []
        for reference in references:
            path = Path(reference["path"]).resolve(strict=True)
            if not path.is_relative_to(ROOT / "target/agent-debug"):
                raise ValueError("session evidence escapes the owned artifact root")
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("session evidence exceeds its size bound")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != reference["sha256"]:
                raise ValueError("session evidence changed or exceeds its size bound")
            sessions.append(json.loads(data))
        return sessions

    @classmethod
    def validate_series(cls, reports, commands, directory):
        """Every supervised matrix command must have its own unchanged session artifact."""
        labels, identities, summaries = [], set(), {}
        if set(reports) != set(MODES):
            raise ValueError("latency matrix requires both indexing modes")
        for mode in MODES:
            expected = [symbol + "/" + window for symbol in cls.symbols for window in cls.windows
                        if not (mode == "lower-peak-memory" and window == "deferred")]
            series = reports[mode]["series"]
            if set(series) != set(expected):
                raise ValueError("latency matrix is missing or inventing a required series")
            for key in expected:
                symbol, window = key.split("/")
                references = series[key]["sessions"]
                for number, reference in enumerate(references):
                    label = f"matrix-{mode}-{symbol}-{window}-{number:03}"
                    if reference.get("label") != label or Path(reference["path"]) != directory / f"{label}-session.json":
                        raise ValueError("latency matrix omitted or substituted a session artifact")
                    labels.append(label)
                sessions = cls.read_sessions(references)
                summaries[mode + "/" + key] = cls.assess(sessions, mode, directory, symbol, window)
                for session in sessions:
                    raw = session["raw"]
                    initialized = [row for row in raw["transport"] if row.get("method") == "initialize"]
                    identity = (raw.get("session", {}).get("serverPid"), initialized[0].get("receivedNs")) if initialized else None
                    if identity is None or identity in identities:
                        raise ValueError("latency matrix reused a server session across series")
                    identities.add(identity)
        observed = [command for command in commands if command.get("phase", "").startswith("matrix-")]
        if [command["phase"] for command in observed] != labels or any(command.get("code") != 0 for command in observed):
            raise ValueError("latency matrix discarded or added a supervised session")
        return summaries

    async def observe_mode(self, mode, directory, workload, command):
        series = {}
        for symbol in self.symbols:
            for window in self.windows:
                if mode == "lower-peak-memory" and window == "deferred":
                    continue
                sessions, references = [], []
                for number in range(120 if window == "deferred" else 20):
                    label = f"matrix-{mode}-{symbol}-{window}-{number:03}"
                    plan = self.series_plan(mode, directory, symbol, window)
                    plan["initializationOptions"]["rustdoc"]["automatic"]["artifactRoot"] = str(directory / label / "compiler")
                    raw = await self.observe_plan(label, plan, directory, command,
                                                  runtime_minutes=17 if symbol != "source" and window == "worker" else 5)
                    session = {"raw": raw, "plan": plan}
                    path = directory / f"{label}-session.json"
                    data = (json.dumps(session, indent=2) + "\n").encode()
                    path.write_bytes(data)
                    sessions.append(session)
                    references.append({"label": label, "path": str(path), "sha256": hashlib.sha256(data).hexdigest()})
                    summary = self.assess(sessions, mode, directory, symbol, window, require_counts=False)
                    series[symbol + "/" + window] = {"sessions": references.copy(), "summary": summary}
                    (directory / f"{mode}-matrix.json").write_text(json.dumps(series, indent=2) + "\n")
                    if summary["completeCounts"]:
                        break
                self.assess(sessions, mode, directory, symbol, window)
        return {"series": series, "initialDeferredWindow": "structurally absent: lower-peak-memory finishes bodies before publication" if mode == "lower-peak-memory" else "observed per request"}


class IdlePairs(Diagnostic):
    """Compare preserved baseline and current binaries after identical generated queries settle."""

    purpose = "three matched settled idle-memory pairs; latency acceptance is separate"
    run_kind = "rsp-idle-pairs"

    @classmethod
    def assess(cls, reports, directory, baseline_digest, candidate_digest):
        directory = directory.resolve()
        if set(reports) != set(MODES) or baseline_digest == candidate_digest:
            raise ValueError("idle comparison requires two indexing modes and distinct binary identities")
        summaries = {}
        for mode in MODES:
            pairs = reports[mode]["pairs"]
            if len(pairs) != 3:
                raise ValueError("idle comparison requires three matched pairs per indexing mode")
            medians, peaks = {"baseline": [], "candidate": []}, {"baseline": [], "candidate": []}
            expected = cls.workload_plan(mode, directory, "generated-captured")
            for pair in pairs:
                if set(pair) != {"baseline", "candidate"}:
                    raise ValueError("idle comparison is missing one member of a pair")
                for kind, digest in (("baseline", baseline_digest), ("candidate", candidate_digest)):
                    session = pair[kind]
                    if session["plan"] != expected or session["binarySha256"] != digest:
                        raise ValueError("idle comparison mixes configurations, workloads or binaries")
                    raw = session["raw"]
                    cls.hover_observation(raw, ["rsp_query", "rsp_without", "rsp_filter"], [("Builder<User>",)] * 3, "current")
                    samples = cls.idle_observation(raw)
                    medians[kind].append(cls.percentile(samples, 50))
                    peaks[kind].append(raw["idleMemory"]["indexingPeakRssBytes"])
            summaries[mode] = {"pairMediansBytes": medians, "indexingPeaksBytes": peaks,
                "medianDeltaBytes": cls.percentile(medians["candidate"], 50) - cls.percentile(medians["baseline"], 50)}
        return summaries

    async def observe_mode(self, mode, directory, workload, command):
        manifest = json.loads(self.baseline_manifest.read_text())
        baseline = Path(manifest["retainedBinary"])
        original = manifest["identity"]
        digest = hashlib.sha256(baseline.read_bytes()).hexdigest()
        if digest != original["binarySha256"] or original["sources"] != self.inventory():
            raise ValueError("preserved baseline binary or application identity changed")
        candidate = self.candidate_binary
        candidate_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        pairs = []
        for number in range(3):
            pair = {}
            # Reverse the middle pair so one binary is not always the page-cache beneficiary.
            order = ("candidate", "baseline") if number == 1 else ("baseline", "candidate")
            for kind in order:
                binary = baseline if kind == "baseline" else candidate
                self.binary = binary
                try:
                    plan = self.workload_plan(mode, directory, "generated-captured")
                    raw = await self.observe_plan(f"idle-{mode}-{number}-{kind}", plan, directory, command)
                finally:
                    self.binary = None
                summary = self.hover_observation(raw, ["rsp_query", "rsp_without", "rsp_filter"],
                                                 [("Builder<User>",)] * 3, "current")
                summary["idleRssBytes"] = self.idle_observation(raw)
                expected_digest = digest if kind == "baseline" else candidate_digest
                if hashlib.sha256(binary.read_bytes()).hexdigest() != expected_digest:
                    raise ValueError("idle-memory binary changed during observation")
                pair[kind] = {"raw": raw, "plan": plan, "summary": summary,
                              "binary": str(binary), "binarySha256": expected_digest}
            if pair["baseline"]["plan"] != pair["candidate"]["plan"]:
                raise ValueError("idle-memory pair used different workloads or configurations")
            pairs.append(pair)
            (directory / f"{mode}-idle-pairs.json").write_text(json.dumps(pairs, indent=2) + "\n")
        medians = {kind: [self.percentile(pair[kind]["summary"]["idleRssBytes"], 50) for pair in pairs]
                   for kind in ("baseline", "candidate")}
        return {"pairs": pairs, "baselineProvenance": manifest,
                "summary": {"pairMediansBytes": medians,
                            "medianDeltaBytes": self.percentile(medians["candidate"], 50) - self.percentile(medians["baseline"], 50),
                            "acceptance": "retained-state inspection and investigation remain required"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    purpose = parser.add_mutually_exclusive_group(required=True)
    purpose.add_argument("--diagnostic", action="store_true",
                          help="small source workload; emits no passing Sudus requirement verdicts")
    purpose.add_argument("--source-series", action="store_true",
                          help="20 fresh source sessions, five repeated requests each, all during observed worker activity")
    purpose.add_argument("--acceptance-matrix", action="store_true", help="all required latency symbols/windows with separate first and repeated cohorts")
    purpose.add_argument("--idle-pairs", action="store_true", help="three matched baseline/candidate idle-memory pairs per mode")
    parser.add_argument("--mode", choices=MODES, action="append")
    parser.add_argument("--no-build", action="store_true", help="intentionally use an existing managed optimized binary")
    parser.add_argument("--workload", choices=("source", "source-only", "source-current", "generated-settled", "generated-captured"), default="source")
    parser.add_argument("--binary", type=Path, help="preserved native baseline binary; requires --no-build")
    parser.add_argument("--baseline-manifest", type=Path, help="preserved baseline provenance for --idle-pairs")
    parser.add_argument("--compiler-seed-manifest", type=Path, help="disclosed owned genuine compiler priming report; --acceptance-matrix only")
    parser.add_argument("--inner-trace", action="store_true", help="disclose additional current-body stage tracing for diagnosis")
    parser.add_argument("--nofile-soft", type=int, help="explicit open-file limit for this diagnostic process and its children only")
    options = parser.parse_args()
    if options.mode and len(set(options.mode)) != len(options.mode):
        parser.error("--mode values must be distinct")
    if options.source_series and options.workload != "source":
        parser.error("--source-series requires the automatic-enabled source workload")
    if options.binary and not options.no_build:
        parser.error("--binary requires --no-build")
    try:
        observer = IdlePairs() if options.idle_pairs else AcceptanceMatrix() if options.acceptance_matrix else SourceSeries() if options.source_series else Diagnostic()
        if options.acceptance_matrix and options.workload != "generated-captured":
            parser.error("--acceptance-matrix requires --workload generated-captured")
        if options.compiler_seed_manifest and not options.acceptance_matrix:
            parser.error("--compiler-seed-manifest requires --acceptance-matrix")
        observer.compiler_seed_manifest = options.compiler_seed_manifest
        observer.binary = options.binary.resolve(strict=True) if options.binary else None
        if options.idle_pairs:
            if options.binary or not options.baseline_manifest or options.workload != "generated-captured":
                parser.error("--idle-pairs requires --baseline-manifest and --workload generated-captured, using the managed candidate binary")
            observer.baseline_manifest = options.baseline_manifest.resolve(strict=True)
            runner = helpers.module("idle_pair_runner", ROOT / "tools/agent-debug.py")
            observer.candidate_binary = runner.suprnova_lsp_binary("release")
        if options.inner_trace:
            observer.log_filter += ",rg_body_ir::build::current=trace,rg_body_ir::resolution=trace,rg_project::storage::loaders=trace"
        asyncio.run(observer.run(options.mode or MODES, options.no_build, options.nofile_soft, options.workload))
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f"responsiveness observation incomplete: {error}", file=sys.stderr)
        sys.exit(2)
