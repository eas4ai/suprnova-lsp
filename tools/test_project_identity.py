#!/usr/bin/env python3
"""Guard current product names while retaining genuine upstream records."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("project_identity", ROOT / ".github/scripts/check_project_identity.py")
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class ProjectIdentity(unittest.TestCase):
    def test_current_tree_has_no_active_upstream_identity(self):
        self.assertEqual(identity.identity_failures(), [])

    def test_rejects_old_identity_in_code_paths_and_editor_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "src/server.rs": 'const BINARY: &str = "rust-glancer";',
                "editors/zed/extension.toml": 'name = "Rust Glancer"',
                "tools/runtime.py": 'def rust_glancer_binary(): pass',
                "src/rust_glancer.rs": "// no old name in the body",
                "src/new.rs": 'const BINARY: &str = "suprnova-lsp";',
            }
            for path, text in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            failures = identity.identity_failures(root, files)
            self.assertEqual(len(failures), 4)
            self.assertFalse(any("src/new.rs" in failure for failure in failures))

    def test_rejects_a_product_name_wrapped_across_documentation_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "README.md"
            path.write_text("# Suprnova LSP\nDownloads the pinned Rust\nGlancer release.\n")
            failures = identity.identity_failures(root, ["README.md"])
            self.assertEqual(len(failures), 1)
            self.assertIn("README.md:2:", failures[0])

    def test_rejects_the_inherited_extension_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "editors/code/images/icon.png"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes((ROOT / "editors/code/test/fixtures/upstream-identity/icon.png").read_bytes())
            failures = identity.identity_failures(root, [relative])
            self.assertEqual(len(failures), 1)
            self.assertIn("upstream extension icon", failures[0])

    def test_keeps_attribution_history_and_upstream_isolation_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "README.md": "# Suprnova LSP\n## Attribution\nBased on Rust Glancer.\n",
                "CHANGELOG.md": "Rust Glancer upstream release",
                "docs/spec/identity.md": "Old rust-glancer settings must not control the fork.",
                "tools/fixtures/baseline.json": '{"binary": "/old/rust-glancer/checkout"}',
                "editors/code/test/fixtures/upstream-identity/package.json": '{"name": "rust-glancer"}',
            }
            for path, text in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            self.assertEqual(identity.identity_failures(root, files), [])
            (root / "README.md").write_text("# Rust Glancer\n## Attribution\nBased on Rust Glancer.\n")
            self.assertEqual(len(identity.identity_failures(root, files)), 1)
            (root / "README.md").write_text("# Suprnova LSP\n## Attribution\nBased on Rust Glancer.\n## Installation\nRun rust-glancer.\n")
            self.assertEqual(len(identity.identity_failures(root, files)), 1)

    def test_external_lint_dependency_keeps_its_published_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {"src/lib.rs": '#[allow(dylint_lib = "rust_glancer_lints", rust_glancer_pub_in)]\n'}
            (root / "src").mkdir()
            (root / "src/lib.rs").write_text(files["src/lib.rs"])
            self.assertEqual(identity.identity_failures(root, files), [])
            (root / "src/lib.rs").write_text(files["src/lib.rs"] + 'const NAME: &str = "Rust Glancer";\n')
            self.assertEqual(len(identity.identity_failures(root, files)), 1)

    def test_compatibility_report_consumer_requires_the_fork_counter(self):
        spec = importlib.util.spec_from_file_location("liveness", ROOT / ".github/scripts/check_lsp_compatibility_liveness.py")
        liveness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(liveness)
        report = {"fixture": {"kind": "rust_analyzer"}, "aggregates": [
            {"method": method, "suprnova_lsp_count": 1}
            for method in liveness.EXPECTED_CLEAN_METHODS]}
        self.assertEqual(liveness.liveness_failures(report), [])
        for entry in report["aggregates"]:
            entry["rust_glancer_count"] = entry.pop("suprnova_lsp_count")
        self.assertEqual(len(liveness.liveness_failures(report)), len(liveness.EXPECTED_CLEAN_METHODS))


if __name__ == "__main__":
    unittest.main()
