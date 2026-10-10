#!/usr/bin/env python3
"""Reject upstream product identities in current fork code and instructions."""

import hashlib
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_IDENTITY = re.compile(r"rust(?:[-_]|\s+)glancer|RustGlancer", re.IGNORECASE)
UPSTREAM_ICON_SHA256 = "b798bed94708c73affe2c508e1afd4631335a19dac00784c62fcfb754883d7bc"

# These records describe upstream or past observations. Rewriting them would
# change attribution, contract history or the provenance of measurements.
HISTORICAL_PATHS = (
    "CHANGELOG.md", "LICENSE-", ".nvimlog", "AGENTS.md", ".sudus/",
    ".planning/", "docs/spec/", "docs/recon.md", "docs/decisions.jsonl",
    "tools/fixtures/", "crates/engine/rustdoc/fixtures/",
    "editors/code/test/fixtures/upstream-identity/",
)
IDENTITY_CONTROLS = {
    "tools/sudus-identity.py", "tools/test_sudus_identity.py",
    "tools/test_project_identity.py", "editors/code/test/identity.test.ts",
    ".github/scripts/check_project_identity.py",
}
EXTERNAL_LINT_NAMES = (
    "rust_glancer_lints", "rust_glancer_impl_helpers",
    "rust_glancer_implicit_local_imports", "rust_glancer_non_adjacent_impls",
    "rust_glancer_pub_in",
)
EXTERNAL_LINTS = re.compile(r"\b(?:" + "|".join(EXTERNAL_LINT_NAMES) + r")\b")
DOCUMENTED_REFERENCES = {
    "Cargo.toml": re.compile(
        r"https://github\.com/rust-glancer/rust-glancer-lints|crates/rust-glancer-lints"),
    "editors/code/README.md": re.compile(r"Copy any desired `rust-glancer\.\*`"),
    ".github/workflows/github-release.yml": re.compile(r"Copy desired Rust Glancer settings"),
}
SKILL_REFERENCES = re.compile(r"(?<=name: )rust-glancer-debugging|(?<=\$)rust-glancer-debugging")
NO_REFERENCES = re.compile(r"(?!)")


def identity_failures(root=ROOT, paths=None):
    if paths is None:
        inventory = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, check=True, timeout=30,
        )
        paths = inventory.stdout.decode().split("\0")
    failures = []
    for relative in sorted(set(paths)):
        if not relative or relative.startswith(HISTORICAL_PATHS) or relative in IDENTITY_CONTROLS:
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        # The debugging skill's registered name is an existing agent entry point,
        # not an editor/server identity. Its instructions still use the fork name.
        registered_skill = relative.startswith(".agents/skills/rust-glancer-debugging/")
        if UPSTREAM_IDENTITY.search(relative) and not registered_skill:
            failures.append(f"{relative}: upstream name in an active project path")
        # The inherited eye logo has no searchable text. Keep its exact bytes as
        # a rejection control so a merge cannot silently restore that identity.
        contents = path.read_bytes()
        if hashlib.sha256(contents).hexdigest() == UPSTREAM_ICON_SHA256:
            failures.append(f"{relative}: upstream extension icon in an active project path")
        try:
            text = contents.decode("utf-8")
        except UnicodeDecodeError:
            continue
        references = SKILL_REFERENCES if registered_skill else DOCUMENTED_REFERENCES.get(relative, NO_REFERENCES)
        attribution = False
        lines = text.splitlines()
        checked_lines = []
        for line in lines:
            if relative == "README.md" and line.startswith("## "):
                attribution = line == "## Attribution"
            if attribution:
                checked_lines.append("")
                continue
            # The pinned external Dylint library still owns its published lint
            # names. Renaming our product must not invent unavailable dependencies.
            checked = references.sub("documented upstream reference", line)
            checked = EXTERNAL_LINTS.sub("external_lint", checked)
            checked_lines.append(checked)
        # Markdown can wrap a display name onto two lines. Check the complete
        # active text while keeping line positions for actionable diagnostics.
        checked = "\n".join(checked_lines)
        for match in UPSTREAM_IDENTITY.finditer(checked):
            line_number = checked.count("\n", 0, match.start()) + 1
            failures.append(f"{relative}:{line_number}: upstream product identity: {lines[line_number - 1].strip()}")
    return failures


def main():
    failures = identity_failures()
    if failures:
        raise SystemExit("Project identity check failed:\n" + "\n".join(failures))
    print("Current project identities use Suprnova LSP; upstream attribution and history are preserved.")


if __name__ == "__main__":
    main()
