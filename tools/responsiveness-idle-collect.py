#!/usr/bin/env python3
"""Collect original IdlePairs once, with exclusive real surrounding captures.

Invocation performs a native observation; importing this module does not. The
900-second managed parent limit changes no original query deadline or workload.
No acceptance is emitted, failed/partial outcomes are preserved, and no retry is
performed. Invoke only when other native timing has ended.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
OWNED = ROOT / 'target/agent-debug'
APP = Path('/home/shawn/workspace2/devlist.app')
PROTECTED = Path('/home/shawn/workspace2/suprnova')
BASELINE = '1889da8c00d21260bc785fb1658d4a8a6ad427449c16bd9f8391af81547ef0b2'
COMMON = OWNED / 'build/x86_64-unknown-linux-gnu/release/suprnova-lsp'
DEADLINE_MS = 900_000
READ_BOUND = 512 * 1024 * 1024
PINS = {
    'lsp-query.py': '67f3701804c25b8d8f556cd37e17d9beb3ee26364482e0c9d34f1f9bd93a1695',
    'sudus-responsiveness.py': 'a759bcb20edfb3789e1086a1d6ca75a6eed0c3a6f2f9260050b353c8be2d45f4',
    'agent-debug.py': 'ee7cc045c60c43dfd0601e6b945d95106c959c94ab84538b72805245b92e1ade',
    'sudus-editor-import.py': 'b4da2f4b49c09f9625ea7c2d2185430803fdd0e0a1e61b58a8ff58178303e11d',
    'sudus-suprnova-user.py': 'dfccc143ae04f789cbe033a8a6223b787d786f07aa791a3a21e602c0b30f4107',
    'responsiveness-cargo-cache.py': 'c2561856c4a2d50227df870d2ec177d88de5e93fcd64adcdfb4c9cd85b3de60a',
}
class CollectionInputs:
    """Resolve the selected compilation and baseline before allocating a launch."""

    def __init__(self, selection):
        names = {'baselineManifest': 'baselineManifest', 'candidateArchiveIdentity': 'candidateArchiveIdentity',
                 'candidateCompiledIdentity': 'candidateCompiledIdentity', 'candidateCompiledReport': 'candidateCompiledReport',
                 'baselineOriginalCompiledReport': 'baselineBuild'}
        self.references = {}
        for name, selected_name in names.items():
            entry = selection[selected_name]
            path = BoundaryCapture.allowed(ROOT / entry['path'], OWNED)
            CaptureProof.require(re.fullmatch('[0-9a-f]{64}', entry['sha256']) is not None,
                                 'selected compilation reference lacks digest: ' + name)
            CaptureProof.require(path.stat().st_size <= READ_BOUND, 'selected reference exceeds bound: ' + name)
            self.references[name] = (path, entry['sha256'])
            BoundaryCapture.reference(path, entry['sha256'])
        self.manifest = self.references['baselineManifest'][0]
        compiled = json.loads(self.references['candidateCompiledIdentity'][0].read_bytes())
        archive = json.loads(self.references['candidateArchiveIdentity'][0].read_bytes())
        self.source, self.binary = compiled['runtimeSourcesSha256'], compiled['binarySha256']
        for key, name in (('compiledBuildIdentity', 'candidateCompiledIdentity'), ('compiledBuildReport', 'candidateCompiledReport')):
            path, digest = self.references[name]
            CaptureProof.require(archive[key] == {'path': str(path), 'sha256': digest},
                                 'archive does not reference selected compilation: ' + name)
        CaptureProof.require(compiled.get('sourcesUnchanged') is True
            and archive['runtimeSourcesSha256'] == self.source and archive['binarySha256'] == self.binary,
            'selected compiled source/archive identity differs')
        self.archive = BoundaryCapture.allowed(archive['binary'], OWNED)
        self.head = BoundaryCapture.head()
        self.argv = ['python3', '-B', 'tools/sudus-responsiveness.py', '--idle-pairs', '--no-build',
            '--workload', 'generated-captured', '--baseline-manifest', str(self.manifest.relative_to(ROOT)),
            '--nofile-soft', '4096']



class CaptureProof:
    """Pure checks for launch/order/boundary records; controls need no native job."""

    @staticmethod
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    @classmethod
    def boundary(cls, record, inputs):
        cls.require(record['schema'] == 'rsp006-real-boundary/v1', 'boundary schema differs')
        cls.require(record['frozenPins'] == PINS, 'six frozen observer hashes differ')
        cls.require(record['head'] == inputs.head and record['runtimeSourcesSha256'] == inputs.source,
                    'actual checkout/source differs from selected compilation')
        cls.require(record['managedBinarySha256'] == record['archiveBinarySha256'] == inputs.binary,
                    'actual managed/archive native bytes differ')
        cls.require(record['baselineBinarySha256'] == BASELINE, 'preserved baseline native bytes differ')
        cls.require(record['managedBinary'] == str(COMMON) and record['archiveBinary'] == str(inputs.archive),
                    'actual candidate native paths differ')
        cls.require(record['baselineOrigin']['compiledCommit'] == '893130fd9d3a4e292a47ddfbb68e171e5bcc6f38'
            and record['baselineOrigin']['manifestObservationCommit'] == '8e17c49d57b1a7913727ba68148be7f23ba8ad91'
            and record['baselineOrigin']['contemporaneousRuntimeFingerprint'] is None
            and record['baselineOrigin']['contemporaneousBuildEndSourceGuard'] is None,
            'historical baseline origin or missing contemporaneous guard was rewritten')
        cls.require(record['externalBoundaries']['protectedCheckoutInspected'] is False,
                    'capture must not inspect the protected framework checkout')

    @classmethod
    def matched(cls, before, after, inputs):
        cls.boundary(before, inputs)
        cls.boundary(after, inputs)
        cls.require(before['completedNs'] < after['startedNs'], 'boundary observations overlap or reorder')
        cls.require(before['observedAt'] < after['observedAt'], 'boundary wall timestamps do not advance')
        for key in ('head', 'frozenPins', 'runtimeSourcesSha256', 'managedBinarySha256',
                    'archiveBinarySha256', 'baselineBinarySha256', 'references', 'applicationInputs',
                    'ordinaryApplicationOutputSentinels', 'captureMechanism'):
            cls.require(before[key] == after[key], 'before/after observation changed: ' + key)

    @classmethod
    def launch(cls, before_completed_ns, committed_ns, invoked_ns, command, args, argv):
        cls.require(type(before_completed_ns) is int and type(committed_ns) is int and type(invoked_ns) is int
            and before_completed_ns <= committed_ns < invoked_ns,
            'before capture was not committed before actual launch invocation')
        cls.require([command, *args] == argv, 'actual original idle CLI was changed')

    @classmethod
    def allocation(cls, path, driver_pid, preexisting, already_selected=None):
        path = Path(path)
        cls.require(type(driver_pid) is int and driver_pid > 0, 'managed driver PID is absent')
        cls.require(path.is_relative_to(OWNED / 'runs') and f'-rsp-idle-pairs-{driver_pid}-' in path.name,
                    'allocated idle run does not belong to actual managed driver PID')
        cls.require(str(path) not in preexisting, 'selected idle run predates this driver launch')
        cls.require(already_selected is None or path == Path(already_selected), 'driver has multiple selected idle runs')


class BoundaryCapture:
    @staticmethod
    def sha(path):
        value = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                value.update(block)
        return value.hexdigest()

    @staticmethod
    def allowed(path, owner):
        resolved = Path(path).resolve(strict=True)
        CaptureProof.require(resolved.is_relative_to(Path(owner).resolve(strict=True)), 'path escapes its owner: ' + str(path))
        CaptureProof.require(resolved != PROTECTED and PROTECTED not in resolved.parents, 'path reaches protected checkout')
        return resolved

    @classmethod
    def reference(cls, path, expected=None):
        path = cls.allowed(path, ROOT)
        digest = cls.sha(path)
        CaptureProof.require(expected is None or expected == digest, 'original compiled/baseline reference changed: ' + str(path))
        return {'path': str(path), 'sha256': digest, 'bytes': path.stat().st_size}

    @staticmethod
    def exclusive_json(path, value):
        data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
        # O_EXCL forbids both replacement and following an existing symlink.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {'path': str(Path(path).resolve()), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}

    @classmethod
    def inventory(cls, root):
        root = cls.allowed(root, ROOT if Path(root).is_relative_to(ROOT) else APP)
        files = {}
        for directory, children, names in os.walk(root, followlinks=False):
            children[:] = sorted(name for name in children if name not in {'.git', 'target', 'node_modules'})
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix == '.rs' or name in {'Cargo.toml', 'Cargo.lock'}:
                    cls.allowed(path, root)
                    files[str(path.relative_to(root))] = cls.sha(path)
        for name in ('.cargo/config.toml', '.cargo/config'):
            path = root / name
            if path.exists():
                cls.allowed(path, root)
                files[name] = cls.sha(path)
        return files

    @classmethod
    def runtime_sources(cls):
        files = cls.inventory(ROOT / 'crates')
        for name in ('Cargo.lock', 'Cargo.toml', 'rust-toolchain.toml'):
            files[name] = cls.sha(cls.allowed(ROOT / name, ROOT))
        return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()

    @classmethod
    def head(cls):
        # Read Git's own ref storage without running an unmanaged command or
        # writing any Git artifact. The native driver independently records HEAD.
        git = ROOT / '.git'
        if git.is_file():
            text = git.read_text().strip()
            CaptureProof.require(text.startswith('gitdir: '), 'unknown worktree Git pointer')
            git = (ROOT / text[len('gitdir: '):]).resolve(strict=True)
        git = cls.allowed(git, ROOT)
        common = git
        if (git / 'commondir').exists():
            common = cls.allowed(git / (git / 'commondir').read_text().strip(), ROOT)
        text = (git / 'HEAD').read_text().strip()
        if text.startswith('ref: '):
            name = text[len('ref: '):]
            CaptureProof.require(name.startswith('refs/') and '..' not in Path(name).parts, 'unsafe Git HEAD ref')
            loose = common / name
            if loose.exists():
                text = cls.allowed(loose, ROOT).read_text().strip()
            else:
                packed = (common / 'packed-refs').read_text().splitlines()
                matches = [line.split()[0] for line in packed if not line.startswith(('#', '^'))
                           and len(line.split()) == 2 and line.split()[1] == name]
                CaptureProof.require(len(matches) == 1, 'current Git HEAD ref absent or ambiguous')
                text = matches[0]
        CaptureProof.require(re.fullmatch('[0-9a-f]{40}', text) is not None, 'actual Git HEAD is not a commit identity')
        return text

    @classmethod
    def output_sentinels(cls):
        target = APP / 'target'
        rows = {}
        if target.exists() or target.is_symlink():
            cls.allowed(target, APP)
            for directory, children, names in os.walk(target, followlinks=False):
                for name in sorted(children + names):
                    path = Path(directory) / name
                    entry = path.lstat()
                    rows[str(path.relative_to(target))] = {'mode': entry.st_mode, 'size': entry.st_size,
                        'mtimeNs': entry.st_mtime_ns, 'ctimeNs': entry.st_ctime_ns,
                        'link': os.readlink(path) if stat.S_ISLNK(entry.st_mode) else None}
                    CaptureProof.require(len(rows) <= 1_000_000, 'ordinary output sentinel traversal exceeds explicit bound')
            target_stat = target.stat()
            target_record = {'exists': True, 'mtimeNs': target_stat.st_mtime_ns,
                'ctimeNs': target_stat.st_ctime_ns, 'entries': len(rows),
                'metadataSha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}
        else:
            target_record = {'exists': False}
        lock = cls.allowed(APP / 'Cargo.lock', APP)
        lock_stat = lock.stat()
        return {'ordinaryCargoTarget': target_record,
            'cargoLock': {'sha256': cls.sha(lock), 'bytes': lock_stat.st_size,
                'mtimeNs': lock_stat.st_mtime_ns, 'ctimeNs': lock_stat.st_ctime_ns},
            'metric': 'Cargo target metadata sentinel plus source/Cargo content inventory; no output content equivalence claim'}

    @classmethod
    def record(cls, boundary, inputs):
        started = time.monotonic_ns()
        references = {name: cls.reference(path, expected) for name, (path, expected) in inputs.references.items()}
        manifest = json.loads(inputs.manifest.read_bytes())
        CaptureProof.require(manifest['identity']['binarySha256'] == BASELINE, 'baseline manifest binary identity differs')
        baseline_path = cls.allowed(manifest['retainedBinary'], OWNED)
        result = {'schema': 'rsp006-real-boundary/v1', 'boundary': boundary, 'startedNs': started,
            'frozenPins': {name: cls.sha(ROOT / 'tools' / name) for name in PINS},
            'runtimeSourcesSha256': cls.runtime_sources(), 'head': cls.head(),
            'managedBinary': str(cls.allowed(COMMON, OWNED)), 'managedBinarySha256': cls.sha(COMMON),
            'archiveBinary': str(cls.allowed(inputs.archive, OWNED)), 'archiveBinarySha256': cls.sha(inputs.archive),
            'baselineBinary': str(baseline_path), 'baselineBinarySha256': cls.sha(baseline_path),
            'references': references, 'applicationInputs': cls.inventory(APP),
            'ordinaryApplicationOutputSentinels': cls.output_sentinels(),
            'captureMechanism': cls.reference(Path(__file__)),
            'baselineOrigin': {'compiledCommit': '893130fd9d3a4e292a47ddfbb68e171e5bcc6f38',
                'manifestObservationCommit': manifest['identity']['commit'], 'contemporaneousRuntimeFingerprint': None,
                'contemporaneousBuildEndSourceGuard': None,
                'limitation': 'Actual original compilation893; later manifest8e differs by a reviewed comment. No modern historical source guard is fabricated.'},
            'externalBoundaries': {'application': str(APP), 'protectedCheckout': str(PROTECTED),
                'protectedCheckoutInspected': False,
                'targetRoot': str(OWNED), 'framework': 'Devlist pinned dependency; original driver verifies metadata'},
            'cacheConditions': 'Existing LSP/compiler caches; no wrapper priming/copying. Original driver creates a fresh export run root.',
            'parentAggregateDeadlineMs': DEADLINE_MS,
            'queryDeadlines': 'Unchanged original observers and native RPC settings; parent deadline is independent.'}
        result['completedNs'] = time.monotonic_ns()
        result['observedAt'] = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        return result


class IdleCollector:
    def __init__(self, inputs, directory):
        self.inputs = inputs
        self.directory = directory
        self.errors, self.commands, self.references = [], [], {}
        self.selected = None
        self.before = self.after = self.outcome = None
        self.before_ref = self.after_ref = self.outcome_ref = None
        self.invoked_ns = self.committed_ns = None
        self.runner = None
        self.boundaries_matched = False

    def error(self, label, error):
        self.errors.append({'label': label, 'error': type(error).__name__ + ': ' + str(error)})

    def write(self, name, value):
        reference = BoundaryCapture.exclusive_json(self.directory / name, value)
        self.references[name] = reference
        return reference

    def load_runner(self):
        path = ROOT / 'tools/agent-debug.py'
        CaptureProof.require(BoundaryCapture.sha(path) == PINS['agent-debug.py'], 'managed helper changed after before capture')
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location('rsp006_exclusive_idle_parent_runner', path)
        self.runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.runner
        spec.loader.exec_module(self.runner)
        self.runner.install_signal_handlers()
        original = self.runner.run_supervised

        async def record_supervised_outcome(*args, **kwargs):
            # observe_command rejects timeout/signal/cleanup after this return.
            # Keep the exact original outcome first, including all failure fields.
            return await self.preserve_supervised_outcome(original, *args, **kwargs)

        # Only this parent's imported module is wrapped. The native driver's
        # separate interpreter imports the six unmodified frozen files itself.
        self.runner.run_supervised = record_supervised_outcome

    async def preserve_supervised_outcome(self, original, *args, **kwargs):
        result = await original(*args, **kwargs)
        self.outcome = result
        self.commands.append(result)
        self.outcome_ref = self.write('launch-outcome.json', result)
        return result

    def select_allocated_run(self, preexisting):
        process_path = self.directory / 'launch/process.json'
        if not process_path.exists():
            return
        process = json.loads(process_path.read_bytes())
        CaptureProof.require([process['command'], *process['args']] == self.inputs.argv, 'actual managed launch argv differs')
        candidates = sorted((OWNED / 'runs').glob(f'*-rsp-idle-pairs-{process["pid"]}-*'))
        CaptureProof.require(len(candidates) <= 1, 'actual managed driver allocated ambiguous idle runs')
        if not candidates:
            return
        path = BoundaryCapture.allowed(candidates[0], OWNED)
        CaptureProof.allocation(path, process['pid'], preexisting, self.selected)
        if self.selected is None:
            self.selected = path
            self.write('selected-run.json', {'schema': 'rsp006-selected-original-idle-run/v1',
                'bindingStage': 'recorded after observing actual driver-owned run allocation',
                'run': str(path), 'driverPid': process['pid'], 'selectedAtNs': time.monotonic_ns(),
                'observedAt': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                'launchProcess': BoundaryCapture.reference(process_path), 'plannedArgv': self.inputs.argv,
                'beforeCapture': self.before_ref, 'captureMechanism': self.before['captureMechanism'],
                'acceptance': False})

    async def launch(self):
        self.load_runner()
        preexisting = {str(path.resolve()) for path in (OWNED / 'runs').glob('*-rsp-idle-pairs-*')}
        self.invoked_ns = time.monotonic_ns()
        CaptureProof.launch(self.before['completedNs'], self.committed_ns, self.invoked_ns, self.inputs.argv[0], self.inputs.argv[1:], self.inputs.argv)
        self.write('launch-plan.json', {'schema': 'rsp006-original-idle-launch/v1', 'plannedArgv': self.inputs.argv,
            'cwd': str(ROOT), 'aggregateDeadlineMs': DEADLINE_MS, 'beforeCapture': self.before_ref,
            'beforeCaptureCommittedNs': self.committed_ns, 'invokedNs': self.invoked_ns,
            'captureMechanism': self.before['captureMechanism'], 'acceptance': False})
        work = asyncio.create_task(self.runner.observe_command(
            self.runner.CommandSpec(self.inputs.argv[0], self.inputs.argv[1:]), ROOT, dict(os.environ),
            self.directory / 'launch', DEADLINE_MS))
        # Source-bound allocation includes the actual driver PID. The frozen
        # stdout file is buffered, so early allocation does not depend on its flush.
        while not work.done():
            try:
                self.select_allocated_run(preexisting)
            except (ValueError, OSError, json.JSONDecodeError) as error:
                self.error('allocation-observation', error)
                break
            await asyncio.sleep(0.05)
        try:
            _, text = await work
            paths = re.findall(r'^Observation report: (.+/report\.json)$', text, re.MULTILINE)
            CaptureProof.require(len(paths) == 1, 'original driver terminal report path absent or ambiguous')
            self.select_allocated_run(preexisting)
            CaptureProof.require(self.selected is not None and Path(paths[0]).parent.resolve() == self.selected,
                                'terminal original report differs from actual allocated run')
            self.references['originalReport'] = BoundaryCapture.reference(paths[0])
        except Exception as error:
            self.error('managed-original-idle', error)
            # The managed outcome is already saved when observe_command rejects.
            # Even failed drivers may allocate a run; preserve that selection too.
            try:
                self.select_allocated_run(preexisting)
            except Exception as selection_error:
                self.error('partial-allocation-observation', selection_error)

    async def run(self):
        CaptureProof.require(not self.directory.exists() and not self.directory.is_symlink(), 'exclusive proof directory already exists')
        BoundaryCapture.allowed(OWNED, ROOT)
        self.directory.mkdir(mode=0o700)
        try:
            self.before = BoundaryCapture.record('before', self.inputs)
            self.before_ref = self.write('before.json', self.before)
            self.committed_ns = time.monotonic_ns()
            CaptureProof.boundary(self.before, self.inputs)
            await self.launch()
        except Exception as error:
            self.error('capture-or-launch', error)
        finally:
            try:
                self.after = BoundaryCapture.record('after', self.inputs)
                self.after_ref = self.write('after.json', self.after)
                CaptureProof.boundary(self.after, self.inputs)
                if self.before is not None:
                    CaptureProof.matched(self.before, self.after, self.inputs)
                    self.boundaries_matched = True
            except Exception as error:
                self.error('after-capture', error)
            for name in ('launch/process.json', 'launch/cleanup.json', 'launch/stdout.log', 'launch/stderr.log'):
                path = self.directory / name
                if path.exists():
                    try:
                        self.references[name] = BoundaryCapture.reference(path)
                    except Exception as error:
                        self.error(name, error)
            binding = None
            if all(value is not None for value in (self.before, self.after, self.before_ref, self.after_ref, self.outcome_ref, self.selected)):
                binding = {'schema': 'rsp006-fresh-original-idle-binding/v1', 'run': str(self.selected),
                    'plannedArgv': self.inputs.argv, 'launchOutcome': self.outcome_ref,
                    'captureMechanism': self.before['captureMechanism'],
                    'selectedRun': self.references.get('selected-run.json'),
                    'launchPlan': self.references.get('launch-plan.json'),
                    'processOwnership': {}, 'acceptance': False,
                    'limitations': ['No independent PID ancestry capture is invented.',
                        'Original baseline has no contemporaneous modern runtime/build-end guard.',
                        'Independent source-bound evidence evaluation is still required.']}
                for name, record, ref in (('before', self.before, self.before_ref), ('after', self.after, self.after_ref)):
                    binding[name] = {'observedAt': record['observedAt'], 'frozenPins': record['frozenPins'],
                        'runtimeSourcesSha256': record['runtimeSourcesSha256'],
                        'managedBinarySha256': record['managedBinarySha256'],
                        'archiveBinarySha256': record['archiveBinarySha256'], 'capture': ref, 'record': record}
                self.write('binding.json', binding)
            cleanup = self.runner.summarize_cleanup(self.commands) if self.runner is not None else {'status': 'unverified', 'runs': 0, 'verifiedRuns': 0}
            report = {'schema': 'rsp006-managed-original-idle-parent/v1', 'plannedArgv': self.inputs.argv,
                'commands': self.commands, 'processCleanup': cleanup, 'errors': self.errors,
                'run': str(self.selected) if self.selected else None, 'references': self.references,
                'observationComplete': not self.errors and self.outcome is not None
                    and self.outcome.get('code') == 0 and self.outcome.get('cleanup', {}).get('verifiedEmpty') is True,
                'captureMechanism': self.before.get('captureMechanism') if self.before else None,
                'beforeAndAfterMatched': self.boundaries_matched,
                'parentAggregateDeadlineMs': DEADLINE_MS, 'acceptance': 'unverified', 'requirements': {'RSP-006': 'unverified'},
                'nativeQueryDeadlinesChanged': False, 'retryPerformed': False,
                'note': 'Parent cleanup does not replace original driver/inner group cleanup; the separate auditor checks all physical attempts.'}
            report_ref = self.write('report.json', report)
            print(json.dumps({'parentReport': report_ref, 'run': report['run'],
                'binding': self.references.get('binding.json'), 'errors': self.errors,
                'actualOriginalDriverCode': self.outcome.get('code') if self.outcome else None,
                'actualOriginalDriverSignal': self.outcome.get('signal') if self.outcome else None,
                'RSP-006': 'unverified'}, indent=2))
        if self.outcome is None:
            return 1
        code = self.runner.exit_code_for(self.outcome)
        return code if code != 0 else 1 if self.errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path, help='SHA-bound selector with idle compilation/baseline references')
    parser.add_argument('--output', required=True, type=Path, help='new exclusive directory under target/agent-debug')
    args = parser.parse_args()
    selector = BoundaryCapture.allowed(args.evidence, ROOT)
    CaptureProof.require(selector.stat().st_size <= 1024 * 1024, 'selector exceeds explicit bound')
    manifest = json.loads(selector.read_bytes())
    CaptureProof.require(manifest.get('schema') == 1, 'unsupported evidence selector schema')
    directory = args.output.resolve()
    CaptureProof.require(directory.parent.resolve(strict=True).is_relative_to(OWNED.resolve(strict=True)),
                         'collection output escapes owned root')
    inputs = CollectionInputs(manifest['idle'])
    return asyncio.run(IdleCollector(inputs, directory).run())


if __name__ == '__main__':
    sys.exit(main())
