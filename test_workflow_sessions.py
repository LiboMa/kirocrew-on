"""Native Session operations: scope, idempotency, recovery and data retention."""
import copy
import io
from pathlib import Path
import json
import socket
import threading
import unittest
from unittest.mock import patch
import zipfile
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import api
import workflow as w
import workflow_sessions as sessions
import test_workflow as fixtures


class SessionOperationsTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    prepare = fixtures.WorkflowTests.prepare
    seed_retry = fixtures.WorkflowTests.seed_retry

    def native(self, *, running=False, lag=False):
        self.manifest, self.run = self.prepare()
        self.snapshot, self.receipts, self.tasks = self.seed_retry(self.manifest, self.run)
        self.parent = {"running": running, "messages": [], "queue": []}
        self.calls = []
        self.closed = False
        self.deleted = False
        self.lag = lag
        self.tasks.append({"id":"deadbeef", "parent":"dashboard:another-session", "done":False})
        self.run.joinpath("workspace/project/keep.txt").write_text("user source survives")
        self.original = self.run.joinpath("workflow-run.json").read_bytes()
        self.input = self.run.joinpath("input.json").read_bytes()
        self.patchers += [patch.object(w, "dispatch_receipts", side_effect=lambda m:self.receipts),
                          patch.object(w, "route_for", return_value={"actual_backend":"claude", "cwd":str(self.run/'workspace/project')}),
                          patch.object(api, "request", side_effect=self.request)]
        for p in self.patchers[-3:]: p.start()
        return self.run

    def request(self, path, body=None, method=None):
        self.calls.append((path, body, method))
        slot = "/api/chat/slots/"+self.manifest["slot"]
        if path == slot and method == "DELETE":
            self.closed = True
            return {"ok":True}
        if path == slot:
            if self.closed: raise api.GatewayError(404,"slot not found")
            return copy.deepcopy(self.parent)
        if path == "/api/spawn": return {"agents":copy.deepcopy(self.tasks)}
        if path == "/api/approvals": return {"approvals":[]}
        if path == slot+"/stop?force=true":
            if not self.lag: self.parent["running"] = False
            return {"ok":True}
        if path == "/api/spawn/stop-all":
            self.assertEqual(body,{"slot":self.manifest["slot"]})
            if not self.lag:
                for t in self.tasks:
                    if t["parent"] == self.manifest["parent"] and t.get("done") is not True:
                        t.update(done=True,stopped=True)
            return {"ok":True,"stopped":1}
        if path == "/api/chat?ws=1":
            self.parent["messages"].append({"role":"user","content":body["message"]})
            return {"ok":True}
        if path == slot+"/resume":
            self.assertEqual(body,{"key":self.manifest["parent"]})
            self.closed = False
            return {"ok":True,"slot":self.manifest["slot"]}
        if path.startswith("/api/sessions/") and method == "DELETE":
            self.assertEqual(path,"/api/sessions/dashboard%3A"+self.manifest["id"])
            self.deleted = True
            return {"ok":True}
        raise AssertionError((path,body,method))

    def writes(self):
        return [c for c in self.calls if c[1] is not None or c[2] == "DELETE"]

    def zip_bytes(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('native-session.json','{"messages":[]}')
        return out.getvalue()

    def unchanged(self):
        self.assertEqual(self.run.joinpath('workflow-run.json').read_bytes(),self.original)
        self.assertEqual(self.run.joinpath('input.json').read_bytes(),self.input)
        self.assertEqual(self.run.joinpath('workspace/project/keep.txt').read_text(),'user source survives')
        self.assertEqual(len(list(w.RUNS.glob('workflow-*'))),1)

    def test_continue_edits_original_run_and_reuses_native_conversation(self):
        run=self.native()
        result=sessions.continue_session(run,'code','修改为支持导出与错误提示','修复错误处理，运行真实测试','continue-test-1')
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(result['input_revision'],1)
        next_call=w.next_step(run)
        self.assertEqual(next_call['tool'],'spawn_continue')
        self.assertEqual(next_call['continue']['conversation'],'abc00001')
        self.assertEqual(next_call['spawn']['cwd'],str(run/'workspace/project'))
        instructions=(run/'workspace/code-instructions.md').read_text()
        self.assertIn('修改为支持导出与错误提示',instructions)
        self.assertIn('修复错误处理，运行真实测试',instructions)
        self.assertIn('本步任务摘要：修复错误处理，运行真实测试',next_call['continue']['task'])
        self.assertIn('不得用旧需求覆盖本次修订',instructions)
        sends=self.writes()
        self.assertEqual(len(sends),1)
        self.assertEqual(sends[0][0],'/api/chat?ws=1')
        self.assertEqual(sends[0][1]['slot'],self.manifest['slot'])
        self.assertTrue((run/'continuations/continue-test-1.json').exists())
        self.unchanged()

    def test_continue_repeated_request_is_once_and_changed_body_conflicts(self):
        run=self.native()
        a=sessions.continue_session(run,'code','需求修订内容','任务修订内容','continue-test-2')
        b=sessions.continue_session(run,'code','需求修订内容','任务修订内容','continue-test-2')
        self.assertEqual(a,b)
        self.assertEqual(len(self.writes()),1)
        with self.assertRaisesRegex(ValueError,'冲突'):
            sessions.continue_session(run,'code','另一个需求','任务修订内容','continue-test-2')

    def test_ambiguous_send_is_reconciled_without_resending(self):
        run=self.native()
        original=self.request
        def timeout(path,body=None,method=None):
            result=original(path,body,method)
            if path=='/api/chat?ws=1':raise TimeoutError('ack lost')
            return result
        with patch.object(api,'request',side_effect=timeout):
            result=sessions.continue_session(run,'code','完整需求内容','任务修订内容','continue-test-3')
        self.assertEqual(result['status'],'uncertain')
        result=sessions.continue_session(run,'code','完整需求内容','任务修订内容','continue-test-3')
        self.assertEqual(result['status'],'accepted')
        self.assertTrue(result['reconciled'])
        self.assertEqual(len(self.writes()),1)
        with patch.object(api,'request',side_effect=timeout):
            result=sessions.continue_session(run,'code','另一轮完整需求','新任务修订内容','continue-test-3b')
        self.assertEqual(result['status'],'uncertain')
        sessions.control(run,'stop','stop-uncertain-3')
        with self.assertRaisesRegex(ValueError,'新的请求 ID'):
            sessions.continue_session(run,'code','另一轮完整需求','新任务修订内容','continue-test-3b')
        result=sessions.continue_session(run,'code','另一轮完整需求','新任务修订内容','continue-test-3c')
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(result['input_revision'],3)

    def test_running_task_refuses_continue_before_any_mutation(self):
        run=self.native(running=True)
        with self.assertRaisesRegex(ValueError,'仍在运行'):
            sessions.continue_session(run,'code','完整需求内容','任务修订内容','continue-test-4')
        self.assertEqual(self.writes(),[])
        self.assertFalse((run/'retry-state.json').exists())
        self.unchanged()

    def test_stop_is_scoped_and_fences_dispatch_until_continue(self):
        run=self.native(running=True)
        self.tasks[1]['done']=False
        result=sessions.control(run,'stop','operation-stop-1')
        self.assertEqual(result['session_status'],'stopped')
        self.assertFalse(self.tasks[-1]['done'])
        self.assertTrue(self.tasks[1]['done'])
        with self.assertRaisesRegex(ValueError,'已停止'):
            w.next_step(run)
        count=len(self.writes())
        sessions.control(run,'stop','operation-stop-1')
        self.assertEqual(len(self.writes()),count)
        resumed=sessions.continue_session(run,'code','停止后的修订需求','继续任务并验证','continue-after-stop')
        self.assertEqual(resumed['status'],'accepted')
        self.unchanged()

    def test_stop_pending_is_not_reported_as_stopped(self):
        run=self.native(running=True,lag=True)
        self.tasks[1]['done']=False
        result=sessions.control(run,'stop','operation-stop-2')
        self.assertEqual(result['status'],'pending')
        self.assertEqual(sessions.state(run)['status'],'stopping')
        with self.assertRaisesRegex(ValueError,'仍在运行'):
            sessions.continue_session(run,'code','停止后的修订需求','继续任务并验证','continue-pending')

    def test_close_and_reopen_keep_original_identity_and_backup(self):
        run=self.native()
        with patch.object(sessions.library,'export_sessions',return_value=self.zip_bytes()) as export:
            result=sessions.control(run,'close','operation-close-1')
        self.assertEqual(result['session_status'],'closed')
        self.assertTrue(sessions.backup_path(run).exists())
        export.assert_called_once_with([run.name])
        result=sessions.control(run,'reopen','operation-open-1')
        self.assertEqual(result['session_status'],'stopped')
        self.assertFalse(self.closed)
        self.assertIsNone(sessions.state(run)['backup'])
        self.unchanged()

    def test_delete_requires_exact_id_and_backup_before_native_history_delete(self):
        run=self.native()
        with self.assertRaisesRegex(ValueError,'完整 Session ID'):
            sessions.control(run,'delete','operation-delete-1','wrong')
        self.assertEqual(self.writes(),[])
        with patch.object(sessions.library,'export_sessions',return_value=self.zip_bytes()):
            result=sessions.control(run,'delete','operation-delete-1',run.name)
        self.assertEqual(result['session_status'],'deleted')
        self.assertTrue(self.deleted)
        self.assertTrue(sessions.backup_path(run).is_file())
        self.assertTrue(w.library.session_meta(run)['deleted'])
        with self.assertRaisesRegex(ValueError,'重新打开'):
            sessions.continue_session(run,'code','修订需求','任务说明','continue-deleted')
        self.unchanged()

    def test_backup_failure_refuses_close_and_delete(self):
        run=self.native()
        with patch.object(sessions.library,'export_sessions',side_effect=RuntimeError('export failed')):
            result=sessions.control(run,'delete','operation-delete-2',run.name)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(self.closed)
        self.assertFalse(self.deleted)
        self.assertFalse(any(c[2]=='DELETE' for c in self.calls))
        self.unchanged()

    def test_invalid_stage_and_cross_session_identity_never_mutate_native(self):
        run=self.native()
        with self.assertRaises(ValueError):
            sessions.continue_session(run,'../code','修订需求','任务说明','continue-invalid')
        manifest=copy.deepcopy(self.manifest);manifest['slot']='another-session'
        w.write_json(run/'workflow-run.json',manifest)
        with self.assertRaisesRegex(ValueError,'身份不一致'):
            sessions.control(run,'stop','operation-stop-3')
        self.assertEqual(self.writes(),[])

    def test_http_operations_require_same_origin_and_exact_target(self):
        run=self.native()
        (self.root/'state').mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        ready=threading.Event();holder={}
        def server(address,handler):
            holder['server']=ThreadingHTTPServer(address,handler);ready.set();return holder['server']
        def call(path,body,origin):
            headers={'Content-Type':'application/json','X-Workflow-MVP':'1'}
            if origin:headers['Origin']=origin
            request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body).encode(),headers=headers)
            try:
                with urllib.request.urlopen(request,timeout=3) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as e:
                with e:return e.code,json.load(e)
        with patch.object(w,'ROOT',self.root),patch.object(w,'observe',return_value=None),patch.object(w,'ThreadingHTTPServer',side_effect=server):
            thread=threading.Thread(target=w.serve,args=(port,),daemon=True);thread.start()
            self.assertTrue(ready.wait(3))
            try:
                origin=f'http://127.0.0.1:{port}'
                control={'run':run.name,'action':'delete','request_id':'http-delete-1','confirmation':run.name}
                continuation={'run':run.name,'stage':'code','input':'修订需求','task_prompt':'修订说明','request_id':'http-continue-1'}
                for path,body in [('/api/session/control',control),('/api/session/continue',continuation)]:
                    self.assertEqual(call(path,body,None)[0],403)
                    self.assertEqual(call(path,body,'http://foreign.test')[0],403)
                    self.assertEqual(call(path,{**body,'run':'../outside'},origin)[0],400)
                self.assertEqual(call('/api/session/control',{**control,'confirmation':'wrong'},origin)[0],400)
                self.assertEqual(self.writes(),[])
                code,result=call('/api/session/control',{'run':run.name,'action':'stop','request_id':'http-stop-1'},origin)
                self.assertEqual(code,200);self.assertEqual(result['session_status'],'stopped')
            finally:
                holder['server'].shutdown();holder['server'].server_close();thread.join(3)


if __name__=='__main__':unittest.main()
