"""Re-evaluate selected raw RSP-005 policy evidence without running its collector.

Historical collector sources are hashed provenance only. The gate also needs the
current three native error cases and the reviewed exact ordinary-query policy.
"""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
OWNED = ROOT / "target/agent-debug"
BOUND = 512 * 1024 * 1024
MODES = ("faster-builds", "lower-peak-memory")
SCENARIOS = ("lifecycle", "missing-producer", "artifact-failure", "prepared")
CONTEXT = ROOT / "crates/lsp/server/src/engine_client/mod.rs"
HANDLERS = {
    'hover': ROOT / 'crates/lsp/server/src/methods/text_document/hover.rs',
    'completion': ROOT / 'crates/lsp/server/src/methods/text_document/completion/mod.rs',
    'inlay_hint': ROOT / 'crates/lsp/server/src/methods/text_document/inlay_hint.rs',
}
REBUILD = {"initialize", "reindex_workspace", "did_save", "external_project_changes", "publish_rustdoc", "rustdoc_requested"}
ENVIRONMENT_MUST_BE_UNSET = ("CARGO_BUILD_BUILD_DIR", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS",
    "CARGO_BUILD_RUSTFLAGS", "CARGO_BUILD_TARGET", "RUSTDOCFLAGS", "CARGO_ENCODED_RUSTDOCFLAGS",
    "RUSTC", "RUSTDOC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER")
PINS = {
    "lsp-query.py": "67f3701804c25b8d8f556cd37e17d9beb3ee26364482e0c9d34f1f9bd93a1695",
    "sudus-responsiveness.py": "a759bcb20edfb3789e1086a1d6ca75a6eed0c3a6f2f9260050b353c8be2d45f4",
    "agent-debug.py": "ee7cc045c60c43dfd0601e6b945d95106c959c94ab84538b72805245b92e1ade",
    "sudus-editor-import.py": "b4da2f4b49c09f9625ea7c2d2185430803fdd0e0a1e61b58a8ff58178303e11d",
    "sudus-suprnova-user.py": "dfccc143ae04f789cbe033a8a6223b787d786f07aa791a3a21e602c0b30f4107",
    "responsiveness-cargo-cache.py": "c2561856c4a2d50227df870d2ec177d88de5e93fcd64adcdfb4c9cd85b3de60a",
}
COLLECTOR_STATES = {
    'e97400b628526f3c6cb5567742565ccf3e2c0ce5fb6726928f00bcf9e10443f3': 'pending',
    '7264c678b1c7cd8c7950601350e1c71f590925b6c597889ca3e482516184d362': 'running',
}


class PolicyEvidenceError(ValueError):
    """Keep failed physical observations available to the requirement emitter."""

    def __init__(self, message, observations):
        super().__init__(message)
        self.observations = observations


def require(ok,message):
    if not ok:raise ValueError(message)


def owned(path):
    p=Path(path).resolve(strict=True)
    require(p.is_relative_to(OWNED.resolve()),'artifact escaped owned root')
    return p


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda:stream.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def read(path):
    p=owned(path);require(p.stat().st_size<=BOUND,'new JSON exceeds512MiB')
    return json.loads(p.read_bytes())


def rust_function(source,name):
    """Focused balanced-body extraction, not a Rust compiler or generic parser."""
    match=re.search(r'\bfn\s+'+re.escape(name)+r'\s*[<(]',source)
    require(match is not None,'policy function missing: '+name)
    start=source.index('{',match.end());depth=0;quote=False;escape=False
    for n in range(start,len(source)):
        ch=source[n]
        if quote:
            if escape:escape=False
            elif ch=='\\':escape=True
            elif ch=='"':quote=False
            continue
        if ch=='"':quote=True
        elif ch=='{':depth+=1
        elif ch=='}':
            depth-=1
            if depth==0:return source[match.start():start],source[start:n+1]
    raise ValueError('unterminated policy function: '+name)


def source_policy(source):
    # Examine violations before identity equality so mutated-source controls prove the reason.
    clean=re.sub(r'//[^\n]*','',source)
    values=re.findall(r'const\s+ANALYSIS_QUERY_RPC_DEADLINE\s*:\s*Duration\s*=\s*Duration::from_secs\((\d+)\)\s*;',clean)
    require(values==['30'],'ordinary analysis deadline is not exactly30seconds')
    sig,query=rust_function(clean,'query')
    context_sig,context=rust_function(clean,'context')
    _,classify=rust_function(clean,'operation_may_rebuild_analysis')
    require(re.findall(r'"([a-z_]+)"',classify)==['initialize','reindex_workspace','did_save','external_project_changes','publish_rustdoc','rustdoc_requested'],'rebuild classification changed or includes ordinary query')
    require('FnOnce(EngineServiceClient, tarpc::context::Context)' in sig,'query callback no longer FnOnce')
    require(len(re.findall(r'\brequest\s*\(',query))==1,'query invokes request more than once')
    code_only=re.sub(r'"(?:\\.|[^"\\])*"','""',query)
    require(not re.search(r'\b(loop|while|for)\b',code_only),'query acquired an automatic retry loop')
    require(len(re.findall(r'\.await\b',query))==1,'query awaits another invocation')
    require('Self::context(operation, true)' in query,'ordinary query no longer uses query context')
    normalized=lambda text:re.sub(r'\s+','',text)
    expected='''{
        let mut context = tarpc::context::current();
        if Self::operation_may_rebuild_analysis(operation) {
            context.deadline = Instant::now() + PROJECT_UPDATE_RPC_DEADLINE;
        } else if is_query {
            context.deadline = Instant::now() + ANALYSIS_QUERY_RPC_DEADLINE;
        }
        context
    }'''
    require(normalized(context)==normalized(expected),'query context override/classification is not the reviewed30s path')
    require('Ok(result) => result' in query and 'Err(QueryError::Internal(EngineError::from(error)))' in query,'typed results or actionable transport errors changed')
    return {'evidenceKind':'source-bound policy; NOT compiled exact-deadline native test',
            'deadlineSeconds':30,'requestCallsInQuery':1,'fnOnce':True,
            'queryFunctionSha256':hashlib.sha256(query.encode()).hexdigest(),
            'contextFunctionSha256':hashlib.sha256(context.encode()).hexdigest(),
            'rebuildOperations':sorted(REBUILD),'ordinaryExamples':['hover','completion','inlay_hint'],
            'limitation':'Focused source reader rejects unfamiliar structure. It does not prove all callers/RPC behavior or replace native error semantics.'}


def handler_policy(sources):
    facts = {}
    for operation, path in HANDLERS.items():
        source = re.sub(r'//[^\n]*', '', sources[operation])
        _, body = rust_function(source, operation)
        require(len(re.findall(r'\.query\s*\(', body)) == 1
                and re.search(r'\.query\s*\(\s*"' + operation + r'"\s*,', body),
                'ordinary handler does not use the reviewed query operation: ' + operation)
        require(re.search(r'engine_client\s*\.\s*' + operation + r'\s*\(\s*request_context\s*,', body),
                'ordinary handler invokes a different native method: ' + operation)
        require(not re.search(r'\.(rustdoc_requested|publish_rustdoc|call_project_update|call_unconditional)\s*\(', body),
                'ordinary handler acquired a direct producer/update invocation')
        finish = {'hover': 'finish_global_operation', 'completion': 'finish_attempt', 'inlay_hint': 'finish_target_query'}[operation]
        require(finish + '(result)' in body, 'ordinary handler bypasses typed scoped result: ' + operation)
        facts[operation] = {'path': str(path), 'sha256': hashlib.sha256(sources[operation].encode()).hexdigest(),
                            'engineOperation': operation, 'queryCallsPerSemanticAttempt': 1}
    completion = sources['completion']
    require('CompletionAttemptOutcome::DocumentAdvanced' in completion
            and 'Err(QueryError::EditorChanged)' in completion
            and 'Err(error) => return Err(methods::into_lsp_error(error))' in completion,
            'completion recapture/error policy differs from the reviewed source')
    return {'handlers': facts, 'externalQueryMethods': ['textDocument/hover', 'textDocument/completion'],
            'inlayEvidence': 'source route and shared native/policy controls; no inlay request in this eight-scenario exec trace',
            'completionRecapture': 'Existing logical completion follows document advancement; FnOnce/no-transport-retry proof applies per semantic invocation.'}


def trace_rows(path):
    """Reconstruct per-PID split exec calls, retaining failed attempts and both clocks."""
    pending = {}
    with Path(path).open(errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            if "execve" not in line:
                continue
            match = re.fullmatch(r"(?:\[pid\s+(\d+)\]|(\d+))\s+(\d+\.\d+)\s+(.*)\n?", line)
            require(match is not None, "unrecognized timestamp/PID exec trace at " + str(line_number))
            pid, clock, body = int(match[1] or match[2]), match[3], match[4].rstrip()
            seconds, fraction = clock.split(".")
            require(len(fraction) <= 9, "exec clock precision exceeds nanoseconds")
            wall = int(seconds) * 1_000_000_000 + int(fraction.ljust(9, "0"))
            resume = re.match(r"<\.\.\. (execve|execveat) resumed>(.*)", body)
            if resume:
                require(pid in pending, "orphan resumed exec at " + str(line_number))
                start_line, start_wall, call, prefix = pending.pop(pid)
                require(call == resume[1] and wall >= start_wall, "mismatched or backward resumed exec")
                body = prefix + resume[2]
            else:
                require(pid not in pending, "another exec before prior resumed record")
                call = re.match(r"(execve|execveat)\(", body)
                require(call is not None, "unrecognized exec call at " + str(line_number))
                start_line, start_wall = line_number, wall
                if body.endswith("<unfinished ...>"):
                    pending[pid] = (line_number, wall, call[1], body[:-len("<unfinished ...>")])
                    continue
            require("<unfinished ...>" not in body and "resumed>" not in body,
                    "unparsed split exec record")
            result = re.search(r"\)\s+=\s+(-?\d+)(?:\s+[A-Z][A-Z0-9_]*(?:\s+\(.*\))?)?$", body)
            require(result is not None and int(result[1]) in (0, -1), "missing/invalid exec outcome")
            require(not re.search(r'"\s*\.\.\.', body), "truncated exec path/argv")
            strings = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
            require(strings, "exec trace lacks path/argv")
            args = [json.loads('"' + s + '"') for s in strings]
            yield {"serializedLine": start_line, "endSerializedLine": line_number,
                   "pid": pid, "wallNs": start_wall, "endWallNs": wall,
                   "strings": args, "success": int(result[1]) == 0, "outcome": int(result[1]),
                   "raw": body}
    require(not pending, "unfinished exec records at EOF: " + str(sorted(pending)))


def process_parents(path):
    """Use successful traced fork/clone returns, including resumed calls, as ancestry."""
    parents, pending = {}, {}
    with Path(path).open(errors='strict') as stream:
        for number, line in enumerate(stream, 1):
            match = re.fullmatch(r'(?:\[pid\s+(\d+)\]|(\d+))\s+\d+\.\d+\s+(.*)\n?', line)
            if not match:
                require(not re.search(r'\b(clone3?|v?fork)\(', line), 'unrecognized process trace')
                continue
            pid, body = int(match[1] or match[2]), match[3].rstrip()
            resume = re.match(r'<\.\.\. (clone3?|v?fork) resumed>(.*)', body)
            if resume:
                require(pid in pending and pending[pid][0] == resume[1], 'orphan/mismatched process resume')
                _, prefix = pending.pop(pid)
                body = prefix + resume[2]
            else:
                call = re.match(r'(clone3?|v?fork)\(', body)
                if not call:
                    continue
                require(pid not in pending, 'process call before prior resume')
                if body.endswith('<unfinished ...>'):
                    pending[pid] = (call[1], body[:-len('<unfinished ...>')])
                    continue
            result = re.search(r'\)\s+=\s+(-?\d+)(?:\s+[A-Z][A-Z0-9_]*(?:\s+\(.*\))?)?$', body)
            require(result is not None, 'unknown process creation outcome at ' + str(number))
            child = int(result[1])
            require(child == -1 or child > 0, 'invalid process creation result')
            if child > 0:
                require(child not in parents or parents[child] == pid, 'conflicting traced process ancestry')
                parents[child] = pid
    require(not pending, 'unfinished process creation at EOF')
    return parents


def typed_queries(report,lsp):
    raw=report['rawQueries'];requirements=report['policy']['requirements']
    ids=[r['id'] for r in raw]
    require(len(ids)==len(set(ids)) and all(type(i)is int for i in ids),'duplicate or invalid raw query id')
    require([r['id'] for r in requirements]==ids,'missing/orphan raw query or expectation')
    retained=[]
    for row,expect in zip(raw,requirements):
        require(row['sent'] is True and row.get('response') is not None,'failed/pending query retained; cannot pass')
        require(type(row.get('sendCount')) is int and row['sendCount']==1,'query has missing or extra writes')
        require(row['request']['id']==row['id'],'request id differs from raw query id')
        require(len(row['responses'])==1 and row['response']['id']==row['id'] and row['response']==row['responses'][0],'duplicate/substituted raw response')
        require(type(row['sendCalledNs'])is int and row['receivedNs']>=row['writtenNs']>=row['sendCalledNs'] and row['durationNs']==row['receivedNs']-row['writtenNs'],'invalid transport ordering')
        require(row['receivedWallNs']>=row['sendWallNs'],'wall-clock boundary moved backward; trace attribution unknown')
        response=row['response'];method=row['request']['method']
        require(expect['phase']==row['phase'] and expect['clientTimeoutMs']==30000,'phase/deadline expectation differs')
        require(expect['params']==row['request']['params'] and re.fullmatch(r'[0-9a-f]{64}',expect['documentTextSha256']),'wrong actual document/position/source expectation')
        if expect['kind']=='closed-document-error':
            require(method=='textDocument/hover' and 'result' not in response and response.get('error')=={'code':-32801,'message':'the request targets a closed document session'},'actionable closed-session error replaced or changed')
            check_closed_error(report,row,expect)
        elif expect['kind']=='hover':
            require('error' not in response and 'result' in response,'real query error retained; expected typed API unavailable')
            require(method=='textDocument/hover' and expect['contains'],'unknown hover semantic expectation')
            require(expect['contains'] in (lsp.hover_text(response['result']) or ''),'typed result replaced by null/empty/wrong owner')
        elif expect['kind']=='completion':
            require('error' not in response and 'result' in response,'real query error retained; expected typed API unavailable')
            require(method=='textDocument/completion','wrong typed method')
            actual=lsp.normalize_completions(response['result'],1000)
            require(not actual['isIncomplete'] and not actual['truncated'],'incomplete completion evidence')
            names=[i['label'].split('(')[0].split('<')[0].strip() for i in actual['items']]
            require(not any(name in names for name in expect['absent']),'generated facts leaked to unrelated owner')
        else:raise ValueError('unexpected policy query kind')
        retained.append(row)
    require(raw,'no actual query observed')
    transports=[t for t in report['frozenTransport'] if t['method'] in {'textDocument/hover','textDocument/completion','textDocument/inlayHint'}]
    require([t['id'] for t in transports]==ids,'sent observer ledger contains dropped/orphan query')
    for t,r in zip(transports,retained):
        is_error='error' in r['response']
        require(t['method']==r['request']['method'] and t['status']==('rpc-error' if is_error else 'success') and all(t[k]==r[k] for k in ('writtenNs','receivedNs','durationNs')),'raw reply and frozen wire ledger disagree')
        require(not is_error or t.get('errorCode')==-32801,'frozen error code disagrees')
    return retained


def boundaries(report):
    rows=report['policy']['boundaries']
    require(len({r['label'] for r in rows})==len(rows),'duplicate policy boundary')
    return {r['label']:r for r in rows}


def check_closed_error(report,row,expect):
    require(report['plan']['scenario']=='prepared' and row['phase']=='prepared-closed-document-error','closed-error expectation used outside prepared scenario')
    require(len([q for q in report['rawQueries'] if q['phase']=='prepared-closed-document-error'])==1,'closed query missing or repeated')
    b=boundaries(report)
    require(all(k in b for k in ('prepared-document-closed','prepared-closed-error-received','prepared-document-reopened')),'missing close/error/reopen boundary')
    close,received,reopen=(b[k] for k in ('prepared-document-closed','prepared-closed-error-received','prepared-document-reopened'))
    path=str(Path(report['plan']['root'])/'src/main.rs');uri=Path(path).as_uri()
    require(close['path']==reopen['path']==path and row['request']['params']=={'textDocument':{'uri':uri},'position':{'line':0,'character':0}},'closed query path/position differs')
    require(received['requestId']==row['id'] and close['monotonicNs']<=row['sendCalledNs']<=row['receivedNs']<=received['monotonicNs']<=reopen['monotonicNs'],'closed query outside close/reopen envelope')
    writes=report['clientWrites']
    require(all(type(k['clientWriteIndex']) is int and 0<=k['clientWriteIndex']<len(writes) for k in (close,reopen)) and close['clientWriteIndex']<reopen['clientWriteIndex'],'invalid close/reopen write references')
    close_write,reopen_write=(writes[k['clientWriteIndex']] for k in (close,reopen))
    require(close_write['sent'] is True and close_write['message']=={'jsonrpc':'2.0','method':'textDocument/didClose','params':{'textDocument':{'uri':uri}}} and close_write['sendCalledNs']<=close_write['writtenNs']<=close['monotonicNs'],'actual didClose write missing or late')
    document=reopen_write['message'].get('params',{}).get('textDocument',{})
    require(reopen_write['sent'] is True and reopen_write['message'].get('method')=='textDocument/didOpen' and document.get('uri')==uri and received['monotonicNs']<=reopen_write['sendCalledNs']<=reopen_write['writtenNs']<=reopen['monotonicNs'],'actual didOpen write missing or premature')
    require(type(document.get('text')) is str and hashlib.sha256(document['text'].encode()).hexdigest()==expect['documentTextSha256'],'reopened document text differs')


def check_raw_stream(path,report):
    calls={q['id']:[] for q in report['rawQueries']};responses=[];writes=[];write_calls=[];workers=[]
    sends={q['id']:[] for q in report['rawQueries']}
    require(owned(path).stat().st_size<=BOUND,'raw transport stream exceeds512MiB')
    with owned(path).open() as stream:
        for line in stream:
            event=json.loads(line);kind=event['event'];row=event['row']
            if kind in ('send-called','sent','response'):
                require(row['id'] in calls,'orphan streamed query')
                calls[row['id']].append(kind)
                if kind=='response':responses.append(row)
                else:sends[row['id']].append(row)
            elif kind=='client-write-sent':writes.append(row)
            elif kind=='client-write-called':write_calls.append(row)
            elif kind=='worker-notification':workers.append(row)
            else:
                raise ValueError('failed or unrecognized raw transport event retained: '+kind)
    require(all(len(events)==3 and events[0]=='send-called' and sorted(events)==['response','send-called','sent'] for events in calls.values()),'missing/extra raw writes or replies')
    # A response can be recorded before send() returns. Its immutable clocks
    # must still match the canonical query; sent/sentNs may be added later.
    send_fields=('id','phase','request','sendCount','sendCalledNs','sendWallNs')
    response_fields=(*send_fields,'response','writtenNs','receivedNs','receivedWallNs','durationNs')
    require(len(responses)==len(report['rawQueries']) and all(all(s[k]==q[k] for k in response_fields) for s,q in zip(responses,report['rawQueries'])),'canonical query rows differ from independently retained response stream')
    require(all(all(all(s[k]==q[k] for k in send_fields) for s in sends[q['id']])
                for q in report['rawQueries']), 'canonical send clocks differ from independently retained send stream')
    require(writes==report['clientWrites'],'canonical client writes differ from retained stream')
    require(len(write_calls)==len(writes) and all(all(c[k]==w[k] for k in ('message','phase','sendCalledNs')) for c,w in zip(write_calls,writes)),'missing/extra auxiliary client writes')
    require(workers==report['workerMessages'],'actual worker notifications differ from retained stream')


def latest_failure(report,boundary):
    root=Path(report['plan']['root']);roots={str(root),root.as_uri()}
    rows=[w['message']['params'] for w in report['workerMessages'] if w['receivedNs']<=boundary['monotonicNs'] and w['message']['params'].get('workspaceRoot',w['message']['params'].get('root')) in roots]
    require(rows and all(type(r.get('generation')) is int and r['generation']>=0 for r in rows),'failed boundary lacks actual worker generation')
    generation=max(r['generation'] for r in rows)
    last=next(r for r in reversed(rows) if r['generation']==generation)
    require(last==boundary['failure'] and last['state']=='failed','failed boundary is not actual latest worker state')


def required_failure_queries(report, specifications, begin_ns, end_ns):
    # These requests are the collector's failed-state API and owner-isolation
    # probes. Require the plan independently so deleting a request and its
    # expectation together cannot turn an incomplete collection into success.
    phases = {specification[0] for specification in specifications}
    queries = [query for query in report['rawQueries'] if query['phase'] in phases]
    require(len(queries) == len(specifications), 'required failure query missing or duplicated')
    for query, (phase, kind, marker, semantics) in zip(queries, specifications):
        expectations = [row for row in report['policy']['requirements'] if row['id'] == query['id']]
        require(len(expectations) == 1, 'required failure query expectation missing or duplicated')
        expectation = expectations[0]
        require(query['phase'] == expectation['phase'] == phase
                and query['request']['method'] == 'textDocument/' + kind
                and expectation['kind'] == kind and expectation['marker'] == marker
                and expectation.get('contains' if kind == 'hover' else 'absent') == semantics
                and expectation['clientTimeoutMs'] == 30000
                and begin_ns <= query['sendCalledNs'] <= query['receivedNs'] <= end_ns,
                'required failure query kind, marker, semantics or boundary differs')
    return queries


def check_scenario(report, held_state='pending'):
    plan=report['plan'];cases=report['cases'];b=boundaries(report)
    require(report['fatal'] is None and cases and all(c['attempted'] and c['passed'] for c in cases.values()),'failed/unattempted scenario')
    events=report['evidence']['events']
    scenario=plan['scenario']
    require(report['policy']['queryRpcRetries']==0,'collector reported retries')
    if scenario=='lifecycle':
        require(set(cases)=={'held-query-cleanup','failed-recovery'},'lifecycle case selection differs')
        begin=b['held-queries-begin'];end=b['held-queries-end']
        require(begin['pending']['state']==held_state and end['pending']['state']==held_state
                and end['pending']['generation']==begin['pending']['generation'],'held generation/status differs')
        for boundary in (begin,end):
            latest_worker_state(report,boundary,'pending',held_state)
        require(begin['child']==end['child'] and begin['child']['pid']!=begin['child']['childPid'],
                'held parent/child identity changed')
        held=[q for q in report['rawQueries'] if q['phase'].startswith('held-pending-')]
        require({q['phase'] for q in held}=={'held-pending-published-api','held-pending-source-api','held-pending-owner-isolation'},'missing genuine held API/isolation query')
        require(all(begin['monotonicNs']<=q['sendCalledNs']<=q['receivedNs']<=end['monotonicNs'] for q in held),'held replies outside observed pending envelope')
        require(not [e for e in events if e['event']=='started' and begin['monotonicNs']<=e['monotonicNs']<=end['monotonicNs']],'ordinary held query triggered producer')
        latest_worker_state(report,b['held-recovered'],'current','current')
        require(b['held-recovered']['childAlive'] is False and b['held-recovered']['descendantAlive'] is False,'compiler descendants survived supersession')
        begin=b['failed-no-retry-begin'];end=b['failed-no-retry-end']
        require(end['monotonicNs']-begin['monotonicNs']>=(plan.get('debounceMs',2000)+1000)*1_000_000,'no-retry window too short')
        require(not [e for e in events if e['event']=='started' and begin['monotonicNs']<=e['monotonicNs']<=end['monotonicNs']],'real failed producer retried without trigger')
        failure=b['failed-queries-begin']['failure']
        require(failure['state']=='failed' and 'automatic_models' in failure.get('message',''),'failure lacks actionable target')
        latest_worker_state(report,b['failed-queries-begin'],'failure','failed')
        required_failure_queries(report, [
            ('failed-preserved-api', 'hover', 'automatic_post', 'Builder<Post>'),
            ('failed-preserved-api', 'completion', 'let automatic_column = post::Column::', []),
            ('failed-owner-isolation', 'completion', 'let automatic_unrelated = unrelated::', ['query', 'filter'])],
            b['failed-queries-begin']['monotonicNs'], b['failed-no-retry-begin']['monotonicNs'])
        failed_begin=b['failed-queries-begin']['monotonicNs'];recovery_begin=b['explicit-failure-recovery-save']['monotonicNs']
        require(not [e for e in events if e['event']=='started' and failed_begin<=e['monotonicNs']<recovery_begin],'failed ordinary queries started an untriggered producer')
        notifications=report['evidence']['notifications']
        require(not [e for e in notifications if e['params'].get('state')=='current' and b['explicit-failure-save-trigger']['monotonicNs']<=e['monotonicNs']<recovery_begin],'failed export falsely reported freshness')
        require(any(e['event']=='finished' and e.get('code')==42 for e in events),'no genuine code42 producer failure')
        latest_worker_state(report,b['failure-recovered'],'current','current')
        require(b['failure-recovered']['current']['state']=='current','no explicit recovery')
    elif scenario=='prepared':
        require(set(cases)=={'prepared'} and not events,'prepared queries launched automatic producer')
        require(plan.get('inputs'),'prepared owner lacks genuine bound export')
        require(len([q for q in report['rawQueries'] if q['phase']=='prepared-closed-document-error'])==1,'prepared real closed-document error absent')
    elif scenario=='missing-producer':
        require(set(cases)=={'missing-producer'},'missing producer case absent')
        latest_worker_state(report,b['missing-producer-source-complete'],'failure','failed')
        query = required_failure_queries(report, [
            ('missing-producer-source-api', 'hover', 'automatic_source', 'u64')],
            0, b['missing-producer-source-complete']['monotonicNs'])[0]
        # The source query must run after the real failed notification, rather
        # than merely finishing before a later failed-state summary.
        latest_worker_state(report, {'monotonicNs': query['sendCalledNs'],
            'failure': b['missing-producer-source-complete']['failure']}, 'failure', 'failed')
        message=b['missing-producer-source-complete']['failure'].get('message','').lower()
        require('toolchain' in message or 'producer' in message,'missing producer error lacks action context')
    elif scenario=='artifact-failure':
        require(set(cases)=={'artifact-recovery'},'artifact failure/recovery case absent')
        begin,end=b['artifact-failed-source-begin'],b['artifact-failed-source-end']
        failure=begin['failure'];recovery=b['artifact-recovery-complete']
        latest_failure(report,begin);latest_failure(report,end)
        require(failure['state']=='failed' and 'artifact' in failure.get('message','').lower() and end['failure']['state']=='failed' and end['failure']['generation']==failure['generation'],'artifact failure state/generation/context missing')
        require(begin['blockedIsFile'] is True and end['blockedIsFile'] is True and begin['blockedPath']==end['blockedPath']==str(Path(plan['artifactRoot'])/'blocked-parent') and begin['blockedSha256']==end['blockedSha256'] and re.fullmatch('[0-9a-f]{64}',begin['blockedSha256']),'blocked artifact changed before recovery')
        queries=[q for q in report['rawQueries'] if q['phase']=='artifact-failed-source-api']
        require(len(queries)==1 and begin['monotonicNs']<=queries[0]['sendCalledNs']<=queries[0]['receivedNs']<=end['monotonicNs'],'genuine failed-state source query missing or misplaced')
        required=[r for r in report['policy']['requirements'] if r['id']==queries[0]['id']]
        require(len(required)==1 and required[0]['kind']=='hover' and required[0]['marker']=='automatic_source' and required[0]['contains']=='u64' and required[0]['clientTimeoutMs']==30000,'artifact failure source expectation is not genuine u64')
        trigger=b['explicit-artifact-recovery-begin']
        require(end['monotonicNs']<=trigger['monotonicNs']<=recovery['monotonicNs'],'artifact recovery precedes failed-state query')
        require(not begin['events'] and not end['events'] and not [e for e in events if e['event']=='started' and e['monotonicNs']<trigger['monotonicNs']],'producer started before explicit artifact recovery')
        write=report['clientWrites'][recovery['reindexWriteIndex']]
        require(write['sent'] is True and write['message'].get('method')=='workspace/executeCommand' and write['message']['params']=={'command':'suprnova-lsp.internal.reindexWorkspace','arguments':[]} and trigger['monotonicNs']<=write['sendCalledNs']<=write['writtenNs']<=recovery['monotonicNs'],'actual explicit artifact reindex missing')
        latest_worker_state(report,recovery,'recovery','current')
        require(recovery['recovery']['state']=='current' and recovery['recovery']['generation']>failure['generation'],'explicit artifact recovery absent or stale')
        replies=[t for t in report['frozenTransport'] if t['id']==write['message']['id']]
        require(len(replies)==1 and replies[0]['method']=='workspace/executeCommand' and replies[0]['status']=='success' and trigger['monotonicNs']<=replies[0]['writtenNs']<=replies[0]['receivedNs']<=recovery['monotonicNs'],'explicit recovery command did not receive a real success')
    else:raise ValueError('unknown policy scenario')
    return {'scenario':scenario,'cases':list(cases),'queryCount':len(report['rawQueries'])}


def latest_worker_state(report, boundary, field, state):
    root = Path(report['plan']['root'])
    roots = {str(root), root.as_uri()}
    rows = [event['message']['params'] for event in report['workerMessages']
            if event['receivedNs'] <= boundary['monotonicNs']
            and event['message']['params'].get('workspaceRoot', event['message']['params'].get('root')) in roots]
    require(rows and all(type(row.get('generation')) is int and row['generation'] >= 0 for row in rows),
            'held boundary lacks actual worker generation')
    generation = max(row['generation'] for row in rows)
    latest = next(row for row in reversed(rows) if row['generation'] == generation)
    require(latest == boundary[field] and latest['state'] == state,
            'held boundary is not actual latest same-generation ' + state)


def check_exec(rows,report,binary,parents=None):
    require(any(r['success'] and r['strings'][0]==binary for r in rows),'actual candidate native exec not observed')
    require(not any(any(Path(s).name=='rust-analyzer' for s in r['strings']) for r in rows),'rust-analyzer exec attempt observed')
    starts=[e for e in report['evidence']['events'] if e['event']=='started']
    native_pids={r['pid'] for r in rows if r['success'] and r['strings'][0]==binary}
    for started in starts:
        require(any(r['pid']==started['pid'] and 'rustdoc' in r['strings'] and r['success'] for r in rows),'producer PID lacks actual exec proof')
        if parents is not None:
            ancestor, visited = started['pid'], set()
            while ancestor in parents and ancestor not in native_pids:
                require(ancestor not in visited, 'cyclic traced process ancestry')
                visited.add(ancestor)
                ancestor = parents[ancestor]
            require(ancestor in native_pids, 'producer lacks traced native process ancestry')
    # These query windows contain no newly triggered producer. Existing held children remain
    # identifiable separately; conservatively reject a new compiler exec inside any query.
    violations=[]
    for r in rows:
        if not any(Path(s).name in {'rustc','rustdoc'} or s=='rustdoc' for s in r['strings']):continue
        for q in report['rawQueries']:
            if r['wallNs']<=q['receivedWallNs'] and r.get('endWallNs',r['wallNs'])>=q['sendWallNs']:
                violations.append({'exec':r,'queryId':q['id']})
    require(not violations,'compiler exec inside ordinary query window')
    return {'execRows':len(rows),'successfulExecRows':sum(r['success'] for r in rows),'producerPids':[e['pid'] for e in starts]}


def native_stderr(report,work,artifacts,execs,binary):
    ref=report['nativeStderr'];path=owned(ref['path'])
    require(path==owned(work/'lsp-server.stderr.log'),'native stderr is not the managed client log')
    actual=sha(path);size=path.stat().st_size
    require(ref['sha256']==actual and type(ref['bytes']) is int and ref['bytes']==size and type(ref['recordedBytes']) is int and ref['recordedBytes']==size and size>0,'native stderr hash/bytes changed or empty')
    require(ref['truncated'] is False and ref['afterClose'] is True and type(ref['serverReturnCode']) is int and ref['serverReturnCode']==0,'native stderr incomplete, truncated or server failed')
    require(type(ref['serverPid']) is int and ref['serverPid']>0 and any(e['pid']==ref['serverPid'] and e['success'] and e['strings'][0]==binary for e in execs),'native stderr PID lacks actual candidate exec proof')
    require({'path':str(path),'sha256':actual} in artifacts,'native stderr omitted from immutable attempt artifacts')
    return ref


class PolicyEvidence:
    """A selected raw collection is evidence; its computed summary is not a gate."""

    @staticmethod
    def reference(ref, external=()):
        require(isinstance(ref, dict) and isinstance(ref.get('path'), str)
                and re.fullmatch('[0-9a-f]{64}', ref.get('sha256', '')), 'malformed SHA reference')
        path = Path(ref['path'])
        if not path.is_absolute():
            path = ROOT / path
        resolved = path.resolve(strict=True)
        allowed = {Path(p).absolute(): Path(p).resolve(strict=True) for p in external if Path(p).is_file()}
        require(resolved.is_relative_to(OWNED.resolve())
                or (path.absolute() in allowed and resolved == allowed[path.absolute()]),
                'external artifact is not an explicitly allowed producer/helper input')
        require(sha(resolved) == ref['sha256'], 'selected artifact changed: ' + str(path))
        if 'bytes' in ref:
            require(type(ref['bytes']) is int and ref['bytes'] == resolved.stat().st_size,
                    'artifact byte count differs')
        return resolved

    @staticmethod
    def empty(command):
        cleanup = command.get('cleanup', {})
        return cleanup.get('verifiedEmpty') is True and cleanup.get('remainingPids') == []

    @classmethod
    def successful(cls, command):
        return (type(command.get('code')) is int and command['code'] == 0
                and command.get('timedOut') is False and not command.get('signal')
                and not command.get('spawnError') and cls.empty(command))

    @classmethod
    def commands(cls, report, count):
        commands = report.get('commands', [])
        require(len(commands) == count and all(cls.successful(c) for c in commands),
                'failed command or incomplete owned-process cleanup')
        require(report.get('processCleanup') == {'status': 'verified', 'runs': count, 'verifiedRuns': count},
                'cleanup summary contradicts physical commands')
        return commands

    @staticmethod
    def physical_cleanup(path, outcome, observer, message):
        # Managed outcome dictionaries retain nulls; the frozen file writer
        # omits them. Every non-null cleanup fact still must match exactly.
        runner = observer.helpers.module('rsp_policy_cleanup_writer', ROOT / 'tools/agent-debug.py')
        require(read(owned(path)) == runner.without_none(outcome['cleanup']), message)

    @staticmethod
    def tracked_helpers():
        return tuple(ROOT / 'tools' / name for name in
                     ('automatic-rustdoc-probe.py', 'automatic-rustdoc-cargo.py', 'sudus-automatic-rustdoc.py'))

    @classmethod
    def native_binding(cls, manifest, observer):
        expected = manifest['binding']
        archive = read(cls.reference(expected['archive']))
        source, binary = expected['runtimeSourcesSha256'], expected['binarySha256']
        require(re.fullmatch('[0-9a-f]{64}', source) and re.fullmatch('[0-9a-f]{64}', binary),
                'invalid runtime/executable digest')
        executable = owned(archive['binary'])
        require(archive['runtimeSourcesSha256'] == source and archive['binarySha256'] == binary
                and sha(executable) == binary, 'actual archived binary/source differs')
        for field in ('compiledBuildIdentity', 'compiledBuildReport'):
            require(archive[field] == expected[field], 'compiled reference differs: ' + field)
            cls.reference(archive[field])
        identity = read(archive['compiledBuildIdentity']['path'])
        require(identity == {'runtimeSourcesSha256': source, 'binarySha256': binary, 'sourcesUnchanged': True},
                'compiled runtime identity changed')
        build = read(archive['compiledBuildReport']['path'])
        command = cls.commands(build, 1)[0]
        require(command['command'] == 'cargo'
                and {'build', '--release', '--locked', '--offline', 'suprnova-lsp'}.issubset(command['args']),
                'not a successful supervised locked/offline release build')
        for name, digest in PINS.items():
            require(sha(ROOT / 'tools' / name) == digest, 'frozen helper changed: ' + name)
        require(observer.Diagnostic.runtime_fingerprint() == source,
                'current source differs from compiled/observed native source')
        cls.reference(manifest['collector'])
        require(manifest['probeHelperSha256'] == sha(ROOT / 'tools/automatic-rustdoc-probe.py'),
                'recorded probe differs from current tracked helper')
        return {'archive': expected['archive'], 'compiledBuildIdentity': archive['compiledBuildIdentity'],
                'compiledBuildReport': archive['compiledBuildReport'], 'source': source,
                'binarySha256': binary, 'binary': str(executable), 'frozenObservers': PINS,
                'collector': manifest['collector'], 'probeHelperSha256': manifest['probeHelperSha256']}

    @classmethod
    def preparation(cls, parent, observer):
        before, after = parent['bindingBefore'], parent['bindingAfter']
        require(before == after, 'managed immutable before/after binding differs')
        require(before['environmentMustBeUnset'] == list(ENVIRONMENT_MUST_BE_UNSET),
                'recorded parent environment guard differs')
        ready = read(cls.reference(before['ready']))
        require(ready.get('schema') == 1 and ready.get('acceptance') is False
                and ready.get('bindingStage') == 'recorded before policy collection', 'wrong readiness stage')
        helpers = cls.tracked_helpers()
        for field in ('helper', 'collector', 'assessor', 'probe', 'producerProxy'):
            cls.reference(ready[field], helpers)
        cls.reference(before['wrapper'])
        manifest_path = cls.reference(ready['manifest'])
        prep_path = cls.reference(ready['preparationReport'])
        require(cls.reference(before['manifest']) == manifest_path
                and cls.reference(before['preparation']) == prep_path, 'parent references differ from readiness')
        manifest, prep = read(manifest_path), read(prep_path)
        require(manifest.get('schema') == 1 and manifest.get('acceptance') is False
                and manifest.get('bindingStage') == 'recorded before policy collection'
                and manifest['unsetInheritedEnvironment'] == list(ENVIRONMENT_MUST_BE_UNSET),
                'wrong prepared policy binding/environment')
        # The manifest stores a path; readiness adds the digest. Resolve that
        # path against the preparation file already validated through both refs.
        require(manifest['collector'] == ready['collector'] and manifest['assessor'] == ready['assessor']
                and manifest['preparationHelper'] == ready['helper']
                and isinstance(manifest['preparationReport'], str)
                and (ROOT / manifest['preparationReport']).resolve(strict=True) == prep_path,
                'prepared source references differ')
        require(prep['observationComplete'] is True and prep['acceptance'] is False and not prep['errors'],
                'preparation failed or incomplete')
        cls.commands(prep, 10)
        require(prep['bindingBefore'] == prep['bindingAfter'] == before['binding'],
                'preparation/native before/after binding differs')
        binding = cls.native_binding(manifest, observer)
        recorded = before['binding']
        require(recorded['native'] == binding and recorded['binding'] == manifest['binding']
                and ready['observationHead'] == manifest['observationHead'] == recorded['observationHead'],
                'recorded native/observation identity differs')
        # Commit identity is historical. Current applicability is the native source hash,
        # so adding a durable Python checker does not pretend the binary was recompiled.
        compiled = read(cls.reference(recorded['compiledBinding']))
        require(compiled['bindingStage'] == 'recorded before launch'
                and compiled['head'] == recorded['compiledHead']
                and compiled['archiveIdentity'] == binding['archive']
                and compiled['compiledRuntimeSourcesSha256'] == binding['source']
                and compiled['binary'] == {'path': binding['binary'], 'sha256': binding['binarySha256']}
                and compiled['frozenPins'] == PINS, 'original compiled prelaunch binding differs')
        for field in ('compiledBuildIdentity', 'compiledBuildReport'):
            require(compiled[field] == binding[field], 'compiled prelaunch build reference differs')
        require(compiled['seedManifest'] == recorded['seedManifest'], 'recorded seed differs')
        cls.reference(recorded['seedManifest'])
        require({Path(ref['path']) for ref in recorded['sourceHelpers']} ==
                {Path(ready['collector']['path']), Path(ready['assessor']['path']), *helpers},
                'recorded helper inventory incomplete or unexpected')
        for ref in recorded['sourceHelpers']:
            cls.reference(ref, helpers)
        fixture = ROOT / 'crates/engine/rustdoc/fixtures/automatic-models'
        require(set(prep['fixtureSourceInventory']) ==
                {str(path.relative_to(fixture)) for path in fixture.rglob('*') if path.is_file()},
                'original fixture inventory omitted an input')
        for name, digest in prep['fixtureSourceInventory'].items():
            path = (fixture / name).resolve(strict=True)
            require(path.is_relative_to(fixture.resolve()) and sha(path) == digest,
                    'original fixture input changed')
        cls.configuration(manifest, before, prep)
        require(before['fixtureInputs'] == prep['fixtureInputs'], 'prepared fixture inventory changed')
        require(before['unownedOutputs'] == prep['unownedOutputsBefore'] == prep['unownedOutputsAfter'],
                'ordinary/inherited outputs changed')
        for path, digest in before['unownedOutputs'].items():
            actual = owned(path)
            require(not Path(path).is_symlink() and
                    (actual.is_dir() if digest == 'directory' else sha(actual) == digest),
                    'ordinary/inherited output changed')
        require(before['artifactSentinels'] == prep['artifactSentinels'] and len(prep['artifactSentinels']) == 8,
                'artifact sentinel inventory differs')
        for ref in prep['artifactSentinels']:
            cls.reference(ref)
        require(before['preparedExports'] == prep['preparedExports']
                and [e['mode'] for e in prep['preparedExports']] == list(MODES), 'prepared exports incomplete')
        for export in prep['preparedExports']:
            for field in ('command', 'produced', 'staged'):
                cls.reference(export[field])
            require(export['produced']['sha256'] == export['staged']['sha256'],
                    'staged prepared export differs from genuine output')
            command = read(export['command']['path'])
            # process.json records the launch, not its outcome. Bind the actual
            # process to one successful supervised command in the preparation.
            outcomes = [row for row in prep['commands'] if row.get('pid') == command['pid']]
            require(type(command['pid']) is int and command['pid'] > 0 and len(outcomes) == 1,
                    'prepared export has no unique physical outcome')
            outcome = outcomes[0]
            require(cls.successful(outcome)
                    and all(command[key] == outcome[key] for key in ('command', 'args', 'pid'))
                    and command['processGroupId'] == command['pid'] == outcome['cleanup']['processGroupId'],
                    'prepared export process argv/group differs from successful outcome')
            cls.physical_cleanup(Path(export['command']['path']).parent / 'cleanup.json', outcome, observer,
                                 'prepared export physical cleanup differs from outcome')
            require(command['command'] == manifest['realCargo']['path']
                    and {'rustdoc', '--locked', '--offline', '--output-format', 'json'}.issubset(command['args']),
                    'prepared export lacks genuine successful locked/offline compiler command')
        cls.plans(manifest, before, prep)
        return manifest, binding

    @classmethod
    def configuration(cls, manifest, before, prep):
        config, cargo = manifest['producerConfiguration'], manifest['realCargo']
        require(config == prep['producerConfiguration'] and cargo == prep['realCargo']
                and before['configuration'] == {'cargoHome': config['cargoHome'],
                    'configurations': config['configurations'], 'realCargo': cargo}, 'producer configuration differs')
        cargo_home = Path.home() / '.cargo'
        require(Path(config['cargoHome']).resolve() == cargo_home.resolve(), 'unrecognized producer Cargo home')
        candidates = {base / '.cargo' / name for base in (ROOT, *ROOT.parents) for name in ('config', 'config.toml')}
        candidates.update(cargo_home / name for name in ('config', 'config.toml'))
        require(config['configurations'] == [{'path': str(p), 'sha256': sha(p)}
                for p in sorted(candidates) if p.is_file()], 'current producer Cargo configuration changed')
        for ref in config['configurations']:
            cls.reference(ref, candidates)
        require(Path(cargo['path']) == cargo_home / 'bin/cargo'
                and Path(cargo['resolvedPath']) == (cargo_home / 'bin/rustup').resolve()
                and Path(cargo['path']).resolve() == Path(cargo['resolvedPath']), 'unexpected real producer executable')
        cls.reference(cargo, (cargo_home / 'bin/cargo',))
        proxy = ROOT / 'tools/automatic-rustdoc-cargo.py'
        require(Path(manifest['producerProxy']['path']) == proxy, 'unexpected producer proxy source')
        cls.reference(manifest['producerProxy'], (proxy,))

    @classmethod
    def plans(cls, manifest, before, prep):
        entries = manifest['plans']
        require(len(entries) == 8 and before['plans'] ==
                [{'path': entry['path'], 'sha256': entry['sha256']} for entry in entries],
                'eight prepared plan references differ')
        roots = set()
        for entry, expected in zip(entries, ((m, s) for m in MODES for s in SCENARIOS)):
            plan = read(cls.reference(entry))
            require((plan['preference'], plan['scenario']) == expected, 'plan mode/scenario order differs')
            root = owned(plan['root'])
            require(root not in roots and plan['binary'] == before['binding']['native']['binary'],
                    'reused fixture or wrong planned native executable')
            roots.add(root)
            if plan['scenario'] == 'lifecycle':
                require(plan.get('onlyCases') == ['held-query-cleanup', 'failed-recovery'],
                        'lifecycle selection acquired omitted cases/retries')
            inventory = prep['fixtureInputs'][str(root)]
            require(set(inventory) == {'.cargo/config.toml', '.ignore', 'Cargo.lock', 'Cargo.toml', 'src/lib.rs', 'src/main.rs'},
                    'unexpected fixture source layout')
            for name, digest in inventory.items():
                path = owned(root / name)
                require(path.is_relative_to(root) and sha(path) == digest, 'fixture source/lock/config changed')

    @classmethod
    def collection(cls, parent_path, parent, manifest, binding, lsp, observer):
        collection_path = cls.reference(parent['collectionReference'])
        require(collection_path == parent_path.parent / 'collection/collection.json', 'wrong managed collection path')
        collection = read(collection_path)
        require(cls.reference(collection['manifest']) == cls.reference(parent['bindingBefore']['manifest']),
                'collection used another manifest')
        require(collection['collector'] == manifest['collector']
                and collection['bindingBefore'] == collection['bindingAfter'] == binding,
                'collection source/native/helper boundaries differ')
        require(collection['fixtureInputsBefore'] == collection['fixtureInputsAfter'], 'fixture inputs changed during collection')
        require(set(collection['fixtureInputsBefore']) ==
                {str(owned(read(entry['path'])['root'])) for entry in manifest['plans']},
                'physical fixture inventory omitted a scenario')
        require(collection['fixtureInputsBefore'] ==
                {str(owned(read(entry['path'])['root'])):
                 observer.Diagnostic.inventory(owned(read(entry['path'])['root'])) for entry in manifest['plans']},
                'physical fixture fingerprints differ from current restored inputs')
        attempts = collection['attempts']
        require(parent['scenarioOutcomes'] == attempts, 'parent and physical scenario ledger differ')
        findings, errors = [], list(collection.get('errors', []))
        if collection.get('observationComplete') is not True or len(attempts) != 8:
            errors.append({'error': 'incomplete collection: all physical attempts retained'})
        retained = []
        for n, attempt in enumerate(attempts):
            try:
                observed = read(attempt['report'])
                retained.append({'attempt': n, 'rawQueries': observed.get('rawQueries', []),
                                 'fatal': observed.get('fatal'), 'cases': observed.get('cases', {})})
                require(n < 8 and attempt['plan'] ==
                        {key: manifest['plans'][n][key] for key in ('path', 'sha256')},
                        'extra/reordered physical scenario')
                plan = read(cls.reference(attempt['plan']))
                work = owned(Path(attempt['trace']).parent)
                require(work == parent_path.parent / 'collection' / f'{n:03}-{plan["preference"]}-{plan["scenario"]}',
                        'physical attempt escaped its fixed managed slot')
                require(cls.successful(attempt['command']), 'failed physical scenario or incomplete cleanup')
                command = read(work / 'process.json')
                require(all(command[key] == attempt['command'][key] for key in ('command', 'args', 'pid')),
                        'physical process argv/group differs from ledger')
                require(command['processGroupId'] == command['pid'] ==
                        attempt['command']['cleanup']['processGroupId'],
                        'physical scenario process group differs from supervised cleanup')
                cls.physical_cleanup(work / 'cleanup.json', attempt['command'], observer,
                                     'physical cleanup differs from ledger')
                require(command['command'] == 'strace' and '-ttt' in command['args']
                        and '--inner' in command['args'] and command['args'][-1] == attempt['plan']['path'],
                        'not the recorded traced inner scenario invocation')
                artifacts = {cls.reference(ref) for ref in attempt['artifacts']}
                allowed = {Path(plan[k]).resolve() for k in ('report', 'events', 'control')}
                require(all(path.is_relative_to(OWNED.resolve()) for path in allowed),
                        'plan report/events/control escaped owned root')
                allowed.add(owned(Path(plan['report']).parent / 'raw-queries.jsonl'))
                require(all(path.is_relative_to(work) or path in allowed for path in artifacts),
                        'attempt artifact escaped its slot')
                require({owned(attempt['trace']), owned(attempt['report']), work / 'process.json',
                        work / 'cleanup.json', work / 'lsp-server.stderr.log',
                        owned(Path(plan['report']).parent / 'raw-queries.jsonl')}.issubset(artifacts),
                        'mandatory physical/raw artifacts omitted')
                require(observed['plan'] == plan and observed['planSha256'] == attempt['plan']['sha256'],
                        'raw plan identity differs')
                queries = typed_queries(observed, lsp)
                raw = cls.reference(observed['rawTransportReference'])
                check_raw_stream(raw, observed)
                require(manifest['collector']['sha256'] in COLLECTOR_STATES,
                        'unreviewed collector held-state semantics')
                events_path = Path(plan['events']).resolve()
                events = []
                if events_path.exists():
                    require(events_path in {cls.reference(ref) for ref in parent['retainedArtifacts']},
                            'actual producer events omitted from parent immutable references')
                    with events_path.open() as stream:
                        events = [json.loads(line) for line in stream]
                require(events[:len(observed['evidence']['events'])] == observed['evidence']['events'],
                        'producer event snapshot differs from actual event stream')
                require(not any(event.get('event') == 'started' for event in
                        events[len(observed['evidence']['events']):]),
                        'late producer launch omitted from scenario snapshot')
                facts = check_scenario(observed, COLLECTOR_STATES[manifest['collector']['sha256']])
                execs = list(trace_rows(attempt['trace']))
                facts.update(check_exec(execs, observed, binding['binary'], process_parents(attempt['trace'])))
                facts['nativeStderr'] = native_stderr(observed, work, attempt['artifacts'], execs, binding['binary'])
                facts.update(mode=plan['preference'], rawQueryCount=len(queries),
                             trace={'path': str(owned(attempt['trace'])), 'sha256': sha(attempt['trace'])})
                findings.append(facts)
            except (ValueError, KeyError, TypeError, OSError, IndexError, AttributeError) as error:
                errors.append({'attempt': n, 'error': repr(error), 'physicalAttempt': attempt})
        return {'findings': findings, 'errors': errors, 'allAttempts': retained, 'physicalAttempts': attempts,
                'auditValid': not errors and len(findings) == 8, 'binding': binding}

    @classmethod
    def assess(cls, evidence):
        selection = json.loads(evidence.manifest.read_bytes())
        require(selection.get('schema') == 1 and isinstance(selection.get('policy'), dict),
                'policy evidence has not been selected')
        path = cls.reference(selection['policy'])
        require(path.name == 'managed-collection.json', 'policy selector must name managed raw collection')
        parent = read(path)
        # Preserve the physical ledger even when guards fail before raw evaluation.
        result = {'passed': False, 'report': str(path), 'sha256': sha(path),
                  'physicalAttempts': parent.get('scenarioOutcomes', []), 'errors': [],
                  'processCleanup': parent.get('processCleanup'),
                  'scenarioCleanup': parent.get('scenarioCleanup'),
                  'parentCommands': parent.get('commands', [])}
        try:
            source = source_policy(CONTEXT.read_text())
            handlers = handler_policy({name: path.read_text() for name, path in HANDLERS.items()})
            manifest, binding = cls.preparation(parent, evidence.observer)
            lsp = evidence.observer.helpers.module('rsp_policy_lsp', ROOT / 'tools/lsp-query.py')
            result.update(cls.collection(path, parent, manifest, binding, lsp, evidence.observer))
            native = evidence.native('RSP-005')
            cls.commands(parent, 1)
            require(parent.get('collectionComplete') is True and not parent.get('executionErrors')
                    and not parent.get('boundaryErrors') and not parent.get('collectionErrors'),
                    'managed parent collection failed/incomplete; physical attempts retained')
            command = parent['commands'][0]
            require(parent['ordinaryQueryTimeoutMs'] == 30000
                    and parent['perScenarioAggregateDeadlineMs'] == 1800000
                    and parent['parentAggregateDeadlineMs'] == 14700000,
                    'ordinary query/aggregate deadline differs')
            expected_args = ['-B', manifest['collector']['path'], '--collect',
                parent['bindingBefore']['manifest']['path'], '--output', str(path.parent / 'collection')]
            require(command['args'] == expected_args, 'parent collector invocation differs')
            invoked = parent['attemptedCollectorInvocation']
            require(invoked['command'] == command['command'] and invoked['args'] == expected_args
                    and invoked['cwd'] == str(ROOT) and invoked['timeoutMs'] == 14700000,
                    'actual parent invocation/deadline differs from recorded attempt')
            process = read(path.parent / 'supervisor/process.json')
            require(all(process[key] == command[key] for key in ('command', 'args', 'pid'))
                    and process['processGroupId'] == process['pid'] == command['cleanup']['processGroupId'],
                    'parent physical process/cleanup differs')
            cls.physical_cleanup(path.parent / 'supervisor/cleanup.json', command, evidence.observer,
                                 'parent physical process/cleanup differs')
            require(parent['scenarioCleanup'] == {'status': 'verified', 'runs': 8, 'verifiedRuns': 8},
                    'nested scenario cleanup incomplete')
            for ref in parent['retainedArtifacts']:
                cls.reference(ref)
            result.update(native=native, sourcePolicy=source, handlerPolicy=handlers,
                          sourcePolicyArtifact={'path': str(CONTEXT), 'sha256': sha(CONTEXT)})
            result['passed'] = result['auditValid']
        except (ValueError, KeyError, TypeError, OSError, IndexError, AttributeError) as error:
            result['errors'].append({'error': repr(error)})
            if 'allAttempts' not in result:
                result['allAttempts'] = []
                for n, attempt in enumerate(result['physicalAttempts']):
                    try:
                        raw = read(attempt['report'])
                        result['allAttempts'].append({'attempt': n, 'unvalidated': True,
                            'rawQueries': raw.get('rawQueries', []), 'fatal': raw.get('fatal'),
                            'cases': raw.get('cases', {})})
                    except (ValueError, KeyError, TypeError, OSError) as salvage:
                        result['allAttempts'].append({'attempt': n, 'unvalidated': True,
                            'preservationError': repr(salvage), 'physicalAttempt': attempt})
        result['limitations'] = ('Closed-document error proves ingress behavior; current native cases prove InternalError mapping. '
            'The exact30s single-call policy is source-bound, not a new compiled native test. '
            'External queries cover hover/completion; inlay is source/native shared-policy coverage, not traced here. '
            'Existing completion document-recapture retries are distinct from EngineClient transport invocations. '
            'Frozen query/status/report caps remain unchanged. No latency, idle or formal-review pass is inferred.')
        if not result.pop('passed'):
            raise PolicyEvidenceError('RSP-005 raw policy evidence failed: ' + str(result['errors']), result)
        return result
