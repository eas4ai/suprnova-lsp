"""Pure corruption controls for settled lifecycle, capture and source provenance."""
import asyncio
import copy
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('responsiveness_idle_tested', ROOT / 'tools/responsiveness-idle.py')
idle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idle)
Historical, Modern, Proof = idle.HistoricalSettledRss, idle.SettledRss, idle.IdleProof
spec = importlib.util.spec_from_file_location('responsiveness_idle_collector_tested', ROOT / 'tools/responsiveness-idle-collect.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
Capture = collector.CaptureProof
class HistoricalControls(unittest.TestCase):
    def setUp(self):
        self.root = '/application'
        self.mode = 'faster-builds'
        self.native = []
        self.raw = {'idleMemory': {'metric': 'sum-of-process-RSS', 'indexingComplete': True,
            'samplingIntervalMs': 100, 'indexingPeakRssBytes': 900, 'indexingSamples': 5,
            'samples': [{'observedNs': 1000+n*100_000_000, 'processRssBytes': {'101': 30, '102': 70}, 'aggregateRssBytes': 100} for n in range(5)]},
            'barriers': {'hoverCleanup': True}, 'transport': [], 'stages': [],
            'lifecycle': [{'method': 'suprnova-lsp/activeWorkspaceChanged', 'receivedNs': 1, 'params': {'root': self.root, 'state': 'indexing'}},
                {'method': 'experimental/serverStatus', 'receivedNs': 2, 'params': {'health': 'ok', 'quiescent': False}},
                {'method': 'suprnova-lsp/deferredIndexingStarted', 'receivedNs': 10, 'params': {'root': self.root}},
                {'method': 'suprnova-lsp/activeWorkspaceChanged', 'receivedNs': 11, 'params': {'root': self.root, 'state': 'ready'}},
                {'method': 'experimental/serverStatus', 'receivedNs': 40, 'params': {'health': 'ok', 'quiescent': True}},
                {'method': 'suprnova-lsp/deferredIndexingFinished', 'receivedNs': 41, 'params': {'root': self.root, 'outcome': 'succeeded'}}]}
        self.add('engine command started: initialize', {'root': self.root})
        self.add('memory report', {'label': 'after project build'}, 5)
        self.add('project stats', {'label': 'initial index'})
        self.add('workspace indexing finished', {'workspace_root': self.root, 'indexing_preference': self.mode, 'stale_retries': 0})
        self.add('deferred indexing background finish started', {'generation': 1})
        self.add('deferred indexing background finish completed', {'generation': 1})
        self.add('engine command started: deferred_indexing_finished', {'generation': 1})
        self.add('memory report', {'label': 'after deferred indexing finish'}, 39)
        for n in range(3):
            self.raw['transport'].append({'id': 2+n, 'method': 'textDocument/hover', 'status': 'success', 'writtenNs': 50+n*20, 'receivedNs': 59+n*20, 'durationNs': 9})
            self.add('analysis query started', {'label': 'hover'}, 51+n*20)
            self.add('analysis query completed', {'query': 'hover', 'status': 'ok'}, 57+n*20)
            self.add('memory report', {'label': 'hover'}, 60+n*20)
    def add(self, message, fields, time=None):
        self.native.append({'message': message, 'fields': fields, 'serializedLine': len(self.native)+1})
        if time is not None:
            self.raw['stages'].append({'message': message, 'fields': copy.deepcopy(fields), 'observedNs': time})
    def assess(self):
        return Historical.assess(self.raw, self.root, self.mode, self.native)
    def event(self, message):
        return next(e for e in self.native if e['message'] == message)
    def lower(self):
        self.mode = 'lower-peak-memory'
        self.event('workspace indexing finished')['fields']['indexing_preference'] = self.mode
        self.native = [e for e in self.native if 'deferred indexing background finish' not in e['message']
                       and e['message'] != 'engine command started: deferred_indexing_finished'
                       and e['fields'].get('label') != 'after deferred indexing finish']
        indexed = self.event('workspace indexing finished')['serializedLine']
        self.native.append({'message': 'deferred indexing already complete', 'fields': {'generation': 1}, 'serializedLine': indexed+1})
        self.native.sort(key=lambda e: e['serializedLine'])
        self.raw['stages'] = [e for e in self.raw['stages'] if e['fields'].get('label') != 'after deferred indexing finish']
        self.raw['lifecycle'] = [e for e in self.raw['lifecycle'] if 'deferredIndexing' not in e['method']]
    def test_actual_shaped_old_faster_without_modern_fields(self):
        self.assertEqual(self.assess()['completionEvidence'][0]['savedGeneration'], 1)
        self.assertIsNone(self.assess()['completionEvidence'][0]['nativeGenerationStageIndex'])
    def test_actual893_missing_modern_publication_is_explicit(self):
        self.assertFalse(any(e['message'] == 'configured rustdoc declarations published' for e in self.native))
        proof = self.assess()['completionEvidence'][0]['historicalSerializedCompletion']
        self.assertIsNone(proof['publishedSerializedLine'])
        self.assertEqual(proof['savedGeneration'], 1)
    def test_actual_shaped_old_lower_without_modern_fields(self):
        self.lower(); self.assertEqual(self.assess()['rssBytes'], [100]*5)
    def test_unchanged_current_core_still_rejects_no_generation(self):
        with self.assertRaisesRegex(ValueError, 'saved indexing generation'):
            Modern.assess(self.raw, self.root, self.mode)
    def test_wrong_worker_generation(self):
        self.event('deferred indexing background finish completed')['fields']['generation'] = 2
        with self.assertRaisesRegex(ValueError, 'generation'): self.assess()
    def test_wrong_initialized_root(self):
        self.event('engine command started: initialize')['fields']['root'] = '/other'
        with self.assertRaisesRegex(ValueError, 'workspace'): self.assess()
    def test_missing_native_finish(self):
        self.native.remove(self.event('deferred indexing background finish completed'))
        with self.assertRaisesRegex(ValueError, 'missing'): self.assess()
    def test_duplicate_worker_start(self):
        self.native.append(copy.deepcopy(self.event('deferred indexing background finish started')))
        with self.assertRaisesRegex(ValueError, 'duplicated'): self.assess()
    def test_native_purge_before_applied_finish(self):
        self.event('engine command started: deferred_indexing_finished')['serializedLine'] = 100
        with self.assertRaisesRegex(ValueError, 'order'): self.assess()
    def test_native_saved_mutation(self):
        self.add('engine command started: saved_files_changed', {})
        with self.assertRaisesRegex(ValueError, 'mutation'): self.assess()
    def test_lower_unfinished_generation(self):
        self.lower(); self.event('deferred indexing already complete')['fields']['generation'] = 2
        with self.assertRaisesRegex(ValueError, 'generation'): self.assess()
    def test_lower_native_background_work(self):
        self.lower(); self.add('deferred indexing background finish started', {'generation': 1})
        with self.assertRaisesRegex(ValueError, 'background'): self.assess()
    def test_failed_wire_terminal(self):
        self.raw['lifecycle'][-1]['params']['outcome'] = 'failed'
        with self.assertRaisesRegex(ValueError, 'deferred lifecycle'): self.assess()
    def test_late_purge(self):
        next(e for e in self.raw['stages'] if e['fields'].get('label') == 'after deferred indexing finish')['observedNs'] = 2000
        with self.assertRaisesRegex(ValueError, 'indexing completion'): self.assess()
    def test_busy_during_sampling_then_recovers(self):
        self.raw['lifecycle'].extend([{'method': 'experimental/serverStatus', 'receivedNs': 2000, 'params': {'health': 'ok', 'quiescent': False}},
            {'method': 'experimental/serverStatus', 'receivedNs': 3000, 'params': {'health': 'ok', 'quiescent': True}}])
        with self.assertRaisesRegex(ValueError, 'busy'): self.assess()
    def test_changed_sample_membership(self):
        self.raw['idleMemory']['samples'][-1]['processRssBytes'] = {'101': 30, '103': 70}
        with self.assertRaisesRegex(ValueError, 'membership'): self.assess()
    def test_missing_request_release(self):
        self.raw['stages'].pop()
        with self.assertRaisesRegex(ValueError, 'release'): self.assess()
    def test_corrupt_aggregate(self):
        self.raw['idleMemory']['samples'][0]['aggregateRssBytes'] = 101
        with self.assertRaisesRegex(ValueError, 'contradicts'): self.assess()


class ProvenanceControls(unittest.TestCase):
    def setUp(self):
        self.before = {'completedNs': 10, 'observedAt': '2026-10-10T01:00:00.000Z',
            'externalBoundaries': {'protectedCheckoutInspected': False}}
        for name in ('head', 'frozenPins', 'runtimeSourcesSha256', 'managedBinarySha256',
                     'archiveBinarySha256', 'baselineBinarySha256', 'references', 'applicationInputs',
                     'ordinaryApplicationOutputSentinels', 'captureMechanism'):
            self.before[name] = {'recorded': name}
        self.after = {**copy.deepcopy(self.before), 'startedNs': 40, 'observedAt': '2026-10-10T01:00:04.000Z'}
        self.argv = ['python3', '-B', 'original']
        self.plan = {'beforeCaptureCommittedNs': 11, 'invokedNs': 12,
                     'aggregateDeadlineMs': 900000, 'plannedArgv': self.argv}
        self.run = Path('/owned/runs/20261010T010002000Z-rsp-idle-pairs-101-suffix')
        self.selected = {'selectedAtNs': 20, 'observedAt': '2026-10-10T01:00:02.000Z',
                         'driverPid': 101, 'run': str(self.run), 'plannedArgv': self.argv}
        self.process = {'pid': 101, 'startedAt': '2026-10-10T01:00:01.000Z',
                        'command': 'python3', 'args': ['-B', 'original']}
        self.outcome = {**self.process, 'code': 0, 'signal': None, 'timedOut': False,
                        'cleanup': {'verifiedEmpty': True, 'remainingPids': []}}
        self.diff = ('diff --git a/crates/lsp/engine/src/engine/query/mod.rs b/crates/lsp/engine/src/engine/query/mod.rs\n'
            '-                // interval includes opening the read view and preparing current declarations.\n'
            '+                // interval includes opening the read view and reaching the first checkpoint.\n')

    def capture(self):
        Proof.capture_order(self.before, self.after, self.plan, self.selected, self.process,
                            self.outcome, self.run, self.argv)

    def test_real_before_launch_allocation_after_shape(self):
        self.capture()

    def test_post_hoc_before_capture_rejected(self):
        self.plan['beforeCaptureCommittedNs'] = 21
        with self.assertRaisesRegex(ValueError, 'order'): self.capture()

    def test_wrong_driver_allocation_rejected(self):
        self.selected['driverPid'] = 102
        with self.assertRaisesRegex(ValueError, 'driver'): self.capture()

    def test_failed_driver_cleanup_rejected(self):
        self.outcome['cleanup']['verifiedEmpty'] = False
        with self.assertRaisesRegex(ValueError, 'cleanup'): self.capture()

    def test_application_output_change_rejected(self):
        self.after['ordinaryApplicationOutputSentinels'] = {'changed': True}
        with self.assertRaisesRegex(ValueError, 'boundary changed'): self.capture()

    def test_query_argv_override_rejected(self):
        self.process['args'] = ['-B', 'different-workload']
        with self.assertRaisesRegex(ValueError, 'CLI'): self.capture()

    def test_original_comment_only_diff_accepted(self):
        Proof.historical_diff(self.diff)

    def test_executable_historical_change_rejected(self):
        with self.assertRaisesRegex(ValueError, 'comment-only'):
            Proof.historical_diff(self.diff + '+let changed = true;\n')

    def test_comment_in_other_native_file_rejected(self):
        with self.assertRaisesRegex(ValueError, 'another native file'):
            Proof.historical_diff(self.diff.replace('query/mod.rs', 'query/lifecycle.rs'))

    def test_empty_historical_diff_not_called_identical(self):
        with self.assertRaisesRegex(ValueError, 'comment-only'): Proof.historical_diff('')

    def test_current_source_bound_retained_review(self):
        Proof.retained_source(idle.RETAINED_REVIEW_SHA, idle.REVIEWED_SOURCE, idle.REVIEWED_SOURCE, idle.REVIEWED_SOURCE)

    def test_stale_retained_review_rejected(self):
        with self.assertRaisesRegex(ValueError, 'current measured'):
            Proof.retained_source(idle.RETAINED_REVIEW_SHA, idle.REVIEWED_SOURCE, 'different-source', idle.REVIEWED_SOURCE)

    def test_substituted_review_bytes_rejected(self):
        with self.assertRaisesRegex(ValueError, 'retained-owner'):
            Proof.retained_source('0' * 64, idle.REVIEWED_SOURCE, idle.REVIEWED_SOURCE, idle.REVIEWED_SOURCE)

    def test_tooling_head_change_not_native_applicability(self):
        Proof.applicability({'currentRuntimeSourcesSha256': 'same-native-source', 'archiveBinarySha256': 'same-binary',
            'managedBinarySha256': 'same-binary', 'head': 'new-tooling-commit'}, 'same-native-source', 'same-binary')

    def test_wrong_current_binary_rejected(self):
        with self.assertRaisesRegex(ValueError, 'executable'):
            Proof.applicability({'currentRuntimeSourcesSha256': 'source', 'archiveBinarySha256': 'other',
                'managedBinarySha256': 'binary'}, 'source', 'binary')

    def test_all_lower_medians_have_no_allowance(self):
        self.assertEqual(Proof.increases({'mode': {'pairMediansBytes': {'baseline': [100, 100, 100],
            'candidate': [99, 98, 97]}, 'medianDeltaBytes': -2}}), {})

    def test_repeatable_increase_requires_reviewed_cause(self):
        with self.assertRaisesRegex(ValueError, 'investigation'):
            Proof.increases({'mode': {'pairMediansBytes': {'baseline': [100, 100, 100],
                'candidate': [101, 101, 99]}, 'medianDeltaBytes': 1}})

    def test_numerical_allowance_does_not_excuse_increase(self):
        with self.assertRaisesRegex(ValueError, 'investigation'):
            Proof.increases({'mode': {'pairMediansBytes': {'baseline': [100, 100, 100],
                'candidate': [101, 101, 99]}, 'medianDeltaBytes': 1, 'allowanceBytes': 1024}})


class ProducerControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('frozen_producer_control', ROOT / 'tools/sudus-suprnova-user.py')
        cls.user = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.user)

    def setUp(self):
        self.metadata = {'workspace_members': ['application'], 'packages': [{'id': 'application',
            'manifest_path': '/application/Cargo.toml'}], 'resolve': {'nodes': [{'id': 'application', 'features': ['one']}]}}
        self.compiler, self.cfg, self.export = 'recorded compiler', 'recorded cfg', b'actual export bytes'
        self.inventory, self.configuration = {'src.rs': 'source-sha'}, {'environment': {'RUSTFLAGS': None}}
        self.producer = {'schema': 1, 'frameworkRevision': self.user.REVISION, 'compiler': self.compiler,
            'targetCfg': self.cfg, 'packages': json.loads(json.dumps(self.user.package_identity(self.metadata))),
            'sources': self.inventory, 'exportSha256': self.user.digest(self.export), 'rustdocArgs': self.user.rustdoc_args(),
            'configuration': self.configuration}

    def validate(self):
        # Replace only expensive filesystem inventory in this pure control.
        # Execute the exact frozen producer validator and package/feature parser.
        with patch.object(self.user, 'source_inventory', return_value=self.inventory), \
             patch.object(self.user, 'producer_configuration', return_value=self.configuration):
            self.user.validate_capture(self.producer, self.metadata, Path('/sysroot'), self.compiler, self.cfg, self.export)

    def test_frozen_validator_checks_actual_input_shape(self):
        self.validate()

    def test_changed_feature_resolution_rejected(self):
        self.metadata['resolve']['nodes'][0]['features'] = ['changed']
        with self.assertRaisesRegex(ValueError, 'feature'): self.validate()

    def test_changed_export_bytes_rejected(self):
        self.export = b'substituted export'
        with self.assertRaisesRegex(ValueError, 'export digest'): self.validate()

    def test_changed_compiler_cfg_rejected(self):
        self.cfg = 'other cfg'
        with self.assertRaisesRegex(ValueError, 'configuration'): self.validate()

    def test_changed_override_configuration_rejected(self):
        self.configuration = {'environment': {'RUSTFLAGS': 'changed'}}
        with self.assertRaisesRegex(ValueError, 'overrides'): self.validate()


class CollectorControls(unittest.TestCase):
    def setUp(self):
        self.inputs = SimpleNamespace(head='measured-head', source='measured-source', binary='measured-binary',
            archive=collector.OWNED / 'authored-archive', argv=['python3', '-B', 'tools/sudus-responsiveness.py',
                '--idle-pairs', '--no-build', '--workload', 'generated-captured', '--baseline-manifest',
                'target/agent-debug/authored-manifest.json', '--nofile-soft', '4096'])
        self.before = {'schema': 'rsp006-real-boundary/v1', 'frozenPins': collector.PINS.copy(),
            'head': self.inputs.head, 'runtimeSourcesSha256': self.inputs.source,
            'managedBinarySha256': self.inputs.binary, 'archiveBinarySha256': self.inputs.binary,
            'baselineBinarySha256': collector.BASELINE, 'managedBinary': str(collector.COMMON),
            'archiveBinary': str(self.inputs.archive), 'startedNs': 10, 'completedNs': 20,
            'observedAt': '2026-10-10 06:00:00', 'references': {'compiled': {'sha256': 'example'}},
            'applicationInputs': {'Cargo.lock': 'a'}, 'ordinaryApplicationOutputSentinels': {'ordinaryCargoTarget': {'exists': False}},
            'captureMechanism': {'path': 'owned/wrapper.py', 'sha256': 'source'},
            'baselineOrigin': {'compiledCommit': '893130fd9d3a4e292a47ddfbb68e171e5bcc6f38',
                'manifestObservationCommit': '8e17c49d57b1a7913727ba68148be7f23ba8ad91',
                'contemporaneousRuntimeFingerprint': None, 'contemporaneousBuildEndSourceGuard': None},
            'externalBoundaries': {'protectedCheckoutInspected': False}}
        self.after = copy.deepcopy(self.before)
        self.after.update(startedNs=100, completedNs=110, observedAt='2026-10-10 06:10:00')
        self.run = collector.OWNED / 'runs/20261010T060000000Z-rsp-idle-pairs-123-abcdef'

    def test_exact_observed_boundaries_match(self):
        Capture.matched(self.before, self.after, self.inputs)

    def test_one_changed_frozen_helper_is_rejected(self):
        self.after['frozenPins']['lsp-query.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'six frozen'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_current_runtime_change_is_rejected(self):
        self.after['runtimeSourcesSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'checkout/source'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_stale_managed_native_binary_is_rejected(self):
        self.before['managedBinarySha256'] = collector.BASELINE
        with self.assertRaisesRegex(ValueError, 'native bytes'):
            Capture.boundary(self.before, self.inputs)

    def test_substituted_archive_native_binary_is_rejected(self):
        self.after['archiveBinarySha256'] = collector.BASELINE
        with self.assertRaisesRegex(ValueError, 'native bytes'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_modified_historical_baseline_binary_is_rejected(self):
        self.after['baselineBinarySha256'] = self.inputs.binary
        with self.assertRaisesRegex(ValueError, 'baseline native bytes'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_fabricated_historical_build_end_guard_is_rejected(self):
        self.before['baselineOrigin']['contemporaneousBuildEndSourceGuard'] = True
        with self.assertRaisesRegex(ValueError, 'historical'):
            Capture.boundary(self.before, self.inputs)

    def test_manifest_commit_cannot_replace_actual_compiled_commit(self):
        self.before['baselineOrigin']['compiledCommit'] = self.before['baselineOrigin']['manifestObservationCommit']
        with self.assertRaisesRegex(ValueError, 'historical'):
            Capture.boundary(self.before, self.inputs)

    def test_application_source_or_lockfile_change_is_rejected(self):
        self.after['applicationInputs']['Cargo.lock'] = 'b'
        with self.assertRaisesRegex(ValueError, 'applicationInputs'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_ordinary_application_output_creation_is_rejected(self):
        self.after['ordinaryApplicationOutputSentinels']['ordinaryCargoTarget']['exists'] = True
        with self.assertRaisesRegex(ValueError, 'OutputSentinels'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_wrapper_source_change_is_rejected(self):
        self.after['captureMechanism']['sha256'] = 'different'
        with self.assertRaisesRegex(ValueError, 'captureMechanism'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_compiled_reference_change_is_rejected(self):
        self.after['references']['compiled']['sha256'] = 'different'
        with self.assertRaisesRegex(ValueError, 'references'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_protected_checkout_capture_is_rejected(self):
        self.before['externalBoundaries']['protectedCheckoutInspected'] = True
        with self.assertRaisesRegex(ValueError, 'protected'):
            Capture.boundary(self.before, self.inputs)

    def test_overlapping_boundary_capture_is_rejected(self):
        self.after['startedNs'] = self.before['completedNs']
        with self.assertRaisesRegex(ValueError, 'overlap'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_nonadvancing_wall_time_is_rejected(self):
        self.after['observedAt'] = self.before['observedAt']
        with self.assertRaisesRegex(ValueError, 'wall timestamps'):
            Capture.matched(self.before, self.after, self.inputs)

    def test_original_cli_and_before_commit_precede_launch(self):
        Capture.launch(20, 21, 22, self.inputs.argv[0], self.inputs.argv[1:], self.inputs.argv)

    def test_before_capture_committed_after_launch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'before actual launch'):
            Capture.launch(20, 23, 22, self.inputs.argv[0], self.inputs.argv[1:], self.inputs.argv)

    def test_binary_override_changes_original_cli(self):
        with self.assertRaisesRegex(ValueError, 'CLI'):
            Capture.launch(20, 21, 22, self.inputs.argv[0], self.inputs.argv[1:] + ['--binary', 'other'], self.inputs.argv)

    def test_selecting_only_one_mode_changes_original_cli(self):
        with self.assertRaisesRegex(ValueError, 'CLI'):
            Capture.launch(20, 21, 22, self.inputs.argv[0], self.inputs.argv[1:] + ['--mode', 'faster-builds'], self.inputs.argv)

    def test_no_build_cannot_be_dropped(self):
        args = [arg for arg in self.inputs.argv[1:] if arg != '--no-build']
        with self.assertRaisesRegex(ValueError, 'CLI'):
            Capture.launch(20, 21, 22, self.inputs.argv[0], args, self.inputs.argv)

    def test_actual_pid_allocated_run_is_selected(self):
        Capture.allocation(self.run, 123, set())

    def test_wrong_driver_pid_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'actual managed driver PID'):
            Capture.allocation(self.run, 456, set())

    def test_historical_run_cannot_supply_this_observation(self):
        with self.assertRaisesRegex(ValueError, 'predates'):
            Capture.allocation(self.run, 123, {str(self.run)})

    def test_external_run_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'driver PID'):
            Capture.allocation(Path('/outside') / self.run.name, 123, set())

    def test_two_actual_driver_run_allocations_are_rejected(self):
        other = self.run.with_name(self.run.name + '-second')
        with self.assertRaisesRegex(ValueError, 'multiple selected'):
            Capture.allocation(self.run, 123, set(), other)

    def preserve_outcome(self, actual):
        parent = collector.IdleCollector(self.inputs, collector.OWNED / "authored-exclusive-proof")
        written = []
        parent.write = lambda name, value: written.append((name, value)) or {'path': 'in-memory', 'sha256': 'authored'}

        async def original(*args, **kwargs):
            return actual

        returned = asyncio.run(parent.preserve_supervised_outcome(original))
        self.assertIs(returned, actual)
        self.assertIs(parent.outcome, actual)
        self.assertEqual(parent.commands, [actual])
        self.assertEqual(written, [('launch-outcome.json', actual)])

    def test_actual_timeout_outcome_is_preserved_without_success_relabeling(self):
        self.preserve_outcome({'code': None, 'signal': 'SIGTERM', 'timedOut': True,
            'cleanup': {'verifiedEmpty': True, 'remainingPids': []}})

    def test_actual_unverified_cleanup_is_preserved_for_rejection(self):
        self.preserve_outcome({'code': 0, 'signal': None, 'timedOut': False,
            'cleanup': {'verifiedEmpty': False, 'remainingPids': [456]}})

    def test_nonzero_empty_outcome_is_preserved_without_retry(self):
        self.preserve_outcome({'code': 143, 'signal': None, 'timedOut': False,
            'cleanup': {'verifiedEmpty': True, 'remainingPids': []}})

    def test_spawn_failure_is_preserved_without_inventing_process_identity(self):
        self.preserve_outcome({'code': None, 'spawnError': 'controlled authored failure', 'timedOut': False,
            'cleanup': {'verifiedEmpty': None, 'processGroupId': None}})



class ActualRetainedRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        retained = ROOT / 'target/agent-debug/current-eight-reader-idle-pairs-assessment-historical-schema-before-native-publication-fix-64c8351d050e.json'
        if not retained.exists():
            raise unittest.SkipTest('genuine original baseline records are not installed in this clone')
        if retained.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('retained baseline fixture exceeds explicit bound')
        data = retained.read_bytes()
        if hashlib.sha256(data).hexdigest() != '64c8351d050ec1e47b4d8fa1c6bbf4301604671b838ae546d911497b3727497a': raise ValueError('retained failed assessment changed')
        report = json.loads(data)
        refs = {row['path']: row['sha256'] for row in report['references']}
        cls.members = []
        root = Path(report['run'])
        for attempt in report['allPhysicalAttempts']:
            label = attempt['label']
            if not label.endswith('-baseline'): continue
            mode = 'lower-peak-memory' if 'lower-peak-memory' in label else 'faster-builds'
            path = root / label / 'stdout.log'
            raw_bytes = path.read_bytes()
            if hashlib.sha256(raw_bytes).hexdigest() != refs[str(path)]: raise ValueError('actual baseline stdout changed')
            text = raw_bytes.decode(); raw = json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
            facts = report['allNativeEvidence'][label]['allSerializedEvents']
            cls.members.append((label, mode, raw, facts))
        if len(cls.members) != 6: raise ValueError('expected six genuine baseline records')
    def test_all_six_actual_baselines_both_modes(self):
        for label, mode, raw, native in self.members:
            with self.subTest(label=label):
                result = Historical.assess(raw, '/home/shawn/workspace2/devlist.app', mode, native)
                self.assertEqual(len(result['rssBytes']), 5)
                self.assertIsNone(result['completionEvidence'][0]['historicalSerializedCompletion']['publishedSerializedLine'])
    def test_each_actual_wrong_worker_or_complete_generation(self):
        for label, mode, raw, native in self.members:
            with self.subTest(label=label):
                events = copy.deepcopy(native)
                message = 'deferred indexing background finish completed' if mode == 'faster-builds' else 'deferred indexing already complete'
                next(e for e in events if e['message'] == message)['fields']['generation'] = 2
                with self.assertRaisesRegex(ValueError, 'generation'):
                    Historical.assess(raw, '/home/shawn/workspace2/devlist.app', mode, events)
    def test_each_actual_missing_completion(self):
        for label, mode, raw, native in self.members:
            with self.subTest(label=label):
                message = 'deferred indexing background finish completed' if mode == 'faster-builds' else 'deferred indexing already complete'
                events = [e for e in native if e['message'] != message]
                with self.assertRaisesRegex(ValueError, 'missing'):
                    Historical.assess(raw, '/home/shawn/workspace2/devlist.app', mode, events)
    def test_each_actual_premature_rss(self):
        for label, mode, raw, native in self.members:
            with self.subTest(label=label):
                changed = copy.deepcopy(raw)
                release = max(e['observedNs'] for e in raw['stages'] if e['message'] == 'memory report' and e['fields'].get('label') == 'hover')
                changed['idleMemory']['samples'][0]['observedNs'] = release-1
                with self.assertRaisesRegex(ValueError, 'settled completion'):
                    Historical.assess(changed, '/home/shawn/workspace2/devlist.app', mode, native)

class CaptureCleanupControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'tools/agent-debug.py'
        if hashlib.sha256(path.read_bytes()).hexdigest() != idle.PINS[path.name]:
            raise AssertionError('frozen runner source changed')
        spec = importlib.util.spec_from_file_location('frozen_idle_cleanup_control', path)
        cls.runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.runner
        spec.loader.exec_module(cls.runner)

    def setUp(self):
        fixture = ProvenanceControls()
        fixture.setUp()
        self.cleanup = {'status': 'verified-empty', 'processGroupId': 101,
            'initialPids': [], 'termSent': False, 'killSent': False,
            'verifiedEmpty': True, 'remainingPids': []}
        self.outcome = {**fixture.outcome, 'cleanup': {**self.cleanup, 'inspectionError': None}}
        capture = {'path': '/owned/before.json', 'sha256': 'before-sha'}
        source = {'path': '/owned/collector.py', 'sha256': idle.ORIGINAL_CAPTURE_SOURCE}
        fixture.before['captureMechanism'] = source
        fixture.after['captureMechanism'] = source
        process_path = '/owned/launch/process.json'
        fixture.selected.update(beforeCapture=capture, captureMechanism=source,
                                launchProcess={'path': process_path, 'sha256': 'process-sha'})
        fixture.plan.update(beforeCapture=capture, captureMechanism=source, cwd=str(ROOT))
        binding = {'captureMechanism': source, 'plannedArgv': fixture.argv,
            'before': {'record': fixture.before, 'capture': capture}, 'after': {'record': fixture.after},
            'selectedRun': {'path': '/owned/selected.json', 'sha256': 'selected-sha'},
            'launchPlan': {'path': '/owned/plan.json', 'sha256': 'plan-sha'},
            'launchOutcome': {'path': '/owned/outcome.json', 'sha256': 'outcome-sha'}}
        records = {'/owned/selected.json': fixture.selected, '/owned/plan.json': fixture.plan,
            process_path: fixture.process, '/owned/outcome.json': self.outcome,
            '/owned/launch/cleanup.json': self.cleanup}
        helpers = SimpleNamespace(module=lambda name, path: self.runner)
        self.assessment = SimpleNamespace(directory=fixture.run,
            selected=lambda name: binding, selected_ref=lambda name: source,
            read=lambda path, digest=None: records[str(path)], sha=lambda path: 'durable-sha',
            ref=lambda path: None, evidence=SimpleNamespace(observer=SimpleNamespace(helpers=helpers)))

    def assess(self):
        return idle.IdleAssessment.capture_proof(self.assessment)

    def test_actual_shaped_null_inspection_error_is_omitted_by_frozen_writer(self):
        self.assertEqual(self.cleanup, self.runner.without_none(self.outcome['cleanup']))
        self.assertEqual(self.assess()['driverPid'], 101)

    def test_retained_actual_outer_cleanup_uses_the_frozen_writer_shape(self):
        path = ROOT / 'target/agent-debug/current-eight-reader-idle-pairs-proof-01/binding.json'
        if not path.exists():
            self.skipTest('retained native capture is not installed')
        binding = json.loads(path.read_bytes())
        outcome_bytes = Path(binding['launchOutcome']['path']).read_bytes()
        self.assertEqual(hashlib.sha256(outcome_bytes).hexdigest(), binding['launchOutcome']['sha256'])
        selected_bytes = Path(binding['selectedRun']['path']).read_bytes()
        self.assertEqual(hashlib.sha256(selected_bytes).hexdigest(), binding['selectedRun']['sha256'])
        selected = json.loads(selected_bytes)
        outcome = json.loads(outcome_bytes)
        cleanup = json.loads((Path(selected['launchProcess']['path']).parent / 'cleanup.json').read_bytes())
        self.assertIsNone(outcome['cleanup']['inspectionError'])
        self.assertNotIn('inspectionError', cleanup)
        self.assertEqual(cleanup, self.runner.without_none(outcome['cleanup']))

    def test_non_null_inspection_error_cannot_be_normalized_away(self):
        self.outcome['cleanup']['inspectionError'] = 'cannot inspect process group'
        with self.assertRaisesRegex(ValueError, 'actual outer cleanup file differs'):
            self.assess()

    def test_non_null_fields_and_group_corruption_remain_exact(self):
        for field, value in (('processGroupId', 102), ('status', 'unverified'),
                             ('remainingPids', [102]), ('termSent', True), ('inspectionError', 'read failed')):
            previous = copy.deepcopy(self.cleanup)
            self.cleanup[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'actual outer cleanup file differs'):
                self.assess()
            self.cleanup.clear()
            self.cleanup.update(previous)


class PublicSelectionControls(unittest.TestCase):
    def test_rejects_an_assessment_or_other_basename_before_reading_native_artifacts(self):
        evidence = SimpleNamespace(bound=lambda name: (Path('/owned/assessment.json'), {}))
        with patch.object(idle, 'IdleAssessment', side_effect=AssertionError('wrong report reached evaluator')):
            with self.assertRaisesRegex(ValueError, 'original driver report.json'):
                idle.IdleEvidence.assess(evidence)

    def test_original_basename_reaches_raw_evaluation_and_preserves_failed_samples(self):
        report = Path('/owned/fresh-run/report.json')
        selection = {'path': str(report), 'sha256': 'bound-sha'}
        evidence = SimpleNamespace(bound=lambda name: (report, {}),
            manifest=SimpleNamespace(read_text=lambda: json.dumps({'schema': 1, 'idle': selection})))
        observations = {'errors': [{'label': 'actual-member', 'error': 'failed RPC'}],
            'allPhysicalAttempts': [{'allSettledSamples': [{'aggregateRssBytes': 123}],
                                     'allTypedResults': [{'status': 'timeout'}]}]}
        assessment = SimpleNamespace(run=lambda: observations)
        with patch.object(idle, 'IdleAssessment', return_value=assessment) as constructor:
            with self.assertRaises(idle.IdleEvidenceError) as rejected:
                idle.IdleEvidence.assess(evidence)
            constructor.assert_called_once_with(report.parent, selection, evidence)
        self.assertIs(rejected.exception.observations, observations)
        self.assertEqual(rejected.exception.observations['allPhysicalAttempts'][0]['allSettledSamples'],
                         [{'aggregateRssBytes': 123}])


if __name__ == '__main__':
    unittest.main()
