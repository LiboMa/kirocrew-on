"use strict";

// Generation is explicit, durable and scoped. Polling never applies a result.
function workflowRecommendation({saved, read, apply, persist, persistClosed}) {
  let job=saved || null, alive=true, timer, polling=false, workerScope="";
  const terminal=new Set(["ready","invalid","blocked","failed","stopped","cancelled"]);
  if(job && !job.id && !terminal.has(job.status))job.status="uncertain";
  const fingerprint=value=>JSON.stringify({input:value.input.trim(),steps:value.steps,team_id:value.team_id||""});
  const changed=()=>{
    if(!alive)return;
    const value=read(),scope=value.team_id||"";
    const workers=state.workers.filter(w=>w.enabled&&(scope?w.team_id===scope:w.owner_id===state.me.id));
    const signature=scope+"/"+workers.map(w=>w.id).join(",");
    if(signature!==workerScope) {
      const previous=$("recommend-worker").value || job?.request?.worker_id;
      $("recommend-worker").innerHTML=workers.length?options(workers.map(w=>[w.id,w.name]),previous || workers[0].id):
        '<option value="">此空间尚无可用 Worker</option>';
      workerScope=signature;
    }
    const active=job && !terminal.has(job.status) && job.status!=="uncertain";
    const stale=job && job.fingerprint!==fingerprint(value);
    $("recommend-start").disabled=!!active || modalWorking || (!$("recommend-worker").value && job?.status!=="uncertain");
    $("recommend-start").textContent=job?.status==="uncertain"?"核实上次提交":active?"KiroCrew 正在推荐…":"让 KiroCrew 推荐";
    $("recommend-apply").hidden=job?.status!=="ready" || !!job?.applied;
    $("recommend-apply").disabled=!!stale || modalWorking;
    $("recommend-stop").hidden=!active || !job?.id;
    $("recommend-stop").disabled=modalWorking;
    $("recommend-open").hidden=!job?.id || !job?.native_parent;
    $("recommend-open").disabled=crewEntriesOpening.has(job?.id);
    $("recommend-native").textContent=job?.native_task_id ?
      `实际后端：${job.evidence?.actual_backend||"待核实"} · 原生 Task：${job.native_task_id} · 实际模型：${job.evidence?.actual_model||"未报告"}` : "";
    const labels={submitting:"正在提交生成任务",preparing:"正在准备设计工作区",ready:"建议已生成并通过格式校验，采用后可继续编辑。",
      submitted:"KiroCrew 已收到生成任务",running:"后台 Kiro Agent 正在设计任务与提示词",ready_to_dispatch:"等待派发",
      waiting_approval:"等待原生工具权限批准",dispatch_unknown:"原生派发待核实，不重复发送",
      blocked:"生成任务需要处理",invalid:"返回内容格式无效",stopping:"正在停止生成任务",stopped:"生成任务已停止",
      uncertain:"提交回执不明确；可安全核实同一请求",failed:"生成失败"};
    $("recommend-status").textContent=!job?"后台生成会实际调用 Kiro Agent；生成结果先供你审阅。":
      job.applied?"已采用建议，可继续修改各 Task 和提示词。":
      (labels[job.status] || states[job.status] || job.status) +
      (stale?" 当前需求或任务已变化，旧建议不会覆盖当前草稿。":"")+
      (job.error?" "+job.error:"")+(job.poll_error?" "+job.poll_error:"");
    $("recommend-preview").hidden=!job?.config;
    $("recommend-preview-body").textContent=job?.config?job.config.name+"\n\n"+job.config.steps.map((s,i)=>
      `${i+1}. ${s.name} · ${tools[s.tool]} / ${s.model} / Effort ${s.effort||"默认"}\n${s.prompt}`).join("\n\n"):"";
  };
  const poll=async()=>{
    clearTimeout(timer);
    if(!alive || !job?.id || polling || terminal.has(job.status))return;
    polling=true;const id=job.id;
    try {
      const result=await api("/designs/"+encodeURIComponent(id));
      if(!alive || job?.id!==id)return;
      job={...job,...result,poll_error:null};changed();persist();
    } catch(error) {
      if(alive && job?.id===id){job.poll_error=error.message;changed();persist();}
    } finally {
      polling=false;if(alive && job?.id===id && !terminal.has(job.status))timer=setTimeout(poll,2000);
    }
  };
  $("recommend-start").onclick=async()=>{
    const value=read();
    if(job?.status!=="uncertain" && value.input.trim().length<3){toast("请先填写研发需求。");return;}
    if(job?.status!=="uncertain" && !value.ready){toast("请先等待模板匹配完成，或编辑任务列表。");return;}
    if(job?.status!=="uncertain") {
      const request={request_id:crypto.randomUUID(),input:value.input.trim(),steps:value.steps,
        worker_id:$("recommend-worker").value,...(value.team_id?{team_id:value.team_id}:{})};
      job={request,fingerprint:fingerprint(value),status:"submitting"};
    }
    const submitted=job;
    changed();persist();
    try {
      const result=await api("/designs","POST",submitted.request);
      // Keep the receipt in the original draft even if its modal was closed.
      Object.assign(submitted,result,{status:result.status});
      if(!alive){persistClosed(submitted);return;}
      changed();persist();poll();
    } catch(error) {
      submitted.status=[400,403,404,409,422].includes(error.status)?"failed":"uncertain";submitted.error=error.message;
      if(alive){changed();persist();}else persistClosed(submitted);
    }
  };
  $("recommend-apply").onclick=()=>{
    if(!job?.config || job.status!=="ready" || job.fingerprint!==fingerprint(read()))return;
    job.applied=true;
    apply(structuredClone(job.config));changed();persist();
  };
  $("recommend-stop").onclick=async()=>{
    if(!job?.id)return;
    const id=job.id;
    try {
      const result=await command("/runs/"+id+"/stop",{expected_version:job.version});
      if(!alive||job?.id!==id)return;
      job.version=result.version;job.status="stopping";changed();persist();poll();
    } catch(error){if(alive)toast(error.message);}
  };
  $("recommend-worker").onchange=persist;
  $("recommend-open").onclick=async()=>{
    const opening=openCrewSession(job?.id,job?.native_parent);
    changed();await opening;changed();
  };
  changed();poll();
  return {snapshot:()=>job,changed,close:()=>{alive=false;clearTimeout(timer);}};
}
