/* Run-scoped continuation and explicit native lifecycle operations. */
const WorkflowSessionOps = (() => {
  let ready=false, busy=false, target="", action="", draft={}, data=null;
  const key=run=>"kirocrew.workflow.session-operation.v1:"+run;
  const node=id=>document.getElementById(id);
  const dialog=()=>node("session-operation");
  function save(){if(!target)return;try{localStorage.setItem(key(target),JSON.stringify(draft));}catch{message("浏览器草稿未能保存，请保持窗口打开。",true);}}
  function message(text,error=false){node("session-operation-message").textContent=text;node("session-operation-message").className=error?"hint error":"hint";}
  function rows(){return data?.steps||[];}
  function inFlight(){return !!data?.parent_running||!!data?.approvals?.length||rows().some(s=>s.task&&s.task.done!==true||["running","submitted","awaiting_approval"].includes(s.status));}
  function render(snapshot,capable=ready){
    data=snapshot;ready=capable;
    if(dialog().open&&target!==data?.manifest?.id&&!busy)dialog().close();
    const status=data?.session_control?.status||"active",exists=!!data?.manifest;
    node("continue-session").hidden=!exists||["closed","deleted"].includes(status);
    node("continue-session").disabled=!ready||busy||data?.connected===false||inFlight();
    node("continue-session").title=!ready?"请手动重启工作台以加载 Session 运维接口。":inFlight()?"先强制停止正在运行的任务，再编辑续作。":"编辑需求和任务说明，在本条 Session 和工作区中继续。";
    for(const name of ["stop","close","delete","reopen"]){
      const n=node("session-"+name);
      n.disabled=!ready||busy||!exists||data?.connected===false;
      n.hidden=name==="reopen"?status!=="closed":status==="deleted"||name!=="delete"&&status==="closed";
      n.title=ready?"":"请手动重启工作台以加载运维接口。";
    }
    node("session-backup").hidden=!data?.session_control?.backup;
    if(exists)node("session-backup").href="/api/session/backup?run="+encodeURIComponent(data.manifest.id);
    if(dialog().open&&target===data?.manifest?.id){
      dialog().querySelectorAll("input,textarea,select").forEach(n=>n.disabled=busy);
      node("session-operation-submit").disabled=busy||action==="continue"&&inFlight()||action==="delete"&&node("session-delete-confirm").value!==target;
    }
  }
  function open(kind){
    if(!ready||busy||!data?.manifest)return;
    target=data.manifest.id;action=kind;
    try{draft=JSON.parse(localStorage.getItem(key(target))||"{}");}catch{draft={};}
    node("session-operation-title").textContent={continue:"继续本 Session",stop:"强制停止 Session",close:"关闭 Session",reopen:"重新打开 Session",delete:"删除 Session"}[kind];
    node("session-operation-identity").textContent=target;
    node("session-operation-scope").textContent="原工作区："+data.manifest.run+"/workspace";
    node("session-operation-description").textContent={
      continue:"保留本条 KiroCrew 对话与工作区。可修改本次需求和所选任务说明，从指定阶段重跑；下游交接需要重新验证。工具、模型与任务顺序保持本次配置。",
      stop:"通过 KiroCrew 强制停止本条父对话及其运行、排队中的子任务。已写入的源码保留，停止不会回滚文件。",
      close:"先停止本条会话，导出备份，再关闭 KiroCrew 对话。历史和工作区保留，可重新打开后继续。",
      reopen:"从 KiroCrew 原生历史重新打开相同 Session ID。不会创建新 Run 或工作区；原生工具进程可能需要重新建立。",
      delete:"先停止、备份并关闭，再永久删除本条 KiroCrew 父对话历史。工作区、任务证据和运维备份保留；删除后不能直接续作。"
    }[kind];
    node("session-continue-form").hidden=kind!=="continue";node("session-delete-field").hidden=kind!=="delete";
    node("session-delete-confirm").value="";
    node("session-operation-submit").textContent={continue:"保存修订并继续",stop:"确认强制停止",close:"确认关闭",reopen:"重新打开",delete:"备份并删除"}[kind];
    node("session-operation-submit").className=kind==="delete"||kind==="stop"?"danger-action":"primary";
    if(kind==="continue"){
      const stages=rows();
      const chosen=stages.find(s=>s.id===draft.stage)||stages.find(s=>["failed","check_failed","pending","awaiting_check"].includes(s.status))||stages.at(-1);
      node("session-continue-stage").replaceChildren(...stages.map(s=>{const o=document.createElement("option");o.value=s.id;o.textContent=s.name+" · "+s.id;return o;}));
      node("session-continue-stage").value=chosen?.id||"";
      node("session-continue-input").value=draft.input??data.input?.text??"";
      draft.prompts=draft.prompts||{};
      node("session-continue-prompt").value=draft.prompts[chosen?.id]??chosen?.prompt??"";
    }
    node("session-menu").open=false;message("");dialog().showModal();render(data);
  }
  function changed(){
    draft.input=node("session-continue-input").value;draft.stage=node("session-continue-stage").value;
    draft.prompts={...draft.prompts,[draft.stage]:node("session-continue-prompt").value};save();
  }
  async function submit(){
    if(busy)return;
    if(action==="continue"&&inFlight())return message("Session 尚未停止，请等待原生任务结束。",true);
    const run=target,kind=action;
    const body={run,...(kind==="continue"?{stage:node("session-continue-stage").value,input:node("session-continue-input").value.trim(),task_prompt:node("session-continue-prompt").value.trim()}:{action:kind,confirmation:node("session-delete-confirm").value})};
    if(kind==="continue"&&(body.input.length<3||body.task_prompt.length<3))return message("请填写开发需求和任务说明（至少 3 个字符）。",true);
    if(kind==="continue")changed();
    const signature=JSON.stringify([kind,body]);
    draft.requests=draft.requests||{};
    const request_id=draft.requests[signature]||crypto.randomUUID();draft.requests[signature]=request_id;save();
    busy=true;node("session-operation-cancel").disabled=true;node("session-operation-submit").disabled=true;
    render(data);
    message(kind==="continue"?"正在保存修订并发送到原会话…":"正在调用 KiroCrew，请等待实际回执…");
    try{
      const result=await post(kind==="continue"?"/api/session/continue":"/api/session/control",{...body,request_id});
      if(!["completed","accepted"].includes(result.status)){
        message((result.status==="uncertain"?"发送结果待确认，请在原 KiroCrew 对话核对；重复点击不会再发送。":result.status==="pending"?"停止处理中，请稍后重试同一操作。":"操作未完成：")+(result.error||""),true);
        return;
      }
      delete draft.requests[signature];
      // Explicit lifecycle actions end any ambiguous prior send. Continuing
      // afterwards must create a fresh revision, not replay an old receipt.
      if(kind!=="continue")for(const signature of Object.keys(draft.requests)){
        if(signature.startsWith('["continue",'))delete draft.requests[signature];
      }
      save();
      dialog().close();
      notice(kind==="continue"?"续作已发送到原 Session；沿用现有工作区，进度继续在这里更新。":{stop:"Session 已停止，源码与历史保留。",close:"Session 已关闭，备份与历史保留。",reopen:"原 Session 已重新打开，可以编辑需求后继续。",delete:"原生对话历史已删除，源码、任务证据与备份保留。"}[kind]);
      await refreshSessions();await poll();
    }catch(e){message(e.message,true);}
    finally{busy=false;node("session-operation-cancel").disabled=false;render(data);}
  }
  function init(){
    node("continue-session").onclick=()=>open("continue");
    for(const kind of ["stop","close","reopen","delete"])node("session-"+kind).onclick=()=>open(kind);
    node("session-operation-submit").onclick=submit;
    node("session-operation-cancel").onclick=()=>{if(!busy)dialog().close();};
    dialog().addEventListener("cancel",e=>{if(busy)e.preventDefault();});
    for(const id of ["session-continue-input","session-continue-prompt"])node(id).oninput=changed;
    node("session-continue-stage").onchange=()=>{
      draft.stage=node("session-continue-stage").value;
      node("session-continue-prompt").value=draft.prompts?.[draft.stage]??rows().find(s=>s.id===draft.stage)?.prompt??"";changed();
    };
    node("session-delete-confirm").oninput=()=>render(data);
  }
  return {init,render,isBusy:()=>busy};
})();
