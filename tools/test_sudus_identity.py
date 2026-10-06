#!/usr/bin/env python3
"""Reject incomplete identity evidence and inconsistent real-test results."""

import copy
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("identity_acceptance", ROOT / "tools/sudus-identity.py")
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class IdentityIntegrity(unittest.TestCase):
    def setUp(self):
        self.sources = {"results": dict.fromkeys(identity.REQUIREMENTS[:4], True)}
        self.packages = {"extension": True, "binary": True, "licenses": True}
        self.protocol = {"passed": True}
        self.regressions = {"AUT": True, "EDT": True}

    def test_each_requirement_rejects_its_observed_violation(self):
        self.assertTrue(all(identity.assess(self.sources, True, self.packages, self.protocol, self.regressions).values()))
        for requirement in identity.REQUIREMENTS[:4]:
            sources = copy.deepcopy(self.sources)
            sources["results"][requirement] = False
            self.assertFalse(identity.assess(sources, True, self.packages, self.protocol, self.regressions)[requirement])
        for regressions in [{}, {"AUT": True}, {"AUT": True, "EDT": False}]:
            self.assertFalse(identity.assess(self.sources, True, self.packages, self.protocol, regressions)["IDN-005"])

    def test_missing_runtime_package_and_editor_observations_cannot_pass(self):
        result = identity.assess(self.sources, False, {}, {}, {})
        self.assertFalse(any(result.values()))
        for field, requirement in [("extension", "IDN-001"), ("binary", "IDN-003"), ("licenses", "IDN-004")]:
            packages = dict(self.packages)
            del packages[field]
            self.assertFalse(identity.assess(self.sources, True, packages, self.protocol, self.regressions)[requirement])
        for protocol in [{}, {"passed": False}, {"passed": "true"}]:
            result = identity.assess(self.sources, True, self.packages, protocol, self.regressions)
            self.assertFalse(result["IDN-003"])
            self.assertFalse(result["IDN-005"])

    def test_missing_duplicate_or_nonboolean_source_results_are_rejected(self):
        for results in [{}, {**self.sources["results"], "IDN-005": True}, {**self.sources["results"], "IDN-001": "true"}]:
            with self.assertRaises(ValueError):
                identity.assess({"results": results}, True, self.packages, self.protocol, self.regressions)

    def test_child_mechanism_must_observe_every_requirement_once(self):
        text = "\n".join(f"sudus: AUT-{number:03}: pass" for number in range(1, 10))
        self.assertTrue(identity.regression_result(text, 0, "AUT", 9))
        failed = text.replace("AUT-001: pass", "AUT-001: fail")
        self.assertFalse(identity.regression_result(failed, 1, "AUT", 9))
        for value, code in [("", 0), (text + "\nsudus: AUT-001: pass", 0), (text, 1), (failed, 0),
                            (text.replace("AUT-001: pass", "AUT-002: pass"), 0)]:
            with self.assertRaises(ValueError):
                identity.regression_result(value, code, "AUT", 9)

    def test_real_editor_report_rejects_skips_absence_and_wrong_identity(self):
        editor = identity.module("identity_test_editor", ROOT / "tools/sudus-editor-import.py")
        editor.EDITOR_CASE = identity.EDITOR_CASE
        case = {"fullTitle": identity.EDITOR_CASE}
        report = {"stats": {"pending": 0}, "tests": [case], "passes": [case], "failures": []}
        self.assertTrue(editor.editor_outcome(json.dumps(report), 0))
        for changes in [{"tests": []}, {"passes": []}, {"stats": {"pending": 1}},
                        {"tests": [{"fullTitle": "unrelated editor case"}]}]:
            with self.assertRaises(ValueError):
                editor.editor_outcome(json.dumps({**report, **changes}), 0)
        with self.assertRaises(ValueError):
            editor.editor_outcome(json.dumps(report), 1)


class PackagedIdentityIntegrity(unittest.TestCase):
    def test_packaged_install_ids_binary_bytes_and_licenses_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "suprnova-lsp"
            binary.write_bytes(b"binary identity control")
            for name in ["LICENSE-MIT", "LICENSE-APACHE"]:
                (root / name).write_text(name + " original license text\n")
            archive = root / "server.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                for name in ["suprnova-lsp", "LICENSE-MIT", "LICENSE-APACHE"]:
                    output.add(root / name, arcname=name)
            values = {
                "extension/package.json": json.dumps({"publisher": "eas4ai", "name": "suprnova-lsp", "displayName": "Suprnova LSP"}).encode(),
                "extension.vsixmanifest": b'<PackageManifest><Identity Publisher="eas4ai" Id="suprnova-lsp" /></PackageManifest>',
                "extension/server/suprnova-lsp": binary.read_bytes(),
                "extension/LICENSE": b"LICENSE-MIT original license text\nLICENSE-APACHE original license text\n",
            }
            vsix = root / "extension.vsix"
            controls = [({}, None), ({"extension.vsixmanifest": b'<PackageManifest><Identity Publisher="rust-glancer" Id="rust-glancer" /></PackageManifest>'}, "extension"),
                        ({"extension/server/suprnova-lsp": b"another executable"}, "binary"),
                        ({"extension/server/rust-glancer": b"upstream executable"}, "binary"),
                        ({"extension/LICENSE": b"different license text"}, "licenses")]
            for changed, rejected in controls:
                with zipfile.ZipFile(vsix, "w") as output:
                    for name, data in {**values, **changed}.items():
                        output.writestr(name, data)
                observed = identity.package_observations(vsix, archive, binary, root)
                if rejected is None:
                    self.assertTrue(all(observed[name] for name in ["extension", "binary", "licenses"]))
                else:
                    self.assertFalse(observed[rejected], changed)
            # A valid VSIX cannot mask an archive that omitted a mandatory asset.
            with tarfile.open(archive, "w:gz") as output:
                output.add(binary, arcname="suprnova-lsp")
            with self.assertRaises(ValueError):
                identity.package_observations(vsix, archive, binary, root)


if __name__ == "__main__":
    unittest.main()
