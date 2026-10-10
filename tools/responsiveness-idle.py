#!/usr/bin/env python3
"""Recheck original matched idle observations, lifecycle and ownership provenance.

Only selected raw artifacts and inspected source bindings establish the result.
No native process is launched or observation rewritten by this evaluator.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
OWNED = ROOT / 'target/agent-debug'
APP = Path('/home/shawn/workspace2/devlist.app')
PROTECTED = Path('/home/shawn/workspace2/suprnova')
BOUND = 512 * 1024 * 1024
MODES = ('faster-builds', 'lower-peak-memory')
LABELS = ('rsp_query', 'rsp_without', 'rsp_filter')
BASELINE = '1889da8c00d21260bc785fb1658d4a8a6ad427449c16bd9f8391af81547ef0b2'
MANIFEST_SHA = '9dc1ef0d5ca6b4a980baf6e03ca2fb6358b8ee17461248b786d71319d00b90a1'
EXPORT_SHA = '1ca416428e5c53e800fca47a6da1981a3f9fca80325bcaa2c87f3f49f44f98ba'
FRAMEWORK = '3229aa9af542c991196274fa3c235cdce88a68e2'
LOG = 'rg_lsp_engine=trace,rg_lsp_server=debug,rg_lsp_server::client_status=trace'
NATIVE_STAGES = {'editor document analysis route published', 'configured rustdoc declarations published',
    'analysis query started', 'analysis query completed', 'document analysis prepared',
    'document analysis phase', 'memory report', 'deferred indexing lifecycle started',
    'deferred indexing lifecycle finished', 'deferred indexing progress'}
PINS = {
    'lsp-query.py': '67f3701804c25b8d8f556cd37e17d9beb3ee26364482e0c9d34f1f9bd93a1695',
    'sudus-responsiveness.py': '218eccce43d0c8d0cc79493ee8f6b48b84ddd3a6ad602f79254a3da4988495a4',
    'agent-debug.py': '023985ba916c0e8b45375517eb41306192889c7b4900ae604c7b649b72bd6314',
    'sudus-editor-import.py': 'd3f72ec0afa19e7390486d38277bab1fdefc1ffa3cf1056858757d63f01b827c',
    'sudus-suprnova-user.py': 'dfccc143ae04f789cbe033a8a6223b787d786f07aa791a3a21e602c0b30f4107',
    'responsiveness-cargo-cache.py': 'c2561856c4a2d50227df870d2ec177d88de5e93fcd64adcdfb4c9cd85b3de60a',
}
ORIGINAL_REPORT_SHA = '58329d5de08d1d85841a95d04c0cf77a28687fcbf3a900d6848c7c966fbbfab1'
ORIGINAL_COMMIT = '893130fd9d3a4e292a47ddfbb68e171e5bcc6f38'
OBSERVATION_COMMIT = '8e17c49d57b1a7913727ba68148be7f23ba8ad91'
ERRORS = (ValueError, KeyError, TypeError, IndexError, OSError, AttributeError, UnicodeError, StopIteration)
RETAINED_REVIEW_SHA = '006b7cbacebbc87abfba622659c64f0496b431c9fa9c2f1f23915d531876dfd8'
REVIEWED_SOURCE = 'a20ba308b39e3cd5029a36593f7a858c13dc9ee8bee3fc780fb8acf088786a01'
ORIGINAL_CAPTURE_SOURCE = '383fb2a6ea0d56db578f146b33b345c12b920d65b9896ce9ebfae2714936eac0'


class IdleProof:
    """Pure invariants shared with authored controls; no native or filesystem work."""

    @staticmethod
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    @staticmethod
    def success(command):
        return (type(command.get('code')) is int and command['code'] == 0
            and command.get('timedOut') is False and command.get('signal') is None
            and not command.get('spawnError') and not command.get('interruptedBy'))

    @staticmethod
    def empty(command):
        cleanup = command.get('cleanup', {})
        return cleanup.get('verifiedEmpty') is True and cleanup.get('remainingPids') == []

    @classmethod
    def commands(cls, report, require_complete=True):
        commands = report['commands']
        cls.require(isinstance(commands, list), 'physical command ledger is not an array')
        cls.require(all(cls.success(c) and cls.empty(c) for c in commands),
                    'unsuccessful command or unverified owned cleanup')
        top = report['processCleanup']
        cls.require(type(top.get('runs')) is int and type(top.get('verifiedRuns')) is int
            and top['runs'] == len(commands) and top['verifiedRuns'] == len(commands)
            and top.get('status') == 'verified', 'cleanup totals differ from actual physical commands')
        if require_complete:
            cls.require(report.get('observationComplete') is True, 'observation is incomplete')
        return {'actualCommands': len(commands), 'successfulCommands': len(commands),
                'verifiedEmptyGroups': len(commands)}

    @staticmethod
    def attempt_labels():
        return [f'idle-{mode}-{number}-{kind}' for mode in MODES for number in range(3)
                for kind in (('candidate', 'baseline') if number == 1 else ('baseline', 'candidate'))]

    @classmethod
    def pair_layout(cls, reports, physical_labels):
        cls.require(set(reports) == set(MODES), 'missing or invented indexing mode')
        cls.require(physical_labels == cls.attempt_labels(), 'physical pair order/count differs from AB/BA/AB')
        for mode in MODES:
            pairs = reports[mode]['pairs']
            cls.require(isinstance(pairs, list) and len(pairs) == 3, 'requires exactly three pairs per mode')
            for pair in pairs:
                cls.require(set(pair) == {'baseline', 'candidate'}, 'pair is missing or invents a member')

    @classmethod
    def typed_wire(cls, raw, wire, plan):
        cls.require(wire == {k: raw[k] for k in ('transport', 'lifecycle', 'stages')},
                    'independent wire ledger differs from stored raw')
        cls.require(raw.get('file') == 'src/models/user.rs', 'different application document')
        hovers = [r for r in raw['transport'] if r.get('method') == 'textDocument/hover']
        initialized = [r for r in raw['transport'] if r.get('method') == 'initialize']
        cls.require(len(initialized) == 1 and initialized[0].get('status') == 'success',
                    'fresh successful initialization missing')
        cls.require(len(hovers) == 3 and len(raw.get('results', [])) == 3,
                    'all three typed replies and physical requests are required')
        cls.require(len({r.get('id') for r in raw['transport']}) == len(raw['transport'])
                    and all(type(r.get('id')) is int for r in raw['transport']), 'RPC identities duplicate or malformed')
        cls.require(all(r.get('status') == 'success' for r in raw['transport']), 'non-success RPC retained')
        previous = initialized[0]['receivedNs']
        for label, result, transport in zip(LABELS, raw['results'], hovers):
            cls.require(result.get('label') == label and result.get('kind') == 'hover'
                and result.get('transport') == transport, 'result label/kind/transport mapping differs')
            cls.require(isinstance(result.get('text'), str) and 'Builder<User>' in result['text'],
                        'generated signature is empty or incorrect')
            contents = (result.get('raw') or {}).get('contents')
            if isinstance(contents, str):
                actual_text = contents
            elif isinstance(contents, dict) and isinstance(contents.get('value'), str):
                actual_text = contents['value']
            elif isinstance(contents, list):
                actual_text = '\n'.join(item if isinstance(item, str) else item['value']
                    for item in contents if isinstance(item, str) or isinstance(item, dict) and item.get('value'))
            else:
                actual_text = None
            cls.require(actual_text == result['text'], 'typed text differs from genuine raw hover contents')
            written, received = transport.get('writtenNs'), transport.get('receivedNs')
            cls.require(type(written) is int and type(received) is int and written > previous
                and received >= written and type(transport.get('durationNs')) is int
                and transport['durationNs'] == received - written, 'hover timestamps/order/duration differ')
            previous = received
            ready = [e for e in raw['lifecycle'] if e.get('method') == 'suprnova-lsp/activeWorkspaceChanged'
                and e.get('params', {}).get('root') == str(APP) and e.get('receivedNs', written + 1) <= written]
            cls.require(ready and ready[-1]['params'].get('state') == 'ready', 'latest workspace route not ready before hover')
            routes = [e for e in raw['stages'] if e.get('message') == 'editor document analysis route published'
                and e.get('fields', {}).get('path') == str(APP / 'src/models/user.rs')
                and e.get('observedNs', written + 1) <= written]
            cls.require(routes and routes[-1]['fields'].get('ready') is True, 'latest document route unavailable')
            offset = plan['text'].index(label)
            expected = {'line': plan['text'][:offset].count('\n') + 1,
                        'col': len(plan['text'][:offset].rsplit('\n', 1)[-1].encode('utf-16-le')) // 2 + 1}
            cls.require(result.get('position') == expected, 'reply belongs to a different current marker position')
        preparations = [e for e in raw['stages'] if e.get('message') == 'document analysis prepared'
                        and e.get('fields', {}).get('query') == 'hover']
        cls.require(len(preparations) == 3 and all(e['fields'].get('source') == 'current' for e in preparations),
                    'current-source route missing or changed')
        return {'initializeReceivedNs': initialized[0]['receivedNs'], 'rpcIds': [r['id'] for r in hovers]}

    @classmethod
    def native_binding(cls, raw, selected):
        expected = [{'message': e['message'], 'fields': e.get('fields') or {}} for e in raw['stages']]
        actual = [{'message': e['message'], 'fields': e['fields']} for e in selected if e['message'] in NATIVE_STAGES]
        cls.require(expected == actual, 'native serialized stages differ from independent observed stages')
        starts = [i for i, e in enumerate(selected) if e['message'] == 'analysis query started'
                  and e['fields'].get('label') == 'hover']
        completed = [i for i, e in enumerate(selected) if e['message'] == 'analysis query completed'
                     and e['fields'].get('query') == 'hover']
        releases = [i for i, e in enumerate(selected) if e['message'] == 'memory report'
                    and e['fields'].get('label') == 'hover']
        cls.require(len(starts) == len(completed) == len(releases) == 3, 'native start/completion/release counts differ')
        for n, (start, end, release) in enumerate(zip(starts, completed, releases)):
            cls.require(start < end < release and selected[end]['fields'].get('status') == 'ok',
                        'native failure or release/purge is not after completion')
            if n < 2:
                cls.require(end < starts[n + 1], 'native hover analysis groups overlap')
            # Stderr can be observed after the next client write. A client timestamp
            # is not a serialized-log ordering proof, so no release-before-write rule.
        return [{'rpcId': row['id'], 'startLine': selected[starts[n]]['serializedLine'],
                 'completionLine': selected[completed[n]]['serializedLine'],
                 'releasePurgeLine': selected[releases[n]]['serializedLine']}
                for n, row in enumerate([r for r in raw['transport'] if r.get('method') == 'textDocument/hover'])]

    @classmethod
    def pair_summary(cls, assessed):
        medians = {kind: [assessed[(n, kind)]['medianRssBytes'] for n in range(3)]
                   for kind in ('baseline', 'candidate')}
        deltas = [c - b for b, c in zip(medians['baseline'], medians['candidate'])]
        summaries = {kind: sorted(values)[1] for kind, values in medians.items()}
        return {'pairMediansBytes': medians, 'pairDeltasBytes': deltas,
                'medianBaselineBytes': summaries['baseline'], 'medianCandidateBytes': summaries['candidate'],
                'medianDeltaBytes': summaries['candidate'] - summaries['baseline'],
                'increaseInAllPairs': all(value > 0 for value in deltas),
                'repeatableIncreaseNeedsInvestigation': sum(value > 0 for value in deltas) >= 2}

    @classmethod
    def runtime_argv(cls, command, runtime, plan_path, binary, common):
        args = ['agent-debug', '--no-build', '--timeout', '5m', '--measure', '--log', LOG,
            'lsp-query', '--workspace-root', str(APP), '--query-file', str(plan_path), '--timeout-ms', '300000', '--json', '--binary', binary]
        cls.require(command['command'] == 'just' and command['args'] == args, 'outer actual member argv differs')
        forwarded = args[args.index('lsp-query') + 1:]
        cls.require(runtime['args'] == [str(ROOT / 'tools/lsp-query.py'), '--binary', common, *forwarded],
                    'inner default/explicit binary override differs')

    @classmethod
    def applicability(cls, current, source, binary):
        cls.require(current.get('currentRuntimeSourcesSha256') == source, 'current native sources differ from compiled measured candidate')
        cls.require(current.get('archiveBinarySha256') == current.get('managedBinarySha256') == binary,
                    'current managed/archive executable differs from measured candidate')

    @classmethod
    def capture_order(cls, before, after, plan, selected, process, outcome, run, argv):
        cls.require(before['completedNs'] <= plan['beforeCaptureCommittedNs'] < plan['invokedNs']
            < selected['selectedAtNs'] < after['startedNs'], 'before/launch/allocation/after order differs')
        cls.require(before['observedAt'] < process['startedAt'] <= selected['observedAt'] < after['observedAt'],
                    'actual launch wall timestamps differ')
        cls.require(plan['aggregateDeadlineMs'] == 900000 and plan['plannedArgv'] == selected['plannedArgv'] == argv
            and [process['command'], *process['args']] == [outcome['command'], *outcome['args']] == argv,
            'actual parent launch CLI/deadline differs')
        cls.require(process['pid'] == outcome['pid'] == selected['driverPid']
            and selected['run'] == str(run) and f'-rsp-idle-pairs-{process["pid"]}-' in run.name,
            'allocated run does not belong to actual driver')
        cls.require(cls.success(outcome) and cls.empty(outcome), 'original driver failed/cleanup incomplete')
        for key in ('head', 'frozenPins', 'runtimeSourcesSha256', 'managedBinarySha256',
                    'archiveBinarySha256', 'baselineBinarySha256', 'references', 'applicationInputs',
                    'ordinaryApplicationOutputSentinels', 'captureMechanism'):
            cls.require(before[key] == after[key], 'native boundary changed: ' + key)
        cls.require(before['externalBoundaries']['protectedCheckoutInspected'] is False
            and after['externalBoundaries']['protectedCheckoutInspected'] is False,
            'collector inspected protected framework checkout')

    @classmethod
    def increases(cls, summaries):
        increases = {}
        for mode, summary in summaries.items():
            medians = summary['pairMediansBytes']
            deltas = [candidate - baseline for baseline, candidate in zip(medians['baseline'], medians['candidate'])]
            # Two positive matched observations demonstrate recurrence; retain
            # individual increases too, rather than averaging them out silently.
            if sum(delta > 0 for delta in deltas) >= 2 or summary['medianDeltaBytes'] > 0:
                increases[mode] = deltas
        cls.require(not increases,
                    'repeatable idle increase requires investigation and presentation of its cause; no memory allowance or string decision bypass applies')
        return increases

    @classmethod
    def historical_diff(cls, diff):
        headers = [line for line in diff.splitlines() if line.startswith('diff --git ')]
        cls.require(headers == ['diff --git a/crates/lsp/engine/src/engine/query/mod.rs b/crates/lsp/engine/src/engine/query/mod.rs'],
                    'historical comment-only diff names another native file')
        changed = [line.strip() for line in diff.splitlines()
                   if line.startswith(('+', '-')) and not line.startswith(('+++', '---'))]
        cls.require(changed == [
            '-                // interval includes opening the read view and preparing current declarations.',
            '+                // interval includes opening the read view and reaching the first checkpoint.'],
            'historical compiled/observation commit native diff is not the reviewed comment-only change')

    @classmethod
    def retained_source(cls, review_sha, reviewed_source, measured_source, current_source):
        cls.require(review_sha == RETAINED_REVIEW_SHA and reviewed_source == measured_source == current_source == REVIEWED_SOURCE,
                    'retained-owner inspection does not describe current measured native source')



class SettledRss:
    @staticmethod
    def integer(value, label, positive=False):
        if type(value) is not int or value < (1 if positive else 0):
            raise ValueError(label + " must be an integer in its observed range")
        return value

    @classmethod
    def assess(cls, raw, workspace_root, mode, historical_native=None):
        memory = raw.get("idleMemory") or {}
        if memory.get("metric") != "sum-of-process-RSS":
            raise ValueError("idle observation has the wrong RSS metric")
        if memory.get("indexingComplete") is not True:
            raise ValueError("indexing did not finish before settled sampling")
        if raw.get("barriers", {}).get("hoverCleanup") is not True:
            raise ValueError("independent hover cleanup barrier is missing")
        if type(memory.get("samplingIntervalMs")) is not int or memory["samplingIntervalMs"] != 100:
            raise ValueError("idle sampling configuration changed")
        samples = memory.get("samples")
        if not isinstance(samples, list) or len(samples) != 5:
            raise ValueError("settled observation requires five actual RSS samples")
        peak = cls.integer(memory.get("indexingPeakRssBytes"), "server/engine indexing peak", True)
        count = cls.integer(memory.get("indexingSamples"), "indexing sample count", True)
        if count < 5:
            raise ValueError("indexing peak lacks sufficient actual samples")

        hovers = [row for row in raw.get("transport", [])
                  if row.get("method") == "textDocument/hover"]
        if len(hovers) != 3 or any(row.get("status") != "success" for row in hovers):
            raise ValueError("settled generated workload lacks all three successful hovers")
        request_ids = [row.get("id") for row in hovers]
        if any(type(value) is not int for value in request_ids) or len(set(request_ids)) != 3:
            raise ValueError("hover request identities are missing or duplicated")
        previous = -1
        for row in hovers:
            written = cls.integer(row.get("writtenNs"), "hover write time")
            received = cls.integer(row.get("receivedNs"), "hover receive time")
            if written <= previous or received < written:
                raise ValueError("hover ledger is nonmonotonic or overlaps")
            if type(row.get("durationNs")) is not int or row["durationNs"] != received - written:
                raise ValueError("hover duration disagrees with its actual timestamps")
            previous = received

        releases = [event for event in raw.get("stages", [])
                    if event.get("message") == "memory report"
                    and event.get("fields", {}).get("label") == "hover"]
        if len(releases) != 3:
            raise ValueError("all three independent request release events are required")
        completed = [event for event in raw.get("stages", [])
                     if event.get("message") == "analysis query completed"
                     and event.get("fields", {}).get("query") == "hover"]
        if len(completed) != 3 or any(event["fields"].get("status") != "ok" for event in completed):
            raise ValueError("all three successful native query completions are required")
        completion_times = [cls.integer(event.get("observedNs"), "query completion time") for event in completed]
        if any(a >= b for a, b in zip(completion_times, completion_times[1:])):
            raise ValueError("query completions are duplicated or reordered")
        release_times = [cls.integer(event.get("observedNs"), "release time") for event in releases]
        if any(a >= b for a, b in zip(release_times, release_times[1:])):
            raise ValueError("request release events are duplicated or reordered")
        for index, release in enumerate(release_times):
            if completion_times[index] < hovers[index]["writtenNs"] or release < completion_times[index]:
                raise ValueError("query completion precedes its request or release precedes completion")

        if mode not in ("faster-builds", "lower-peak-memory"):
            raise ValueError("unknown indexing mode")
        lifecycle = raw.get("lifecycle", [])
        statuses = [(index, event) for index, event in enumerate(lifecycle)
                    if event.get("method") == "experimental/serverStatus"]
        if not statuses:
            raise ValueError("actual indexing quiescence is missing")
        deferred_names = {"suprnova-lsp/deferredIndexingStarted", "suprnova-lsp/deferredIndexingFinished"}
        deferred = [(index, event) for index, event in enumerate(lifecycle)
                    if event.get("method") in deferred_names
                    and event.get("params", {}).get("root") == workspace_root]
        if mode == "lower-peak-memory" and any(event.get("method") in deferred_names for event in lifecycle):
            raise ValueError("lower-memory mode unexpectedly ran deferred indexing")
        active = [(index, event) for index, event in enumerate(lifecycle)
                  if event.get("method") == "suprnova-lsp/activeWorkspaceChanged"
                  and event.get("params", {}).get("root") == workspace_root]
        historical = None
        if historical_native is None:
            native_names = {"deferred indexing lifecycle started", "deferred indexing lifecycle finished"}
            generations = [(index, event) for index, event in enumerate(raw.get("stages", []))
                           if event.get("message") in native_names
                           and event.get("fields", {}).get("root") == workspace_root]
            if not generations:
                raise ValueError("saved indexing generation evidence is missing")
        else:
            historical = HistoricalSettledRss.serialized_completion(historical_native, workspace_root, mode)
            purge_label = "after deferred indexing finish" if mode == "faster-builds" else "after project build"
            purges = [event for event in raw.get("stages", []) if event.get("message") == "memory report"
                      and event.get("fields", {}).get("label") == purge_label]
            if len(purges) != 1:
                raise ValueError("historical indexing release/purge stage is missing or duplicated")
            purge_time = cls.integer(purges[0].get("observedNs"), "historical indexing purge receipt")
        for events in (statuses, deferred, active):
            times = [cls.integer(event.get("receivedNs"), "lifecycle observation time") for _, event in events]
            if any(a > b for a, b in zip(times, times[1:])):
                raise ValueError("lifecycle observations are reordered")
        if historical is None:
            native_times = [cls.integer(event.get("observedNs"), "native generation observation time") for _, event in generations]
            if any(a > b for a, b in zip(native_times, native_times[1:])):
                raise ValueError("native generation observations are reordered")
            for _, event in generations:
                cls.integer(event["fields"].get("generation"), "saved indexing generation")
                if mode == "lower-peak-memory" and event["message"] == "deferred indexing lifecycle started":
                    raise ValueError("lower-memory mode unexpectedly started deferred work")
        previous = max(previous, *release_times, *completion_times)
        membership = None
        values = []
        timestamps = []
        completion_evidence = []
        for sample in samples:
            timestamp = cls.integer(sample.get("observedNs"), "RSS sample time")
            if timestamp <= previous:
                raise ValueError("RSS sample precedes settled completion or repeats a timestamp")
            if timestamps and timestamp - previous < 100_000_000:
                raise ValueError("RSS samples violate the recorded 100 ms interval")
            # Notifications do not carry a saved generation. Its identity is
            # in the existing native stage; bind both streams at each sample.
            status_before = [(i, e) for i, e in statuses if e["receivedNs"] <= timestamp]
            if not status_before or status_before[-1][1]["params"].get("health") != "ok" or status_before[-1][1]["params"].get("quiescent") is not True:
                raise ValueError("latest indexing status is not healthy and quiescent before RSS")
            active_before = [(i, e) for i, e in active if e["receivedNs"] <= timestamp]
            if active_before and active_before[-1][1]["params"].get("state") != "ready":
                raise ValueError("latest selected workspace is not ready before RSS")
            if historical is None:
                native_before = [(i, e) for i, e in generations if e["observedNs"] <= timestamp]
                if not native_before:
                    raise ValueError("RSS precedes actual saved generation evidence")
                generation = max(event["fields"]["generation"] for _, event in native_before)
                native_index, latest_native = next((i, e) for i, e in reversed(native_before) if e["fields"]["generation"] == generation)
                if latest_native["message"] != "deferred indexing lifecycle finished" or latest_native["fields"].get("outcome", "Succeeded") != "Succeeded":
                    raise ValueError("latest saved generation is not successfully settled before RSS")
                deferred_index = None
                settled_times = [status_before[-1][1]["receivedNs"], latest_native["observedNs"]]
            else:
                generation, native_index = historical["savedGeneration"], None
                deferred_index = None
                settled_times = [status_before[-1][1]["receivedNs"], purge_time]
            if active_before:
                settled_times.append(active_before[-1][1]["receivedNs"])
            if mode == "faster-builds":
                deferred_before = [(i, e) for i, e in deferred if e["receivedNs"] <= timestamp]
                if not deferred_before or deferred_before[-1][1]["method"] != "suprnova-lsp/deferredIndexingFinished" or deferred_before[-1][1]["params"].get("outcome") != "succeeded":
                    raise ValueError("latest deferred lifecycle is not successfully settled before RSS")
                deferred_index = deferred_before[-1][0]
                settled_times.append(deferred_before[-1][1]["receivedNs"])
            if timestamp <= max(settled_times):
                raise ValueError("RSS sample does not follow its actual indexing completion")
            if timestamps:
                first = timestamps[0]
                if any(first < e["receivedNs"] <= timestamp and (e["params"].get("health") != "ok" or e["params"].get("quiescent") is not True) for _, e in statuses):
                    raise ValueError("indexing became busy or failed during settled sampling")
                if historical is None and any(first < e["observedNs"] <= timestamp and e["message"] == "deferred indexing lifecycle started" for _, e in generations):
                    raise ValueError("saved generation work started during settled sampling")
                if any(first < e["receivedNs"] <= timestamp and (e["method"] == "suprnova-lsp/deferredIndexingStarted" or e["params"].get("outcome") != "succeeded") for _, e in deferred):
                    raise ValueError("deferred work changed during settled sampling")
                if any(first < e["receivedNs"] <= timestamp and e["params"].get("state") != "ready" for _, e in active):
                    raise ValueError("selected workspace stopped being ready during settled sampling")
            completion_evidence.append({"sampleNs": timestamp, "statusLifecycleIndex": status_before[-1][0],
                "deferredLifecycleIndex": deferred_index, "savedGeneration": generation,
                "nativeGenerationStageIndex": native_index})
            if historical is not None:
                completion_evidence[-1]["historicalSerializedCompletion"] = historical
            processes = sample.get("processRssBytes")
            if not isinstance(processes, dict) or len(processes) < 2:
                raise ValueError("RSS sample lacks the server/engine process set")
            for pid, rss in processes.items():
                if not isinstance(pid, str) or not pid.isdecimal() or int(pid) <= 0:
                    raise ValueError("RSS process identity is malformed")
                cls.integer(rss, "per-process RSS", True)
            current = frozenset(processes)
            if membership is not None and current != membership:
                raise ValueError("RSS sampler process membership changed")
            membership = current
            aggregate = cls.integer(sample.get("aggregateRssBytes"), "aggregate RSS", True)
            if aggregate != sum(processes.values()):
                raise ValueError("aggregate RSS contradicts per-process RSS")
            # Indexing sampling ends separately from the later query/idle
            # phase. Its peak is evidence, never an idle-memory allowance.
            values.append(aggregate)
            timestamps.append(timestamp)
            previous = timestamp
        return {"rssBytes": values, "sampleTimesNs": timestamps,
                "sampledPids": sorted(membership),
                "medianRssBytes": sorted(values)[2],
                "serverEngineIndexingPeakRssBytes": peak,
                "compilerPeakRssBytes": None,
                "completionEvidence": completion_evidence,
                "ownership": "requires independent native/sampler binding",
                "acceptance": "unverified: provenance, matched pairs and retained-owner evidence required"}


class HistoricalSettledRss(SettledRss):
    """Actual 893 lifecycle selection; all common RSS/RPC/release guards are shared."""

    @classmethod
    def assess(cls, raw, workspace_root, mode, native):
        return super().assess(raw, workspace_root, mode, historical_native=native)

    @classmethod
    def serialized_completion(cls, native, root, mode):
        def matching(message, label=None):
            return [e for e in native if e.get("message") == message
                    and (label is None or e.get("fields", {}).get("label") == label)]
        def one(message, label=None):
            rows = matching(message, label)
            if len(rows) != 1:
                raise ValueError("historical native boundary missing or duplicated: " + message)
            return rows[0]
        initialized = one("engine command started: initialize")
        if initialized["fields"].get("root") != root:
            raise ValueError("historical initialized workspace differs")
        # Actual 893 does not log modern configured-import publication. Its source
        # replace_saved precedes initial-index stats and workspace indexing finished.
        # The genuine worker/already-complete log supplies generation; publication
        # is source-bound, not an invented contemporaneous generation field.
        generation_event = one("deferred indexing background finish started" if mode == "faster-builds"
                               else "deferred indexing already complete")
        generation = cls.integer(generation_event["fields"].get("generation"), "historical saved generation", True)
        indexed = one("workspace indexing finished")
        if indexed["fields"].get("workspace_root") != root or indexed["fields"].get("indexing_preference") != mode:
            raise ValueError("historical indexed workspace or mode differs")
        # Original state.rs:37 starts generation IDs at 1; initial.rs:113 allocates
        # the first build. A fresh single initialize with no source retries or
        # mutation is therefore source-bound to 1, not guessed from a wire field.
        if generation != 1 or indexed["fields"].get("stale_retries") != 0:
            raise ValueError("historical initial saved generation or retry history differs")
        initial_stats = one("project stats", "initial index")
        initial_purge = one("memory report", "after project build")
        hover = [e for e in native if e.get("message") == "analysis query started"
                 and e.get("fields", {}).get("label") == "hover"]
        if len(hover) != 3:
            raise ValueError("historical hover boundaries differ")
        first_hover = hover[0]["serializedLine"]
        # A fresh original workload initializes once and then only reads. Reject any
        # saved mutation/recovery or extra worker lifecycle rather than inferring a
        # generation from the generation-less private notification payloads.
        allowed = {"initialize", "hover", "set_deferred_indexing_priority",
                   "deferred_indexing_progress", "deferred_indexing_products", "deferred_indexing_finished"}
        for e in native:
            message = e.get("message", "")
            if message.startswith("engine command started: ") and message.split(": ", 1)[1] not in allowed:
                raise ValueError("historical saved mutation or unknown command is present")
        if not initialized["serializedLine"] < initial_purge["serializedLine"] < initial_stats["serializedLine"] < indexed["serializedLine"] < first_hover:
            raise ValueError("historical saved publication/indexing order differs")
        if mode == "faster-builds":
            started = one("deferred indexing background finish started")
            completed = one("deferred indexing background finish completed")
            applied = one("engine command started: deferred_indexing_finished")
            purged = one("memory report", "after deferred indexing finish")
            for e in (started, completed, applied):
                if e["fields"].get("generation") != generation:
                    raise ValueError("historical worker generation differs from initial worker saved generation")
            if not indexed["serializedLine"] < started["serializedLine"] < completed["serializedLine"] < applied["serializedLine"] < purged["serializedLine"] < first_hover:
                raise ValueError("historical worker completion/publication/purge order differs")
            if matching("deferred indexing already complete"):
                raise ValueError("historical faster lifecycle contains conflicting terminal evidence")
            terminal = purged
        elif mode == "lower-peak-memory":
            terminal = one("deferred indexing already complete")
            if terminal["fields"].get("generation") != generation or not indexed["serializedLine"] < terminal["serializedLine"] < first_hover:
                raise ValueError("historical batch completion generation/order differs")
            if any("deferred indexing background finish" in e.get("message", "") or e.get("message") == "engine command started: deferred_indexing_finished" for e in native):
                raise ValueError("historical lower-memory mode ran background work")
        else:
            raise ValueError("unknown indexing mode")
        return {"savedGeneration": generation, "publishedSerializedLine": None, "publicationBasis": "actual893 replace_saved before initial-index stats",
                "initialStatsSerializedLine": initial_stats["serializedLine"],
                "workspaceIndexedSerializedLine": indexed["serializedLine"], "settledSerializedLine": terminal["serializedLine"],
                "firstHoverSerializedLine": first_hover, "nativeGenerationReceiptNs": None,
                "basis": "old source validates current saved coverage before terminal notification; serialized native generation and genuine purge plus wire status"}


class IdleAssessment:
    def __init__(self, directory, selection, evidence):
        self.directory = self.owned(directory)
        self.selection = selection
        self.evidence = evidence
        self.binding = (ROOT / selection['binding']['path']).resolve(strict=True)
        self.binding_sha = selection['binding']['sha256']
        self.source = None
        self.binary = None
        self.references, self.errors, self.warnings = {}, [], []
        self.attempts, self.members, self.native_facts = [], [], {}
        self.core = SettledRss
        self.historical_core = HistoricalSettledRss
        self.current_applicability = {}
        self.initial_pins = {}

    def selected_path(self, name):
        entry = self.selection[name]
        path = (ROOT / entry['path']).resolve(strict=True)
        IdleProof.require(path.is_relative_to(ROOT) and path != PROTECTED and PROTECTED not in path.parents,
                          'selected evidence escapes repository: ' + name)
        IdleProof.require(isinstance(entry['sha256'], str) and re.fullmatch('[0-9a-f]{64}', entry['sha256']),
                          'selected evidence lacks digest: ' + name)
        return path

    def selected_ref(self, name):
        path = self.selected_path(name)
        IdleProof.require(path.stat().st_size <= BOUND, 'selected artifact exceeds parser bound: ' + name)
        digest = self.sha(path)
        IdleProof.require(digest == self.selection[name]['sha256'], 'selected artifact changed: ' + name)
        ref = {'path': str(path), 'sha256': digest, 'bytes': path.stat().st_size}
        IdleProof.require(ref['bytes'] <= BOUND, 'selected artifact exceeds parser bound: ' + name)
        self.references[str(path)] = ref
        return ref

    def selected(self, name):
        ref = self.selected_ref(name)
        return json.loads(Path(ref['path']).read_bytes())

    def source_review(self):
        review = self.selected_ref('retainedReview')
        IdleProof.retained_source(review['sha256'], self.selection['retainedReview']['runtimeSourcesSha256'],
                                  self.source, self.runtime_source())
        user = self.evidence.observer.helpers.module('rsp_idle_compact_imports', ROOT / 'tools/sudus-suprnova-user.py')
        IdleProof.require(user.compact_layout_ok(ROOT), 'saved compiler import retains a broad export graph')
        # The full native fingerprint binds the inspected owner graph and all
        # fork changes. These focused guards make its release/reader boundary
        # explicit; a new native fingerprint needs another source review.
        txn = (ROOT / 'crates/engine/semantic-ir/src/store/txn.rs').read_text()
        lifecycle = (ROOT / 'crates/lsp/engine/src/engine/query/lifecycle.rs').read_text()
        IdleProof.require('crates.len().min(8)' in txn and 'std::thread::scope' in txn and 'reader.join()' in txn,
                          'inspected temporary-reader join boundary changed')
        IdleProof.require(lifecycle.index('self.project.release_query_memory();')
            < lifecycle.index('MemoryReporter::purge_and_report_delta_debug'), 'inspected request-release/purge changed')
        return {'review': review, 'runtimeSourcesSha256': self.source,
                'independentAllocatorReachabilitySnapshot': None,
                'basis': 'inspected saved/request owners, complete matching native source fingerprint and compact import layout'}

    def historical_source_review(self):
        review = self.selected_ref('baselineReview')
        IdleProof.require(review['sha256'] == '8f70f78486165b51bef63eeb74a6f0a1eb3773e9bb88753a389024f9cd9793fd',
                          'original baseline source review changed')
        # Read Git objects only. No old checkout/build or regenerated observation
        # stands in for the genuine historical compile at 893.
        diff = subprocess.run(['git', 'diff', ORIGINAL_COMMIT, OBSERVATION_COMMIT, '--',
            'crates', 'Cargo.lock', 'Cargo.toml', 'rust-toolchain.toml'], cwd=ROOT,
            capture_output=True, text=True, check=True, timeout=5).stdout
        IdleProof.historical_diff(diff)
        return {'review': review, 'nativeDiffSha256': hashlib.sha256(diff.encode()).hexdigest(),
            'compiledCommit': ORIGINAL_COMMIT, 'manifestObservationCommit': OBSERVATION_COMMIT,
            'contemporaneousRuntimeFingerprint': None, 'contemporaneousBuildEndSourceGuard': None,
            'acceptedBasis': 'genuine clean-prebuild compiled-origin record and exact preserved executable; historical missing end guard remains disclosed'}

    def capture_proof(self):
        binding = self.selected('binding')
        source = self.selected_ref('captureSource')
        durable = ROOT / 'tools/responsiveness-idle-collect.py'
        supported = {ORIGINAL_CAPTURE_SOURCE}
        if durable.exists():
            supported.add(self.sha(durable))
        IdleProof.require(source['sha256'] in supported
            and binding['captureMechanism']['sha256'] == source['sha256'],
            'capture mechanism is not the independently reviewed original or durable collector')
        before, after = (binding[name]['record'] for name in ('before', 'after'))
        selected = self.read(binding['selectedRun']['path'], binding['selectedRun']['sha256'])
        plan = self.read(binding['launchPlan']['path'], binding['launchPlan']['sha256'])
        process = self.read(selected['launchProcess']['path'], selected['launchProcess']['sha256'])
        outcome = self.read(binding['launchOutcome']['path'], binding['launchOutcome']['sha256'])
        IdleProof.capture_order(before, after, plan, selected, process, outcome, self.directory, binding['plannedArgv'])
        IdleProof.require(selected['beforeCapture'] == plan['beforeCapture'] == binding['before']['capture']
            and selected['captureMechanism'] == plan['captureMechanism'] == before['captureMechanism']
            and plan['cwd'] == str(ROOT), 'actual launch/allocation/source references differ')
        cleanup = self.read(Path(selected['launchProcess']['path']).parent / 'cleanup.json')
        # The frozen writer omits null dictionary values recursively. Preserve
        # every non-null field while comparing its file with the raw outcome.
        runner = self.evidence.observer.helpers.module('rsp_idle_cleanup_writer', ROOT / 'tools/agent-debug.py')
        IdleProof.require(cleanup == runner.without_none(outcome['cleanup']), 'actual outer cleanup file differs')
        self.ref(Path(selected['launchProcess']['path']).parent / 'stdout.log')
        self.ref(Path(selected['launchProcess']['path']).parent / 'stderr.log')
        return {'source': source, 'driverPid': process['pid'], 'run': str(self.directory),
                'beforeLaunchRecorded': True, 'afterAllocationRecorded': True, 'aggregateDeadlineMs': 900000}

    def producer_proof(self):
        producer = self.selected('producer')
        original = self.selected('producerRun')
        # The original producer summary has no modern observationComplete field.
        # Its five actual successful commands and empty groups are the evidence.
        commands = IdleProof.commands(original, require_complete=False)
        IdleProof.require([command['phase'] for command in original['commands']]
            == ['compiler', 'cfg', 'sysroot', 'metadata', 'export'], 'original producer command ledger differs')
        original_path = self.selected_path('producerRun')
        IdleProof.require((ROOT / producer['compilerRun']).resolve() == original_path.parent,
                          'original compiler run differs from producer provenance')
        export_command = next(command for command in original['commands'] if command['phase'] == 'export')
        self.bind_command(original_path.parent, export_command)
        user = self.evidence.observer.helpers.module('rsp_idle_producer', ROOT / 'tools/sudus-suprnova-user.py')
        IdleProof.require(export_command['command'] == 'cargo' and export_command['args'] == user.rustdoc_args(),
                          'original compiler export argv differs from captured provenance')
        metadata = json.loads(self.data(self.directory / 'metadata/stdout.log'))
        compiler = self.data(self.directory / 'producer-compiler/stdout.log').decode()
        cfg = self.data(self.directory / 'producer-cfg/stdout.log').decode()
        sysroot = Path(self.data(self.directory / 'producer-sysroot/stdout.log').decode().strip()) / 'lib/rustlib/src/rust/library'
        export = self.data(self.directory / 'directory.json', EXPORT_SHA)
        IdleProof.require(len(export) <= 256 * 1024 * 1024, 'compiler export exceeds frozen reader bound')
        # Existing frozen validation independently recomputes package/features,
        # dependency/sysroot sources, compiler overrides/configuration and export.
        user.validate_capture(producer, metadata, sysroot, compiler, cfg, export)
        del export
        return {'producer': self.selected_ref('producer'), 'originalCommands': commands,
            'exportSha256': EXPORT_SHA, 'frameworkRevision': FRAMEWORK,
            'configurationRevalidated': True, 'compilerPeakRssBytes': None,
            'peakLimitation': 'No contemporary compiler peak is inferred from historical export or indexing RSS.'}

    @staticmethod
    def owned(path):
        resolved = Path(path).resolve(strict=True)
        IdleProof.require(resolved.is_relative_to(OWNED.resolve()), 'artifact escapes owned root: ' + str(path))
        return resolved

    @staticmethod
    def sha(path):
        result = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                result.update(chunk)
        return result.hexdigest()

    def ref(self, path, expected=None):
        path = self.owned(path)
        ref = {'path': str(path), 'sha256': self.sha(path), 'bytes': path.stat().st_size}
        self.references[str(path)] = ref
        IdleProof.require(expected is None or ref['sha256'] == expected, 'artifact SHA mismatch: ' + str(path))
        return ref

    def data(self, path, expected=None):
        ref = self.ref(path, expected)
        IdleProof.require(ref['bytes'] <= BOUND, 'artifact exceeds 512MiB parser bound: ' + str(path))
        return Path(ref['path']).read_bytes()

    def read(self, path, expected=None):
        return json.loads(self.data(path, expected))

    def check(self, label, work):
        try:
            return work()
        except ERRORS as error:
            self.errors.append({'label': label, 'error': type(error).__name__ + ': ' + str(error)})
            return None

    @staticmethod
    def inventory(root):
        result = {}
        for directory, children, names in os.walk(root, followlinks=False):
            children[:] = sorted(n for n in children if n not in {'.git', 'target', 'node_modules'})
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix == '.rs' or name in {'Cargo.toml', 'Cargo.lock'}:
                    IdleProof.require(path.resolve() != PROTECTED and PROTECTED not in path.resolve().parents,
                                      'input reaches protected framework checkout')
                    result[str(path.relative_to(root))] = IdleAssessment.sha(path)
        for name in ('.cargo/config.toml', '.cargo/config'):
            path = root / name
            if path.exists():
                IdleProof.require(path.resolve().is_relative_to(root.resolve()), 'Cargo configuration escapes checkout')
                result[name] = IdleAssessment.sha(path)
        return result

    @classmethod
    def runtime_source(cls):
        files = cls.inventory(ROOT / 'crates')
        for name in ('Cargo.lock', 'Cargo.toml', 'rust-toolchain.toml'):
            files[name] = cls.sha(ROOT / name)
        return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()

    def baseline_origin(self, report):
        manifest_path = self.selected_path('baselineManifest')
        manifest = self.selected('baselineManifest')
        IdleProof.require(manifest['identity']['binarySha256'] == BASELINE
            and manifest['identity']['commit'] == OBSERVATION_COMMIT, 'historical baseline manifest identity differs')
        self.ref(manifest['retainedBinary'], BASELINE)
        IdleProof.require(manifest['identity']['sources'] == report['identity']['sources'], 'baseline/current application inputs differ')
        original_path = self.selected_path('baselineBuild')
        original_directory = original_path.parent
        original = self.selected('baselineBuild')
        IdleProof.require(self.sha(original_path) == ORIGINAL_REPORT_SHA, 'original historical compiled report differs')
        IdleProof.commands(original)
        IdleProof.require(original['identity']['binarySha256'] == BASELINE, 'original compiled baseline differs')
        command = next(c for c in original['commands'] if c['phase'] == 'build')
        IdleProof.require(command['command'] == 'cargo'
            and {'build', '--release', '--locked', '--offline', 'suprnova-lsp', 'x86_64-unknown-linux-gnu'}.issubset(command['args']),
            'baseline lacks genuine locked offline optimized compilation')
        self.bind_command(original_directory, command)
        self.data(original_directory / 'build/stderr.log', 'e8be1f8592e41ced31bd3497cfc20017b1faa6e6326fb2ab67ad62b9718056ec')
        actual_commit = self.data(original_directory / 'commit/stdout.log',
            '317b11bee8fcb1ebc9b0596b712e4b17f147f219cf30a0eedf0b1306bba8aa92').decode().strip()
        IdleProof.require(actual_commit == ORIGINAL_COMMIT, 'original build checkout differs')
        runtime = next(c for c in original['commands'] if c['phase'] == 'runtime-inputs')
        self.bind_command(original_directory, runtime)
        IdleProof.require('--exit-code' in runtime['args'], 'baseline clean-input check absent')
        IdleProof.require(self.data(original_directory / 'runtime-inputs/stdout.log') == b'', 'baseline clean-input output differs')
        runtime_process = self.read(original_directory / 'runtime-inputs/process.json')
        build_process = self.read(original_directory / 'build/process.json')
        IdleProof.require(runtime_process['startedAt'] < build_process['startedAt'], 'baseline clean-input check is not before compilation')
        for mode_report in report.get('reports', {}).values():
            IdleProof.require(mode_report.get('baselineProvenance') == manifest, 'fresh idle ledger substitutes baseline provenance')
        return {'manifest': self.references[str(manifest_path)], 'compiledReport': self.references[str(original_directory / 'report.json')],
            'compiledCommit': ORIGINAL_COMMIT, 'manifestObservationCommit': OBSERVATION_COMMIT,
            'nativeDiffReview': self.selected_ref('baselineReview'),
            'contemporaneousRuntimeFingerprint': None, 'contemporaneousBuildEndSourceGuard': None}

    def bind_command(self, directory, command, require_success=True):
        label = command['phase']
        process = self.read(directory / label / 'process.json')
        IdleProof.require(process['command'] == command['command'] and process['args'] == command['args']
            and process['pid'] == command['pid'], 'physical process differs from command ledger: ' + label)
        if require_success:
            IdleProof.require(IdleProof.success(command) and IdleProof.empty(command), 'physical command failed/cleanup unverified: ' + label)
        self.ref(directory / label / 'stdout.log')
        self.ref(directory / label / 'stderr.log')
        return process

    def candidate_origin(self, report):
        identity = report['identity']
        self.source, self.binary = identity['runtimeSourcesSha256'], identity['binarySha256']
        archive = self.selected('candidateArchiveIdentity')
        compiled = self.selected('candidateCompiledIdentity')
        build = self.selected('candidateCompiledReport')
        for key, name in (('compiledBuildIdentity', 'candidateCompiledIdentity'), ('compiledBuildReport', 'candidateCompiledReport')):
            IdleProof.require(archive[key] == {'path': str(self.selected_path(name)), 'sha256': self.selection[name]['sha256']},
                              'archive does not reference selected actual compilation: ' + name)
        IdleProof.require(archive['runtimeSourcesSha256'] == self.source and archive['binarySha256'] == self.binary,
                          'candidate compiled archive/source binding differs')
        IdleProof.require(compiled == {'runtimeSourcesSha256': self.source, 'binarySha256': self.binary, 'sourcesUnchanged': True},
                          'candidate compilation source/binary guard differs')
        IdleProof.require(len(build['commands']) == 1 and build['processCleanup'] == {'status': 'verified', 'runs': 1, 'verifiedRuns': 1},
                          'candidate compiled build count/cleanup differs')
        command = build['commands'][0]
        IdleProof.require(IdleProof.success(command) and IdleProof.empty(command) and command['command'] == 'cargo'
            and {'build', '--release', '--locked', '--offline', 'suprnova-lsp', 'x86_64-unknown-linux-gnu'}.issubset(command['args']),
            'candidate lacks successful locked offline optimized compilation')
        build_dir = self.selected_path('candidateCompiledReport').parent
        process = self.read(build_dir / 'build/process.json')
        IdleProof.require(process['command'] == command['command'] and process['args'] == command['args']
            and process['pid'] == command['pid'], 'candidate actual compiled process differs')
        self.ref(build_dir / 'build/stderr.log')
        self.ref(archive['binary'], self.binary)
        common = self.owned(identity['binary'])
        self.ref(common, self.binary)
        self.current_applicability = {'currentRuntimeSourcesSha256': self.runtime_source(),
            'archiveBinarySha256': self.sha(archive['binary']), 'managedBinarySha256': self.sha(common)}
        IdleProof.applicability(self.current_applicability, self.source, self.binary)
        return {'compiledArchive': archive, 'compiledIdentity': compiled}

    def provenance(self, report):
        identity = report['identity']
        IdleProof.require(identity.get('purpose') == 'three matched settled idle-memory pairs; latency acceptance is separate'
            and identity.get('workload') == 'generated-captured' and identity.get('application') == str(APP)
            and identity.get('frameworkRevision') == FRAMEWORK and identity.get('buildProfile') == 'release'
            and identity.get('producerToolchain') == 'nightly-2026-08-19'
            and identity['openFileLimits']['effective'][0] == 4096, 'original idle workload/configuration/limits differ')
        IdleProof.require(identity['observers'] == {name: PINS[name] for name in ('lsp-query.py', 'sudus-responsiveness.py', 'agent-debug.py')},
                          'recorded frozen observer pins differ')
        for flag in ('applicationInputsUnchanged', 'binaryUnchanged', 'runtimeSourcesUnchanged'):
            IdleProof.require(report.get(flag) is True, 'terminal source/application/binary guard missing: ' + flag)
        IdleProof.require(identity['sources'] == self.inventory(APP), 'current application sources/lockfile/config differ')
        IdleProof.require(identity.get('cacheState') == 'existing LSP/compiler caches; fresh owned export artifact root'
            and 'compilerCache' not in identity and report.get('compilerSeedUnchanged') is None,
            'original idle cache conditions or seed override differ')
        IdleProof.require(identity['runtimeDiffSha256'] == hashlib.sha256(b'').hexdigest(), 'native working diff nonempty')
        IdleProof.require(self.data(self.directory / 'runtime-inputs/stdout.log') == b''
            and self.data(self.directory / 'commit/stdout.log').decode().strip() == identity['commit'], 'actual fresh runtime/commit guards differ')
        metadata = json.loads(self.data(self.directory / 'metadata/stdout.log'))
        IdleProof.require(hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest() == identity['metadataSha256'],
                          'actual pinned dependency metadata differs')
        framework = [p for p in metadata['packages'] if p['name'] == 'suprnova']
        IdleProof.require(len(framework) == 1 and (framework[0].get('source') or '').endswith('#' + FRAMEWORK), 'wrong pinned framework dependency')
        for package in metadata['packages']:
            path = Path(package['manifest_path']).resolve()
            IdleProof.require(path != PROTECTED and PROTECTED not in path.parents, 'protected checkout appears in metadata')
        for phase, key in (('build-compiler', 'buildCompiler'), ('producer-compiler', 'producerCompiler')):
            IdleProof.require(self.data(self.directory / phase / 'stdout.log').decode() == identity[key], 'compiler stdout identity differs')
        IdleProof.require(identity['buildCompiler'].startswith('rustc 1.98.1') and 'nightly' in identity['producerCompiler'], 'compiler versions differ')
        self.ref(self.directory / 'directory.json', EXPORT_SHA)
        IdleProof.require(identity['capturedExportSha256'] == EXPORT_SHA, 'generated compiler export differs')
        self.ref(self.directory / 'producer-cfg/stdout.log')
        self.ref(self.directory / 'producer-sysroot/stdout.log')

    def fresh_binding(self):
        if self.binding is None:
            raise ValueError('fresh idle boundary selection is required')
        IdleProof.require(isinstance(self.binding_sha, str) and re.fullmatch('[0-9a-f]{64}', self.binding_sha), 'binding SHA is required')
        binding = self.read(self.binding, self.binding_sha)
        expected_argv = ['python3', '-B', 'tools/sudus-responsiveness.py', '--idle-pairs', '--no-build',
            '--workload', 'generated-captured', '--baseline-manifest',
            str(self.selected_path('baselineManifest').relative_to(ROOT)), '--nofile-soft', '4096']
        IdleProof.require(binding['run'] == str(self.directory) and binding['plannedArgv'] == expected_argv,
                          'fresh idle selected run/CLI differs')
        for boundary in ('before', 'after'):
            observed = binding[boundary]
            IdleProof.require(observed['frozenPins'] == PINS and observed['runtimeSourcesSha256'] == self.source
                and observed['managedBinarySha256'] == observed['archiveBinarySha256'] == self.binary,
                'fresh idle boundary pin/source/native executable differs')
            # The selected digest pins original raw capture files. Stated dictionaries
            # alone cannot prove that a post-hoc reconstructed boundary was observed.
            capture = self.read(observed['capture']['path'], observed['capture']['sha256'])
            IdleProof.require(capture == observed['record'] and capture['observedAt'] == observed['observedAt']
                and capture['frozenPins'] == PINS and capture['runtimeSourcesSha256'] == self.source
                and capture['managedBinarySha256'] == capture['archiveBinarySha256'] == self.binary,
                'boundary capture file does not bind stated observed facts')
        IdleProof.require(binding['before']['observedAt'] < binding['after']['observedAt'], 'boundary capture order differs')
        outer = self.read(binding['launchOutcome']['path'], binding['launchOutcome']['sha256'])
        IdleProof.require(IdleProof.success(outer) and IdleProof.empty(outer), 'top-level driver launch failed/cleanup unverified')

    def expected_plan(self, mode):
        original = (APP / 'src/models/user.rs').read_text()
        signature = 'pub fn verify_password(&self, password: &str) -> Result<bool, FrameworkError> {'
        IdleProof.require(original.count(signature) == 1, 'Devlist source signature changed')
        text = original.replace(signature, signature + '\n        use suprnova::eloquent::Model as _;\n'
            '        let rsp_query = User::query();\n        let rsp_without = User::without_global_scopes();\n'
            '        let rsp_filter = User::filter("email", "member@example.test");\n')
        return {'file': 'src/models/user.rs', 'format': 'json', 'readinessBarrier': 'ready',
            'documentReadinessBarrier': True, 'deferredBarrier': 'before-queries', 'idleMemory': True,
            'hoverCleanupBarrier': True, 'rustdocBarrier': 'none', 'rustdocTimeoutMs': 900000, 'text': text,
            'queries': [{'kind': 'hover', 'label': label, 'marker': label} for label in LABELS],
            'initializationOptions': {'cfg': {'test': False}, 'cache': {'packageResidency': 'workspace'},
                'indexing': {'performancePreference': mode}, 'rustdoc': {'automatic': {'enabled': False},
                    'inputs': [{'workspaceRoot': str(APP), 'manifestPath': str(APP / 'Cargo.toml'),
                        'targetName': 'directory', 'targetKind': 'lib', 'exportPath': str(self.directory / 'directory.json'),
                        'itemPath': 'directory::models::user::User'}]}}}

    @staticmethod
    def stdout_raw(data):
        text = data.decode()
        begin = text.index('{\n  "file"')
        return json.JSONDecoder().raw_decode(text[begin:])[0]

    def native(self, label, inner):
        path = self.owned(inner / 'run-001/lsp-server.stderr.log')
        IdleProof.require(path.stat().st_size <= BOUND, 'native stderr exceeds parser bound')
        digest, size, selected, all_serialized = hashlib.sha256(), 0, [], []
        with path.open('rb') as stream:
            for number, line in enumerate(stream, 1):
                digest.update(line)
                size += len(line)
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    self.errors.append({'label': label, 'error': 'unparsed native stderr', 'serializedLine': number,
                                        'textPrefix': line[:1000].decode(errors='replace')})
                    continue
                if not isinstance(event, dict) or event.get('schema') != 'suprnova-lsp-log/v1':
                    self.errors.append({'label': label, 'error': 'unknown native log schema', 'serializedLine': number})
                    continue
                fields = event.get('fields') or {}
                if not isinstance(fields, dict):
                    self.errors.append({'label': label, 'error': 'native fields not object', 'serializedLine': number})
                    continue
                fact = {'serializedLine': number, 'message': event.get('message'), 'fields': fields,
                        'level': event.get('level'), 'component': event.get('component'), 'engine': event.get('engine')}
                all_serialized.append(fact)
                if fact['level'] in ('WARN', 'ERROR'):
                    self.warnings.append({'label': label, 'nativeLog': str(path), **fact})
                    if fact['level'] == 'ERROR':
                        self.errors.append({'label': label, 'error': 'native ERROR', 'serializedLine': number})
                if fact['message'] in NATIVE_STAGES or fact['message'] in {
                        'hover request-owned analysis released', 'hover query finished'}:
                    selected.append(fact)
        self.references[str(path)] = {'path': str(path), 'sha256': digest.hexdigest(), 'bytes': size}
        self.native_facts[label] = {'nativeLog': self.references[str(path)], 'selectedSerializedEvents': selected, 'allSerializedEvents': all_serialized}
        return selected

    def physical_attempt(self, command, member=None, mode=None):
        label = command['phase']
        # Preserve outer records before any validation so a failed second member
        # absent from the completed-pair ledger cannot disappear from the report.
        retained = {'label': label, 'outerOutcome': command, 'pairedMemberPresent': member is not None}
        self.attempts.append(retained)
        if member is not None:
            retained['allTypedResults'] = member.get('raw', {}).get('results', [])
            retained['allSettledSamples'] = member.get('raw', {}).get('idleMemory', {}).get('samples', [])
        self.bind_command(self.directory, command, require_success=False)
        stderr = self.data(self.directory / label / 'stderr.log').decode()
        paths = re.findall(r'run artifacts: (.+)', stderr)
        IdleProof.require(len(paths) == 1, 'ambiguous or missing inner runtime artifacts: ' + label)
        inner = self.owned(paths[0])
        retained['innerRun'] = str(inner)
        summary = self.read(inner / 'summary.json')
        metadata = self.read(inner / 'metadata.json')
        results = summary.get('results', [])
        retained['allInnerOutcomes'] = results
        retained['innerProcessCleanup'] = summary.get('processCleanup')
        wire_path = inner / 'run-001/lsp-transport.json'
        wire = self.read(wire_path) if wire_path.exists() else None
        retained['wireReference'] = self.references.get(str(wire_path))
        if wire is not None:
            retained['allTransport'] = wire.get('transport', [])
            retained['nonSuccessTransport'] = [r for r in wire.get('transport', []) if r.get('status') != 'success']
        native = self.native(label, inner)
        IdleProof.require(IdleProof.success(command) and IdleProof.empty(command), 'outer runtime failed/cleanup unverified')
        IdleProof.require(len(results) == 1 and not summary.get('build') and not summary.get('warmups'), 'unexpected inner build/warmup/runtime count')
        runtime = results[0]
        process = self.read(inner / 'run-001/process.json')
        IdleProof.require(process['command'] == runtime['command'] and process['args'] == runtime['args']
            and process['pid'] == runtime['pid'], 'inner physical process differs')
        IdleProof.require(IdleProof.success(runtime) and IdleProof.empty(runtime)
            and summary.get('status') == 'completed' and summary.get('exitCode') == 0
            and summary['processCleanup'] == {'status': 'verified', 'runs': 1, 'verifiedRuns': 1},
            'inner runtime unsuccessful or cleanup incomplete')
        if member is None:
            raise ValueError('unpaired physical attempt is retained but cannot supply acceptance')
        expected_plan = self.expected_plan(mode)
        plan_path = self.directory / (label + '-plan.json')
        IdleProof.require(self.read(plan_path) == member['plan'] == expected_plan, 'pair/on-disk plan differs from original frozen generated workload')
        binary = member['binary']
        digest = BASELINE if label.endswith('-baseline') else self.binary
        IdleProof.require(member['binarySha256'] == digest, 'member binary digest differs')
        self.ref(binary, digest)
        common = str(OWNED / 'build/x86_64-unknown-linux-gnu/release/suprnova-lsp')
        expected_binary = common if label.endswith('-candidate') else str(self.selected('baselineManifest')['retainedBinary'])
        IdleProof.require(binary == expected_binary, 'original idle actual member binary path differs')
        IdleProof.runtime_argv(command, runtime, plan_path, binary, common)
        runner = metadata['runner']
        IdleProof.require(runner['logFilter'] == LOG and runner['repeat'] == 1 and runner['warmup'] == 0
            and runner['measure'] is True and runner['timeoutMs'] == 300000, 'managed sampler runner conditions differ')
        raw = member['raw']
        retained['allTypedResults'] = raw.get('results', [])
        retained['allSettledSamples'] = raw.get('idleMemory', {}).get('samples', [])
        for path in (self.directory / label / 'stdout.log', inner / 'run-001/stdout.log'):
            IdleProof.require(self.stdout_raw(self.data(path)) == raw, 'stdout copy differs from stored raw: ' + str(path))
        IdleProof.require(str((ROOT / raw['binary']).resolve()) == binary, 'raw actual native executable differs')
        identity = IdleProof.typed_wire(raw, wire, expected_plan)
        retained['freshSessionIdentity'] = {'innerRun': str(inner), 'supervisorPid': process['pid'],
                                          'supervisorStartedAt': process['startedAt'], **identity}
        retained['nativeGroups'] = IdleProof.native_binding(raw, native)
        IdleProof.require(self.core is not None, 'unchanged settled RSS core could not load')
        # Baseline is the compiled 893 archive already authenticated above. Its actual
        # schema has worker-generation logs but no modern selected lifecycle stages.
        # Current candidate keeps every unchanged modern core guard.
        assessed = (self.historical_core.assess(raw, str(APP), mode, self.native_facts[label]['allSerializedEvents'])
                    if digest == BASELINE else self.core.assess(raw, str(APP), mode))
        IdleProof.require(member['summary'].get('idleRssBytes') == assessed['rssBytes'], 'stored member RSS samples differ from independently checked raw')
        retained['settledCore'] = assessed
        retained['sampledProcessOwnership'] = {'basis': 'genuine execution of SHA-pinned native descendant RSS sampler',
            'samplerSourceSha256': PINS['lsp-query.py'], 'ownedRssSourceLines': [798, 829],
            'idleMemorySourceLines': [843, 857], 'metric': 'sum-of-process-RSS',
            'semantics': 'Starts at actual child server PID, follows every task/children, excludes non-LSP descendants when settled and requires five samples with stable membership.',
            'independentRuntimeAncestrySnapshot': None,
            'limitation': 'Original IdlePairs does not record independent per-sample argv/start-time/ancestry; this is distinct from the source-bound sampler ownership metric.'}
        self.members.append({'label': label, 'binarySha256': digest, 'medianRssBytes': assessed['medianRssBytes'],
            'serverEngineIndexingPeakRssBytes': assessed['serverEngineIndexingPeakRssBytes'],
            'compilerPeakRssBytes': None, 'rssBytes': assessed['rssBytes'], 'freshSessionIdentity': retained['freshSessionIdentity']})
        return assessed

    def preserve_partial_attempt(self, label):
        """Retain wire/native records even when argv or outcome validation stopped."""
        stderr_path = self.directory / label / 'stderr.log'
        if not stderr_path.exists():
            return
        stderr = self.data(stderr_path).decode(errors='replace')
        paths = re.findall(r'run artifacts: (.+)', stderr)
        if len(paths) != 1:
            return
        inner = self.owned(paths[0])
        retained = next((a for a in self.attempts if a['label'] == label), None)
        if retained is None:
            retained = {'label': label, 'partialAttempt': True}
            self.attempts.append(retained)
        retained['partialInnerRun'] = str(inner)
        for name in ('summary.json', 'metadata.json', 'run-001/process.json', 'run-001/stdout.log', 'run-001/stderr.log'):
            path = inner / name
            if path.exists():
                self.ref(path)
        for name in ('stdout.log',):
            path = self.directory / label / name
            if path.exists():
                try:
                    partial = self.stdout_raw(self.data(path))
                    retained['partialAllTypedResults'] = partial.get('results', [])
                    retained['partialAllSettledSamples'] = partial.get('idleMemory', {}).get('samples', [])
                    retained['partialStdoutRawReference'] = self.references[str(path)]
                except ERRORS as error:
                    retained['partialStdoutDecodeLimitation'] = str(error)
        wire_path = inner / 'run-001/lsp-transport.json'
        if wire_path.exists():
            wire = self.read(wire_path)
            retained['wireReference'] = self.references[str(wire_path)]
            retained['allTransport'] = wire.get('transport', [])
            retained['nonSuccessTransport'] = [r for r in wire.get('transport', []) if r.get('status') != 'success']
            retained['partialWireLifecycleAndStagesReference'] = self.references[str(wire_path)]
        if label not in self.native_facts and (inner / 'run-001/lsp-server.stderr.log').exists():
            self.native(label, inner)

    def run(self):
        # Pin inputs at the beginning and end of the AUDIT. These checks cannot
        # substitute for six contemporaneous pins surrounding the NATIVE run.
        self.initial_pins = {name: self.sha(ROOT / 'tools' / name) for name in PINS}
        self.check('frozen-auditor-entry', lambda: IdleProof.require(self.initial_pins == PINS, 'frozen helper changed before audit'))
        report = self.check('original-report', lambda: self.read(self.directory / 'report.json', self.selection['sha256']))
        if report is None:
            report = {'reports': {}, 'commands': []}
        baseline = self.check('baseline-origin', lambda: self.baseline_origin(report))
        candidate = self.check('candidate-origin', lambda: self.candidate_origin(report))
        self.check('fresh-binding', self.fresh_binding)
        self.check('fresh-provenance', lambda: self.provenance(report))
        cleanup = self.check('outer-command-cleanup', lambda: IdleProof.commands(report))
        commands = report.get('commands', [])
        expected_preparation = ['metadata', 'build-compiler', 'producer-compiler', 'producer-cfg', 'producer-sysroot', 'commit', 'runtime-inputs']
        self.check('preparation-order', lambda: IdleProof.require([c.get('phase') for c in commands[:7]] == expected_preparation, 'original seven preparation commands missing/reordered'))
        for command in commands[:7]:
            self.check(command.get('phase', 'unnamed'), lambda c=command: self.bind_command(self.directory, c))
        physical = [c for c in commands if str(c.get('phase', '')).startswith('idle-')]
        physical_labels = [c['phase'] for c in physical]
        self.check('all-physical-command-kinds', lambda: IdleProof.require(len(commands) == len(physical) + 7, 'unexpected extra physical command outside original preparation/member workload'))
        reports = report.get('reports', {})
        self.check('pair-mode-layout', lambda: IdleProof.pair_layout(reports, physical_labels))
        paired, summaries = {}, {}
        for mode in MODES:
            ledger = self.check(mode + '-disk-pairs', lambda m=mode: self.read(self.directory / (m + '-idle-pairs.json')))
            stored = reports.get(mode, {})
            self.check(mode + '-disk-copy', lambda l=ledger, s=stored: IdleProof.require(l == s.get('pairs'), 'completed-pair ledger differs from outer report'))
            if not isinstance(ledger, list):
                self.errors.append({'label': mode, 'error': 'on-disk pairs are not an array'})
                continue
            for n, pair in enumerate(ledger):
                if not isinstance(pair, dict):
                    self.errors.append({'label': mode, 'pair': n, 'error': 'on-disk pair is not an object'})
                    continue
                for kind, member in pair.items():
                    label = f'idle-{mode}-{n}-{kind}'
                    paired[label] = (member, mode, n, kind)
        assessed = {mode: {} for mode in MODES}
        for command in physical:
            label = command['phase']
            selected = paired.get(label)
            if selected is None:
                self.check(label, lambda c=command: self.physical_attempt(c))
            else:
                member, mode, n, kind = selected
                result = self.check(label, lambda c=command, m=member, md=mode: self.physical_attempt(c, m, md))
                if result is not None:
                    assessed[mode][(n, kind)] = result
            self.check(label + '-partial-preservation', lambda l=label: self.preserve_partial_attempt(l))
        for label in set(paired) - set(physical_labels):
            self.errors.append({'label': label, 'error': 'paired member has no physical command'})
        # A supervisor failure may occur before command() appends its result.
        # Enumerate direct attempt directories too; never recurse through caches.
        recorded = set(physical_labels)
        for directory in sorted(self.directory.glob('idle-*')):
            if directory.is_dir() and directory.name not in recorded:
                retained = {'label': directory.name, 'pairedMemberPresent': directory.name in paired,
                            'unrecordedPhysicalAttempt': True}
                self.attempts.append(retained)
                for name in ('process.json', 'stdout.log', 'stderr.log'):
                    self.check(directory.name + '/' + name, lambda p=directory / name: self.ref(p))
                self.errors.append({'label': directory.name, 'error': 'physical attempt missing from outer ledger; retain artifacts'})
                self.check(directory.name + '-partial-preservation', lambda l=directory.name: self.preserve_partial_attempt(l))
        for mode in MODES:
            if len(assessed[mode]) == 6:
                summary = IdleProof.pair_summary(assessed[mode])
                stored = reports[mode]['summary']
                self.check(mode + '-summary', lambda s=stored, a=summary: IdleProof.require(s['pairMediansBytes'] == a['pairMediansBytes']
                    and type(s['medianDeltaBytes']) is int and s['medianDeltaBytes'] == a['medianDeltaBytes'], 'stored session medians/pair summary differ'))
                summaries[mode] = summary
            else:
                self.errors.append({'label': mode, 'error': 'incomplete/invalid matched pairs; no acceptance summary'})
        identities = [(m['freshSessionIdentity']['innerRun'], m['freshSessionIdentity']['supervisorPid'],
                       m['freshSessionIdentity']['supervisorStartedAt'], m['freshSessionIdentity']['initializeReceivedNs']) for m in self.members]
        self.check('fresh-12-members', lambda: IdleProof.require(len(self.members) == len(set(identities)) == 12
            and len({i[0] for i in identities}) == 12, 'requires twelve distinct fresh managed inner sessions'))
        final_pins = {name: self.sha(ROOT / 'tools' / name) for name in PINS}
        self.check('frozen-auditor-exit', lambda: IdleProof.require(final_pins == self.initial_pins == PINS, 'frozen helper changed during audit'))
        return {'run': str(self.directory), 'baselineOrigin': baseline, 'candidateOrigin': candidate,
            'currentApplicability': self.current_applicability, 'outerCleanup': cleanup,
            'physicalAttemptCount': len(self.attempts), 'validatedMembers': len(self.members),
            'validatedTypedHoverCount': 3 * len(self.members), 'validatedIdleSampleCount': 5 * len(self.members),
            'modeSummaries': summaries, 'allPhysicalAttempts': self.attempts,
            'allNativeEvidence': self.native_facts, 'references': list(self.references.values()),
            'errors': self.errors, 'nativeWarnings': self.warnings,
            'samplerSourceProof': {'path': str(ROOT / 'tools/lsp-query.py'), 'sha256': PINS['lsp-query.py'],
                'sourceLines': {'nativeServerStart': [780, 792], 'ownedRss': [798, 829], 'settledSamples': [843, 857]},
                'engineSpawnSource': 'crates/lsp/server/src/engine_process.rs:160-190'},
            'rssMetric': 'sum-of-process-RSS; shared pages are counted per process'}



class IdleEvidenceError(ValueError):
    """Keep every physical attempt and failure available to the mechanism caller."""

    def __init__(self, message, observations):
        super().__init__(message)
        self.observations = observations


class IdleEvidence:
    @staticmethod
    def assess(evidence):
        path, _ = evidence.bound('idle')
        IdleProof.require(path.name == 'report.json', 'idle selector must name the original driver report.json')
        manifest = json.loads(evidence.manifest.read_text())
        selection = manifest['idle']
        assessment = IdleAssessment(path.parent, selection, evidence)
        observations = assessment.run()
        if observations['errors']:
            raise IdleEvidenceError('idle raw evidence failed: ' + json.dumps(observations['errors']), observations)
        try:
            retained = assessment.source_review()
            historical = assessment.historical_source_review()
            launch = assessment.capture_proof()
            producer = assessment.producer_proof()
            if observations['nativeWarnings']:
                raise ValueError('unexpected native warnings require investigation: ' + json.dumps(observations['nativeWarnings']))
            increases = IdleProof.increases(observations['modeSummaries'])
        except (ValueError, KeyError, OSError, TypeError, StopIteration, subprocess.SubprocessError) as error:
            observations['errors'].append({'label': 'source-provenance-integration', 'error': str(error)})
            raise IdleEvidenceError(str(error), observations) from error
        # No old summary or draft gap-string is a verdict input. Each named gap
        # above has a distinct re-evaluated proof; missing historical fields stay
        # explicit in those proofs rather than being filled with modern values.
        return {'report': str(path), 'modes': observations['modeSummaries'],
            'physicalAttempts': observations['physicalAttemptCount'], 'freshMembers': observations['validatedMembers'],
            'typedReplies': observations['validatedTypedHoverCount'], 'idleSamples': observations['validatedIdleSampleCount'],
            'cleanup': observations['outerCleanup'], 'retainedOwnerProof': retained,
            'historicalSourceProof': historical, 'launchProof': launch, 'producerProof': producer,
            'repeatableIncreases': increases, 'numericalMemoryAllowance': None,
            'compilerPeakRssBytes': None, 'rssMetric': observations['rssMetric'],
            'allPhysicalAttempts': observations['allPhysicalAttempts'],
            'references': list(assessment.references.values()),
            'ownershipBasis': observations['samplerSourceProof'],
            'independentNativeAncestrySnapshots': None}
