#!/usr/bin/env python3
"""Observe repository-scoped runners, guarded routing and genuine smoke jobs."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "target/agent-debug/local-runners"
REPOSITORY = "eas4ai/suprnova-lsp"
NAMES = {"linux": "rust-linux-x64", "macos": "rust-macos-arm64", "windows": "rust-windows-x64"}
LABELS = {"linux": ["self-hosted", "Linux", "X64", "rust-ci"],
          "macos": ["self-hosted", "macOS", "ARM64", "rust-ci"],
          "windows": ["self-hosted", "Windows", "X64", "rust-ci"]}
PLATFORMS = {"ubuntu-latest": "linux", "windows-latest": "windows", "macos-latest": "macos"}
TARGETS = {"linux": "x86_64-unknown-linux-gnu", "macos": "aarch64-apple-darwin", "windows": "x86_64-pc-windows-msvc"}
TRUST = ("github.repository == 'eas4ai/suprnova-lsp' && "
         "(github.event_name == 'push' || github.event_name == 'workflow_dispatch') && "
         "github.actor == github.repository_owner && github.triggering_actor == github.repository_owner")
SOURCE_FILES = [".github/workflows/ci.yml", ".github/workflows/build-server.yml",
                ".github/workflows/platform-checks.yml", ".github/workflows/github-release.yml",
                ".github/workflows/runner-smoke.yml", ".github/actions/setup-rust/action.yml",
                ".github/actions/setup-python/action.yml",
                ".github/runner-tools.json",
                ".github/actions/cargo-cache/action.yml", ".github/scripts/runner_smoke.py"]


class RunnerAcceptance:
    @staticmethod
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    @staticmethod
    def github(endpoint):
        result = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/" + endpoint],
                                capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError("GitHub observation failed: " + endpoint)
        return json.loads(result.stdout)

    @staticmethod
    def hosts():
        path = Path.home() / "workspace2/runners/scripts/hosts.py"
        spec = importlib.util.spec_from_file_location("runner_hosts", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @classmethod
    def defaults(cls):
        hosts = cls.hosts()
        values = {}
        for role in NAMES:
            command = ["bash", "-c", "cd / && rustup show active-toolchain"] if role == "linux" else "rustup show active-toolchain"
            values[role] = hosts.execute(role, command).strip()
        return values

    @classmethod
    def services(cls, repository):
        """Read profiles without returning connection strings or credentials."""
        hosts = cls.hosts()
        slug = repository.split("/")[1]
        unit = f"github-runner.eas4ai.{slug}.rust-linux-x64.service"
        output = hosts.execute("linux", ["systemctl", "--user", "show", unit,
            "--property=ActiveState,UnitFileState,MemoryHigh,MemoryMax,Slice"])
        linux = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        environment = hosts.execute("linux", ["systemctl", "--user", "show", unit, "--property=Environment"])
        linux["jobs"] = int(re.search(r"\bCARGO_BUILD_JOBS=(\d+)", environment).group(1))
        mac_code = '''import json,os,plistlib,subprocess
from pathlib import Path
root=Path.home()/'.local/share/github-runners'/REPOSITORY/'rust-macos-arm64'
service=Path((root/'.service').read_text().strip())
if not service.is_absolute(): service=Path.home()/'Library/LaunchAgents'/service
plist=plistlib.loads(service.read_bytes())
status=subprocess.run(['launchctl','print','gui/'+str(os.getuid())+'/'+plist['Label']],capture_output=True,text=True)
print(json.dumps({'running':status.returncode==0 and 'state = running' in status.stdout,'atLogin':plist.get('RunAtLoad') is True}))
'''.replace("REPOSITORY", repr(repository))
        windows_code = '''$ErrorActionPreference='Stop';
$root=Join-Path $env:ProgramData 'github-runners\\REPO\\rust-windows-x64';
$name=(Get-Content (Join-Path $root '.service') -Raw).Trim();
$service=Get-CimInstance Win32_Service | Where-Object Name -eq $name;
$values=@{}; foreach($line in Get-Content (Join-Path $root '.env')){if($line -match '^([^=]+)=(.*)$'){$values[$matches[1]]=$matches[2]}};
@{running=($service.State -eq 'Running'); automatic=($service.StartMode -eq 'Auto'); networkService=($service.StartName -eq 'NT AUTHORITY\\NETWORK SERVICE'); jobs=[int]$values.CARGO_BUILD_JOBS; isolatedCargo=($values.CARGO_HOME -eq (Join-Path $env:ProgramData 'github-runners\\toolchains\\cargo')); isolatedRustup=($values.RUSTUP_HOME -eq (Join-Path $env:ProgramData 'github-runners\\toolchains\\rustup'))}|ConvertTo-Json -Compress;
'''.replace("REPO", repository.replace("/", "\\"))
        return {"linux": linux, "macos": json.loads(hosts.execute("macos", hosts.python_remote(mac_code))),
                "windows": json.loads(hosts.execute("windows", hosts.powershell(windows_code)))}

    @staticmethod
    def native_expression(selector):
        choices = " || ".join(f"{selector} == '{platform}' && '{json.dumps(LABELS[role], separators=(',', ':'))}'"
                              for platform, role in PLATFORMS.items())
        return "${{ fromJSON(" + TRUST + " && (" + choices + ") || toJSON(" + selector + ")) }}"

    @classmethod
    def registrations(cls, settings, runners):
        cls.require(settings.get("enabled") is True, "Actions is disabled")
        cls.require(len(runners) == 3 and {r["name"] for r in runners} == set(NAMES.values()), "Three consumer registrations required")
        for role, name in NAMES.items():
            runner = next(r for r in runners if r["name"] == name)
            cls.require(runner["status"] == "online" and set(LABELS[role]) <= {label["name"] for label in runner["labels"]}, role + " runner offline or mislabeled")

    @classmethod
    def profiles(cls, profiles):
        linux = profiles["linux"]
        cls.require(linux.get("ActiveState") == "active" and linux.get("UnitFileState") == "enabled"
                    and linux.get("MemoryHigh") == "21474836480" and linux.get("MemoryMax") == "32212254720"
                    and linux.get("Slice") == "github-runners.slice" and linux.get("jobs") == 8, "Linux service profile changed")
        cls.require(profiles["macos"] == {"running": True, "atLogin": True}, "Mac launch agent unavailable")
        cls.require(profiles["windows"] == {"running": True, "automatic": True, "networkService": True,
                    "jobs": 2, "isolatedCargo": True, "isolatedRustup": True}, "Windows service profile changed")

    @staticmethod
    def workflows(root):
        return {path.name: yaml.load(path.read_text(), Loader=yaml.BaseLoader)
                for path in (root / ".github/workflows").glob("*.yml")}

    @classmethod
    def routing(cls, workflows, root=ROOT):
        ci = workflows["ci.yml"]
        cls.require(ci["jobs"]["tests"]["runs-on"] == cls.native_expression("matrix.os"), "Native test routing changed")
        cls.require(set(ci["jobs"]["tests"]["strategy"]["matrix"]["os"]) == set(PLATFORMS), "Native CI matrix changed")
        platform = workflows["platform-checks.yml"]["jobs"]
        cls.require(platform["build-server"]["uses"] == "./.github/workflows/build-server.yml"
                    and platform["build-server"]["with"]["os"] == "${{ inputs.os }}", "Reusable build routing lost")
        for job in (platform["client"], platform["build-editor-packages"], platform["compare-lsp-windows"], workflows["build-server.yml"]["jobs"]["build"]):
            cls.require(job["runs-on"] == cls.native_expression("inputs.os"), "Reusable consumer routing changed")
        native_jobs = [ci["jobs"]["tests"], *[platform[name] for name in ("client", "build-editor-packages", "compare-lsp-windows")], workflows["build-server.yml"]["jobs"]["build"]]
        for job in native_jobs:
            cls.require(job.get("env", {}).get("RUSTUP_TOOLCHAIN") == "1.98.1"
                        and any(step.get("uses") == "./.github/actions/setup-rust" for step in job["steps"]), "Native job lacks explicit toolchain setup")
        release = workflows["github-release.yml"]["jobs"]["packages"]
        cls.require(release["runs-on"] == "${{ fromJSON(" + TRUST + " && matrix.local_runner || toJSON(matrix.runner)) }}", "Release routing changed")
        targets = {entry["vscode_target"]: entry for entry in release["strategy"]["matrix"]["include"]}
        cls.require(set(targets) == {"linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64", "win32-x64"}, "Release platform matrix changed")
        for target, role in {"linux-x64": "linux", "darwin-arm64": "macos", "win32-x64": "windows"}.items():
            cls.require(json.loads(targets[target]["local_runner"]) == LABELS[role] and targets[target]["rust_target"] == TARGETS[role], "Release labels or target changed")
        for target in ("linux-arm64", "darwin-x64"):
            cls.require(not targets[target].get("local_runner"), "Unsupported release architecture selects local hardware")
        cls.require(targets["linux-x64"]["container"] == "quay.io/pypa/manylinux_2_28_x86_64@sha256:4dc41da7df20400310c80d162a2fe2d2c2f3d9734d8dec20f6b9843711618deb", "Linux release compatibility changed")
        container = release["container"]
        native_linux = TRUST + " && matrix.vscode_target == 'linux-x64'"
        cls.require(isinstance(container, dict) and container.get("image") == "${{ matrix.container }}"
                    and container.get("volumes") == ["${{ " + native_linux + " && 'suprnova-lsp-cargo:/github/service-cargo' || '/github/home/.cargo' }}"]
                    and container.get("options") == "${{ " + native_linux + " && format('--user {0}', vars.SUPRNOVA_LSP_LINUX_CONTAINER_USER) || '' }}"
                    and container.get("env") == {"CARGO_HOME": "${{ " + native_linux + " && '/github/service-cargo' || '/github/home/.cargo' }}", "RUSTUP_HOME": "/github/home/.rustup"},
                    "Release container loses the service Cargo home or owner")
        installer = next(step for step in release["steps"] if step.get("name") == "Install Rust in Linux compatibility container")
        cls.require('CARGO_HOME="$HOME/.container-cargo-tools" sh' in installer["run"], "Container Rust installer modifies shared Cargo binaries")
        smoke = workflows["runner-smoke.yml"]
        cls.require(set(smoke["on"]) == {"workflow_dispatch"} and smoke["jobs"]["smoke"]["if"] == TRUST, "Smoke dispatch guard changed")
        cls.require(smoke["jobs"]["smoke"]["runs-on"] == "${{ matrix.labels }}", "Smoke routing changed")
        cls.require({entry["role"]: entry["labels"] for entry in smoke["jobs"]["smoke"]["strategy"]["matrix"]["include"]} == LABELS, "Smoke platform matrix changed")
        mount_probe = next(step for step in smoke["jobs"]["smoke"]["steps"] if step.get("name") == "Check Linux release Cargo mount")
        cls.require(mount_probe["if"] == "matrix.role == 'linux'"
                    and mount_probe["env"] == {"SERVICE_USER": "${{ vars.SUPRNOVA_LSP_LINUX_CONTAINER_USER }}", "RELEASE_IMAGE": targets["linux-x64"]["container"]}
                    and 'source=suprnova-lsp-cargo,target=/github/service-cargo' in mount_probe["run"]
                    and '--user "$SERVICE_USER"' in mount_probe["run"] and 'EXPECTED_INODE' in mount_probe["run"], "Release container boundary probe differs")
        guarded = {("ci.yml", "tests"), ("build-server.yml", "build"),
                   *[("platform-checks.yml", name) for name in ("client", "build-editor-packages", "compare-lsp-windows")],
                   ("github-release.yml", "packages"), ("runner-smoke.yml", "smoke")}
        for name, workflow in workflows.items():
            for job_name, job in workflow["jobs"].items():
                if "runs-on" not in job or (name, job_name) in guarded:
                    continue
                runner = job["runs-on"]
                cls.require(isinstance(runner, str) and (runner.startswith(("ubuntu-", "windows-", "macos-"))
                            or (name == "release.yml" and runner == "${{ matrix.runner }}")), "Additional local job lacks reviewed event/actor routing")
        cache = (root / ".github/actions/cargo-cache/action.yml").read_text()
        cls.require(cache.count("${{ steps.cargo-home.outputs.path }}") == 8
                    and '${CARGO_HOME:-$HOME/.cargo}' in cache, "Cargo cache ignores service Cargo home")
        rust = yaml.load((root / ".github/actions/setup-rust/action.yml").read_text(), Loader=yaml.BaseLoader)
        cls.require(rust["inputs"]["components"]["default"] == "rustfmt", "Formatting tool missing from the pinned toolchain")

    @staticmethod
    def source_digest(root=ROOT):
        return hashlib.sha256(json.dumps({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                             for name in SOURCE_FILES}, sort_keys=True).encode()).hexdigest()

    @classmethod
    def smoke(cls, run, jobs, artifacts, observations, digest):
        cls.require(run.get("status") == "completed" and run.get("conclusion") == "success"
                    and run.get("event") == "workflow_dispatch" and run["actor"]["login"] == "eas4ai"
                    and run["triggering_actor"]["login"] == "eas4ai", "Actual owner smoke run missing or unsuccessful")
        cls.require(len(jobs) == 3 and {job["runner_name"] for job in jobs} == set(NAMES.values()), "Smoke did not run on all consumer runners")
        cls.require({artifact["name"] for artifact in artifacts if artifact.get("expired") is False}
                    == {"runner-smoke-" + role for role in NAMES}, "Smoke artifacts absent or expired")
        cls.require(set(observations) == set(NAMES), "Downloaded platform observations incomplete")
        for role, name in NAMES.items():
            job = next(job for job in jobs if job["runner_name"] == name)
            cls.require(job["conclusion"] == "success" and set(LABELS[role]) <= set(job["labels"]), "Smoke job failed or labels differ")
            for step_name in ("Install native test tools", "Check required native tools", "Compile and test Rust probe", "Upload runner observation"):
                cls.require(any(step["name"] == step_name and step["conclusion"] == "success" for step in job["steps"]), "Smoke step did not pass: " + step_name)
            if role == "linux":
                cls.require(any(step["name"] == "Check Linux release Cargo mount" and step["conclusion"] == "success" for step in job["steps"]), "Actual release Cargo mount probe missing")
            value = observations[role]
            cls.require(value["runId"] == str(run["id"]) and value["sha"] == run["head_sha"]
                        and value["runnerName"] == name and value["role"] == role and value["host"] == TARGETS[role]
                        and value["toolchain"] == "1.98.1" and value["cargoTestExit"] == 0
                        and value["sourceDigest"] == digest, "Downloaded smoke evidence disagrees with run, host or current sources")

    @classmethod
    def main(cls):
        results = {}
        for requirement, check in [("CIR-001", cls.check_installation), ("CIR-002", cls.check_routing), ("CIR-003", cls.check_acceptance)]:
            try:
                check()
                results[requirement] = True
            except (ValueError, RuntimeError, OSError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as error:
                print(requirement + ": " + str(error), flush=True)
                results[requirement] = False
            print(f"sudus: {requirement}: {'pass' if results[requirement] else 'fail'}", flush=True)
        return 0 if all(results.values()) else 1

    @classmethod
    def check_installation(cls):
        cls.registrations(cls.github("actions/permissions"), cls.github("actions/runners?per_page=100")["runners"])
        before = json.loads((EVIDENCE / "before.json").read_text())
        cls.require(before["ownerToolchains"] == cls.defaults(), "Owner default Rust toolchain changed")
        hosts = cls.hosts()
        current = hosts.github("repos/eas4ai/runners/actions/runners?per_page=100")["runners"]
        cls.require({r["id"]: (r["name"], sorted(label["name"] for label in r["labels"])) for r in before["pilotRunners"]}
                    == {r["id"]: (r["name"], sorted(label["name"] for label in r["labels"])) for r in current}
                    and all(r["status"] == "online" for r in current), "Pilot registrations changed or offline")
        cls.profiles(cls.services(REPOSITORY))
        cls.require(before["pilotServices"] == cls.services("eas4ai/runners"), "Pilot service profiles changed")
        # Scan only changed/tracked CI inputs; never print credential contents.
        names = subprocess.check_output(["git", "ls-files", "-z", ".github", "tools/sudus-local-runners.py", "tools/test_sudus_local_runners.py"], cwd=ROOT).decode().split("\0")
        endpoints = json.loads(hosts.HOST_FILE.read_text())
        forbidden = {host["ssh"] for host in endpoints.values()}
        forbidden.update(value.rsplit("@", 1)[-1] for value in tuple(forbidden))
        for name in filter(None, names):
            data = (ROOT / name).read_bytes()
            cls.require(not re.search(rb"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}|(?<![A-Za-z0-9_])[A-Z0-9]{29}(?![A-Za-z0-9_])", data)
                        and not any(value.encode() in data for value in forbidden), "Credential or endpoint found in tracked CI inputs")

    @classmethod
    def check_routing(cls):
        cls.routing(cls.workflows(ROOT))
        hosts = cls.hosts()
        environment = hosts.execute("linux", ["systemctl", "--user", "show", "github-runner.eas4ai.suprnova-lsp.rust-linux-x64.service", "--property=Environment", "--value"])
        cargo_home = dict(value.split("=", 1) for value in shlex.split(environment))["CARGO_HOME"]
        options = json.loads(hosts.execute("linux", ["docker", "volume", "inspect", "suprnova-lsp-cargo", "--format", "{{json .Options}}"] ))
        cls.require(options == {"device": cargo_home, "o": "bind", "type": "none"}, "Container volume does not bind the configured service Cargo home")
        owner = hosts.execute("linux", ["python3", "-c", "import os; print(str(os.getuid())+':'+str(os.getgid()))"]).strip()
        cls.require(cls.github("actions/variables/SUPRNOVA_LSP_LINUX_CONTAINER_USER")["value"] == owner, "Release container user differs from service owner")
        cls.require(cls.github("actions/permissions/fork-pr-contributor-approval")["approval_policy"] == "all_external_contributors", "Public fork approval policy differs")
        cls.require(cls.github("actions/workflows/release.yml")["state"] == "disabled_manually", "Marketplace workflow is enabled")
        for name in ("ci.yml", "performance.yml", "github-release.yml", "runner-smoke.yml"):
            cls.require(cls.github("actions/workflows/" + name)["state"] == "active", "Required workflow is disabled: " + name)
        result = subprocess.run([str(EVIDENCE / "bin/actionlint"), "-shellcheck=", "-pyflakes="], cwd=ROOT, capture_output=True, text=True, timeout=30)
        cls.require(result.returncode == 0, "Workflow syntax check failed: " + result.stdout[:2000])

    @classmethod
    def check_acceptance(cls):
        run_id = json.loads((EVIDENCE / "run.json").read_text())["id"]
        run = cls.github(f"actions/runs/{run_id}")
        jobs = cls.github(f"actions/runs/{run_id}/jobs?per_page=100")["jobs"]
        artifacts = cls.github(f"actions/runs/{run_id}/artifacts?per_page=100")["artifacts"]
        observations = {role: json.loads((EVIDENCE / f"artifacts/runner-smoke-{role}/{role}.json").read_text()) for role in NAMES}
        cls.smoke(run, jobs, artifacts, observations, cls.source_digest())
        for name in SOURCE_FILES:
            content = cls.github(f"contents/{name}?ref={run['head_sha']}")
            cls.require(base64.b64decode(content["content"]) == (ROOT / name).read_bytes(), "Smoke run predates current CI inputs: " + name)
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tools", "-p", "test_sudus_local_runners.py"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        cls.require(result.returncode == 0, "Runner observer controls failed: " + result.stderr[:2000])


if __name__ == "__main__":
    sys.exit(RunnerAcceptance.main())
