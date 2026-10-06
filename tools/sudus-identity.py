#!/usr/bin/env python3
"""Observe fork installation identities and their real editor/server consumers."""

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
import xml.etree.ElementTree as ET

from importlib.util import module_from_spec, spec_from_file_location


ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = [f"IDN-{number:03}" for number in range(1, 6)]
EDITOR_CASE = "Suprnova LSP identity IDN-001 keeps fork identity and upstream commands/settings independent"
NOTIFICATIONS = {"rustdocStatus", "activeWorkspaceChanged", "deferredIndexingStarted", "deferredIndexingFinished"}
LICENSE_DIGESTS = {
    "LICENSE-MIT": "6c804de184c81c8dc13ffef14213df42ebcdaf1d37d19ced4bd9c497f110c366",
    "LICENSE-APACHE": "a6cba85bc92e0cff7a450b1d873c0eaa2e9fc96bf472df0247a26bec77bf3ff9",
}


def module(name, path):
    spec = spec_from_file_location(name, path)
    value = module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def source_observations(root=ROOT):
    package = json.loads((root / "editors/code/package.json").read_text())
    files = lambda directory, suffix: list((root / directory).rglob(f"*{suffix}"))
    client = "\n".join(path.read_text() for path in files("editors/code/src", ".ts"))
    server = "\n".join(path.read_text() for path in files("crates/lsp", ".rs") if "tests" not in path.parts)
    log = (root / "crates/rust-glancer/src/logging.rs").read_text()
    cli = (root / "crates/rust-glancer/src/main.rs").read_text()
    cache = (root / "crates/engine/project/src/storage/cache/instance.rs").read_text()
    worker = (root / "crates/lsp/server/src/rustdoc_worker/task.rs").read_text()
    cargo = tomllib.loads((root / "crates/rust-glancer/Cargo.toml").read_text())
    workspace_manifest = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]
    workspace = workspace_manifest["package"]
    properties = package["contributes"]["configuration"]["properties"]
    commands = package["contributes"]["commands"]
    notifications = {f"suprnova-lsp/{name}" for name in NOTIFICATIONS}
    descriptions = json.dumps(properties)
    entrypoints = "\n".join(path.read_text() for path in [
        root / "Justfile", root / "editors/code/scripts/package-vsix.mjs",
        root / ".github/scripts/package_server_archive.py", *files(".github/workflows", ".yml"),
        *files(".vscode", ".json")])
    # The separate Zed package keeps its internal name. Resolve Cargo selectors
    # against manifests so an invented fork-prefixed name cannot pass this check.
    package_names = {tomllib.loads((root / member / "Cargo.toml").read_text())["package"]["name"]
                     for member in workspace_manifest["members"]}
    selected_packages = re.findall(r'(?<!\S)-p\s+([\w-]+)', entrypoints)
    valid_packages = bool(selected_packages) and all(name in package_names for name in selected_packages)
    benchmark = (root / "crates/engine/project/benches/shared/mod.rs").read_text()
    codspeed = (root / ".github/workflows/codspeed.yml").read_text()
    benchmark_variables = re.findall(r'^\s+(SUPRNOVA_LSP_[A-Z_]+):', codspeed, re.MULTILINE)
    valid_benchmark_env = bool(benchmark_variables) and all(
        f'std::env::var("{variable}")' in benchmark for variable in benchmark_variables)
    valid_benchmark_env = valid_benchmark_env and "RUST_GLANCER_BENCH_TARGETS" not in benchmark
    old_entrypoint = re.search(r'(?<![/\w])rust-glancer(?=[\s"\'`.-]|$)',
                              entrypoints.replace("rust-glancer-zed", ""))
    licenses = all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
                   for name, digest in LICENSE_DIGESTS.items())
    identities = {
        "IDN-001": package.get("publisher") == "eas4ai" and package.get("name") == "suprnova-lsp"
            and package.get("displayName") == "Suprnova LSP" and "Rust Glancer" not in client
            and bool(re.search(r'new LanguageClient\(\s*"suprnova-lsp",\s*"Suprnova LSP"', client)),
        "IDN-002": bool(properties) and all(key.startswith("suprnova-lsp.") for key in properties)
            and bool(commands) and all(command["command"].startswith("suprnova-lsp.") for command in commands)
            and 'getConfiguration("suprnova-lsp")' in client and "rust-glancer" not in client
            and all(value in client and value in server for value in notifications)
            and "rust-glancer/" not in server and "rust-glancer.internal." not in server
            and "suprnova-lsp-log" in json.dumps(package["contributes"]["languages"])
            and 'diagnosticCollectionName: "suprnova-lsp"' in client,
        "IDN-003": cargo["package"]["name"] == "suprnova-lsp" and 'name = "suprnova-lsp"' in cli
            and 'name: "Suprnova LSP"' in server and '"SUPRNOVA_LSP_LOG"' in log
            and '"SUPRNOVA_LSP_ENGINE_ID"' in log and '"suprnova-lsp-log/v1"' in log
            and 'CACHE_DIR_NAME: &str = "suprnova_lsp"' in cache
            and '"target/suprnova-lsp/rustdoc"' in worker and '"rust-glancer"' not in client
            and not re.search(r'"(?:__)?RUST_GLANCER_[A-Z_]+"', client + server + log)
            and valid_benchmark_env,
        "IDN-004": package.get("repository", {}).get("url") == "https://github.com/eas4ai/suprnova-lsp"
            and workspace.get("repository") == "https://github.com/eas4ai/suprnova-lsp"
            and workspace.get("license") == "MIT OR Apache-2.0"
            and "Igor Aleksanov <popzxc@yandex.ru>" in workspace.get("authors", [])
            and licenses and old_entrypoint is None and valid_packages and valid_benchmark_env
            and (root / "README.md").read_text().startswith("# Suprnova LSP")
            and (root / "editors/code/README.md").read_text().startswith("# Suprnova LSP")
            and "Rust Glancer" in (root / "README.md").read_text()
            and "suprnova-lsp.rustdoc.inputs" in descriptions,
    }
    return {"results": identities, "manifest": {key: package.get(key) for key in ["name", "displayName", "publisher", "repository"]}}


def regression_result(text, code, prefix, count):
    observations = re.findall(rf"^sudus: ({prefix}-\d{{3}}): (pass|fail)$", text, re.M)
    expected = {f"{prefix}-{number:03}" for number in range(1, count + 1)}
    if len(observations) != count or {req for req, _ in observations} != expected:
        raise ValueError(f"{prefix}: absent or duplicate requirement observations")
    passed = all(result == "pass" for _, result in observations)
    if code != (0 if passed else 1):
        raise ValueError(f"{prefix}: result contradicts exit status")
    return passed


def package_observations(vsix, archive, binary, root=ROOT):
    executable = "suprnova-lsp"
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    with zipfile.ZipFile(vsix) as contents:
        manifest = json.loads(contents.read("extension/package.json"))
        bundled = hashlib.sha256(contents.read(f"extension/server/{executable}")).hexdigest()
        license_text = contents.read("extension/LICENSE.txt").decode()
        assets = contents.namelist()
        xml = ET.fromstring(contents.read("extension.vsixmanifest"))
        install_ids = [node.attrib for node in xml.iter() if node.tag.rsplit("}", 1)[-1] == "Identity"]
        identity = (manifest.get("publisher"), manifest.get("name"), manifest.get("displayName"))
        license_ok = all((root / name).read_text().strip() in license_text for name in ["LICENSE-MIT", "LICENSE-APACHE"])
    with tarfile.open(archive, "r:gz") as contents:
        names = contents.getnames()
        if set(names) != {executable, "LICENSE-MIT", "LICENSE-APACHE"} or len(names) != 3:
            raise ValueError("server archive has missing, duplicate or upstream assets")
        server = contents.extractfile(executable)
        if server is None:
            raise ValueError("archive executable missing")
        archived = hashlib.sha256(server.read()).hexdigest()
        for name in ["LICENSE-MIT", "LICENSE-APACHE"]:
            asset = contents.extractfile(name)
            if asset is None or asset.read() != (root / name).read_bytes():
                raise ValueError(f"archive changed canonical license: {name}")
    return {"extension": identity == ("eas4ai", "suprnova-lsp", "Suprnova LSP") and len(install_ids) == 1
            and install_ids[0].get("Publisher") == "eas4ai" and install_ids[0].get("Id") == "suprnova-lsp",
        "binary": digest == bundled == archived and [name for name in assets if name.startswith("extension/server/")]
            == [f"extension/server/{executable}"],
        "licenses": license_ok, "binarySha256": digest, "archiveMembers": names}


def assess(sources, editor_passed, packages, protocol, regressions):
    observed = sources["results"]
    if set(observed) != set(REQUIREMENTS[:4]) or any(type(value) is not bool for value in observed.values()):
        raise ValueError("source identities lack exact requirement observations")
    # A genuine editor failure proves the old identity cannot serve the renamed flow.
    # Missing later phases cannot turn a passing editor into passing acceptance.
    return {
        "IDN-001": observed["IDN-001"] and editor_passed and packages.get("extension") is True,
        "IDN-002": observed["IDN-002"] and editor_passed,
        "IDN-003": observed["IDN-003"] and packages.get("binary") is True and protocol.get("passed") is True,
        "IDN-004": observed["IDN-004"] and packages.get("licenses") is True and packages.get("binary") is True,
        "IDN-005": editor_passed and regressions == {"AUT": True, "EDT": True} and protocol.get("passed") is True,
    }


async def protocol_probe(plan):
    helpers = module("identity_editor_helpers", ROOT / "tools/sudus-editor-import.py")
    lsp = helpers.module("identity_lsp_client", ROOT / "tools/lsp-query.py")
    root = Path(plan["root"])
    client = await lsp.LspClient.start(Path(plan["binary"]), root, 300000, False)
    report = {}
    try:
        reply = await client.request("initialize", {"processId": os.getpid(), "rootUri": root.as_uri(),
            "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
            "capabilities": {"experimental": {"serverStatusNotification": True, "rustdocStatusNotification": True}},
            "initializationOptions": {"cfg": {"test": False}, "cache": {"packageResidency": "all-offloadable"}}})
        assert reply.get("result", {}).get("serverInfo", {}).get("name") == "Suprnova LSP", reply
        await client.notify("initialized", {})
        source = root / "src/lib.rs"
        await client.notify("textDocument/didOpen", {"textDocument": {
            "uri": source.as_uri(), "languageId": "rust", "version": 1, "text": source.read_text()}})
        await lsp.wait_until_ready(client, 300000)
        status = await client.wait_for_notification(lambda event: event.get("method") == "suprnova-lsp/rustdocStatus"
            and event.get("params", {}).get("state") in {"current", "failed"}, "default artifact worker", 900000)
        assert status["params"]["state"] == "current", status
        worker_parent = root / "target/suprnova-lsp/rustdoc"
        owned = list(worker_parent.glob(".suprnova-lsp-rustdoc-*"))
        assert owned, "default worker artifact namespace was not observed"
        cache_root = Path(os.environ["CARGO_TARGET_DIR"]) / "suprnova_lsp"
        assert list(cache_root.rglob("instance.lock")), "fork cache namespace was not claimed"
        response = await client.request("workspace/executeCommand", {"command": "suprnova-lsp.internal.reindexWorkspace", "arguments": []})
        assert "error" not in response, response
        report = {"passed": True, "initialize": reply, "workerStatus": status,
                  "workerParent": str(worker_parent), "cacheRoot": str(cache_root)}
    finally:
        await client.close()
        assert client.process.returncode == 0, "server did not shut down normally"
        logs = [json.loads(line) for line in client.stderr.splitlines() if line.startswith("{")]
        logs = [log for log in logs if "schema" in log]
        assert logs and all(log.get("schema") == "suprnova-lsp-log/v1" for log in logs), "fork structured log schema absent or mismatched"
        report["logSchemas"] = sorted({log["schema"] for log in logs})
        Path(plan["report"]).write_text(json.dumps(report, indent=2) + "\n")
    return 0


async def main():
    helpers = module("identity_editor", ROOT / "tools/sudus-editor-import.py")
    runner = helpers.module("identity_runner", ROOT / "tools/agent-debug.py")
    helpers.EDITOR_CASE = EDITOR_CASE
    runner.install_signal_handlers()
    directory = runner.create_run_directory("identity")
    environment = dict(os.environ, RUSTUP_TOOLCHAIN="1.98.1", CARGO_BUILD_JOBS="2", RAYON_NUM_THREADS="2",
        RUST_MIN_STACK="16777216", CARGO_NET_OFFLINE="true", RUSTUP_AUTO_INSTALL="0", CARGO_CACHE_RUSTC_INFO="0")
    commands = []

    async def run(label, command, args, cwd=ROOT, env=None, timeout=20 * 60_000):
        print(f"observing {label}", flush=True)
        output = directory / label
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = await runner.run_supervised(runner.CommandSpec(command, args), cwd, env or environment, output, timeout)
        result["phase"] = label
        commands.append(result)
        if result.get("timedOut") or result.get("spawnError") or result.get("signal") or not result["cleanup"].get("verifiedEmpty"):
            raise ValueError(f"{label}: command or process cleanup incomplete")
        return result["code"], (output / "stdout.log").read_text()

    try:
        code, _ = await run("integrity", sys.executable, [str(ROOT / "tools/test_sudus_identity.py")], timeout=60000)
        if code != 0:
            raise ValueError("identity observer integrity checks failed")
        sources = source_observations()
        build = runner.build_spec(runner.RunnerOptions(build_profile="debug"))
        code, _ = await run("build", build.command, [*build.args, "--locked", "--offline"])
        if code != 0:
            raise ValueError("current server failed to build")
        binary = runner.rust_glancer_binary("debug")
        manifest = json.loads((ROOT / "editors/code/package.json").read_text())
        prefix = "SUPRNOVA_LSP" if manifest["name"] == "suprnova-lsp" else "RUST_GLANCER"
        upstream = directory / "upstream-identity"
        upstream.mkdir()
        (upstream / "package.json").write_text(json.dumps({"name": "rust-glancer", "publisher": "rust-glancer",
            "displayName": "Upstream identity fixture", "version": "0.0.1", "engines": {"vscode": "^1.119.0"},
            "main": "./extension.js", "activationEvents": ["onCommand:rust-glancer.reindexWorkspace"],
            "contributes": {"commands": [{"command": "rust-glancer.reindexWorkspace", "title": "Upstream Fixture: Reindex"}],
                "configuration": {"properties": {"rust-glancer.cargo.target": {"type": "string", "default": "upstream-default"}}}}}))
        (upstream / "extension.js").write_text("const vscode = require('vscode');\nexports.activate = context => { let count = 0; context.subscriptions.push(vscode.commands.registerCommand('rust-glancer.reindexWorkspace', () => ++count)); };\n")
        editor_report = directory / "editor-results.json"
        editor_env = dict(environment, **{f"{prefix}_TEST_SERVER": str(binary),
            f"{prefix}_EXTENSION_TEST_GREP": "IDN-001 keeps fork", f"{prefix}_EXTENSION_TEST_REPORT": str(editor_report),
            "SUPRNOVA_LSP_IDENTITY_FIXTURE": str(upstream)})
        code, _ = await run("editor", "xvfb-run", ["-a", "npm", "run", "test:e2e:prebuilt"],
            cwd=ROOT / "editors/code", env=editor_env, timeout=5 * 60_000)
        editor_passed = helpers.editor_outcome(editor_report.read_text(), code)
        packages, protocol, regressions = {}, {}, {}
        if all(sources["results"].values()) and editor_passed:
            # Existing mechanisms retain their genuine compiler, Devlist, refresh and trace proofs.
            for name, count, script in [("AUT", 9, "sudus-automatic-rustdoc.py"), ("EDT", 6, "sudus-editor-import.py")]:
                code, text = await run(name.lower(), sys.executable, [str(ROOT / "tools" / script)], timeout=3 * 60 * 60_000)
                regressions[name] = regression_result(text, code, name, count)
            stage = directory / "package-workspace"
            tracked = subprocess.check_output(["git", "ls-files", "-z", "editors/code", ".github/scripts/package_server_archive.py"], cwd=ROOT).decode().split("\0")
            for name in filter(None, tracked):
                source = ROOT / name
                if source.is_file():
                    destination = stage / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            for name in ["LICENSE-MIT", "LICENSE-APACHE", "Cargo.toml"]:
                shutil.copy2(ROOT / name, stage / name)
            (stage / "editors/code/node_modules").symlink_to(ROOT / "editors/code/node_modules", target_is_directory=True)
            target = runner.host_target()
            # Package the genuine current executable in an owned staging workspace, without rebuilding or installing.
            staged_binary = stage / "target" / target / "release/suprnova-lsp"
            staged_binary.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(binary, staged_binary)
            vsix, archive = directory / "suprnova-lsp.vsix", directory / "suprnova-lsp.tar.gz"
            code, _ = await run("vsix", "node", [str(stage / "editors/code/scripts/package-vsix.mjs"), "--skip-build", "--target", target, "--out", str(vsix)])
            if code != 0:
                raise ValueError("local VSIX packaging failed")
            code, _ = await run("archive", sys.executable, [str(stage / ".github/scripts/package_server_archive.py"), "--target", target,
                "--version", manifest["version"], "--out", str(archive)])
            if code != 0:
                raise ValueError("local server archive packaging failed")
            packages = package_observations(vsix, archive, binary)
            fixture = directory / "protocol-fixture"
            shutil.copytree(ROOT / "crates/engine/rustdoc/fixtures/automatic-models", fixture)
            cache = directory / "protocol-cache"
            old_cache = cache / "rust_glancer/upstream-sentinel"
            old_cache.parent.mkdir(parents=True)
            old_cache.write_text("preserve upstream cache\n")
            old_worker = fixture / "target/rust-glancer/rustdoc/upstream-sentinel"
            old_worker.parent.mkdir(parents=True)
            old_worker.write_text("preserve upstream worker\n")
            old_roots = [cache / "rust_glancer", fixture / "target/rust-glancer"]
            before = {str(path): path.read_bytes() if path.is_file() else "directory"
                      for parent in old_roots for path in parent.rglob("*")}
            env = dict(environment, CARGO_TARGET_DIR=str(cache), SUPRNOVA_LSP_LOG="info", RUST_GLANCER_LOG="off")
            code, _ = await run("fixture-lock", "cargo", ["generate-lockfile", "--offline", "--manifest-path", str(fixture / "Cargo.toml")], env=env, cwd=fixture)
            if code != 0:
                raise ValueError("owned genuine fixture lock resolution failed")
            plan = directory / "protocol-plan.json"
            report = directory / "protocol-report.json"
            plan.write_text(json.dumps({"binary": str(binary), "root": str(fixture), "report": str(report)}))
            code, _ = await run("protocol", sys.executable, [str(Path(__file__).resolve()), "--probe", str(plan)], env=env, timeout=30 * 60_000)
            if code != 0:
                raise ValueError("fork protocol/default-path observation failed")
            protocol = json.loads(report.read_text())
            after = {str(path): path.read_bytes() if path.is_file() else "directory"
                     for parent in old_roots for path in parent.rglob("*")}
            if before != after:
                raise ValueError("protocol probe modified upstream artifact contents or directories")
        results = assess(sources, editor_passed, packages, protocol, regressions)
        runner.write_json(directory / "observations.json", {"sources": sources, "editor": editor_passed,
            "packages": packages, "protocol": protocol, "regressions": regressions, "results": results})
        for requirement, passed in results.items():
            print(f"sudus: {requirement}: {'pass' if passed else 'fail'}", flush=True)
        return 0 if all(results.values()) else 1
    finally:
        runner.write_json(directory / "summary.json", {"commands": commands, "processCleanup": runner.summarize_cleanup(commands)})
        print(f"identity mechanism artifacts: {directory}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(protocol_probe(json.loads(Path(sys.argv[2]).read_text())) if len(sys.argv) == 3 and sys.argv[1] == "--probe" else main()))
    except (OSError, ValueError, KeyError, TypeError, AssertionError, RuntimeError) as error:
        print(f"identity observation incomplete: {error}", file=sys.stderr)
        sys.exit(2)
