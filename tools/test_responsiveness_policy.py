#!/usr/bin/env python3
"""Pure policy-evidence controls; synthetic fixtures launch no native work."""
import copy
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('rsp005_assessor_controls',Path(__file__).with_name('responsiveness-policy.py'))
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def frozen_helpers():
    def module(name, path):
        path = Path(path)
        if path.name == 'lsp-query.py':
            return LspFixture
        if path.name != 'agent-debug.py' or audit.sha(path) != audit.PINS[path.name]:
            raise AssertionError('unexpected frozen helper')
        spec = importlib.util.spec_from_file_location(name, path)
        helper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = helper
        spec.loader.exec_module(helper)
        return helper
    return SimpleNamespace(module=module)


class LspFixture:
    @staticmethod
    def hover_text(result):return result['contents']['value'] if result else ''

    @staticmethod
    def normalize_completions(result,limit):
        return {'isIncomplete':result['isIncomplete'],'truncated':len(result['items'])>limit,'items':result['items']}


def query_fixture():
    return {'rawQueries':[{'id':3,'phase':'held-pending-published-api','sent':True,'sendCount':1,
        'request':{'id':3,'method':'textDocument/hover','params':{}},
        'sendCalledNs':10,'sentNs':11,'writtenNs':11,'receivedNs':20,'durationNs':9,'sendWallNs':100,'receivedWallNs':200,
        'response':{'id':3,'result':{'contents':{'value':'Builder<Post>'}}},
        'responses':[{'id':3,'result':{'contents':{'value':'Builder<Post>'}}}]}],
        'policy':{'requirements':[{'id':3,'phase':'held-pending-published-api','kind':'hover',
            'marker':'automatic_post','contains':'Builder<Post>','clientTimeoutMs':30000,
            'params':{},'documentTextSha256':'0'*64}]},
        'frozenTransport':[{'id':3,'method':'textDocument/hover','status':'success','writtenNs':11,'receivedNs':20,'durationNs':9}]}


class SourcePolicyControls(unittest.TestCase):
    def setUp(self):self.source=audit.CONTEXT.read_text()

    def test_actual_source_exact_deadline_and_single_invocation(self):
        facts=audit.source_policy(self.source)
        self.assertEqual(facts['deadlineSeconds'],30)
        self.assertEqual(facts['requestCallsInQuery'],1)

    def test_real_source_copy_31_seconds_rejected(self):
        changed=self.source.replace('ANALYSIS_QUERY_RPC_DEADLINE: Duration = Duration::from_secs(30);','ANALYSIS_QUERY_RPC_DEADLINE: Duration = Duration::from_secs(31);')
        self.assertNotEqual(changed,self.source)
        with self.assertRaisesRegex(ValueError,'not exactly30'):audit.source_policy(changed)

    def test_real_source_copy_35_seconds_rejected(self):
        changed=self.source.replace('ANALYSIS_QUERY_RPC_DEADLINE: Duration = Duration::from_secs(30);','ANALYSIS_QUERY_RPC_DEADLINE: Duration = Duration::from_secs(35);')
        self.assertNotEqual(changed,self.source)
        with self.assertRaisesRegex(ValueError,'not exactly30'):audit.source_policy(changed)

    def test_real_source_copy_repeatable_callback_rejected(self):
        changed=self.source.replace('F: FnOnce(EngineServiceClient, tarpc::context::Context) -> Fut,','F: FnMut(EngineServiceClient, tarpc::context::Context) -> Fut,')
        with self.assertRaisesRegex(ValueError,'FnOnce'):audit.source_policy(changed)

    def test_real_source_copy_query_override_rejected(self):
        changed=self.source.replace('context.deadline = Instant::now() + ANALYSIS_QUERY_RPC_DEADLINE;','context.deadline = Instant::now() + Duration::from_secs(35);')
        with self.assertRaisesRegex(ValueError,'context override'):audit.source_policy(changed)

    def test_real_source_copy_retry_loop_rejected(self):
        changed=self.source.replace('        match request(','        loop { break; }\n        match request(',1)
        self.assertNotEqual(changed,self.source)
        with self.assertRaisesRegex(ValueError,'retry loop'):audit.source_policy(changed)


class RawQueryControls(unittest.TestCase):
    def test_valid_raw_held_reply(self):
        self.assertEqual(len(audit.typed_queries(query_fixture(),LspFixture)),1)

    def test_null_success_rejects_without_replacing_original(self):
        report=query_fixture();original=copy.deepcopy(report)
        report['rawQueries'][0]['response']['result']=None
        report['rawQueries'][0]['responses']=[copy.deepcopy(report['rawQueries'][0]['response'])]
        with self.assertRaisesRegex(ValueError,'null/empty'):audit.typed_queries(report,LspFixture)
        self.assertEqual(original['rawQueries'][0]['response']['result']['contents']['value'],'Builder<Post>')

    def test_wrong_generated_owner_rejected(self):
        report=query_fixture();report['rawQueries'][0]['response']['result']['contents']['value']='Builder<Other>'
        report['rawQueries'][0]['responses']=[copy.deepcopy(report['rawQueries'][0]['response'])]
        with self.assertRaisesRegex(ValueError,'wrong owner'):audit.typed_queries(report,LspFixture)

    def test_actionable_error_is_retained_failure(self):
        report=query_fixture();error={'id':3,'error':{'code':-32603,'message':'while loading declarations: cache section missing'}}
        report['rawQueries'][0]['response']=error
        report['rawQueries'][0]['responses']=[copy.deepcopy(error)]
        with self.assertRaisesRegex(ValueError,'real query error retained'):audit.typed_queries(report,LspFixture)
        self.assertEqual(report['rawQueries'][0]['response'],error)

    def test_dropped_query_and_orphan_response_rejected(self):
        report=query_fixture();report['rawQueries']=[]
        with self.assertRaisesRegex(ValueError,'missing/orphan'):audit.typed_queries(report,LspFixture)

    def test_duplicate_response_rejected(self):
        report=query_fixture();report['rawQueries'][0]['responses']*=2
        with self.assertRaisesRegex(ValueError,'duplicate'):audit.typed_queries(report,LspFixture)

    def test_pending_attempt_rejected(self):
        report=query_fixture();report['rawQueries'][0]['response']=None
        with self.assertRaisesRegex(ValueError,'pending query retained'):audit.typed_queries(report,LspFixture)

    def test_frozen_wire_duration_disagreement_rejected(self):
        report=query_fixture();report['frozenTransport'][0]['durationNs']=10
        with self.assertRaisesRegex(ValueError,'wire ledger disagree'):audit.typed_queries(report,LspFixture)

    def test_valid_empty_completion_remains_valid(self):
        report=query_fixture();report['rawQueries'][0]['request']['method']='textDocument/completion'
        report['frozenTransport'][0]['method']='textDocument/completion'
        result={'id':3,'result':{'isIncomplete':False,'items':[]}}
        report['rawQueries'][0].update(response=result,responses=[result])
        report['policy']['requirements'][0].update(kind='completion',absent=['query','filter'])
        self.assertEqual(len(audit.typed_queries(report,LspFixture)),1)


class ExecControls(unittest.TestCase):
    def fixture(self):
        report=query_fixture();report['evidence']={'events':[]}
        native={'pid':1,'wallNs':1,'strings':['/owned/suprnova-lsp'],'success':True,'serializedLine':1}
        return report,[native]

    def test_actual_timestamp_and_success_parse(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'target/agent-debug') as d:
            p=Path(d)/'exec';p.write_text('123 1700000000.123456 execve("/owned/suprnova-lsp", ["suprnova-lsp"], 0x0) = 0\n')
            rows=list(audit.trace_rows(p));self.assertEqual(rows[0]['wallNs'],1700000000123456000)
            self.assertTrue(rows[0]['success'])

    def test_real_compiler_exec_window_rejected(self):
        report,rows=self.fixture();rows.append({'pid':2,'wallNs':150,'strings':['/usr/bin/rustc'],'success':True,'serializedLine':2})
        with self.assertRaisesRegex(ValueError,'ordinary query window'):audit.check_exec(rows,report,'/owned/suprnova-lsp')

    def test_missing_exec_native_proof_rejected(self):
        report,_=self.fixture()
        with self.assertRaisesRegex(ValueError,'native exec'):audit.check_exec([],report,'/owned/suprnova-lsp')

    def test_attempted_rust_analyzer_rejected(self):
        report,rows=self.fixture();rows.append({'pid':2,'wallNs':300,'strings':['/usr/bin/rust-analyzer'],'success':False,'serializedLine':2})
        with self.assertRaisesRegex(ValueError,'rust-analyzer'):audit.check_exec(rows,report,'/owned/suprnova-lsp')

    def test_unknown_split_exec_never_silently_disappears(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'target/agent-debug') as d:
            p=Path(d)/'exec';p.write_text('123 1700000000.1 execve("cargo", ["cargo"], 0x0 <unfinished ...>\n')
            with self.assertRaisesRegex(ValueError,'unfinished exec'):list(audit.trace_rows(p))




def closed_fixture():
    report=query_fixture();uri='file:///owned/fixture/src/main.rs';text='fn main() {}'
    response={'id':3,'error':{'code':-32801,'message':'the request targets a closed document session'}}
    q=report['rawQueries'][0]
    q.update(phase='prepared-closed-document-error',response=response,responses=[copy.deepcopy(response)])
    q['request']['params']={'textDocument':{'uri':uri},'position':{'line':0,'character':0}}
    expect=report['policy']['requirements'][0]
    expect.update(kind='closed-document-error',phase=q['phase'],params=q['request']['params'],documentTextSha256=audit.hashlib.sha256(text.encode()).hexdigest())
    report['frozenTransport'][0].update(status='rpc-error',errorCode=-32801)
    report['plan']={'scenario':'prepared','root':'/owned/fixture','inputs':['genuine-export-fixture']}
    report['workerMessages']=[]
    report['clientWrites']=[
        {'message':{'jsonrpc':'2.0','method':'textDocument/didClose','params':{'textDocument':{'uri':uri}}},'phase':'prepared-close-document','sendCalledNs':1,'writtenNs':2,'sent':True},
        {'message':{'jsonrpc':'2.0','method':'textDocument/didOpen','params':{'textDocument':{'uri':uri,'text':text,'languageId':'rust','version':1}}},'phase':'prepared-reopen-document','sendCalledNs':22,'writtenNs':23,'sent':True}]
    report['policy']['boundaries']=[
        {'label':'prepared-document-closed','path':'/owned/fixture/src/main.rs','clientWriteIndex':0,'monotonicNs':3},
        {'label':'prepared-closed-error-received','requestId':3,'monotonicNs':21},
        {'label':'prepared-document-reopened','path':'/owned/fixture/src/main.rs','clientWriteIndex':1,'monotonicNs':24}]
    return report


class ExpectedErrorControls(unittest.TestCase):
    def test_valid_actual_shaped_closed_error_is_not_typed_success(self):
        report=closed_fixture()
        self.assertEqual(len(audit.typed_queries(report,LspFixture)),1)
        self.assertEqual(report['frozenTransport'][0]['status'],'rpc-error')
        self.assertIn('closed document session',report['rawQueries'][0]['response']['error']['message'])

    def test_empty_success_cannot_replace_expected_error(self):
        r=closed_fixture();r['rawQueries'][0].update(response={'id':3,'result':None},responses=[{'id':3,'result':None}]);r['frozenTransport'][0]['status']='success'
        with self.assertRaisesRegex(ValueError,'replaced or changed'):audit.typed_queries(r,LspFixture)

    def test_changed_error_code_message_or_id_rejected(self):
        for field,value in (('code',-32603),('message','temporarily unavailable'),('id',4)):
            r=closed_fixture();q=r['rawQueries'][0]
            if field=='id':q['response']['id']=value
            else:q['response']['error'][field]=value
            q['responses']=[copy.deepcopy(q['response'])]
            with self.subTest(field=field),self.assertRaises(ValueError):audit.typed_queries(r,LspFixture)

    def test_extra_writes_and_replies_rejected(self):
        for kind in ('count','duplicate-query','reply'):
            r=closed_fixture()
            if kind=='count':r['rawQueries'][0]['sendCount']=2
            elif kind=='reply':r['rawQueries'][0]['responses']*=2
            else:r['rawQueries']*=2
            with self.subTest(kind=kind),self.assertRaises(ValueError):audit.typed_queries(r,LspFixture)

    def test_missing_close_boundary_or_notification_rejected(self):
        for kind in ('boundary','notification','index'):
            r=closed_fixture()
            if kind=='boundary':r['policy']['boundaries'].pop(0)
            elif kind=='notification':r['clientWrites'][0]['message']['method']='textDocument/didOpen'
            else:r['policy']['boundaries'][0]['clientWriteIndex']=-1
            with self.subTest(kind=kind),self.assertRaises(ValueError):audit.typed_queries(r,LspFixture)

    def test_wrong_timeout_or_frozen_error_status_rejected(self):
        for kind in ('timeout','status','code'):
            r=closed_fixture()
            if kind=='timeout':r['policy']['requirements'][0]['clientTimeoutMs']=35000
            elif kind=='status':r['frozenTransport'][0]['status']='success'
            else:r['frozenTransport'][0]['errorCode']=-32603
            with self.subTest(kind=kind),self.assertRaises(ValueError):audit.typed_queries(r,LspFixture)

    def test_error_outside_close_envelope_and_wrong_reopen_text_rejected(self):
        for kind in ('close','reopen','text'):
            r=closed_fixture()
            if kind=='close':r['policy']['boundaries'][0]['monotonicNs']=30
            elif kind=='reopen':r['clientWrites'][1]['sendCalledNs']=19
            else:r['clientWrites'][1]['message']['params']['textDocument']['text']='different'
            with self.subTest(kind=kind),self.assertRaises(ValueError):audit.typed_queries(r,LspFixture)

    def test_expected_error_cannot_be_reused_in_ordinary_success_phase(self):
        r=closed_fixture();r['plan']['scenario']='lifecycle'
        with self.assertRaisesRegex(ValueError,'outside prepared'):audit.typed_queries(r,LspFixture)


class StreamAndNativeControls(unittest.TestCase):
    def stream_fixture(self,path,r):
        q=r['rawQueries'][0]
        rows=[{'event':'send-called','row':q},{'event':'sent','row':q},{'event':'response','row':q}]
        for w in r['clientWrites']:rows.extend([{'event':'client-write-called','row':w},{'event':'client-write-sent','row':w}])
        path.write_text(''.join(json.dumps(e)+'\n' for e in rows))
        return rows

    def test_valid_stream_and_extra_serialized_write_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'target/agent-debug') as d:
            p=Path(d)/'raw';r=closed_fixture();events=self.stream_fixture(p,r)
            audit.check_raw_stream(p,r)
            events.insert(1,events[1]);p.write_text(''.join(json.dumps(e)+'\n' for e in events))
            with self.assertRaisesRegex(ValueError,'missing/extra'):audit.check_raw_stream(p,r)

    def test_response_observed_before_send_return_is_not_an_extra_write(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'target/agent-debug') as d:
            p=Path(d)/'raw';r=closed_fixture();events=self.stream_fixture(p,r)
            events=json.loads(json.dumps(events))
            events[2]['row']['sent']=False
            events[2]['row'].pop('sentNs',None)
            events[1],events[2]=events[2],events[1]
            p.write_text(''.join(json.dumps(e)+'\n' for e in events))
            audit.check_raw_stream(p,r)

    def test_dropped_auxiliary_write_or_response_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'target/agent-debug') as d:
            p=Path(d)/'raw';r=closed_fixture();events=self.stream_fixture(p,r)
            for remove in (2,3):
                p.write_text(''.join(json.dumps(e)+'\n' for n,e in enumerate(events) if n!=remove))
                with self.assertRaises(ValueError):audit.check_raw_stream(p,r)

    def test_query_wall_window_cannot_diverge_from_retained_response(self):
        with tempfile.TemporaryDirectory(dir=audit.OWNED) as directory:
            path = Path(directory) / 'raw'; report = closed_fixture()
            self.stream_fixture(path, report)
            report['rawQueries'][0].update(sendWallNs=300, receivedWallNs=400)
            with self.assertRaisesRegex(ValueError, 'differ'):
                audit.check_raw_stream(path, report)

    def test_send_called_clock_cannot_diverge_from_canonical_query(self):
        with tempfile.TemporaryDirectory(dir=audit.OWNED) as directory:
            path = Path(directory) / 'raw'; report = closed_fixture()
            events = self.stream_fixture(path, report)
            events = json.loads(json.dumps(events))
            events[0]['row']['sendWallNs'] = 99
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            with self.assertRaisesRegex(ValueError, 'differ'):
                audit.check_raw_stream(path, report)

    def test_actual_shaped_native_log_binding_and_corruption(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'target/agent-debug') as d:
            work=Path(d);p=work/'lsp-server.stderr.log';p.write_text('{"message":"native fixture"}\n')
            ref={'path':str(p),'sha256':audit.sha(p),'bytes':p.stat().st_size,'recordedBytes':p.stat().st_size,'truncated':False,'serverPid':10,'serverReturnCode':0,'afterClose':True}
            r={'nativeStderr':ref};artifacts=[{'path':str(p),'sha256':ref['sha256']}];execs=[{'pid':10,'success':True,'strings':['/owned/native']}]
            self.assertEqual(audit.native_stderr(r,work,artifacts,execs,'/owned/native'),ref)
            for field,value in (('sha256','0'*64),('bytes',0),('recordedBytes',0),('truncated',True),('afterClose',False),('serverPid',11),('serverReturnCode',1)):
                bad={'nativeStderr':{**ref,field:value}}
                with self.subTest(field=field),self.assertRaises(ValueError):audit.native_stderr(bad,work,artifacts,execs,'/owned/native')
            with self.assertRaisesRegex(ValueError,'omitted'):audit.native_stderr(r,work,[],execs,'/owned/native')




def artifact_fixture():
    r=query_fixture();q=r['rawQueries'][0];q['phase']='artifact-failed-source-api'
    q['response']['result']['contents']['value']='u64';q['responses']=[copy.deepcopy(q['response'])]
    r['policy']['requirements'][0].update(phase=q['phase'],marker='automatic_source',contains='u64')
    failure={'state':'failed','generation':1,'message':'artifact directory creation failed','workspaceRoot':'/owned/fixture'}
    recovery={'state':'current','generation':2,'workspaceRoot':'/owned/fixture'}
    common={'blockedPath':'/owned/artifacts/blocked-parent','blockedIsFile':True,'blockedSha256':'a'*64,'events':[]}
    r['policy'].update(queryRpcRetries=0,boundaries=[
        {'label':'artifact-failed-source-begin','monotonicNs':5,'failure':failure,**common},
        {'label':'artifact-failed-source-end','monotonicNs':21,'failure':copy.deepcopy(failure),**common},
        {'label':'explicit-artifact-recovery-begin','monotonicNs':22},
        {'label':'artifact-recovery-complete','monotonicNs':60,'recovery':recovery,'reindexWriteIndex':0}])
    r['plan']={'scenario':'artifact-failure','root':'/owned/fixture','artifactRoot':'/owned/artifacts'}
    r.update(fatal=None,cases={'artifact-recovery':{'passed':True,'attempted':True}},evidence={'events':[{'event':'started','monotonicNs':40,'pid':11}]},workerMessages=[
        {'message':{'method':'suprnova-lsp/rustdocStatus','params':failure},'receivedNs':4},
        {'message':{'method':'suprnova-lsp/rustdocStatus','params':copy.deepcopy(recovery)},'receivedNs':50}])
    r['clientWrites']=[{'message':{'jsonrpc':'2.0','id':4,'method':'workspace/executeCommand','params':{'command':'suprnova-lsp.internal.reindexWorkspace','arguments':[]}},'phase':'artifact-recovery-published-api','sendCalledNs':30,'writtenNs':31,'sent':True}]
    r['frozenTransport'].append({'id':4,'method':'workspace/executeCommand','status':'success','writtenNs':31,'receivedNs':35,'durationNs':4})
    return r


class ArtifactStateControls(unittest.TestCase):
    def test_valid_failed_state_u64_then_explicit_recovery(self):
        r=artifact_fixture()
        self.assertEqual(len(audit.typed_queries(r,LspFixture)),1)
        self.assertEqual(audit.check_scenario(r)['scenario'],'artifact-failure')

    def test_blocked_path_generation_or_real_status_corruption_rejected(self):
        for kind in ('blocked','path','generation','notification'):
            r=artifact_fixture()
            if kind=='blocked':r['policy']['boundaries'][1]['blockedIsFile']=False
            elif kind=='path':r['policy']['boundaries'][1]['blockedPath']='/elsewhere'
            elif kind=='generation':r['policy']['boundaries'][1]['failure']['generation']=2
            else:r['workerMessages']=[]
            with self.subTest(kind=kind),self.assertRaises(ValueError):audit.check_scenario(r)

    def test_recovery_summary_requires_actual_current_notification(self):
        for corruption in ('missing', 'late', 'failed', 'different-generation'):
            report = artifact_fixture()
            if corruption == 'missing': report['workerMessages'].pop()
            elif corruption == 'late': report['workerMessages'][-1]['receivedNs'] = 61
            elif corruption == 'failed': report['workerMessages'][-1]['message']['params']['state'] = 'failed'
            else: report['workerMessages'][-1]['message']['params']['generation'] = 3
            with self.subTest(corruption=corruption), self.assertRaisesRegex(ValueError, 'actual latest'):
                audit.check_scenario(report)

    def test_source_query_not_u64_or_outside_failed_envelope_rejected(self):
        for kind in ('contains','phase','clock'):
            r=artifact_fixture()
            if kind=='contains':r['policy']['requirements'][0]['contains']='String'
            elif kind=='phase':r['rawQueries'][0]['phase']='artifact-recovery-published-api'
            else:r['policy']['boundaries'][1]['monotonicNs']=19
            with self.subTest(kind=kind),self.assertRaises(ValueError):audit.check_scenario(r)

    def test_implicit_producer_early_recovery_or_stale_success_rejected(self):
        for kind in ('producer','trigger','generation','command-error'):
            r=artifact_fixture()
            if kind=='producer':r['evidence']['events'][0]['monotonicNs']=15
            elif kind=='trigger':r['policy']['boundaries'][2]['monotonicNs']=19
            elif kind=='generation':r['policy']['boundaries'][3]['recovery']['generation']=1
            else:r['frozenTransport'][-1]['status']='rpc-error'
            with self.subTest(kind=kind),self.assertRaises(ValueError):audit.check_scenario(r)





def planned_policy_query(identifier, phase, kind, marker, semantics, sent):
    fixture = query_fixture()
    query, expectation, wire = (fixture['rawQueries'][0], fixture['policy']['requirements'][0], fixture['frozenTransport'][0])
    method = 'textDocument/hover' if kind == 'hover' else 'textDocument/completion'
    result = ({'contents': {'value': semantics}} if kind == 'hover'
              else {'isIncomplete': False, 'items': [] if semantics else [{'label': 'Title'}]})
    response = {'id': identifier, 'result': result}
    query.update(id=identifier, phase=phase, sendCalledNs=sent, writtenNs=sent+1,
                 receivedNs=sent+5, durationNs=4, response=response, responses=[copy.deepcopy(response)])
    query['request'].update(id=identifier, method=method)
    expectation.update(id=identifier, phase=phase, kind=kind, marker=marker)
    expectation.pop('contains', None)
    expectation['contains' if kind == 'hover' else 'absent'] = semantics
    wire.update(id=identifier, method=method, writtenNs=sent+1, receivedNs=sent+5, durationNs=4)
    return query, expectation, wire


def lifecycle_recovery_fixture():
    root = '/owned/fixture'
    def state(generation, status):
        return {'generation': generation, 'state': status, 'workspaceRoot': root}
    held, held_current = state(1, 'running'), state(2, 'current')
    failure, failure_current = state(3, 'failed'), state(4, 'current')
    failure['message'] = 'automatic_models producer failed'
    child = {'pid': 20, 'childPid': 21}
    boundaries = [
        {'label': 'held-queries-begin', 'monotonicNs': 20, 'pending': held, 'child': child},
        {'label': 'held-queries-end', 'monotonicNs': 60, 'pending': held, 'child': child},
        {'label': 'held-recovered', 'monotonicNs': 100, 'current': held_current,
         'childAlive': False, 'descendantAlive': False},
        {'label': 'explicit-failure-save-trigger', 'monotonicNs': 110},
        {'label': 'failed-queries-begin', 'monotonicNs': 130, 'failure': failure},
        {'label': 'failed-no-retry-begin', 'monotonicNs': 200},
        {'label': 'failed-no-retry-end', 'monotonicNs': 1_000_000_200},
        {'label': 'explicit-failure-recovery-save', 'monotonicNs': 1_000_000_300},
        {'label': 'failure-recovered', 'monotonicNs': 1_000_000_500, 'current': failure_current}]
    report = {'plan': {'scenario': 'lifecycle', 'root': root, 'debounceMs': 0}, 'fatal': None,
        'cases': {name: {'passed': True, 'attempted': True} for name in ('held-query-cleanup', 'failed-recovery')},
        'policy': {'queryRpcRetries': 0, 'boundaries': boundaries},
        'rawQueries': [{'phase': phase, 'sendCalledNs': 21 + n * 10, 'receivedNs': 29 + n * 10}
            for n, phase in enumerate(('held-pending-published-api', 'held-pending-source-api', 'held-pending-owner-isolation'))],
        'workerMessages': [{'receivedNs': clock, 'message': {'params': snapshot}}
            for clock, snapshot in ((10, held), (90, held_current), (120, failure), (1_000_000_400, failure_current))],
        'evidence': {'events': [{'event': 'finished', 'code': 42, 'monotonicNs': 125}], 'notifications': []}}
    specs = [
        ('held-pending-published-api', 'hover', 'automatic_post', 'Builder<Post>', 21),
        ('held-pending-source-api', 'hover', 'automatic_source', 'u64', 31),
        ('held-pending-owner-isolation', 'completion', 'let automatic_unrelated = unrelated::', ['query','filter'], 41),
        ('failed-preserved-api', 'hover', 'automatic_post', 'Builder<Post>', 131),
        ('failed-preserved-api', 'completion', 'let automatic_column = post::Column::', [], 141),
        ('failed-owner-isolation', 'completion', 'let automatic_unrelated = unrelated::', ['query','filter'], 151)]
    rows = [planned_policy_query(n+3, *spec) for n, spec in enumerate(specs)]
    report['rawQueries'] = [row[0] for row in rows]
    report['policy']['requirements'] = [row[1] for row in rows]
    report['frozenTransport'] = [row[2] for row in rows]
    return report


def missing_source_fixture():
    query, expectation, wire = planned_policy_query(3, 'missing-producer-source-api', 'hover', 'automatic_source', 'u64', 20)
    failure = {'state': 'failed', 'generation': 1, 'workspaceRoot': '/owned/fixture', 'message': 'producer toolchain unavailable'}
    return {'plan': {'scenario': 'missing-producer', 'root': '/owned/fixture'},
        'fatal': None, 'cases': {'missing-producer': {'passed': True, 'attempted': True}},
        'policy': {'queryRpcRetries': 0, 'requirements': [expectation], 'boundaries': [
            {'label': 'missing-producer-source-complete', 'monotonicNs': 30, 'failure': failure}]},
        'rawQueries': [query], 'frozenTransport': [wire], 'evidence': {'events': []},
        'workerMessages': [{'receivedNs': 10, 'message': {'params': failure}}]}


class PlannedFailureQueryControls(unittest.TestCase):
    def test_retained_actual_failure_query_plans_and_concordant_removal(self):
        collection_path = ROOT / 'target/agent-debug/rsp005-current-policy-managed-running-e986-01/collection/collection.json'
        if not collection_path.exists():
            self.skipTest('retained native collection is not installed')
        collection = json.loads(collection_path.read_text())
        count = 0
        for attempt in collection['attempts']:
            path = Path(attempt['report'])
            reference = next(ref for ref in attempt['artifacts'] if ref['path'] == str(path))
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), reference['sha256'])
            report = json.loads(data)
            if report['plan']['scenario'] not in ('lifecycle', 'missing-producer'):
                continue
            audit.check_scenario(report, 'running')
            count += 1
            phases = {'failed-preserved-api', 'failed-owner-isolation', 'missing-producer-source-api'}
            for query in report['rawQueries']:
                if query['phase'] not in phases:
                    continue
                removed = copy.deepcopy(report)
                for field in ('rawQueries', 'frozenTransport'):
                    removed[field] = [row for row in removed[field] if row['id'] != query['id']]
                removed['policy']['requirements'] = [row for row in removed['policy']['requirements'] if row['id'] != query['id']]
                with self.subTest(scenario=report['plan']['scenario'], identifier=query['id']), self.assertRaisesRegex(ValueError, 'required failure query'):
                    audit.check_scenario(removed, 'running')
        self.assertEqual(count, 4)

    def test_exact_failed_queries_and_missing_source_query_are_present(self):
        for report in (lifecycle_recovery_fixture(), missing_source_fixture()):
            audit.typed_queries(report, LspFixture)
            audit.check_scenario(report, 'running')

    def test_concordant_removal_of_each_required_query_rejects(self):
        for identifier in (6, 7, 8, 3):
            report = missing_source_fixture() if identifier == 3 else lifecycle_recovery_fixture()
            report['rawQueries'] = [q for q in report['rawQueries'] if q['id'] != identifier]
            report['policy']['requirements'] = [q for q in report['policy']['requirements'] if q['id'] != identifier]
            report['frozenTransport'] = [q for q in report['frozenTransport'] if q['id'] != identifier]
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, 'required failure query'):
                audit.check_scenario(report, 'running')

    def test_required_query_semantics_cannot_be_substituted(self):
        for scenario in ('lifecycle', 'missing-producer'):
            for field, value in (('kind', 'completion'), ('marker', 'automatic_other'), ('contains', 'Builder<Other>')):
                report = lifecycle_recovery_fixture() if scenario == 'lifecycle' else missing_source_fixture()
                expectation = next(r for r in report['policy']['requirements']
                                   if r['phase'] == ('failed-preserved-api' if scenario == 'lifecycle' else 'missing-producer-source-api'))
                expectation[field] = value
                with self.subTest(scenario=scenario, field=field), self.assertRaisesRegex(ValueError, 'required failure query'):
                    audit.check_scenario(report, 'running')
        report = lifecycle_recovery_fixture()
        report['policy']['requirements'][-1]['absent'] = []
        with self.assertRaisesRegex(ValueError, 'required failure query'):
            audit.check_scenario(report, 'running')

    def test_failed_queries_must_precede_recovery_and_follow_actual_failure(self):
        report = lifecycle_recovery_fixture(); report['rawQueries'][3]['receivedNs'] = 201
        with self.assertRaisesRegex(ValueError, 'required failure query'):
            audit.check_scenario(report, 'running')
        report = missing_source_fixture(); report['workerMessages'][0]['receivedNs'] = 29
        with self.assertRaisesRegex(ValueError, 'actual worker|actual latest'):
            audit.check_scenario(report)



class LifecycleRecoveryControls(unittest.TestCase):
    def test_both_recoveries_match_actual_current_worker_generations(self):
        self.assertEqual(audit.check_scenario(lifecycle_recovery_fixture(), 'running')['scenario'], 'lifecycle')

    def test_each_recovery_rejects_missing_late_failed_or_other_generation(self):
        for index in (1, 3):
            for corruption in ('missing', 'late', 'failed', 'different-generation'):
                report = lifecycle_recovery_fixture()
                if corruption == 'missing': report['workerMessages'].pop(index)
                elif corruption == 'late':
                    report['workerMessages'][index]['receivedNs'] = (101 if index == 1 else 1_000_000_501)
                elif corruption == 'failed': report['workerMessages'][index]['message']['params'] = {
                    **report['workerMessages'][index]['message']['params'], 'state': 'failed'}
                else: report['workerMessages'][index]['message']['params'] = {
                    **report['workerMessages'][index]['message']['params'],
                    'generation': report['workerMessages'][index]['message']['params']['generation'] + 1}
                with self.subTest(recovery=index, corruption=corruption), self.assertRaisesRegex(ValueError, 'actual latest'):
                    audit.check_scenario(report, 'running')


class DurableBindingControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=audit.OWNED)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def ref(self, name, content):
        path = self.root / name
        path.write_text(json.dumps(content) if not isinstance(content, str) else content)
        return {'path': str(path), 'sha256': audit.sha(path)}

    @staticmethod
    def command(code=0, empty=True):
        return {'command': 'cargo', 'args': ['build', '--release', '--locked', '--offline', '-p', 'suprnova-lsp'],
                'code': code, 'timedOut': False, 'signal': None, 'spawnError': None,
                'cleanup': {'verifiedEmpty': empty, 'remainingPids': [] if empty else [12]}}

    def fixture(self):
        source = '1' * 64
        executable = self.ref('suprnova-lsp', 'synthetic executable bytes, never executed')
        identity = self.ref('build-identity.json', {'runtimeSourcesSha256': source,
                            'binarySha256': executable['sha256'], 'sourcesUnchanged': True})
        report = self.ref('build-report.json', {'commands': [self.command()],
                         'processCleanup': {'status': 'verified', 'runs': 1, 'verifiedRuns': 1}})
        archive = self.ref('archive.json', {'runtimeSourcesSha256': source,
                     'binary': executable['path'], 'binarySha256': executable['sha256'],
                     'compiledBuildIdentity': identity, 'compiledBuildReport': report})
        manifest = {'collector': self.ref('collector.py', 'raise RuntimeError("must not import")'),
                    'probeHelperSha256': audit.sha(ROOT / 'tools/automatic-rustdoc-probe.py'),
                    'binding': {'archive': archive, 'runtimeSourcesSha256': source,
                                'binarySha256': executable['sha256'],
                                'compiledBuildIdentity': identity, 'compiledBuildReport': report}}
        observer = SimpleNamespace(Diagnostic=SimpleNamespace(runtime_fingerprint=lambda: source))
        return manifest, observer

    def test_full_actual_shaped_build_binding_without_invented_build_flag(self):
        manifest, observer = self.fixture()
        facts = audit.PolicyEvidence.native_binding(manifest, observer)
        self.assertEqual(facts['source'], '1' * 64)
        self.assertEqual(facts['collector'], manifest['collector'])

    def test_build_failure_empty_cleanup_and_missing_success_rejected(self):
        for change in ('nonzero', 'timeout', 'empty', 'topcount', 'offline'):
            manifest, observer = self.fixture()
            report_path = Path(manifest['binding']['compiledBuildReport']['path'])
            report = json.loads(report_path.read_text())
            command = report['commands'][0]
            if change == 'nonzero': command['code'] = 143
            elif change == 'timeout': command['timedOut'] = True
            elif change == 'empty': command['cleanup']['remainingPids'] = [7]
            elif change == 'topcount': report['processCleanup']['verifiedRuns'] = 0
            else: command['args'].remove('--offline')
            report_path.write_text(json.dumps(report))
            manifest['binding']['compiledBuildReport']['sha256'] = audit.sha(report_path)
            archive_path = Path(manifest['binding']['archive']['path'])
            archive = json.loads(archive_path.read_text())
            archive['compiledBuildReport'] = manifest['binding']['compiledBuildReport']
            archive_path.write_text(json.dumps(archive))
            manifest['binding']['archive']['sha256'] = audit.sha(archive_path)
            with self.subTest(change=change), self.assertRaises(ValueError):
                audit.PolicyEvidence.native_binding(manifest, observer)

    def test_changed_actual_binary_despite_claimed_identity_rejected(self):
        manifest, observer = self.fixture()
        archive = json.loads(Path(manifest['binding']['archive']['path']).read_text())
        Path(archive['binary']).write_text('changed actual executable')
        with self.assertRaisesRegex(ValueError, 'actual archived'):
            audit.PolicyEvidence.native_binding(manifest, observer)

    def test_current_source_mismatch_rejected_without_head_equality(self):
        manifest, observer = self.fixture()
        observer.Diagnostic.runtime_fingerprint = lambda: '2' * 64
        with self.assertRaisesRegex(ValueError, 'current source differs'):
            audit.PolicyEvidence.native_binding(manifest, observer)

    def test_unapproved_external_reference_and_escaping_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            outside = Path(d) / 'secret'; outside.write_text('external')
            ref = {'path': str(outside), 'sha256': audit.sha(outside)}
            with self.assertRaisesRegex(ValueError, 'explicitly allowed'):
                audit.PolicyEvidence.reference(ref)
            link = self.root / 'link'; link.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'explicitly allowed'):
                audit.PolicyEvidence.reference({'path': str(link), 'sha256': audit.sha(outside)})

    def test_nonzero_is_distinct_from_verified_empty_cleanup(self):
        command = self.command(code=143)
        self.assertTrue(audit.PolicyEvidence.empty(command))
        self.assertFalse(audit.PolicyEvidence.successful(command))
        self.assertFalse(audit.PolicyEvidence.empty(self.command(empty=False)))

    def test_new_bound_is_at_least_512_mib(self):
        self.assertGreaterEqual(audit.BOUND, 512 * 1024 * 1024)


class SplitExecControls(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory(dir=audit.OWNED) as d:
            path = Path(d) / 'process.exec'; path.write_text(text)
            return list(audit.trace_rows(path))

    def test_actual_native_split_retains_start_end_lines_and_clocks(self):
        rows = self.parse('1242242 1791612803.413363 execve("/owned/native", ["native", "lsp"], 0x7ffe /* 175 vars */ <unfinished ...>\n'
                          '1242243 1791612803.413400 clone(child_stack=NULL, flags=SIGCHLD) = 20\n'
                          '1242242 1791612803.413795 <... execve resumed>) = 0\n')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['serializedLine'], rows[0]['endSerializedLine']), (1, 3))
        self.assertEqual(rows[0]['wallNs'], 1791612803413363000)
        self.assertEqual(rows[0]['endWallNs'], 1791612803413795000)
        self.assertTrue(rows[0]['success'])

    def test_interleaved_split_calls_and_failed_path_search_remain_visible(self):
        rows = self.parse('1 1.000001 execve("native", ["native"], 0x0 <unfinished ...>\n'
                          '2 1.000002 execve("absent", ["python3"], 0x0 <unfinished ...>\n'
                          '2 1.000003 <... execve resumed>) = -1 ENOENT (No such file or directory)\n'
                          '1 1.000004 <... execve resumed>) = 0\n')
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['outcome'] for row in rows], [-1, 0])
        self.assertEqual(rows[0]['strings'][0], 'absent')

    def test_orphan_mismatched_unclosed_backwards_and_truncated_rejected(self):
        fixtures = (
            '1 1.0 <... execve resumed>) = 0\n',
            '1 1.0 execve("n", ["n"], 0x0 <unfinished ...>\n1 1.1 <... execveat resumed>) = 0\n',
            '1 1.0 execve("n", ["n"], 0x0 <unfinished ...>\n',
            '1 1.1 execve("n", ["n"], 0x0 <unfinished ...>\n1 1.0 <... execve resumed>) = 0\n',
            '1 1.0 execve("n", ["long"...], 0x0) = 0\n',
            '1 1.0 execve("n", ["n"], 0x0) = ?\n',
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture), self.assertRaises(ValueError): self.parse(fixture)

    def test_execveat_and_failed_rust_analyzer_attempt_are_not_discarded(self):
        rows = self.parse('1 1.0 execveat(AT_FDCWD, "/absent/rust-analyzer", ["rust-analyzer"], 0x0, 0) = -1 ENOENT (No such file or directory)\n')
        self.assertFalse(rows[0]['success'])
        report = query_fixture(); report['evidence'] = {'events': []}
        native = {'pid': 2, 'strings': ['/native'], 'success': True, 'wallNs': 0}
        with self.assertRaisesRegex(ValueError, 'rust-analyzer'):
            audit.check_exec([native, *rows], report, '/native')


class HeldGenerationControls(unittest.TestCase):
    def test_latest_running_same_generation_is_required(self):
        state = {'state': 'running', 'generation': 2, 'workspaceRoot': '/owned/fixture'}
        report = {'plan': {'root': '/owned/fixture'}, 'workerMessages': [
            {'receivedNs': 10, 'message': {'params': {**state, 'state': 'pending'}}},
            {'receivedNs': 20, 'message': {'params': state}}]}
        boundary = {'monotonicNs': 30, 'pending': state}
        audit.latest_worker_state(report, boundary, 'pending', 'running')
        for corruption in ('historic-pending', 'new-generation', 'current'):
            changed = copy.deepcopy(report)
            if corruption == 'historic-pending': boundary = {'monotonicNs': 30, 'pending': {**state, 'state': 'pending'}}
            elif corruption == 'new-generation': changed['workerMessages'].append({'receivedNs': 25, 'message': {'params': {**state, 'generation': 3}}})
            else: changed['workerMessages'][-1]['message']['params']['state'] = 'current'
            with self.subTest(corruption=corruption), self.assertRaises(ValueError):
                audit.latest_worker_state(changed, boundary, 'pending', 'running')






class PreparedParentControls(unittest.TestCase):
    setUp = DurableBindingControls.setUp
    ref = DurableBindingControls.ref
    command = staticmethod(DurableBindingControls.command)
    fixture = DurableBindingControls.fixture
    def prepared(self):
        manifest, observer = self.fixture()
        cargo_home = Path.home() / '.cargo'
        candidates = {base / '.cargo' / name for base in (ROOT, *ROOT.parents) for name in ('config', 'config.toml')}
        candidates.update(cargo_home / name for name in ('config', 'config.toml'))
        config = {'cargoHome': str(cargo_home), 'toolchain': 'nightly-2026-08-19',
                  'configurations': [{'path': str(p), 'sha256': audit.sha(p)} for p in sorted(candidates) if p.is_file()]}
        cargo = {'path': str(cargo_home / 'bin/cargo'), 'resolvedPath': str((cargo_home / 'bin/cargo').resolve()),
                 'sha256': audit.sha(cargo_home / 'bin/cargo')}
        helpers = audit.PolicyEvidence.tracked_helpers()
        refs = [{'path': str(p), 'sha256': audit.sha(p)} for p in helpers]
        assessor = self.ref('old-assessor.py', 'historical metadata only; must never execute')
        prepare = self.ref('old-prepare.py', 'historical metadata only; must never execute')
        wrapper = self.ref('old-wrapper.py', 'historical metadata only; must never execute')
        manifest.update(schema=1, acceptance=False, bindingStage='recorded before policy collection',
            assessor=assessor, preparationHelper=prepare, observationHead='historical-observation-head',
            unsetInheritedEnvironment=list(audit.ENVIRONMENT_MUST_BE_UNSET), producerConfiguration=config,
            realCargo=cargo, producerProxy=refs[1])
        native = audit.PolicyEvidence.native_binding(manifest, observer)
        seed = self.ref('seed.json', {'genuineShape': 'synthetic seed receipt; never native proof'})
        compiled = self.ref('compiled-prelaunch.json', {'bindingStage': 'recorded before launch',
            'head': 'historical-compile-head', 'archiveIdentity': native['archive'],
            'compiledRuntimeSourcesSha256': native['source'],
            'binary': {'path': native['binary'], 'sha256': native['binarySha256']},
            'frozenPins': audit.PINS, 'seedManifest': seed,
            'compiledBuildIdentity': native['compiledBuildIdentity'], 'compiledBuildReport': native['compiledBuildReport']})
        recorded = {'native': native, 'binding': manifest['binding'], 'observationHead': manifest['observationHead'],
            'compiledHead': 'historical-compile-head', 'compiledBinding': compiled, 'seedManifest': seed,
            'sourceHelpers': [manifest['collector'], assessor, *refs]}
        fixture = ROOT / 'crates/engine/rustdoc/fixtures/automatic-models'
        original = {str(p.relative_to(fixture)): audit.sha(p) for p in fixture.rglob('*') if p.is_file()}
        plans, inputs, sentinels, exports = [], {}, [], []
        commands = [self.command() for _ in range(10)]
        for n, (mode, scenario) in enumerate((m, s) for m in audit.MODES for s in audit.SCENARIOS):
            base = self.root / f'{n:03}-{mode}-{scenario}'; root = base / 'fixture'; root.mkdir(parents=True, exist_ok=True)
            for name in original:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((fixture / name).read_bytes())
            for name in ('.cargo/config.toml', '.ignore', 'Cargo.lock'):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic prepared fixture input; no compiler invocation')
            inputs[str(root)] = {str(p.relative_to(root)): audit.sha(p) for p in root.rglob('*') if p.is_file()}
            plan = {'root': str(root), 'binary': native['binary'], 'scenario': scenario, 'preference': mode,
                'report': str(base / 'report.json'), 'events': str(base / 'events.jsonl'), 'control': str(base / 'control.json'),
                'unownedBuildDirectories': [str(base / 'ordinary-target')], 'artifactRoot': str(base / 'artifacts')}
            if scenario == 'lifecycle': plan['onlyCases'] = ['held-query-cleanup', 'failed-recovery']
            plans.append({**self.ref(str(base.relative_to(self.root) / 'plan.json'), plan), 'environment': {}})
            sentinels.append(self.ref(str(base.relative_to(self.root) / 'sentinel'), 'owned sentinel'))
            if scenario == 'prepared':
                command = {**self.command(), 'command': cargo['path'],
                           'args': ['rustdoc', '--locked', '--offline', '--output-format', 'json'], 'pid': 100 + n}
                command['cleanup'].update(processGroupId=command['pid'], inspectionError=None)
                commands[n] = command
                process = {key: command[key] for key in ('command', 'args', 'pid')}
                process.update(launchedCommand=command['command'], launchedArgs=command['args'],
                               processGroupId=command['pid'], startedAt='2026-10-10T01:00:00.000Z')
                self.ref(str(base.relative_to(self.root) / 'cleanup.json'),
                         {key: value for key, value in command['cleanup'].items() if value is not None})
                exports.append({'mode': mode,
                    'command': self.ref(str(base.relative_to(self.root) / 'process.json'), process),
                    'produced': self.ref(str(base.relative_to(self.root) / 'produced.json'), {'index': 'synthetic'}),
                    'staged': self.ref(str(base.relative_to(self.root) / 'staged.json'), {'index': 'synthetic'})})
        manifest['plans'] = plans
        prep = {'observationComplete': True, 'acceptance': False, 'errors': [],
            'commands': commands,
            'processCleanup': {'status': 'verified', 'runs': 10, 'verifiedRuns': 10},
            'bindingBefore': recorded, 'bindingAfter': copy.deepcopy(recorded), 'fixtureSourceInventory': original,
            'fixtureInputs': inputs, 'unownedOutputsBefore': {}, 'unownedOutputsAfter': {},
            'artifactSentinels': sentinels, 'preparedExports': exports, 'producerConfiguration': config, 'realCargo': cargo}
        prep_ref = self.ref('preparation.json', prep)
        manifest['preparationReport'] = prep_ref['path']
        manifest_ref = self.ref('manifest.json', manifest)
        ready = {'schema': 1, 'acceptance': False, 'bindingStage': 'recorded before policy collection',
            'preparationReport': prep_ref, 'manifest': manifest_ref, 'helper': prepare,
            'collector': manifest['collector'], 'assessor': assessor, 'probe': refs[0], 'producerProxy': refs[1],
            'observationHead': manifest['observationHead']}
        before = {'ready': self.ref('ready.json', ready), 'manifest': manifest_ref, 'preparation': prep_ref,
            'binding': recorded, 'environmentMustBeUnset': list(audit.ENVIRONMENT_MUST_BE_UNSET),
            'configuration': {'cargoHome': str(cargo_home), 'configurations': config['configurations'], 'realCargo': cargo},
            'plans': [{k: p[k] for k in ('path', 'sha256')} for p in plans],
            'fixtureInputs': inputs, 'unownedOutputs': {}, 'artifactSentinels': sentinels,
            'preparedExports': exports, 'wrapper': wrapper}
        observer.helpers = frozen_helpers()
        return {'bindingBefore': before, 'bindingAfter': copy.deepcopy(before)}, observer, manifest

    def test_full_actual_shaped_preparation_chain_no_ignored_code_import(self):
        parent, observer, manifest = self.prepared()
        actual, binding = audit.PolicyEvidence.preparation(parent, observer)
        self.assertEqual(actual, manifest)
        self.assertEqual(binding['source'], '1' * 64)
        self.assertEqual(len(actual['plans']), 8)

    def test_prepared_export_requires_unique_successful_outcome_with_actual_identity(self):
        for corruption in ('failure', 'missing', 'duplicate', 'wrong-pid', 'argv', 'group', 'physical-cleanup', 'inspection-error'):
            parent, observer, manifest = self.prepared()
            before = parent['bindingBefore']
            prep = json.loads(Path(before['preparation']['path']).read_text())
            command = prep['commands'][3]
            if corruption == 'failure': command['code'] = 42
            elif corruption == 'missing': prep['commands'][3] = self.command()
            elif corruption == 'duplicate': prep['commands'][0] = copy.deepcopy(command)
            elif corruption == 'wrong-pid': command['pid'] += 100
            elif corruption == 'argv': command['args'].remove('--offline')
            elif corruption == 'group': command['cleanup']['processGroupId'] += 1
            elif corruption == 'inspection-error': command['cleanup']['inspectionError'] = 'read failed'
            else:
                path = Path(prep['preparedExports'][0]['command']['path']).parent / 'cleanup.json'
                physical = json.loads(path.read_text()); physical['processGroupId'] += 1
                path.write_text(json.dumps(physical))
            prep_ref = self.ref('preparation.json', prep)
            ready = json.loads(Path(before['ready']['path']).read_text())
            ready['preparationReport'] = before['preparation'] = prep_ref
            before['ready'] = self.ref('ready.json', ready)
            parent['bindingAfter'] = copy.deepcopy(before)
            with self.subTest(corruption=corruption), self.assertRaises(ValueError):
                audit.PolicyEvidence.preparation(parent, observer)

    def test_manifest_preparation_path_and_ready_reference_corruptions_reject(self):
        for corruption in ('wrong-path', 'manifest-ref-object', 'ready-digest', 'ready-path'):
            parent, observer, manifest = self.prepared()
            before = parent['bindingBefore']
            ready = json.loads(Path(before['ready']['path']).read_text())
            if corruption == 'wrong-path':
                manifest['preparationReport'] = self.ref('other-preparation.json', {})['path']
            elif corruption == 'manifest-ref-object':
                manifest['preparationReport'] = ready['preparationReport']
            elif corruption == 'ready-digest':
                ready['preparationReport'] = {**ready['preparationReport'], 'sha256': '0' * 64}
            else:
                ready['preparationReport'] = self.ref('other-preparation.json', {})
            manifest_ref = self.ref('manifest.json', manifest)
            ready['manifest'] = manifest_ref
            before['manifest'] = manifest_ref
            before['ready'] = self.ref('ready.json', ready)
            parent['bindingAfter'] = copy.deepcopy(before)
            with self.subTest(corruption=corruption), self.assertRaisesRegex(ValueError,
                    'prepared source references differ|selected artifact changed|parent references differ'):
                audit.PolicyEvidence.preparation(parent, observer)

    def test_preparation_readiness_boundary_and_sentinel_changes_reject(self):
        for corruption in ('boundary', 'ready', 'sentinel', 'fixture'):
            parent, observer, _ = self.prepared()
            if corruption == 'boundary': parent['bindingAfter']['environmentMustBeUnset'].pop()
            elif corruption == 'ready': Path(parent['bindingBefore']['ready']['path']).write_text('{}')
            elif corruption == 'sentinel': Path(parent['bindingBefore']['artifactSentinels'][0]['path']).write_text('changed')
            else:
                root = next(iter(parent['bindingBefore']['fixtureInputs']))
                (Path(root) / 'src/lib.rs').write_text('changed source')
            with self.subTest(corruption=corruption), self.assertRaises(ValueError):
                audit.PolicyEvidence.preparation(parent, observer)

    def test_missing_or_reordered_plan_rejected_even_with_plausible_summary(self):
        parent, observer, manifest = self.prepared()
        prep = json.loads(Path(parent['bindingBefore']['preparation']['path']).read_text())
        for corruption in ('missing', 'reorder'):
            changed = copy.deepcopy(manifest)
            if corruption == 'missing': changed['plans'].pop()
            else: changed['plans'][0], changed['plans'][1] = changed['plans'][1], changed['plans'][0]
            with self.subTest(corruption=corruption), self.assertRaises(ValueError):
                audit.PolicyEvidence.plans(changed, parent['bindingBefore'], prep)

    def test_unknown_external_producer_helper_rejects(self):
        parent, observer, manifest = self.prepared()
        manifest['producerProxy'] = self.ref('arbitrary-proxy.py', 'metadata')
        with self.assertRaisesRegex(ValueError, 'unexpected producer proxy'):
            audit.PolicyEvidence.configuration(manifest, parent['bindingBefore'],
                json.loads(Path(parent['bindingBefore']['preparation']['path']).read_text()))





class PhysicalAttemptControls(unittest.TestCase):
    setUp = DurableBindingControls.setUp
    ref = DurableBindingControls.ref

    def fixture(self):
        parent_path = self.root / 'managed-collection.json'; parent_path.write_text('{}')
        native = '/synthetic/native-not-executed'
        collector = self.ref('collector.py', 'historical, never imported')
        manifest = {'collector': collector, 'plans': []}
        binding = {'binary': native}
        attempts, parent_artifacts = [], []
        for n, (mode, scenario) in enumerate((m, s) for m in audit.MODES for s in audit.SCENARIOS):
            work = self.root / 'collection' / f'{n:03}-{mode}-{scenario}'; work.mkdir(parents=True)
            fixture = self.root / f'fixture-{n}'; fixture.mkdir()
            plan = {'preference': mode, 'scenario': scenario, 'root': str(fixture), 'binary': native,
                    'report': str(fixture / 'report.json'), 'events': str(fixture / 'events.jsonl'),
                    'control': str(fixture / 'control.json')}
            plan_ref = self.ref(f'fixture-{n}/plan.json', plan)
            manifest['plans'].append({**plan_ref, 'environment': {}})
            query = query_fixture()
            query.update(plan=plan, planSha256=plan_ref['sha256'], fatal=None,
                         cases={scenario: {'passed': True, 'attempted': True}},
                         clientWrites=[], workerMessages=[], evidence={'events': []})
            path = work / 'lsp-server.stderr.log'; path.write_text('{"message":"native fixture"}\n')
            query['nativeStderr'] = {'path': str(path), 'sha256': audit.sha(path),
                    'bytes': path.stat().st_size, 'recordedBytes': path.stat().st_size,
                    'truncated': False, 'afterClose': True, 'serverPid': 10, 'serverReturnCode': 0}
            q = query['rawQueries'][0]
            raw = fixture / 'raw-queries.jsonl'
            raw.write_text(''.join(json.dumps({'event': event, 'row': q}) + '\n' for event in ('send-called','sent','response')))
            query['rawTransportReference'] = {'path': str(raw), 'sha256': audit.sha(raw)}
            report = fixture / 'report.json'; report.write_text(json.dumps(query))
            trace = work / 'process.exec'; trace.write_text(f'10 0.000001 execve("{native}", ["native"], 0x0) = 0\n')
            cleanup = {'verifiedEmpty': True, 'remainingPids': [], 'processGroupId': 100 + n, 'inspectionError': None}
            command = {'command': 'strace', 'args': ['-ttt', '--inner', str(fixture / 'plan.json')],
                       'pid': 100 + n, 'code': 0, 'timedOut': False,
                       'signal': None, 'spawnError': None, 'cleanup': cleanup}
            process = {k: command[k] for k in ('command','args','pid')}
            process.update(processGroupId=command['pid'], launchedCommand=command['command'], launchedArgs=command['args'])
            (work / 'process.json').write_text(json.dumps(process))
            (work / 'cleanup.json').write_text(json.dumps({key:value for key,value in cleanup.items() if value is not None}))
            artifacts = [{'path': str(p), 'sha256': audit.sha(p)} for p in
                        (report, raw, trace, work / 'process.json', work / 'cleanup.json', path)]
            attempt = {'plan': plan_ref, 'report': str(report), 'trace': str(trace),
                       'command': command, 'artifacts': artifacts}
            attempts.append(attempt); parent_artifacts += artifacts
        manifest_ref = self.ref('manifest.json', manifest)
        collection = {'manifest': manifest_ref, 'collector': collector,
            'bindingBefore': binding, 'bindingAfter': binding, 'fixtureInputsBefore': {},
            'fixtureInputsAfter': {}, 'observationComplete': True, 'errors': [], 'attempts': attempts}
        inventories = {str(self.root / f'fixture-{n}'): {'synthetic': str(self.root / f'fixture-{n}')} for n in range(8)}
        collection.update(fixtureInputsBefore=inventories, fixtureInputsAfter=copy.deepcopy(inventories))
        ref = self.ref('collection/collection.json', collection)
        parent = {'collectionReference': ref, 'bindingBefore': {'manifest': manifest_ref},
                  'scenarioOutcomes': attempts, 'retainedArtifacts': parent_artifacts}
        return parent_path, parent, manifest, binding, collection

    def evaluate(self, fixture):
        path, parent, manifest, binding, _ = fixture
        # Composition seam only: scenario semantics have independent raw controls.
        # No synthetic fixture is native acceptance evidence.
        with patch.dict(audit.COLLECTOR_STATES, {manifest['collector']['sha256']: 'pending'}), \
             patch.object(audit, 'check_scenario', side_effect=lambda report, state:
                          {'scenario': report['plan']['scenario']}):
            observer = SimpleNamespace(Diagnostic=SimpleNamespace(inventory=lambda root: {'synthetic': str(root)}), helpers=frozen_helpers())
            return audit.PolicyEvidence.collection(path, parent, manifest, binding, LspFixture, observer)

    def update_collection(self, fixture):
        _, parent, _, _, collection = fixture
        path = Path(parent['collectionReference']['path']); path.write_text(json.dumps(collection))
        parent['collectionReference']['sha256'] = audit.sha(path)

    def test_all_eight_physical_attempts_rechecked_without_importing_historical_code(self):
        result = self.evaluate(self.fixture())
        self.assertTrue(result['auditValid'])
        self.assertEqual(len(result['findings']), 8)
        self.assertEqual(len(result['allAttempts']), 8)

    def test_raw_null_success_rejected_despite_successful_process_and_summary(self):
        fixture = self.fixture(); attempt = fixture[4]['attempts'][3]
        path = Path(attempt['report']); raw = json.loads(path.read_text())
        raw['rawQueries'][0]['response']['result'] = None
        raw['rawQueries'][0]['responses'] = [copy.deepcopy(raw['rawQueries'][0]['response'])]
        path.write_text(json.dumps(raw))
        for ref in attempt['artifacts']:
            if ref['path'] == str(path): ref['sha256'] = audit.sha(path)
        self.update_collection(fixture)
        result = self.evaluate(fixture)
        self.assertFalse(result['auditValid'])
        self.assertEqual(len(result['physicalAttempts']), 8)
        self.assertIsNone(result['allAttempts'][3]['rawQueries'][0]['response']['result'])

    def test_nonzero_empty_scenario_and_missing_eighth_remain_failures(self):
        fixture = self.fixture(); fixture[4]['attempts'][2]['command']['code'] = 143
        self.update_collection(fixture)
        result = self.evaluate(fixture)
        self.assertFalse(result['auditValid'])
        self.assertEqual(result['physicalAttempts'][2]['command']['code'], 143)
        self.assertTrue(result['physicalAttempts'][2]['command']['cleanup']['verifiedEmpty'])
        fixture[4]['attempts'].pop(); self.update_collection(fixture)
        result = self.evaluate(fixture)
        self.assertFalse(result['auditValid'])
        self.assertEqual(len(result['physicalAttempts']), 7)

    def test_changed_actual_process_or_omitted_raw_artifact_rejected(self):
        fixture = self.fixture(); attempt = fixture[4]['attempts'][0]
        attempt['artifacts'] = [ref for ref in attempt['artifacts'] if not ref['path'].endswith('raw-queries.jsonl')]
        self.update_collection(fixture)
        self.assertFalse(self.evaluate(fixture)['auditValid'])

    def test_real_result_shape_has_pid_and_cleanup_group_not_top_group(self):
        fixture = self.fixture()
        self.assertNotIn('processGroupId', fixture[4]['attempts'][0]['command'])
        self.assertTrue(self.evaluate(fixture)['auditValid'])
        command = fixture[4]['attempts'][0]['command']
        command['cleanup']['processGroupId'] += 1
        work = Path(fixture[4]['attempts'][0]['trace']).parent
        (work / 'cleanup.json').write_text(json.dumps(command['cleanup']))
        for ref in fixture[4]['attempts'][0]['artifacts']:
            if ref['path'] == str(work / 'cleanup.json'): ref['sha256'] = audit.sha(work / 'cleanup.json')
        self.update_collection(fixture)
        result = self.evaluate(fixture)
        self.assertFalse(result['auditValid'])
        self.assertTrue(any('process group' in error['error'] for error in result['errors']))

    def test_physical_cleanup_non_null_error_or_group_corruption_rejects(self):
        for field, value in (('inspectionError', 'cannot inspect'), ('processGroupId', 999)):
            self.setUp()
            fixture = self.fixture(); attempt = fixture[4]['attempts'][0]
            path = Path(attempt['trace']).parent / 'cleanup.json'
            physical = json.loads(path.read_text()); physical[field] = value
            path.write_text(json.dumps(physical))
            for ref in attempt['artifacts']:
                if ref['path'] == str(path): ref['sha256'] = audit.sha(path)
            self.update_collection(fixture)
            with self.subTest(field=field): self.assertFalse(self.evaluate(fixture)['auditValid'])





class ProcessAncestryControls(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory(dir=audit.OWNED) as d:
            path = Path(d) / 'process.exec'; path.write_text(text)
            return audit.process_parents(path)

    def test_real_shaped_resumed_clone_and_failed_clone3(self):
        parents = self.parse('10 1.1 clone3({flags=CLONE_VM}, 88) = -1 ENOSYS (Function not implemented)\n'
            '10 1.2 clone(child_stack=0x1, flags=CLONE_VM|CLONE_THREAD <unfinished ...>\n'
            '20 1.3 execve("n", ["n"], 0x0) = 0\n'
            '10 1.4 <... clone resumed>) = 20\n'
            '20 1.5 vfork() = 30\n')
        self.assertEqual(parents, {20: 10, 30: 20})

    def test_orphan_and_unknown_process_creation_are_not_guessed(self):
        for text in ('10 1.1 <... clone resumed>) = 20\n',
                     '10 1.1 fork() = ?\n',
                     '10 1.1 clone(0x0 <unfinished ...>\n'):
            with self.subTest(text=text), self.assertRaises(ValueError): self.parse(text)

    def test_actual_producer_exec_needs_native_ancestry(self):
        report = query_fixture(); report['evidence'] = {'events': [{'event': 'started', 'pid': 30}]}
        rows = [{'pid': 10, 'wallNs': 0, 'strings': ['/native'], 'success': True},
                {'pid': 30, 'wallNs': 300, 'strings': ['/proxy/cargo', 'rustdoc'], 'success': True}]
        audit.check_exec(rows, report, '/native', {30: 20, 20: 10})
        with self.assertRaisesRegex(ValueError, 'traced native'):
            audit.check_exec(rows, report, '/native', {30: 40})

    def test_compiler_split_span_crossing_query_is_rejected(self):
        report = query_fixture(); report['evidence'] = {'events': []}
        rows = [{'pid': 10, 'wallNs': 0, 'strings': ['/native'], 'success': True},
                {'pid': 30, 'wallNs': 99, 'endWallNs': 101, 'strings': ['rustdoc'], 'success': True}]
        with self.assertRaisesRegex(ValueError, 'ordinary query window'):
            audit.check_exec(rows, report, '/native')





class GateCompositionControls(unittest.TestCase):
    setUp = DurableBindingControls.setUp
    ref = DurableBindingControls.ref
    physical_fixture = PhysicalAttemptControls.fixture

    def fixture(self):
        path, parent, manifest, binding, _ = self.physical_fixture()
        work = self.root / 'supervisor'; work.mkdir()
        args = ['-B', manifest['collector']['path'], '--collect', parent['bindingBefore']['manifest']['path'],
                '--output', str(self.root / 'collection')]
        cleanup = {'verifiedEmpty': True, 'remainingPids': [], 'processGroupId': 2000, 'inspectionError': None}
        command = {'command': 'python3', 'args': args, 'pid': 2000, 'code': 0,
                   'timedOut': False, 'signal': None, 'spawnError': None, 'cleanup': cleanup}
        parent.update(collectionComplete=True, executionErrors=[], boundaryErrors=[], collectionErrors=[],
            commands=[command], processCleanup={'status': 'verified', 'runs': 1, 'verifiedRuns': 1},
            scenarioCleanup={'status': 'verified', 'runs': 8, 'verifiedRuns': 8}, ordinaryQueryTimeoutMs=30000,
            perScenarioAggregateDeadlineMs=1800000, parentAggregateDeadlineMs=14700000,
            attemptedCollectorInvocation={'command': 'python3', 'args': args, 'cwd': str(ROOT), 'timeoutMs':14700000})
        (work / 'process.json').write_text(json.dumps({**{k: command[k] for k in ('command','args','pid')}, 'processGroupId':2000}))
        (work / 'cleanup.json').write_text(json.dumps({key:value for key,value in cleanup.items() if value is not None}))
        path.write_text(json.dumps(parent))
        selector = self.root / 'selection.json'
        selector.write_text(json.dumps({'schema': 1, 'policy': {'path': str(path), 'sha256': audit.sha(path)}}))
        from unittest.mock import Mock
        evidence = SimpleNamespace(manifest=selector, native=Mock(return_value={'cases': {'three_native_cases': True}}),
            observer=SimpleNamespace(helpers=frozen_helpers()))
        raw = {'auditValid': True, 'errors': [], 'physicalAttempts': parent['scenarioOutcomes'],
               'allAttempts': [{'attempt':n, 'rawQueries': [query_fixture()['rawQueries'][0]]} for n in range(8)]}
        return evidence, path, parent, manifest, binding, raw

    def assess(self, fixture):
        evidence, _, _, manifest, binding, raw = fixture
        with patch.object(audit.PolicyEvidence, 'preparation', return_value=(manifest,binding)), \
             patch.object(audit.PolicyEvidence, 'collection', return_value=raw):
            return audit.PolicyEvidence.assess(evidence)

    def test_success_returns_computed_proof_without_pass_boolean(self):
        fixture = self.fixture(); result = self.assess(fixture)
        self.assertNotIn('passed', result)
        self.assertTrue(result['auditValid'])
        fixture[0].native.assert_called_once_with('RSP-005')
        self.assertEqual(result['sourcePolicy']['deadlineSeconds'],30)

    def test_failed_raw_gate_raises_with_every_observation(self):
        fixture = self.fixture(); fixture[-1].update(auditValid=False, errors=[{'error':'a genuine query failed'}])
        with self.assertRaises(audit.PolicyEvidenceError) as caught: self.assess(fixture)
        self.assertEqual(len(caught.exception.observations['physicalAttempts']),8)
        self.assertEqual(len(caught.exception.observations['allAttempts']),8)

    def test_native_three_failure_cannot_be_hidden_by_successful_raw_summary(self):
        fixture = self.fixture(); fixture[0].native.side_effect = ValueError('InternalError native case failed')
        with self.assertRaises(audit.PolicyEvidenceError) as caught: self.assess(fixture)
        self.assertIn('InternalError', str(caught.exception))
        self.assertEqual(len(caught.exception.observations['allAttempts']),8)

    def test_parent_nonzero_empty_cleanup_and_wrong_group_reject(self):
        fixture = self.fixture(); fixture[2]['commands'][0]['code'] = 143
        fixture[1].write_text(json.dumps(fixture[2]))
        selector = json.loads(fixture[0].manifest.read_text()); selector['policy']['sha256']=audit.sha(fixture[1])
        fixture[0].manifest.write_text(json.dumps(selector))
        with self.assertRaises(audit.PolicyEvidenceError) as caught: self.assess(fixture)
        self.assertEqual(caught.exception.observations['parentCommands'][0]['code'],143)
        self.assertTrue(caught.exception.observations['parentCommands'][0]['cleanup']['verifiedEmpty'])
        fixture[2]['commands'][0]['code'] = 0
        fixture[2]['commands'][0]['cleanup']['processGroupId'] = 2001
        (self.root / 'supervisor/cleanup.json').write_text(json.dumps(fixture[2]['commands'][0]['cleanup']))
        fixture[1].write_text(json.dumps(fixture[2]))
        selector['policy']['sha256'] = audit.sha(fixture[1]); fixture[0].manifest.write_text(json.dumps(selector))
        with self.assertRaises(audit.PolicyEvidenceError) as caught: self.assess(fixture)
        self.assertIn('parent physical process/cleanup', str(caught.exception))

    def test_fatal_preparation_guard_salvages_raw_physical_attempts(self):
        fixture = self.fixture()
        with patch.object(audit.PolicyEvidence, 'preparation', side_effect=ValueError('changed guard')), \
             self.assertRaises(audit.PolicyEvidenceError) as caught:
            audit.PolicyEvidence.assess(fixture[0])
        self.assertEqual(len(caught.exception.observations['allAttempts']),8)
        self.assertTrue(all(row['unvalidated'] for row in caught.exception.observations['allAttempts']))

    def test_parent_physical_cleanup_retains_non_null_error_and_group_checks(self):
        for field, value in (('inspectionError', 'cannot inspect'), ('processGroupId', 999)):
            self.setUp()
            fixture = self.fixture(); path = self.root / 'supervisor/cleanup.json'
            physical = json.loads(path.read_text()); physical[field] = value
            path.write_text(json.dumps(physical))
            with self.subTest(field=field), self.assertRaises(audit.PolicyEvidenceError):
                self.assess(fixture)





class HandlerPolicyControls(unittest.TestCase):
    def setUp(self):
        self.sources = {name: path.read_text() for name, path in audit.HANDLERS.items()}

    def test_three_current_handlers_shared_policy_and_inlay_trace_limitation(self):
        facts = audit.handler_policy(self.sources)
        self.assertEqual(set(facts['handlers']), {'hover','completion','inlay_hint'})
        self.assertNotIn('textDocument/inlayHint', facts['externalQueryMethods'])
        self.assertIn('per semantic invocation', facts['completionRecapture'])
        self.assertIn('no inlay request', facts['inlayEvidence'])

    def test_rebuild_operation_or_direct_producer_in_query_handler_rejected(self):
        changed = copy.deepcopy(self.sources)
        changed['hover'] = changed['hover'].replace('.query("hover",', '.query("did_save",')
        with self.assertRaisesRegex(ValueError, 'reviewed query operation'): audit.handler_policy(changed)
        changed = copy.deepcopy(self.sources)
        changed['hover'] = changed['hover'].replace('    Ok(hover)', '    ctx.engine_client.rustdoc_requested(1);\n    Ok(hover)')
        with self.assertRaisesRegex(ValueError, 'producer/update'): audit.handler_policy(changed)

    def test_changed_inlay_native_call_or_swallowed_completion_error_rejected(self):
        changed = copy.deepcopy(self.sources)
        changed['inlay_hint'] = changed['inlay_hint'].replace('engine_client.inlay_hint(request_context,', 'engine_client.hover(request_context,')
        with self.assertRaisesRegex(ValueError, 'different native method'): audit.handler_policy(changed)
        changed = copy.deepcopy(self.sources)
        changed['completion'] = changed['completion'].replace('Err(error) => return Err(methods::into_lsp_error(error)),', 'Err(error) => continue,')
        with self.assertRaisesRegex(ValueError, 'recapture/error policy'): audit.handler_policy(changed)


if __name__ == '__main__': unittest.main()
