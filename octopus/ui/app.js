"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const tools = {kiro:"Kiro CLI", claude:"Claude Code", codex:"Codex", opencode:"OpenCode"};
const states = {pending:"待执行",preparing:"准备源码",ready:"准备派发",dispatching:"派发中",running:"执行中",accepted:"已交接",completed:"已完成",blocked:"流程已暂停",failed:"执行失败",diagnosing:"协调者诊断中",waiting_worker:"待分配 Worker",waiting_approval:"等待原生权限",dispatch_unknown:"派发待核实",stopping:"停止中",stopped:"已停止",cancelled:"已取消",reopening:"重新打开中"};
const activeStates = new Set(["preparing","ready","dispatching","running","waiting_approval","dispatch_unknown","stopping","reopening","diagnosing"]);
let templates = {};
const state = {token:sessionStorage.getItem("octopus.token") || "",me:null,teams:[],flows:[],runs:[],workers:[],users:[],space:"",selected:null,run:null,events:[],stream:null,refreshTimer:null};
const pendingKeys = new Map();
let modalHandler = null, modalCleanup = null, modalWorking = false, toastTimer, streamSequence = 0;
function toast(message){$("toast").textContent=message;$("toast").hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$("toast").hidden=true,5000);}
async function api(path,method="GET",body){
  const response=await fetch("/v1"+path,{method,headers:{"Authorization":"Bearer "+state.token,...(body!==undefined?{"Content-Type":"application/json"}:{})},body:body!==undefined?JSON.stringify(body):undefined,cache:"no-store"});
  const result=await response.json();
  if(!response.ok){if(response.status===401 && path!=="/login")logout();const error=new Error(result.error || "请求失败");error.status=response.status;error.code=result.code;throw error;}
  return result;
}
async function command(path,body){
  const signature=path+JSON.stringify(body);
  if(!pendingKeys.has(signature))pendingKeys.set(signature,crypto.randomUUID());
  const value=await api(path,"POST",{...body,request_id:pendingKeys.get(signature)});
  pendingKeys.delete(signature);return value;
}
function selectedWorker(id){return state.workers.find(w=>w.id===id);}
function workerOptions(team=state.space,current=""){
  return '<option value="">稍后分配 Worker</option>'+state.workers.filter(w=>w.enabled&&(team?w.team_id===team:w.owner_id===state.me.id)).map(w=>`<option value="${esc(w.id)}"${w.id===current?" selected":""}>${esc(w.name)} · ${w.capabilities.isolation==="dedicated"?"专属环境":"个人本机"}</option>`).join("");
}
function badge(status){return `<span class="badge" data-state="${esc(status)}">${esc(states[status] || status)}</span>`;}
function options(values,current){return values.map(([value,label])=>`<option value="${esc(value)}"${value===current?" selected":""}>${esc(label)}</option>`).join("");}
function modal(title,content,onSubmit,{kicker="DEVELOPMENT WORKSPACE",submit="保存",note=""}={}){
  modalCleanup?.();modalCleanup=null;
  $("modal-title").textContent=title;$("modal-kicker").textContent=kicker;
  $("modal-body").innerHTML=content;$("modal-error").textContent="";$("modal-submit").textContent=submit;
  $("modal-submit").hidden=!onSubmit;$("modal-note").textContent=note;modalHandler=onSubmit;
  if(!$("modal").open)$("modal").showModal();
}
function closeModal(){modalCleanup?.();modalCleanup=null;$("modal").close();modalHandler=null;$("modal-body").replaceChildren();}
function download(filename,value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function logout(){state.token="";state.me=null;state.stream?.abort();streamSequence++;clearInterval(state.refreshTimer);sessionStorage.removeItem("octopus.token");$("app").hidden=true;$("login").hidden=false;state.selected=null;state.run=null;closeModal();}
async function boot(){
  try{state.me=await api("/me");$("account").textContent=state.me.username.slice(0,2).toUpperCase();$("account").title=state.me.username;
    await refresh();$("login").hidden=true;$("app").hidden=false;
    const id=new URLSearchParams(location.search).get("run");
    if(id&&state.runs.some(r=>r.id===id))await selectRun(id);
    clearInterval(state.refreshTimer);state.refreshTimer=setInterval(()=>refresh().catch(e=>toast(e.message)),10000);
  }catch(error){logout();$("login-error").textContent=error.message;}
}
async function refresh(){
  const values=await Promise.all([api("/teams"),api("/workflows"),api("/runs"),api("/workers"),api("/users"),api("/workflow-templates")]);
  [state.teams,state.flows,state.runs,state.workers,state.users]=values;
  templates=Object.fromEntries(values[5].map(t=>[t.id,t]));
  if(state.space&&!state.teams.some(t=>t.id===state.space))state.space="";
  $("space").innerHTML='<option value="">我的开发空间</option>'+state.teams.map(t=>`<option value="${esc(t.id)}"${t.id===state.space?" selected":""}>${esc(t.name)} · Team</option>`).join("");
  renderNav();
  if(state.selected && !state.runs.some(r=>r.id===state.selected)){state.stream?.abort();streamSequence++;state.selected=null;state.run=null;renderRun();toast("该 Session 已不可访问。");}
}
function renderNav(){
  const runs=state.runs.filter(r=>(r.team_id || "")===state.space&&r.lifecycle!=="deleted");
  $("session-count").textContent=runs.length;
  $("session-list").innerHTML=runs.length?runs.map(r=>`<button class="nav-item ${r.id===state.selected?"active":""}" data-run="${esc(r.id)}"><strong>${esc((r.name||r.input).slice(0,40))}</strong><small><span class="nav-state">${r.lifecycle==="closed"?"已关闭":esc(states[r.status]||r.status)}</span>${r.progress.accepted}/${r.progress.total} 交接 · ${esc(r.id.slice(-6))}</small></button>`).join(""):'<p class="hint">此空间还没有开发任务。</p>';
  $("flow-list").innerHTML=state.flows.length?state.flows.map(f=>`<button class="nav-item" data-flow="${esc(f.id)}"><strong>${esc(f.name)}</strong><small>v${f.version} · ${f.steps.length} Tasks · ${f.role==="owner"?"我的":esc(state.users.find(u=>u.id===f.owner_id)?.username || "已共享")}</small></button>`).join(""):'<p class="hint">从模板构建你的第一个工作流。</p>';
}
async function selectRun(id){
  state.stream?.abort();streamSequence++;state.selected=id;state.events=[];state.run=null;
  const run=await api("/runs/"+encodeURIComponent(id));
  if(state.selected!==id)return;
  state.run=run;state.space=run.team_id || "";$("space").value=state.space;
  history.replaceState(null,"","?run="+encodeURIComponent(id));renderNav();renderRun();startStream(id);
}
function renderRun(){
  const r=state.run;$("empty").hidden=!!r;$("run-view").hidden=!r;if(!r)return;
  const flow=state.flows.find(f=>f.id===r.workflow_id);
  $("run-kicker").textContent=(state.teams.find(t=>t.id===r.team_id)?.name || "PERSONAL")+" / "+(r.workflow_name || flow?.name || "定制任务");
  const title=r.name||r.input;
  $("run-title").textContent=title.length>125?title.slice(0,125)+"…":title;
  $("run-goal").textContent=r.input;$("run-goal").hidden=!r.name||r.name===r.input;
  $("run-title").title=r.input;$("run-id").textContent=r.id+" · revision "+r.version;
  $("run-status").textContent=r.lifecycle==="closed"?"会话已关闭":states[r.status]||r.status;$("run-status").dataset.state=r.status;
  if(r.status==="blocked"&&r.attention)$("run-status").textContent=r.attention.title;
  if(["ready","running","dispatching"].includes(r.status)&&r.recovery?.latest?.status==="applied")$("run-status").textContent="正在返工与复审";
  $("progress-number").textContent=r.progress.accepted+" / "+r.progress.total;
  $("overall-progress").value=r.progress.total?100*r.progress.accepted/r.progress.total:0;
  $("run-continue").disabled=activeStates.has(r.status)||r.lifecycle!=="open";
  if(r.purpose==="workflow_design")$("run-continue").disabled=true;
  $("task-workflow").hidden=r.purpose==="workflow_design";
  $("run-stop").disabled=r.lifecycle!=="open"||r.status==="stopped"||r.status==="stopping";
  $("run-close").textContent=r.lifecycle==="closed"?"重新打开":"关闭";$("run-close").disabled=r.lifecycle==="deleted"||r.status==="stopping";
  $("run-delete").disabled=r.lifecycle==="deleted";
  const crewReason=crewEntryReason(r),crewOpening=crewEntriesOpening.has(r.id);
  $("run-open-crew").disabled=!!crewReason||crewOpening;
  $("run-open-crew").textContent=crewOpening?"正在连接 KiroCrew…":"在 KiroCrew 中打开 ↗";
  $("run-open-crew").title=crewReason||"在新标签页打开此 Session 对应的 KiroCrew 原生父会话";
  $("crew-entry-hint").textContent=crewReason;$("crew-entry-hint").hidden=!crewReason;
  $("run-error").hidden=!r.error||!!r.attention;$("run-error").textContent=r.error||"";
  renderRecovery(r);
  $("worker-notice").hidden=r.status!=="waiting_worker";
  $("worker-notice").innerHTML=r.status==="waiting_worker"?'需求已保存。请选择此开发空间的 Worker 后开始执行。<button type="button" id="assign-worker">分配 Worker</button>':"";
  const open=new Set([...$("task-list").querySelectorAll("details[open]")].map(d=>d.dataset.task));
  $("task-list").innerHTML=r.tasks.map((t,i)=>{
    const attempts=t.attempts || [],spec=t.spec;
    return `<article class="task"><div class="task-header"><span class="task-index">${String(i+1).padStart(2,"0")}</span><div class="task-title"><h3>${esc(spec.name)}</h3><div class="task-config">${esc(tools[spec.tool])} · ${esc(spec.model)} · Effort ${esc(spec.effort||"继承默认")}</div></div>${badge(t.status)}</div><p class="task-prompt">${esc(spec.prompt)}</p><footer><button type="button" data-retry="${esc(t.id)}"${activeStates.has(r.status)||r.lifecycle!=="open"||r.tasks.slice(0,i).some(x=>x.status!=="accepted")?" disabled":""}>配置并续作</button><small>Task ${esc(t.id)} · ${attempts.length} 次执行</small></footer>${attempts.length?`<details data-task="${esc(t.id)}"${open.has(t.id)?" open":""}><summary>执行证据与历史 Attempt</summary>${attempts.map(a=>`<div class="attempt"><p>Attempt ${a.number} ${badge(a.status)}</p><div class="meta">请求：${esc(tools[a.spec.tool])} / ${esc(a.spec.model)} / ${esc(a.spec.effort||"默认")}<br>实际后端：${esc(a.evidence.actual_backend||"未报告")}<br>实际模型：${esc(a.evidence.actual_model||"未报告")}<br>实际 Effort：${esc(a.evidence.actual_effort||"未报告")}<br>原生 Task：${esc(a.native_id||"待核实")}<br>工具 Session：${esc(a.evidence.native_session_id||"未报告")}<br>Context SHA-256：${esc(a.context_hash||"未形成")}</div>${a.error?`<p class="error">${esc(a.error)}</p>`:""}${a.result?`<pre>${esc(a.result)}</pre>`:""}</div>`).join("")}</details>`:""}</article>`;
  }).join("");
  if(r.purpose==="workflow_design")$("task-list").querySelectorAll("[data-retry]").forEach(b=>b.disabled=true);
  $("native-details").innerHTML=`Workspace<br><span class="mono">${esc(r.workspace||"尚未准备")}</span><br>原生父对话<br><span class="mono">${esc(r.native_parent||"尚未建立")}</span><br>来源<br><span class="mono">${esc(r.source.url||"新建项目")}</span><br>运行与源码由此空间的 Worker 保管。`;
}
const eventLabels={"run.created":"需求已登记","attempt.created":"创建新的执行 Attempt","attempt.state":"原生任务状态更新","run.state":"开发状态更新","run.completed":"全部任务已交接","command.continue":"已提交原地续作","command.recover":"收到诊断请求","recovery.started":"Coordinator 开始诊断","recovery.applied":"已安排返工与复审","recovery.blocked":"协调者等待补充条件","command.stop":"收到停止请求","command.close":"收到关闭请求","command.delete":"收到删除请求","command.reopen":"收到重新打开请求","session.stop":"原生任务已停止","session.close":"已备份并关闭","session.delete":"原生会话已删除，源码保留"};
function renderEvents(){
  $("event-count").textContent=state.events.length+" 条";
  $("event-list").innerHTML=state.events.slice(-60).reverse().map(e=>`<div class="event"><time>${new Date(e.created*1000).toLocaleTimeString("zh-CN",{hour12:false})}</time><strong>${esc(eventLabels[e.kind]||e.kind)}</strong><p>${esc(e.payload.task_id || e.payload.status&&states[e.payload.status] || e.payload.error || "")}</p></div>`).join("");
}
async function startStream(rid){
  const controller=new AbortController(),sequence=++streamSequence;state.stream=controller;let after=0;
  while(sequence===streamSequence&&state.selected===rid&&!controller.signal.aborted){
    try{
      const response=await fetch(`/v1/runs/${encodeURIComponent(rid)}/events?stream=1&after=${after}`,{headers:{Authorization:"Bearer "+state.token},signal:controller.signal});
      if(!response.ok)throw new Error("状态流授权失效");
      $("stream-status").textContent="● 实时状态已连接";
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="";
      while(true){
        const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let boundary;
        while((boundary=buffer.indexOf("\n\n"))>=0){
          const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
          if(block.includes("event: access_revoked")){controller.abort();await refresh();throw new Error("访问授权已撤销");}
          const line=block.split("\n").find(x=>x.startsWith("data: "));if(!line)continue;
          const event=JSON.parse(line.slice(6));after=Math.max(after,event.id);
          if(sequence!==streamSequence||state.selected!==rid)return;
          if(!state.events.some(x=>x.id===event.id))state.events.push(event);
          renderEvents();
          const run=await api("/runs/"+rid);if(sequence!==streamSequence||state.selected!==rid)return;
          state.run=run;renderRun();
        }
      }
    }catch(error){if(controller.signal.aborted)return;$("stream-status").textContent="状态流重连中…";}
    if(!controller.signal.aborted)await new Promise(resolve=>setTimeout(resolve,1500));
  }
}
function templateCopy(key){return templates[key].steps.map(s=>({...s,model:"auto",effort:""}));}
function agentFields(spec,prefix,workerId){
  const worker=selectedWorker(workerId);const models=worker?.capabilities.models?.[spec.tool] || ["auto"];
  return `<div class="form-row triple"><label>Coding 工具<select id="${prefix}-tool">${options(Object.entries(tools),spec.tool)}</select></label><label>模型<input id="${prefix}-model" list="${prefix}-models" value="${esc(spec.model||"auto")}" required><datalist id="${prefix}-models">${models.map(m=>`<option value="${esc(m)}"></option>`).join("")}</datalist></label><label>Effort<select id="${prefix}-effort"${spec.tool==="opencode"?" disabled":""}>${options([["","继承默认"],...["low","medium","high","xhigh","max"].map(e=>[e,e])],spec.effort||"")}</select></label></div>`;
}
function bindAgent(prefix,workerId){
  $(prefix+"-tool").addEventListener("change",()=>{
    const tool=$(prefix+"-tool").value;$(prefix+"-model").value="auto";$(prefix+"-effort").value="";$(prefix+"-effort").disabled=tool==="opencode";
    $(prefix+"-models").innerHTML=(selectedWorker(workerId)?.capabilities.models?.[tool]||["auto"]).map(m=>`<option value="${esc(m)}"></option>`).join("");
  });
}
function readAgent(prefix){const tool=$(prefix+"-tool").value;return{tool,model:$(prefix+"-model").value.trim(),effort:tool==="opencode"?"":$(prefix+"-effort").value};}
function flowEditor(flow=null,template="chain"){
  const existing=flow?.id,canEdit=!flow||["owner","editor"].includes(flow.role);
  let draft=structuredClone(flow?.steps || templateCopy(template));
  const content=`<div class="template-row">${["chain","proof","fix"].map(key=>`<button type="button" data-template="${key}">${esc(templates[key].name)}</button>`).join("")}<button type="button" id="import-flow">导入 JSON</button></div><label>工作流名称<input id="flow-name" value="${esc(flow?.name||templates[template].name)}" maxlength="80" required></label><p class="hint">先定义任务，再选择工具与模型。保存后形成新版本；已有运行保留自己的版本。显式模型在执行 Worker 校验；当前原生 spawn 不接受含 /、:、[] 的模型 ID。</p><div id="flow-steps"></div><button type="button" id="add-step">＋ 添加 Task</button>${flow?'<hr class="divider"><div class="template-row"><button type="button" id="export-flow">导出定义</button>'+(flow.role!=="viewer"?'<button type="button" id="launch-flow">用此工作流发起任务</button>':"")+(flow.role==="owner"?'<button type="button" id="share-flow">共享授权</button><button type="button" id="archive-flow" class="danger">归档工作流</button>':"")+"</div>":""}`;
  const capture=()=>draft.map((s,i)=>({...s,name:$("step-"+i+"-name").value,prompt:$("step-"+i+"-prompt").value,...readAgent("step-"+i)}));
  const render=()=>{
    const wid=state.workers.find(w=>w.enabled&&(state.space?w.team_id===state.space:w.owner_id===state.me.id))?.id;
    $("flow-steps").innerHTML=draft.map((s,i)=>`<section class="flow-step"><div class="step-top"><span>TASK ${String(i+1).padStart(2,"0")} · ${esc(s.id)}</span><button type="button" data-up="${i}"${i===0?" disabled":""} title="上移任务">↑</button><button type="button" data-remove="${i}"${draft.length===1?" disabled":""}>移除</button></div><label>任务名称<input id="step-${i}-name" value="${esc(s.name)}" required maxlength="80"></label><label>任务说明<textarea id="step-${i}-prompt" required>${esc(s.prompt)}</textarea></label>${agentFields(s,"step-"+i,wid)}</section>`).join("");
    draft.forEach((s,i)=>bindAgent("step-"+i,wid));
    if(!canEdit)$("flow-steps").querySelectorAll("input,textarea,select,button").forEach(e=>e.disabled=true);
  };
  modal(existing?"编辑工作流":"构建工作流",content,canEdit?async()=>{
    const body={name:$("flow-name").value,steps:capture(),...(existing?{expected_version:flow.version}:{})};
    await api("/workflows"+(existing?"/"+existing+"/versions":""),"POST",body);closeModal();await refresh();toast("工作流已保存；新运行使用新版本。");
  }:null,{kicker:existing?`WORKFLOW · V${flow.version} · ${flow.role.toUpperCase()}`:"BUILD YOUR WORKFLOW",note:"名称在同一 Owner 下唯一。"});
  render();$("flow-name").disabled=!canEdit;
  if(canEdit){
    const recommend=document.createElement("button");recommend.type="button";recommend.id="recommend-flow";
    recommend.textContent="按需求让 KiroCrew 推荐";
    recommend.onclick=()=>newRun({mode:"workflow",workflow:flow?{id:flow.id,version:flow.version}:null,
      steps:capture(),name:$("flow-name").value});
    $("modal-body").querySelector(".template-row").prepend(recommend);
  }
  $("modal-body").querySelectorAll("[data-template]").forEach(b=>{b.disabled=!canEdit;b.onclick=()=>{draft=templateCopy(b.dataset.template);$("flow-name").value=templates[b.dataset.template].name;render();};});
  $("flow-steps").onclick=e=>{const b=e.target.closest("button");if(!b)return;draft=capture();if(b.dataset.remove!==undefined)draft.splice(Number(b.dataset.remove),1);if(b.dataset.up!==undefined){const i=Number(b.dataset.up);[draft[i-1],draft[i]]=[draft[i],draft[i-1]];}render();};
  $("add-step").disabled=!canEdit;$("add-step").onclick=()=>{draft=capture();if(draft.length>=8){toast("首版支持最多 8 个顺序任务。");return;}draft.push({id:"task-"+crypto.randomUUID().slice(0,8),name:"新任务",prompt:"",tool:"claude",model:"auto",effort:""});render();};
  $("import-flow").disabled=!canEdit;$("import-flow").onclick=()=>{const input=document.createElement("input");input.type="file";input.accept=".json";input.onchange=async()=>{try{const value=JSON.parse(await input.files[0].text());if(!Array.isArray(value.steps)||value.steps.length<1||value.steps.length>8)throw new Error("需要 1–8 个 steps");draft=value.steps;$("flow-name").value=value.name||"导入的工作流";render();}catch(e){toast("导入失败："+e.message);}};input.click();};
  if(flow){$("export-flow").onclick=()=>download("workflow-"+flow.id+".json",{name:flow.name,steps:flow.steps});if(flow.role==="owner"){$("share-flow").onclick=()=>sharing(flow);$("archive-flow").onclick=()=>confirmAction("归档工作流",`归档“${esc(flow.name)}”后不再创建新运行。已有 Session 和历史版本保留。`,async()=>{await api("/workflows/"+flow.id+"/archive","POST",{expected_version:flow.version});closeModal();await refresh();});}}
  if($("launch-flow"))$("launch-flow").onclick=()=>newRun({workflow_id:flow.id});
}
function sharing(flow){
  const candidates=[...state.teams.map(t=>["team:"+t.id,"Team · "+t.name]),...state.users.filter(u=>u.id!==state.me.id).map(u=>["user:"+u.id,"用户 · "+u.username])];
  const title=g=>g.kind==="team"?(state.teams.find(t=>t.id===g.principal)?.name||g.principal):(state.users.find(u=>u.id===g.principal)?.username||g.principal);
  modal("共享 · "+flow.name,`<p class="hint">只共享工作流定义。查看、运行、编辑逐级包含；共享管理仍由 Owner 负责。Team 成员退出后立即失去该 Team 授权。</p><div>${flow.grants.length?flow.grants.map((g,i)=>`<div class="list-row"><span>${esc(title(g))}<small>${g.kind} · ${g.role}</small></span><button type="button" data-revoke="${i}" class="danger">撤销</button></div>`).join(""):'<p class="hint">当前为私有工作流。</p>'}</div><hr class="divider"><div class="form-row"><label>共享给<select id="share-target">${options(candidates,"")}</select></label><label>授予权限<select id="share-role">${options([["viewer","查看"],["runner","查看并运行"],["editor","查看、运行和编辑"]],"runner")}</select></label></div>`,async()=>{const [kind,principal]=$("share-target").value.split(":");await api("/workflows/"+flow.id+"/shares","POST",{kind,principal,role:$("share-role").value});await refresh();sharing(state.flows.find(f=>f.id===flow.id));toast("共享授权已更新。");},{submit:"添加授权",note:"运行选择 Team 空间时，该次运行对 Team 成员可见。"});
  $("modal-body").querySelectorAll("[data-revoke]").forEach(b=>b.onclick=async()=>{try{await api("/workflows/"+flow.id+"/shares","DELETE",flow.grants[Number(b.dataset.revoke)]);await refresh();sharing(state.flows.find(f=>f.id===flow.id));}catch(e){$("modal-error").textContent=e.message;}});
}
function newRun(seed={}){launchComposer(typeof seed==="string"?{workflow_id:seed}:seed);}
function continueRun(taskId){
  const r=state.run;if(!r)return;
  const t=r.tasks.find(t=>t.id===taskId)||r.tasks.find(t=>t.status!=="accepted")||r.tasks[0];
  modal("继续此 Session · "+t.spec.name,`<p class="hint">保留当前 Session ID、Workspace 与上游交接。此 Task 和后续 Task 将重新执行；旧 Attempt 保留为历史证据。</p><label>本次完整需求<textarea id="continue-input" required minlength="3" rows="4">${esc(r.input)}</textarea></label><label>任务名称<input id="continue-name" value="${esc(t.spec.name)}" required maxlength="80"></label><label>任务说明<textarea id="continue-prompt" required>${esc(t.spec.prompt)}</textarea></label>${agentFields(t.spec,"continue",r.worker_id)}${recoveryFields(r.recovery?.policy,"continue-recovery")}<p class="mono">${esc(r.id)} / ${esc(t.id)}</p>`,async()=>{await command("/runs/"+r.id+"/continue",{expected_version:r.version,task_id:t.id,input:$("continue-input").value,recovery:readRecovery("continue-recovery"),execution:{name:$("continue-name").value,...readAgent("continue"),prompt:$("continue-prompt").value}});closeModal();await refresh();await selectRun(r.id);toast("续作已登记，保留原 Workspace。");},{submit:"保存并续作",note:"工具/模型改变会创建新的原生执行 Attempt。"});
  bindAgent("continue",r.worker_id);
}
function confirmAction(title,message,fn,typed=false){
  modal(title,`<p>${message}</p>${typed?'<label>输入完整 Session ID 确认<input id="confirm-id" required autocomplete="off"></label>':""}`,fn,{submit:"确认"+title,note:typed?"删除原生对话前先备份；本版保留源码和审计记录。":""});
}
async function sessionControl(action){
  const r=state.run;if(!r)return;
  const execute=async()=>{await command("/runs/"+r.id+"/"+action,{expected_version:r.version,...(action==="delete"?{confirmation:$("confirm-id").value}: {})});closeModal();await refresh();await selectRun(r.id);toast("请求已接收；以原生状态确认完成。");};
  if(action==="stop"){await execute();return;}
  confirmAction({close:"关闭会话",reopen:"重新打开",delete:"删除会话"}[action],action==="delete"?`将停止并备份此原生会话，然后删除对话记录。源码与平台审计保留。<br><span class="mono">${esc(r.id)}</span>`:action==="close"?"停止未结束的任务，备份并关闭原生会话。之后可重新打开。":"恢复原生会话；选择 Task 续作后开始新一轮执行。",execute,action==="delete");
}
function assignWorker(){
  const r=state.run;modal("分配执行 Worker",`<label>开发空间的执行环境<select id="assign-choice" required>${workerOptions(r.team_id||"")}</select></label><p class="hint">未找到可用环境时，请由管理员在“执行环境”中登记该空间的 Worker。</p>`,async()=>{await command("/runs/"+r.id+"/assign-worker",{expected_version:r.version,worker_id:$("assign-choice").value});closeModal();await refresh();await selectRun(r.id);},{submit:"开始执行"});
}
function teamManager(){
  const team=state.teams.find(t=>t.id===state.space);
  modal("团队与成员",`<div class="form-row"><label>创建 Team<input id="team-name" placeholder="团队名称" maxlength="80"></label><div><p class="hint">所有成员共享 Team 运行；Owner 管理成员。</p><button type="button" id="create-team">创建 Team</button></div></div>${team?`<hr class="divider"><h3>${esc(team.name)}</h3>${team.members.map(m=>`<div class="list-row"><span>${esc(m.username)}<small>${esc(m.role)}</small></span>${team.role==="owner"?`<button type="button" data-remove-member="${esc(m.username)}" class="danger">移除</button>`:""}</div>`).join("")}${team.role==="owner"?`<div class="form-row"><label>添加现有用户<select id="member-name">${options(state.users.map(u=>[u.username,u.username]),"")}</select></label><label>角色<select id="member-role">${options([["member","Member"],["owner","Owner"]],"member")}</select></label></div><button type="button" id="add-member">添加 / 更新成员</button>`:""}`:'<p class="hint">切换至一个 Team 后，可在这里管理它的成员。</p>'}${state.me.admin?'<hr class="divider"><h3>创建平台账户</h3><p class="hint">用户随后可加入 Team，或拥有自己的私有开发空间。</p><div class="form-row"><label>用户名<input id="user-name" autocomplete="off"></label><label>初始密码（至少 12 位）<input id="user-password" type="password" autocomplete="new-password"></label></div><button type="button" id="create-user">创建用户</button>':""}`,null,{kicker:"TEAM ACCESS"});
  const action=fn=>async()=>{try{await fn();await refresh();teamManager();}catch(e){$("modal-error").textContent=e.message;}};
  $("create-team").onclick=action(async()=>{const result=await api("/teams","POST",{name:$("team-name").value});state.space=result.id;});
  if(team?.role==="owner"){
    $("add-member").onclick=action(()=>api("/teams/"+team.id+"/members","POST",{username:$("member-name").value,role:$("member-role").value}));
    $("modal-body").querySelectorAll("[data-remove-member]").forEach(b=>b.onclick=action(()=>api("/teams/"+team.id+"/members","DELETE",{username:b.dataset.removeMember})));
  }
  if(state.me.admin)$("create-user").onclick=action(()=>api("/users","POST",{username:$("user-name").value,password:$("user-password").value}));
}
async function apiSettings(){
  const tokens=await api("/tokens");
  modal("API 与 Coding Agent 接入",`<p class="hint">为 Claude Code、Kiro CLI/KiroCrew 等客户端各创建一枚 Token。所有请求沿用当前用户的共享权限；撤销 Token 会关闭对应状态流。</p><div class="code-block">REST: ${esc(location.origin)}/v1<br>事件: GET /v1/runs/{id}/events?stream=1<br>MCP: python3 -m octopus mcp<br>环境变量: OCTOPUS_URL, OCTOPUS_API_TOKEN</div><p class="hint">MCP 通过 API 以该 Token 的用户身份操作，不会自动继承工具当前 Chat 的原生 Session 身份。OpenAPI：/v1/openapi.json。</p>${tokens.map(t=>`<div class="list-row"><span>${esc(t.label)}<small>${esc(JSON.parse(t.scopes).join(" · "))} · ${new Date(t.expires*1000).toLocaleDateString()} 到期</small></span><button type="button" data-token="${esc(t.id)}" class="danger">撤销</button></div>`).join("")}<hr class="divider"><div class="form-row"><label>新 Token 名称<input id="token-label" placeholder="Claude Code / Kiro CLI"></label><label>权限<select id="token-scope">${options([["read","只读状态"],["execute","读取与提交开发"],["manage","读取、执行与配置"]],"execute")}</select></label></div>`,async()=>{const selected=$("token-scope").value;const scopes=selected==="read"?["read"]:selected==="execute"?["read","execute"]:["read","execute","manage"];const result=await api("/tokens","POST",{label:$("token-label").value||"Coding client",scopes,ttl:7*86400});modal("Token 已创建",`<p class="hint">仅显示这一次。将它配置到对应客户端的私有环境变量中。</p><div class="secret">${esc(result.token)}</div><p class="hint">7 天后到期，可随时撤销。</p>`,null);},{submit:"创建 API Token"});
  $("modal-body").querySelectorAll("[data-token]").forEach(b=>b.onclick=async()=>{try{await api("/tokens/"+b.dataset.token,"DELETE",{});await apiSettings();}catch(e){toast(e.message);}});
}
function workerSettings(){
  modal("执行环境 · KiroCrew Worker",`<p class="hint">每个 Worker 对应一个 KiroCrew Gateway。个人本机验证可使用 trusted-local；Team 使用管理员准备的独立账号、容器或主机。isolation 字段是部署声明，不能替代实际操作系统隔离。</p>${state.workers.map(w=>`<div class="list-row"><span>${esc(w.name)}<small>${w.capabilities.isolation==="dedicated"?"专属环境":"个人本机"} · ${w.enabled?"可用":"已停用"} · ${esc(state.teams.find(t=>t.id===w.team_id)?.name||state.users.find(u=>u.id===w.owner_id)?.username||"已分配空间")}</small></span><button type="button" data-refresh-worker="${esc(w.id)}">刷新模型</button>${state.me.admin?`<button type="button" data-toggle-worker="${esc(w.id)}">${w.enabled?"停用":"启用"}</button>`:""}</div>`).join("")}${state.me.admin?`<hr class="divider"><h3>登记已启动的 Worker</h3><p class="hint">Worker 连接凭据由管理员填写，普通用户不会获得。Gateway 与 Coding 工具的认证保留在 Worker 内。</p><div class="form-row"><label>名称<input id="worker-name" required placeholder="team-a-worker"></label><label>绑定空间<select id="worker-owner">${options([...state.users.map(u=>["user:"+u.id,"个人 · "+u.username]),...state.teams.map(t=>["team:"+t.id,"Team · "+t.name])],"user:"+state.me.id)}</select></label></div><div class="form-row"><label>Worker URL<input id="worker-url" required placeholder="http://127.0.0.1:8928"></label><label>Worker Token<input id="worker-token" type="password" required autocomplete="off" minlength="32"></label></div>`:'<p class="hint">执行环境由管理员登记和分配。</p>'}`,state.me.admin?async()=>{const [kind,id]=$("worker-owner").value.split(":");await api("/workers","POST",{name:$("worker-name").value,[kind==="team"?"team_id":"owner_id"]:id,connection:{url:$("worker-url").value,token:$("worker-token").value}});await refresh();workerSettings();toast("Worker 已登记。");}:null,{submit:"登记 Worker"});
  $("modal-body").querySelectorAll("[data-refresh-worker]").forEach(b=>b.onclick=async()=>{try{await api("/workers/"+b.dataset.refreshWorker+"/capabilities");await refresh();workerSettings();toast("已重新读取 Worker 模型目录。");}catch(e){$("modal-error").textContent=e.message;}});
  $("modal-body").querySelectorAll("[data-toggle-worker]").forEach(b=>b.onclick=async()=>{try{const w=selectedWorker(b.dataset.toggleWorker);await api("/workers/"+w.id+"/enabled","POST",{enabled:!w.enabled});await refresh();workerSettings();}catch(e){$("modal-error").textContent=e.message;}});
}
$("login-form").onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const values=new FormData(e.currentTarget);const result=await api("/login","POST",{username:values.get("username"),password:values.get("password")});state.token=result.token;sessionStorage.setItem("octopus.token",result.token);e.target.reset();await boot();}catch(error){$("login-error").textContent=error.message;}finally{button.disabled=false;}};
$("modal-form").onsubmit=async e=>{e.preventDefault();if(!modalHandler||modalWorking)return;modalWorking=true;const controls=[...$("modal-form").querySelectorAll("input,textarea,select,button")].map(node=>[node,node.disabled]);controls.forEach(([node])=>node.disabled=true);$("modal-error").textContent="";try{await modalHandler();}catch(error){$("modal-error").textContent=error.message;}finally{modalWorking=false;controls.forEach(([node,disabled])=>{if(node.isConnected)node.disabled=disabled;});}};
$("modal-close").onclick=$("modal-cancel").onclick=()=>{if(!modalWorking)closeModal();};
$("modal").addEventListener("cancel",e=>{e.preventDefault();if(!modalWorking)closeModal();});
$("space").onchange=()=>{state.space=$("space").value;state.selected=null;state.run=null;state.stream?.abort();streamSequence++;history.replaceState(null,"",location.pathname);renderNav();renderRun();};
$("session-list").onclick=e=>{const b=e.target.closest("[data-run]");if(b)selectRun(b.dataset.run).catch(e=>toast(e.message));};
$("flow-list").onclick=e=>{const b=e.target.closest("[data-flow]");if(b)flowEditor(state.flows.find(f=>f.id===b.dataset.flow));};
$("new-flow").onclick=()=>flowEditor();$("new-run").onclick=()=>newRun();
$("quick-chain").onclick=()=>newRun({template_id:"chain"});$("quick-proof").onclick=()=>newRun({template_id:"proof"});
$("intent-start").onclick=()=>newRun();
$("task-workflow").onclick=()=>newRun({steps:state.run.tasks.map(t=>structuredClone(t.spec))});
$("run-continue").onclick=()=>continueRun();$("task-list").onclick=e=>{const b=e.target.closest("[data-retry]");if(b&&!b.disabled)continueRun(b.dataset.retry);};
$("run-open-crew").onclick=()=>openCrewSession(state.run?.id,state.run?.native_parent);
$("run-recover").onclick=requestRecovery;
$("run-stop").onclick=()=>sessionControl("stop").catch(e=>toast(e.message));
$("run-close").onclick=()=>sessionControl(state.run.lifecycle==="closed"?"reopen":"close").catch(e=>toast(e.message));
$("run-delete").onclick=()=>sessionControl("delete").catch(e=>toast(e.message));
$("run-export").onclick=async()=>{try{download(state.run.id+".json",await api("/runs/"+state.run.id+"/export"));}catch(e){toast(e.message);}};
$("worker-notice").onclick=e=>{if(e.target.id==="assign-worker")assignWorker();};
$("team-manage").onclick=teamManager;$("api-settings").onclick=()=>apiSettings().catch(e=>toast(e.message));$("worker-settings").onclick=workerSettings;
$("account").onclick=()=>modal(state.me.username,`<p class="hint">${state.me.admin?"平台管理员":"平台成员"} · ${esc(state.me.id)}</p>`,async()=>{try{await api("/logout","POST",{});}finally{logout();}},{submit:"退出登录"});
if(state.token)boot();
