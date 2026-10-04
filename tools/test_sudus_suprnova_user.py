#!/usr/bin/env python3
"""Integrity tests reject incomplete and violating observations, never manufacture engine passes."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("user_gate", Path(__file__).with_name("sudus-suprnova-user.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class ObservationIntegrity(unittest.TestCase):
    def observations(self):
        queries = {name: True for name in ["query", "without", "filter", "key", "entity", "column", "source", "ownerIsolation"]}
        report = {"queries": queries, "indexingComplete": True, "idleResidentPages": [10] * 5,
                  "comparison": {"allocator": "mimalloc", "residency": "workspace", "residentPackages": ["directory"],
                      "sysroot": "/capture/sysroot", "targetCfg": "unix", "queryWorkload": sorted(queries)},
                  "candidates": {name: True for name in ["targetRejected", "referenceRejected", "previousPreserved"]}}
        return {"lower": {"selected": True, "lowered": {"directOwnership": True, "filterBounds": [
            "suprnova::eloquent::builder::IntoColumn", "suprnova::eloquent::builder::IntoVal"]}},
            **{mode: copy.deepcopy(report) for mode in ["initial", "batched", "source"]}}

    def assess(self, reports, target=True, forbidden=False, retained=True):
        traces = [{"mode": mode, "forbidden": ["rustdoc"] if forbidden else []} for mode in ["initial", "batched", "source"]]
        return gate.assess(reports, target, traces, retained)

    def test_every_requirement_rejects_its_observed_violation(self):
        self.assertTrue(all(self.assess(self.observations()).values()))
        controls = [
            ("SUP-001", lambda reports: reports["lower"].update(selected=False)),
            ("SUP-002", lambda reports: reports["batched"]["queries"].update(query=False)),
            ("SUP-003", lambda reports: reports["initial"]["queries"].update(entity=False)),
            ("SUP-004", lambda reports: reports["initial"]["candidates"].update(previousPreserved=False)),
            ("SUP-006", lambda reports: reports["source"].pop("idleResidentPages")),
        ]
        for requirement, mutate in controls:
            with self.subTest(requirement=requirement):
                reports = self.observations()
                mutate(reports)
                self.assertIs(self.assess(reports)[requirement], False)
        self.assertIs(self.assess(self.observations(), forbidden=True)["SUP-005"], False)

    def test_unreached_preservation_and_launch_checks_cannot_pass(self):
        results = self.assess({})
        self.assertNotIn("SUP-004", results)
        self.assertNotIn("SUP-005", results)
        self.assertIs(self.assess({}, target=False)["SUP-004"], False)
        self.assertIs(self.assess({}, forbidden=True)["SUP-005"], False)

    def test_bounds_and_retained_graph_are_required(self):
        reports = self.observations()
        reports["lower"]["lowered"]["filterBounds"] = []
        self.assertIs(self.assess(reports)["SUP-003"], False)
        self.assertIs(self.assess(self.observations(), retained=False)["SUP-006"], False)

    def test_mismatched_effective_residency_cannot_pass(self):
        reports = self.observations()
        reports["initial"]["comparison"]["residentPackages"] = ["directory", "suprnova"]
        self.assertIs(self.assess(reports)["SUP-006"], False)

    def test_empty_unknown_string_booleans_and_peak_only_reports_cannot_pass(self):
        reports = self.observations()
        reports["initial"]["queries"]["query"] = "true"
        reports["source"].pop("idleResidentPages")
        reports["source"]["peakRss"] = 10
        self.assertIs(self.assess(reports)["SUP-002"], False)
        self.assertIs(self.assess(reports)["SUP-006"], False)

    def test_application_report_requires_exact_successful_completion(self):
        line = 'suprnova-observation: {"mode":"initial"}\n'
        self.assertEqual(gate.observation(line, "initial", 0)["mode"], "initial")
        for text, mode, code in [("", "initial", 0), (line * 2, "initial", 0), (line, "source", 0), (line, "initial", 1)]:
            with self.assertRaises(ValueError):
                gate.observation(text, mode, code)

    def test_target_control_requires_test_events_not_build_failure(self):
        def event(state):
            return json.dumps({"type": "test", "name": "rg_project::rg_project$" + gate.WRONG_TARGET_TEST, "event": state}) + "\n"
        self.assertTrue(gate.target_test_outcome(event("started") + event("ok"), 0))
        self.assertFalse(gate.target_test_outcome(event("started") + event("failed"), 100))
        for output, code in [("", 100), (event("ok"), 0), (event("started") + event("ok") * 2, 0), (event("started") + event("ok"), 100)]:
            with self.assertRaises(ValueError):
                gate.target_test_outcome(output, code)

    def test_provenance_checks_every_recorded_boundary(self):
        metadata = {"workspace_members": ["directory"], "packages": [{"id": "directory", "manifest_path": "/capture/Cargo.toml"}],
                    "resolve": {"nodes": [{"id": "directory", "features": []}]}}
        sources = {"/capture/src/models/user.rs": "hash"}
        export = b"compiler export"
        producer = {"schema": 1, "frameworkRevision": gate.REVISION, "compiler": "compiler", "targetCfg": "cfg",
                    "packages": json.loads(json.dumps(gate.package_identity(metadata))), "sources": sources,
                    "exportSha256": gate.digest(export), "rustdocArgs": gate.rustdoc_args()}
        with patch.object(gate, "source_inventory", return_value=sources):
            gate.validate_capture(producer, metadata, "/sysroot", "compiler", "cfg", export)
            for key in ["frameworkRevision", "compiler", "targetCfg", "packages", "sources", "exportSha256", "rustdocArgs"]:
                changed = dict(producer, **{key: "mismatch"})
                with self.subTest(key=key), self.assertRaises(ValueError):
                    gate.validate_capture(changed, metadata, "/sysroot", "compiler", "cfg", export)

    def test_retained_export_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compiler = root / "crates/engine/project/src/indexing/compiler/mod.rs"
            declaration = root / "crates/engine/item-tree/src/compiler/mod.rs"
            compiler.parent.mkdir(parents=True)
            declaration.parent.mkdir(parents=True)
            declaration.write_text("struct CompilerTypeDeclarations {}")
            compiler.write_text("struct CompilerImports { declarations: CompilerTypeDeclarations, export: RustdocExport }")
            self.assertFalse(gate.compact_layout_ok(root))


if __name__ == "__main__":
    unittest.main()
