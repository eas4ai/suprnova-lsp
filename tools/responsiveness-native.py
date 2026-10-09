#!/usr/bin/env python3
"""Retain named native responsiveness tests and their actual terminal outcomes."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import importlib.util


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rsp_native_observer", ROOT / "tools/sudus-responsiveness.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class NativeEvidence:
    # These cases cover individual invariants. They do not replace Devlist
    # timings, the sustained request stream, editor checks or memory evidence.
    cases = {
        "RSP-002": {
            "rg_project": ["storage::loaders::tests::live_artifact_release_worker_is_retained_at_observation_deadline"],
            "rg_body_ir": [
            "resolution::infer::tests::binding_hover_skips_unrelated_statements_only_after_its_type_settles",
            "resolution::infer::tests::binding_hover_omits_only_irrelevant_obligations_for_closed_calls"],
              "rg_ty": [
                  "trait_selection::tests::canonical_impls::canonical_projection_skips_disjoint_direct_impl_candidates"],
            "rg_lsp_engine": [
                  "engine::project::tests::hover::binding_hover_prepares_source_once_across_declaration_probe_and_body_fallback",
                "engine::queue::tests::hover_bypasses_queued_analysis_but_the_oldest_query_gets_the_next_turn",
                "engine::queue::tests::completion_receives_the_same_bounded_preference_as_hover",
                "engine::queue::tests::finite_lookahead_does_not_drain_an_unbounded_query_prefix"]},
        "RSP-003": {
            "rg_ty": [
                "trait_selection::tests::canonical_impls::canonical_bounds_and_blanket_projection_preserve_pending_goals",
                "trait_selection::tests::canonical_impls::canonical_projection_retains_alias_and_unresolved_headers",
                "trait_selection::tests::canonical_impls::live_variable_and_alias_receivers_keep_concrete_projection_answers",
                "trait_selection::tests::canonical_impls::canonical_projection_keeps_cancellation_and_incomplete_environment_unavailable"],
            "rg_project": [
                "tests::body_products::saved_generation_rejects_old_products_before_touching_its_artifact",
                "tests::failed_saved_candidate_preserves_published_generation",
                "tests::captured_saved_source_publishes_the_captured_text_and_revision",
                "tests::captured_saved_source_rejects_newer_disk_without_changing_the_published_project"],
            "rg_lsp_engine": [
                "engine::queue::tests::hover_cannot_cross_an_earlier_save_background_completion_or_shutdown",
                "engine::project::tests::rustdoc_candidates_use_the_captured_graph_and_reject_late_inputs",
                "engine::project::tests::hover::declaration_hover_preserves_pending_saved_bodies_and_current_header_coordinates",
                "tests::utils::rustdoc_import::edt_004_rejects_invalid_startup_without_replacing_the_previous_project",
                "tests::utils::rustdoc_import::edt_004_saved_body_and_reindex_keep_the_imported_api",
                "tests::document_read_flow::document_reads_combine_current_locals_with_saved_global_semantics"],
            "rg_lsp_server": [
                "methods::query_response::tests::document_query_reports_an_edit_that_arrived_after_analysis",
                "methods::query_response::tests::target_document_query_rejects_an_action_overtaken_by_a_new_version",
                "ingress::state::tests::target_change_invalidates_both_target_and_open_document_identity",
                "ingress::state::tests::sibling_edit_invalidates_only_the_open_document_set_identity",
                "ingress::state::tests::save_proposal_keeps_the_revision_seen_at_ingress",
                "ingress::service::tests::later_request_keeps_incrementally_changed_text_when_futures_finish_in_reverse",
                "ingress::service::tests::save_echo_is_recorded_before_any_handler_future_is_polled"]},
        "RSP-004": {
            "rg_project": [
                "indexing::split::build::tests::deferred_faster_builds_reserve_parallelism_for_queries",
                "indexing::split::build::tests::deferred_width_preserves_explicit_rayon_environment",
                "indexing::split::build::tests::deferred_width_preserves_lower_memory_and_synchronous_limits"],
            "rg_lsp_engine": [
                "engine::queue::tests::interactive_burst_does_not_starve_ordinary_analysis",
                "engine::queue::tests::replenished_interactive_requests_leave_oldest_commands_a_turn",
                "engine::queue::tests::dropping_the_queue_releases_buffered_and_unread_request_payloads",
                "engine::tests::cancelled_running_and_queued_hovers_release_the_lane_for_a_valid_hover",
                "engine::query::lifecycle::tests::query_stops_when_response_closes_during_analysis",
                "engine::query::lifecycle::tests::query_stops_when_request_token_is_cancelled_during_analysis",
                "engine::query::lifecycle::tests::cancellation_before_publication_cleans_up_and_allows_the_next_query"],
            "rg_body_ir": ["resolution::infer::tests::cancelling_recursive_inference_never_finalizes_partial_body_facts"],
            "rg_lsp_server": [
                "ingress::service::tests::admitted_cancellation_drops_pending_hover_without_polling_its_notification_future",
                "ingress::service::tests::pending_cancellation_future_survives_ingress_poll_and_completes_once"]},
        "RSP-005": {
            "rg_lsp_engine": [
                "engine::query::lifecycle::tests::wrapped_cancellation_is_distinct_from_a_source_failure_racing_with_cancellation"],
            "rg_lsp_server": [
                "methods::query_response::tests::internal_error_preserves_context_chain",
                "methods::query_response::tests::maps_typed_retryable_query_errors_to_content_modified"]},
    }

    @classmethod
    def assess(cls, discovery, events, code):
        wanted = observer.helpers.discover(discovery, cls.cases)
        outcomes = observer.helpers.outcomes(events, wanted, code)
        return {requirement: {case: outcomes[case] for cases in packages.values() for case in cases}
                for requirement, packages in cls.cases.items()}

    @classmethod
    async def run(cls):
        runner = observer.helpers.module("rsp_native_runner", ROOT / "tools/agent-debug.py")
        runner.install_signal_handlers()
        directory = runner.create_run_directory("rsp-native")
        before = observer.Diagnostic.runtime_fingerprint()
        observer_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        env = dict(os.environ, RUSTUP_TOOLCHAIN="1.98.1", CARGO_NET_OFFLINE="true",
                   NEXTEST_EXPERIMENTAL_LIBTEST_JSON="1", CARGO_TARGET_DIR=str(ROOT / "target"))
        packages = sorted({package for group in cls.cases.values() for package in group})
        names = sorted({case for group in cls.cases.values() for cases in group.values() for case in cases})
        selection = ["--locked", "--offline", "--lib"]
        for package in packages:
            selection.extend(["-p", package])
        selection.extend(["-E", " | ".join("test(=" + name + ")" for name in names)])
        commands, tests = [], {}
        complete = False
        try:
            outputs = []
            for label, args in [("discovery", ["nextest", "list", *selection, "--message-format", "json"]),
                                ("native", ["nextest", "run", *selection, "--message-format", "libtest-json",
                                    "--no-fail-fast", "--retries", "0", "--no-tests", "fail", "--test-threads", "2",
                                    "--failure-output", "immediate", "--success-output", "never"])]:
                print("observing " + label + ": " + str(directory / label), flush=True)
                result, text = await runner.observe_command(runner.CommandSpec("cargo", args), ROOT, env, directory / label, 20 * 60_000)
                result["phase"] = label
                commands.append(result)
                if label == "discovery" and result["code"] != 0:
                    raise ValueError("native test discovery or compilation failed")
                outputs.append(text)
            tests = cls.assess(*outputs, commands[-1]["code"])
            complete = True
        finally:
            report = {"purpose": "named native invariant cases; other RSP gates remain separate",
                "runtimeSourcesSha256": before, "runtimeSourcesUnchanged": before == observer.Diagnostic.runtime_fingerprint(),
                "observerSha256": observer_digest,
                "observerUnchanged": observer_digest == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "artifacts": {label: hashlib.sha256((directory / label / "stdout.log").read_bytes()).hexdigest()
                              for label in ("discovery", "native") if (directory / label / "stdout.log").exists()},
                "observationComplete": complete, "tests": tests, "commands": commands,
                "processCleanup": runner.summarize_cleanup(commands)}
            (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print("Native evidence: " + str(directory / "report.json"), flush=True)
        return directory / "report.json"


if __name__ == "__main__":
    asyncio.run(NativeEvidence.run())
