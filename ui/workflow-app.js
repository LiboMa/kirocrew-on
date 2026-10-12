/* UI for the existing Workflow MVP API. Coding remains in native KiroCrew chat. */
const $ = id => document.getElementById(id);
const STORAGE = "kirocrew.workflow.workbench.v3";
const LEGACY_STORAGE = "kirocrew.workflow.workbench.v2";
const labels = {ready:"等待输入",running:"执行中",awaiting_approval:"等待授权",failed:"执行失败",check_failed:"交接未通过",completed:"全部交接完成",pending:"待执行",accepted:"已交接",awaiting_check:"等待核对",submitted:"等待调度",updating:"准备续作",stopping:"停止中",stopped:"已停止",closed:"已关闭",deleted:"已删除"};
const toolLabels = {kiro:"Kiro CLI",claude:"Claude Code",codex:"Codex",opencode:"OpenCode"};
const initials = {kiro:"K",claude:"CL",codex:"CX",opencode:"OC"};
const el = (tag,text,cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const button = (text,handler,cls="") => {const n=el("button",text,cls);n.type="button";n.onclick=handler;return n;};
const copy = value => JSON.parse(JSON.stringify(value));
const option = (value,text) => {const n=el("option",text);n.value=value;return n;};
const timeLabel = value => value?new Date(value*1000).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}):"";
function stored(key=STORAGE){try{return JSON.parse(localStorage.getItem(key)||"{}");}catch{return {};}}
const saved=stored();
const editorStore=new WorkflowEditorStore.Store(localStorage);
let editorScope="",drafts={},requests=new Map(),inputTouched=false;
let config, catalog={}, templates=[], definitions=[], runs=[], snapshots=new Map(), deletedFlows={};
let managingSessions=false, collapsedFlows=new Set(saved.collapsedFlows||[]);
let selectedRuns=new Set(),visibleRuns=[],generation=null,generationBusy=false;
let activeRun="",selectedTask="",taskScope="current",view="progress";
let selected=0,stage=0;
let dirty=false,busy=false,runtimeReady=false,retryReady=false,sessionOpsReady=false,builderActive=false,sessionFilter="all";
let pollBusy=false,refreshBusy=false,configSequence=0,sessionRenderKey="",tasksRenderKey="",stageRenderKey="";
let lifecycleIntent=null,lifecycleBusy=false,lifecycleScroll=0,builderBackdropDown=false;
let retryBusy=false;
const retryRequests=new Map();
let suggestedInput="";
let templateCategory="quick";
const templateCategories={quick:"快速验证",web:"Web 原型",engineering:"工程实现",existing:"已有代码"};
function persist(){
  try{
    if(editorScope&&config)editorStore.write(editorScope,{config,drafts,dirty,requests:[...requests],generation,
      input:$("requirements").value,inputTouched,suggestedInput,chat:$("chat-entry").checked,stage,selected,active:builderActive});
    localStorage.setItem(STORAGE,JSON.stringify({schema:3,collapsedFlows:[...collapsedFlows]}));
  }
  catch{$("save-state").textContent="浏览器未能保存草稿，请保持页面打开或导出工作流。";}
}
function scopeUrl(scope){const [kind,id]=scope.split(":");return "?"+(kind==="run"?"run":kind==="flow"?"workflow":"draft")+"="+encodeURIComponent(id);}
function canNavigate(){if(!busy&&!lifecycleBusy&&!WorkflowSessionOps.isBusy())return true;notice("当前请求正在处理，请完成后切换。",true);return false;}
function activateEditor(scope,seed,{run="",input="",navigate=true,internal=false}={}){
  if(!internal&&!canNavigate())return false;
  persist();++configSequence;
  if(lifecycleIntent)closeLifecycle(false);
  if($("workflow-builder").open)$("workflow-builder").close();
  const state=editorStore.read(scope)||{};
  editorScope=scope;activeRun=run;config=copy(state.config||seed);
  drafts=copy(state.drafts||{});dirty=!!state.dirty;requests=new Map(state.requests||[]);
  generation=state.generation||null;generationBusy=false;builderActive=!!state.active;
  stage=Math.max(0,Math.min(2,state.stage||0));selected=Math.min(state.selected||0,config.steps.length-1);
  $("requirements").value=typeof state.input==="string"?state.input:input;
  inputTouched=!!state.inputTouched;suggestedInput=state.suggestedInput||"";$("chat-entry").checked=!!state.chat;
  selectedTask="";taskScope=run?"current":"draft";tasksRenderKey="";sessionRenderKey="";
  $("notice").hidden=true;
  collapsedFlows.delete(config.id);
  if(navigate)history.replaceState(null,"",scopeUrl(scope));
  sideClose();renderConfig();renderGeneration(generation);renderBoard();persist();refreshGeneration();
  return true;
}
function pendingEditors(){return editorStore.list().filter(s=>s.scope.startsWith("draft:")&&s.active&&s.config?.steps);}
function allDraftDefinitions(){return [...Object.values(drafts),...pendingEditors().map(s=>s.config)];}
function chooseDraft(scope,{navigate=true}={}){
  const state=editorStore.read(scope);if(!state?.config)return;
  activateEditor(scope,state.config,{navigate});
}
function chooseWorkflow(id,{navigate=true}={}){
  const definition=definitions.find(d=>d.id===id);if(!definition)return;
  activateEditor("flow:"+id,definition,{navigate});
}
function notice(text,error=false){$("notice-text").textContent=text;$("notice").className=error?"error":"";$("notice").hidden=false;
  $("builder-message").textContent=text;$("builder-message").className=error?"hint error":"hint";$("builder-message").hidden=!$("workflow-builder").open;}
$("dismiss-notice").onclick=()=>{$("notice").hidden=true;};
async function get(path){const r=await fetch(path);const d=await r.json();if(!r.ok)throw Error(d.error||`HTTP ${r.status}`);return d;}
async function post(path,body){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Workflow-MVP":"1"},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||`HTTP ${r.status}`);return d;}
const current=()=>snapshots.get(activeRun);
const runtimeSteps=()=>{const data=current();return data?.steps?.length?data.steps.map(s=>{
  const key=s.task?.id?"subagent:"+s.task.id:"";
  const waiting=s.task?.awaiting_approval||(key&&data.approvals?.some(a=>(a.session_key||a.session)===key));
  return s.status==="running"&&waiting?{...s,status:"awaiting_approval"}:s;
}):(data?.manifest?.workflow.steps||[]).map(s=>({...s,status:"pending"}));};
function progress(data){return WorkflowProgress.calculate(data);}
function sessionLabel(data){return data?.retry&&data.status==="ready"?"等待续作":labels[data?.status]||"等待输入";}
function runTitle(m,data){return (data?.input?.text||"").trim().split("\n")[0].slice(0,85)||`${m.workflow.name} · 等待输入`;}
function setPill(id,status){$(id).textContent=labels[status]||status;$(id).className="status-pill "+status;}
function sideClose(){delete document.body.dataset.sidebar;$("side-backdrop").hidden=true;$("toggle-sessions").setAttribute("aria-expanded","false");$("toggle-tasks").setAttribute("aria-expanded","false");}
for(const name of ["sessions","tasks"])$("toggle-"+name).onclick=()=>{if(document.body.dataset.sidebar===name)return sideClose();document.body.dataset.sidebar=name;$("side-backdrop").hidden=false;$("toggle-"+name).setAttribute("aria-expanded","true");};
$("side-backdrop").onclick=sideClose;document.querySelectorAll("[data-close-sidebar]").forEach(n=>n.onclick=sideClose);
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("workflow-builder").open)sideClose();});
function workflowGroups(){const groups=new Map(definitions.map(d=>[d.id,d]));
  for(const m of runs)if(!groups.has(m.workflow.id))groups.set(m.workflow.id,m.workflow);
  for(const d of allDraftDefinitions())if(d?.steps&&!groups.has(d.id))groups.set(d.id,d);
  if(config&&!groups.has(config.id))groups.set(config.id,config);return [...groups.values()];}
function renderSessions(){
  const search=$("session-search").value.toLowerCase(),flow=$("session-workflow-filter").value;
  const rows=runs.filter(m=>{const d=snapshots.get(m.id),s=d?.status||"ready",archived=!!m.session_meta?.archived||!!m.session_meta?.closed,deleted=!!deletedFlows[m.workflow.id],removed=!!m.session_meta?.deleted;
    return (!flow||m.workflow.id===flow)&&(!search||(runTitle(m,d)+m.workflow.name+m.id).toLowerCase().includes(search))&&
      (sessionFilter==="deleted"?removed:!removed&&(sessionFilter==="archived"?(archived||deleted):!archived&&!deleted&&(sessionFilter==="all"||(sessionFilter==="completed"?s==="completed":["running","awaiting_approval","failed","check_failed","stopped","stopping"].includes(s)))));});
  visibleRuns=rows.map(m=>m.id);const groups=workflowGroups().filter(g=>(!flow||flow===g.id)&&
    (rows.some(m=>m.workflow.id===g.id)||((!search||g.name.toLowerCase().includes(search))&&sessionFilter==="all"&&!deletedFlows[g.id])||(sessionFilter==="archived"&&deletedFlows[g.id])));
  const activeFlow=runs.find(r=>r.id===activeRun)?.workflow.id||config?.id;groups.sort((a,b)=>Number(b.id===activeFlow)-Number(a.id===activeFlow));
  $("session-count").textContent=groups.length;$("selection-count").textContent=selectedRuns.size+" 项";
  $("session-selection").hidden=$("session-bulk").hidden=!managingSessions;
  $("manage-sessions").textContent=managingSessions?"完成":"管理";$("manage-sessions").setAttribute("aria-pressed",String(managingSessions));
  for(const id of ["export-sessions","archive-sessions","clear-selection"])$(id).disabled=!selectedRuns.size;
  const allArchived=selectedRuns.size&&[...selectedRuns].every(id=>runs.find(r=>r.id===id)?.session_meta?.archived);
  $("archive-sessions").textContent=allArchived?"恢复":"归档";
  $("select-visible").checked=!!rows.length&&rows.every(m=>selectedRuns.has(m.id));
  $("select-visible").indeterminate=rows.some(m=>selectedRuns.has(m.id))&&!$("select-visible").checked;
  const pending=pendingEditors();
  const key=JSON.stringify([editorScope,activeRun,search,flow,sessionFilter,managingSessions,[...collapsedFlows],[...selectedRuns],pending.map(d=>[d.scope,d.config.id,d.config.name,d.input]),groups.map(g=>[g.id,g.name,g.revision,!!deletedFlows[g.id]]),rows.map(m=>[m.id,m.session_meta,runTitle(m,snapshots.get(m.id)),snapshots.get(m.id)?.status,progress(snapshots.get(m.id))])]);
  if(key===sessionRenderKey)return;sessionRenderKey=key;
  $("sessions-list").replaceChildren(...groups.map(g=>{const group=el("section",undefined,"workflow-group");group.dataset.workflow=g.id;
    const children=rows.filter(m=>m.workflow.id===g.id),expanded=!!search||!collapsedFlows.has(g.id);
    const header=el("div",undefined,"workflow-group-heading"),toggle=button(undefined,()=>{
      if(!canNavigate())return;
      if(config?.id!==g.id||!children.length){if(definitions.some(d=>d.id===g.id))chooseWorkflow(g.id);else {const draft=pending.find(d=>d.config.id===g.id);if(draft)chooseDraft(draft.scope);}}
      if(expanded)collapsedFlows.add(g.id);else collapsedFlows.delete(g.id);persist();renderSessions();
    },"workflow-group-title");
    toggle.setAttribute("aria-expanded",String(expanded));toggle.setAttribute("aria-controls","sessions-of-"+g.id);toggle.title=g.name+" · "+g.id;
    const text=el("span");text.append(el("strong",g.name));toggle.append(el("span",expanded?"⌄":"›","flow-disclosure"),text,el("span",String(children.length),"workflow-group-count"));
    header.append(toggle);group.append(header);
    const body=el("div",undefined,"workflow-sessions");body.id="sessions-of-"+g.id;body.hidden=!expanded;
    if(deletedFlows[g.id])body.append(el("p","工作流已删除，可在编辑中恢复。","list-empty"));
    for(const m of children){const d=snapshots.get(m.id),p=progress(d||{manifest:m}),state=d?.status||"ready",row=el("div",undefined,"session-row"+(managingSessions?" selectable":""));
      if(managingSessions){const checkbox=el("input");checkbox.type="checkbox";checkbox.checked=selectedRuns.has(m.id);checkbox.setAttribute("aria-label","选择 Session "+m.id);
      checkbox.onchange=()=>{if(checkbox.checked)selectedRuns.add(m.id);else selectedRuns.delete(m.id);renderSessions();};row.append(checkbox);}
      const b=button(undefined,()=>chooseRun(m.id),"session-item"+(m.id===activeRun?" active":""));b.dataset.run=m.id;b.setAttribute("aria-current",String(m.id===activeRun));b.title=runTitle(m,d)+"\n"+m.id;
      const meta=el("p"),status=el("span",undefined,"session-state with-cycle "+state);
      const label=m.session_meta?.archived?"已归档":sessionLabel(d);
      status.append(WorkflowProgress.cycle(p,m.session_meta?.archived?"archived":state,label),el("span",label));
      meta.append(status,el("span",p.done+"/"+p.total));
      const date=new Date(m.created_at*1000).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});
      b.append(el("strong",runTitle(m,d)),el("small",`${date} · r${m.workflow.revision}`,"session-date"),meta);row.append(b);body.append(row);}
    const flowDrafts=pending.filter(d=>d.config.id===g.id);
    for(const draft of flowDrafts){const item=button(undefined,()=>chooseDraft(draft.scope),"session-item draft-session"+(editorScope===draft.scope?" active":""));item.dataset.editorScope=draft.scope;item.setAttribute("aria-current",String(editorScope===draft.scope));
      item.append(el("strong",draft.input?.trim().split("\n")[0]||draft.config.name),el("small","未开始的独立草稿","session-date"));body.append(item);}
    if(!children.length&&!flowDrafts.length)body.append(el("p","在中央工作区编辑此工作流，或创建新 Session。","list-empty"));group.append(body);return group;}));
  if(!groups.length)$("sessions-list").append(el("p","没有匹配的工作流或 Session。","list-empty"));
}
$("manage-sessions").onclick=()=>{managingSessions=!managingSessions;if(!managingSessions)selectedRuns.clear();renderSessions();};
$("session-search").oninput=renderSessions;$("session-workflow-filter").onchange=renderSessions;
document.querySelectorAll("[data-session-filter]").forEach(b=>b.onclick=()=>{sessionFilter=b.dataset.sessionFilter;document.querySelectorAll("[data-session-filter]").forEach(x=>{x.classList.toggle("active",x===b);x.setAttribute("aria-pressed",String(x===b));});renderSessions();});
async function refreshSessions(){if(refreshBusy)return;refreshBusy=true;try{
  runs=await get("/api/runs");const filter=$("session-workflow-filter"),previous=filter.value;
  const flows=new Map(workflowGroups().map(d=>[d.id,d.name]));filter.replaceChildren(option("","全部工作流"),...[...flows].map(([id,name])=>option(id,name)));filter.value=flows.has(previous)?previous:"";
  const results=await Promise.allSettled(runs.map(async m=>[m.id,await get("/api/state?run="+encodeURIComponent(m.id))]));
  for(const result of results){if(result.status==="fulfilled")snapshots.set(...result.value);}
  renderSessions();if(activeRun)renderBoard();
}catch(e){notice("Session 列表暂时无法更新："+e.message,true);}finally{refreshBusy=false;}}
async function chooseRun(id,{navigate=true}={}){const m=runs.find(r=>r.id===id);if(!m)return;
  if(!activateEditor("run:"+id,m.workflow,{run:id,input:snapshots.get(id)?.input?.text||"",navigate}))return;
  collapsedFlows.delete(m.workflow.id);renderSessions();await poll();}
window.addEventListener("popstate",()=>{if(!canNavigate()){history.replaceState(null,"",scopeUrl(editorScope));return;}selectLocation();});
function activity(s){if(s.model_mismatch&&s.status==="check_failed")return `执行已返回，但模型核对未通过。请求 ${s.model}，实际 ${s.resolved_model}。请选择支持的模型，再建立新运行。`;
  if(s.retry&&s.status==="pending")return "旧候选已失效，等待在 KiroCrew 中继续执行并重新核对交接。";
  if(s.check_error)return s.check_error;if(s.task?.error)return s.task.error;
  if(s.status==="accepted")return "交接已核对，结果与产物已保存。";
  if(s.status==="awaiting_approval"||s.task?.awaiting_approval)return "等待在 KiroCrew 中批准工具操作。";
  if(s.status==="running")return s.task?.last_tool?"正在执行："+s.task.last_tool:"Agent 正在处理任务。";
  if(s.status==="awaiting_check")return "Agent 已返回，等待父会话核对交接。";
  if(s.status==="submitted")return "任务已接收，等待调度执行。";
  return s.status==="failed"?"执行未完成，请查看实际记录。":"等待任务开始或前序交接。";}
function selectTask(id){selectedTask=id;renderBoard();sideClose();}
function renderTasks(){if(!config)return;const data=current(),rows=taskScope==="current"?runtimeSteps():config.steps;
  const key=JSON.stringify([activeRun,taskScope,selectedTask,selected,config.id,config.revision,dirty,rows.map(s=>[s.id,s.name,s.tool,s.status,s.model,s.effort,s.task?.id])]);
  $("scope-current").classList.toggle("active",taskScope==="current");$("scope-draft").classList.toggle("active",taskScope==="draft");
  $("scope-current").setAttribute("aria-pressed",String(taskScope==="current"));$("scope-draft").setAttribute("aria-pressed",String(taskScope==="draft"));
  $("task-count").textContent=rows.length;$("tasks-caption").textContent=taskScope==="current"?(data?`本次固定 r${data.manifest.workflow.revision} · 配置修改用于下次运行。`:"选择一个 Session 查看任务。"):`${config.name} · r${config.revision}${dirty?" 草稿":""} · 仅下次运行生效。`;
  $("config-label").textContent=config.name;$("config-revision").textContent=`r${config.revision}${dirty?" · 草稿":""}`;
  $("draft-indicator").textContent=dirty?"有未保存修改，向导中可继续编辑。":"通过向导构建，下次运行使用新版本。";
  if(key===tasksRenderKey)return;tasksRenderKey=key;
  $("tasks-list").replaceChildren(...rows.map((s,i)=>{const status=taskScope==="current"?(s.status||"pending"):"draft";
    const box=el("div",undefined,"task-item "+status+((taskScope==="current"?selectedTask===s.id:selected===i)?" selected":""));
    const choose=button(undefined,()=>{if(taskScope==="current")selectTask(s.id);else {selected=i;renderTasks();persist();}},"task-select");choose.setAttribute("aria-label",`查看任务 ${s.name}`);choose.setAttribute("aria-current",String(taskScope==="current"?selectedTask===s.id:selected===i));
    const text=el("span",undefined,"task-copy"),meta=el("span",undefined,"task-row-meta");
    meta.append(el("small",toolLabels[s.tool]||s.tool),el("span",status==="draft"?"下次运行":labels[status]||status,"status-pill "+status));text.append(el("strong",s.name),meta);
    const uid=taskScope==="current"?`${activeRun}/${s.id}`:`${config.id}/${s.id}`;choose.title=`${s.id}\n${uid}`;box.dataset.taskUid=uid;
    choose.append(el("span",status==="accepted"?"✓":status==="failed"||status==="check_failed"?"!":String(i+1),"task-number"),text);box.append(choose);return box;}));
  if(!rows.length)$("tasks-list").append(el("p","通过向导配置任务与 Agent。","list-empty"));
}
$("scope-current").onclick=()=>{taskScope="current";renderTasks();};$("scope-draft").onclick=()=>{taskScope="draft";renderTasks();};
function renderEvidence(s){$("evidence").textContent=!s?"等待原生任务记录。":[
  s.name,`配置：${toolLabels[s.tool]} / ${s.model} / effort=${s.effort||"继承默认"}`,
  `状态：${labels[s.status]||s.status}`,s.status==="check_failed"?"任务已返回，交接条件未满足；这不表示代码测试本身失败。":"",
  `实际后端：${s.route?.actual_backend||"未创建"}`,
  `Task ID：${activeRun}/${s.id}`,`原生执行 Task ID：${s.task?.id||s.native_request?.task_id||"尚未分派"}`,`实际模型：${s.resolved_model||"未报告"}`,
  `原生请求模型：${s.requested_model||"—"}`,`Effort 请求：${s.native_request?(s.native_request.reasoning_effort||"继承默认"):"未分派"}`,
  `Effort 实际值：${s.effort_observation?.value||"未报告"}（${s.effort_observation?.source||"—"}）`,
  `KiroCrew Session：${s.route?.crew_session_key||"—"}`,`工具 Session：${s.route?.native_session_id||"—"}`,
  s.native_request?.conversation?`原生续作：${s.native_request.conversation} → ${s.native_request.task_id}`:"",
  `证据来源：${s.evidence_source||"等待调用"}`,s.check_error?`交接核对：${s.check_error}`:"",s.task?.error?`错误：${s.task.error}`:"",
  "",s.task?.result||"Agent 尚未返回最终结果。"].filter(x=>x!=="").join("\n\n");}
function renderProject(data){
  const record=data?.project,manifest=data?.manifest;
  $("project-status").hidden=!manifest||(!record&&(manifest.development_contract||1)<2);
  if($("project-status").hidden)return;
  const status=record?.status||"pending";
  $("project-status").dataset.status=status;
  $("project-status-title").textContent={pending:"等待项目准备",preparing:"正在准备项目源码",ready:"项目源码已就绪",failed:"项目准备需要处理"}[status]||"项目准备";
  $("project-baseline").textContent=record?.commit?`基线 ${record.commit.slice(0,12)}`:record?.kind==="new"?"新建项目":"派发前置阶段";
  $("project-source").textContent=record?.source||(record?.kind==="new"?"按用户需求创建新项目":"由协调者从用户需求确定");
  $("project-directory").textContent=record?.directory?manifest.run+"/"+record.directory:"准备成功后记录";
  $("project-branch").textContent=record?.branch||"—";
  $("project-status-note").textContent=record?.error||(status==="ready"?"后续 Agent 共用此项目检出；依赖、构建与测试按任务和项目脚本执行。":"源码就绪后才分派第一项任务。可在 KiroCrew 对话中查看准备动作或处理认证。");
}
function renderBoard(){const data=current(),m=data?.manifest,rows=runtimeSteps(),p=progress(data);
  WorkflowSessionOps.render(data,sessionOpsReady);
  renderProject(data);
  document.querySelector(".overview").dataset.status=data?.status||"ready";
  $("empty-state").hidden=!!m;$("view-progress").hidden=!m||view!=="progress";$("view-evidence").hidden=!m||view!=="evidence";$("view-report").hidden=!m||view!=="report";
  const percent=p.percent;$("overall-percent").textContent=percent+"%";$("overall-progress").value=percent;$("overall-count").textContent=p.done+" / "+p.total+" 项任务已交接 · 按阶段完成度估算";
  $("running-count").textContent=rows.filter(s=>s.status==="running").length;$("waiting-count").textContent=rows.filter(s=>["pending","submitted"].includes(s.status||"pending")).length;$("approval-count").textContent=data?.approvals?.length||0;
  $("session-title").textContent=m?runTitle(m,data):config?.name||"构建工作流，开始开发";$("session-title").title=m?runTitle(m,data):"";
  $("overview-caption").textContent=m?m.workflow.name+" / Session 开发进度":"先构建工作流，再跟踪开发状态";
  $("connection").classList.toggle("connected",!!data&&data.connected!==false);$("connection").textContent=data?.connected===false?"Gateway 暂时不可达":data?"本机 Gateway 已连接":"本机工作台";
  setPill("status",data?.status||"ready");$("status").textContent=sessionLabel(data);$("mobile-status").textContent=data?sessionLabel(data):"开发工作台";
  $("run-caption").title=m?.id||"";$("run-caption").textContent=m?`Session ${new Date(m.created_at*1000).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"})} · 固定 r${m.workflow.revision}`:"选择一个 Session，或构建新工作流";
  $("updated").textContent=data?.at?"更新 "+timeLabel(data.at):"";
  $("app-link").hidden=!m;$("report").hidden=!m;$("export-current-session").hidden=!m;$("session-menu").hidden=!m;$("new-session").hidden=!m;$("archive-current-session").textContent=runs.find(r=>r.id===activeRun)?.session_meta?.archived?"恢复 Session":"归档 Session";
  if(m){$("app-link").href="/crew-entry.html?run="+encodeURIComponent(m.id);$("report").href="/api/report?run="+encodeURIComponent(activeRun);}
  WorkflowView.render(data,rows,toolLabels,id=>{selectTask(id);setView("progress");});
  if(!m){renderTasks();return;}
  if(!rows.some(s=>s.id===selectedTask))selectedTask=(rows.find(s=>s.status!=="accepted")||rows.at(-1))?.id||"";
  $("workflow-snapshot").textContent=`${rows.length} 个顺序任务 · r${m.workflow.revision}`;
  const trackKey=JSON.stringify([selectedTask,rows.map(s=>[s.id,s.name,s.status])]);
  if(trackKey!==stageRenderKey){stageRenderKey=trackKey;$("stage-track").replaceChildren(...rows.map((s,i)=>{const b=button(undefined,()=>selectTask(s.id),"stage-stop "+(s.status||"pending"));b.setAttribute("aria-current",String(selectedTask===s.id));b.setAttribute("aria-label",`进度：${s.name} ${labels[s.status]||"待执行"}`);b.append(el("span",s.status==="accepted"?"✓":String(i+1),"stage-indicator"),el("strong",s.name),el("small",labels[s.status]||"待执行"));return b;}));}
  const focus=rows.find(s=>s.id===selectedTask);$("task-focus").hidden=!focus;
  $("focus-retry").hidden=!focus||!["failed","check_failed","accepted"].includes(focus.status);
  $("focus-retry").textContent=focus?.status==="accepted"?"优化重试":"重试此阶段";
  $("focus-retry").disabled=!retryReady||retryBusy||data.connected===false||(data.session_control?.status||"active")!=="active"||!!data.parent_running||
    !!data.approvals?.length||rows.some(s=>["running","submitted","awaiting_check","awaiting_approval"].includes(s.status));
  $("focus-retry").title=retryReady?"重新执行此阶段；下游已完成结果会失效并需要重新验证。":"后台进程尚未加载此接口，请按启动指南手动重启工作台后使用。";
  $("copy-retry").hidden=!data.retry;
  if(focus){$("task-focus").dataset.status=focus.status||"pending";$("focus-caption").textContent=focus.status==="running"?"正在开发":"任务状态";$("focus-name").textContent=focus.name;setPill("focus-status",focus.status||"pending");$("focus-activity").textContent=activity(focus);$("focus-agent").textContent=toolLabels[focus.tool]||focus.tool;$("focus-model").textContent=focus.resolved_model||`未报告（请求 ${focus.model}）`;$("focus-effort").textContent=focus.effort_observation?.value||`未报告 · ${focus.effort?"请求 "+focus.effort:"继承默认"}`;$("focus-task-id").textContent=`Task ID：${activeRun}/${focus.id}\n原生执行：${focus.task?.id||"尚未分派"}`;}
  WorkflowView.activities(activeRun,rows,activity,initials,timeLabel,id=>{selectTask(id);setView("progress");});
  const mismatch=rows.find(s=>s.model_mismatch&&s.status==="check_failed");
  const attention=data.connected===false?"连接中断":data.approvals?.length?"需要在 KiroCrew 中授权":mismatch?"执行已返回，模型核对未通过":data.status==="check_failed"?"交接需要处理":data.status==="failed"?"任务需要处理":"";
  $("attention").hidden=!attention;$("attention-title").textContent=attention;$("attention-text").textContent=data.connected===false?(data.error||"显示的是上次收到的状态。"):data.approvals?.length?`${data.approvals.length} 项权限请求等待处理，完成后继续执行。`:mismatch?activity(mismatch):rows.find(s=>s.check_error||s.task?.error)?.check_error||"打开原会话查看失败原因和实际返回。";$("attention-app").href=$("app-link").href;
  $("run-requirement").textContent=data.input?.text||"这个 Session 已准备好，请在 KiroCrew 中输入需求。";
  $("preview").hidden=!data.preview;if(data.preview)$("preview").href="http://localhost:8917/p/"+encodeURIComponent(activeRun)+"/"+data.preview;
  renderEvidence(focus);renderTasks();
}
async function retryFocusedStage(){
  if(retryBusy||!retryReady)return;
  const data=current(),run=activeRun,stage=selectedTask,key=run+"/"+stage;
  if(!data||!runtimeSteps().some(s=>s.id===stage&&["failed","check_failed","accepted"].includes(s.status)))return;
  if(!retryRequests.has(key))retryRequests.set(key,crypto.randomUUID());
  retryBusy=true;renderBoard();
  try{
    const result=await post("/api/retry",{run,stage,request_id:retryRequests.get(key)});
    retryRequests.delete(key);
    notice("已请求重试，请在 KiroCrew 中继续执行。"+
      (result.invalidated.length?"需重新验证的下游阶段："+result.invalidated.join("、")+"。":"")+
      "可点击“复制续作指令”，粘贴到原会话。");
    await poll();
  }catch(e){notice(e.message,true);}
  finally{retryBusy=false;renderBoard();}
}
$("focus-retry").onclick=retryFocusedStage;
$("copy-retry").onclick=async()=>{
  try{await navigator.clipboard.writeText(current().retry.next_hint);notice("续作指令已复制，请粘贴到此 Session 的 KiroCrew 对话。");}
  catch(e){notice("复制失败："+e.message,true);}
};
function setView(next){view=next;for(const name of ["progress","evidence","report"]){$("tab-"+name).setAttribute("aria-selected",String(name===view));$("tab-"+name).tabIndex=name===view?0:-1;}renderBoard();}
$("tab-report").onclick=()=>setView("report");$("tab-progress").onclick=()=>setView("progress");$("tab-evidence").onclick=()=>setView("evidence");$("focus-records").onclick=()=>setView("evidence");
async function poll(){if(!activeRun||pollBusy)return;pollBusy=true;const id=activeRun;try{const data=await get("/api/state?run="+encodeURIComponent(id));snapshots.set(id,data);if(id===activeRun){
  if(!inputTouched&&editorScope==="run:"+id&&data.input?.text!==undefined){$("requirements").value=data.input.text;persist();renderReview();renderGeneration(generation);}
  renderBoard();renderSessions();renderConfigMeta();
}}catch(e){if(id===activeRun){WorkflowView.disconnect();$("connection").textContent="连接异常";$("connection").classList.remove("connected");$("updated").textContent=e.message;}}finally{pollBusy=false;}}
function definitionOptions(){const all=new Map(definitions.map(d=>[d.id,d]));if(config)all.set(config.id,config);for(const [id,d] of Object.entries(drafts))if(!all.has(id)&&d?.steps)all.set(id,d);
  $("definition").replaceChildren(...[...all.values()].map(d=>option(d.id,`${d.name} · r${d.revision}${deletedFlows[d.id]?" · 已删除":""}`)));if(config)$("definition").value=config.id;}
async function refreshDefinitions(){const data=await get("/api/library");definitions=data.definitions;deletedFlows=data.deleted;definitionOptions();renderSessions();}
async function loadConfig(id,{discard=false}={}){const seq=++configSequence,scope=editorScope;let data=definitions.find(d=>d.id===id);if(data)data=await get("/api/config?id="+encodeURIComponent(id));else if(!drafts[id])throw Error("工作流不存在，请重新选择。");if(seq!==configSequence||scope!==editorScope)return;
  if(discard&&!data)throw Error("这个工作流还没有已保存版本；请先保存，或选择另一个已有工作流。");
  if(discard)delete drafts[id];config=copy(drafts[id]||data);dirty=!!drafts[id];selected=Math.min(selected,config.steps.length-1);renderConfig();persist();}
function markDirty(){dirty=true;builderActive=true;drafts[config.id]=copy(config);persist();renderConfigMeta();renderDraftLists();renderTasks();renderReview();}
function renderConfigMeta(){if(!config)return;$("config-label").textContent=config.name;$("config-revision").textContent=`r${config.revision}${dirty?" · 草稿":""}`;
  const manifest=runs.find(r=>r.id===activeRun);
  $("editor-context").textContent=manifest?`当前 Session · ${runTitle(manifest,current())}`:editorScope.startsWith("draft:")?"独立草稿 · "+config.name:"当前工作流 · "+config.name;
  $("editor-context").title=activeRun||config.id;
  $("editor-draft-status").textContent=manifest?`本次固定 r${manifest.workflow.revision}；此 Session 的编辑草稿独立保存，下次运行生效。`:"需求与生成结果仅属于当前草稿；开始后创建独立 Session。";
  $("edit-workflow").disabled=busy;$("edit-workflow").textContent="编辑当前工作流";
  $("save-state").textContent=dirty?"本条草稿独立保存。保存生成新版本，当前运行保持原配置。":`配置 r${config.revision} · 需求与生成记录不会带入其他 Session。`;
  $("reload").textContent=dirty?"放弃当前草稿，重新加载已保存配置":"重新加载已保存配置";
  $("file-command").textContent=`workflows/${config.id}.json\n\npython3 workflow.py save workflows/${config.id}.json --expected-revision ${config.revision}`;
  renderNameValidity();renderActions();}
function modelIssue(s){if(s.tool!=="claude"||["auto","default"].includes(s.model))return "";
  const models=catalog.claude?.advertised_models||[];
  return models.includes(s.model)?"":`${s.name}：${s.model||"未选择模型"} 不在 Claude Code 当前公布的候选中。请刷新并选择候选，或明确选择 auto。`;
}
function renderModelOptions(){const s=config.steps[selected],auto=["auto","default",""].includes(s.model),models=(catalog[s.tool]?.models||["auto"]).filter(x=>typeof x==="string");$("model-options").replaceChildren(...models.map(x=>option(x,x)));
  const issue=modelIssue(s);$("model-help").textContent=issue||(s.tool==="claude"?"候选来自 Claude ACP 最近一次公布的列表；显式模型不在列表时阻止开始。账号权限由工具检查。":"候选来自工具缓存及本 PoC 的运行记录，可能不完整。实际模型与请求不符时停止交接。");$("model-help").className=issue?"hint model-error":"hint";$("step-model").setAttribute("aria-invalid",String(!!issue));
  $("model-shortcuts").replaceChildren(...[...new Set(["auto",s.model,...(catalog[s.tool]?.observed_models||[]),...models])].filter(x=>models.includes(x)&&x!=="default").slice(0,6).map(id=>{const b=button(id==="auto"?"auto · 工具默认":id,()=>{s.model=id;if(["auto","default"].includes(id))s.effort="";markDirty();renderForm();},id===s.model?"active":"");b.title=id;return b;}));
  $("step-effort").replaceChildren(...(auto?[""]:(catalog[s.tool]?.efforts||[""])).map(x=>option(x,x||"继承默认")));$("step-effort").value=s.effort;
  $("step-effort").disabled=busy||s.tool==="opencode"||auto;$("effort-help").textContent=s.tool==="opencode"?"当前 OpenCode 接入使用默认 Effort，不能设置单独覆盖。":auto?"选择具体模型后，可以设置它支持的 Effort。":"实际支持范围取决于模型与适配器；开发记录会区分请求和实际返回。";
  $("agent-title").textContent=toolLabels[s.tool];$("agent-avatar").textContent=initials[s.tool];$("agent-task-caption").textContent="任务："+s.name;}
function renderForm(){if(!config)return;const s=config.steps[selected];$("editing-task-title").textContent=`编辑任务 ${selected+1}：${s.name}`;for(const f of ["id","name","prompt","tool","model"])$("step-"+f).value=s[f];renderModelOptions();}
function renderDraftLists(){if(!config)return;
  $("draft-steps").replaceChildren(...config.steps.map((s,i)=>{const row=el("div",undefined,"draft-step"+(selected===i?" selected":"")),choose=button(undefined,()=>{selected=i;renderDraftLists();renderForm();persist();});choose.append(el("strong",s.name),el("small",`${s.id} · ${toolLabels[s.tool]}`));
    const actions=el("div",undefined,"draft-actions");for(const [label,delta] of [["↑",-1],["↓",1]]){const b=button(label,()=>moveStep(i,delta));b.setAttribute("aria-label",`${delta<0?"上移":"下移"} ${s.name}`);b.dataset.locked=String(i+delta<0||i+delta>=config.steps.length);b.disabled=busy||b.dataset.locked==="true";actions.append(b);}
    const remove=button("移除",()=>{if(config.steps.length<=1||busy)return;config.steps.splice(i,1);selected=Math.min(selected,config.steps.length-1);markDirty();renderForm();});remove.setAttribute("aria-label",`移除 ${s.name}`);remove.dataset.locked=String(config.steps.length<=1);remove.disabled=busy||remove.dataset.locked==="true";actions.append(remove);row.append(el("span",String(i+1)),choose,actions);return row;}));
  $("drawer-add-step").disabled=busy||config.steps.length>=8;
}
function moveStep(i,delta){if(busy)return;const j=i+delta;if(j<0||j>=config.steps.length)return;[config.steps[i],config.steps[j]]=[config.steps[j],config.steps[i]];selected=j;markDirty();renderForm();}
function addStep(){if(!config||busy)return;if(config.steps.length>=8)return notice("首版最多支持 8 个顺序任务。",true);config.steps.push({id:"task-"+crypto.randomUUID().slice(0,6),name:"新任务",tool:"kiro",model:"auto",effort:"",prompt:"根据本次需求完成这个任务，验证结果并写出交接说明。"});selected=config.steps.length-1;markDirty();showBuilder(1);renderForm();}
function renderReview(){if(!config)return;$("review-name").textContent=config.name;$("review-version").textContent=dirty?`将保存 r${config.revision+1}`:`使用 r${config.revision}`;
  $("review-steps").replaceChildren(...config.steps.map(s=>{const n=el("li");n.append(el("strong",s.name),el("small",`${toolLabels[s.tool]} / ${s.model} / Effort ${s.effort||"继承默认"}`));return n;}));
  $("review-input").textContent=$("chat-entry").checked?"创建 KiroCrew Session 后，在 Chat 中输入需求。":$("requirements").value.trim()||"还没有输入需求。可以返回填写，或仅保存工作流稍后开始。";
  const issues=config.steps.map(modelIssue).filter(Boolean);$("review-model-warning").hidden=!issues.length;$("review-model-warning").textContent=issues.join("\n");renderActions();}
function renderActions(){renderLifecycle();if(!config)return;const bridge=config.steps.some(s=>s.tool==="codex"&&s.effort),chat=$("chat-entry").checked,deleted=!!deletedFlows[config.id];
  $("wizard-prev").disabled=busy||stage===0;$("wizard-next").disabled=busy;$("save-only").disabled=busy||deleted||!!workflowNameIssue();$("start").disabled=busy||deleted||!!workflowNameIssue()||config.steps.some(modelIssue)||(!chat&&($("requirements").value.trim().length<3||(bridge&&!runtimeReady)));
  $("start").textContent=busy?"正在处理…":chat?(dirty?"保存并打开 KiroCrew":"打开 KiroCrew 输入需求"):(dirty?"保存并开始":"开始开发");
  $("runtime-note").textContent=bridge&&!runtimeReady&&!chat?"Gateway 尚未加载 Effort 桥接，请通过启动器检查后再开始。":"";
  document.querySelectorAll("#workflow-builder .drawer-scroll input,#workflow-builder .drawer-scroll select,#workflow-builder .drawer-scroll textarea,#workflow-builder .drawer-scroll button").forEach(n=>n.disabled=busy||n.dataset.locked==="true");
  $("save-only").disabled=busy||deleted||!!workflowNameIssue();
  $("delete-workflow").textContent=deleted?"恢复工作流":"删除工作流";
  $("generate-workflow").disabled=busy||generationBusy;
  $("apply-generated").disabled=busy||generation?.intent?.trim()!==$("requirements").value.trim();
  $("step-effort").disabled=busy||config.steps[selected].tool==="opencode"||["auto","default",""].includes(config.steps[selected].model);
  $("drawer-add-step").disabled=busy||config.steps.length>=8;
  document.querySelectorAll(".draft-actions button").forEach(n=>{if(busy)n.disabled=true;});
}
function renderConfig(){if(!config)return;$("workflow-name").value=config.name;definitionOptions();renderForm();renderDraftLists();renderReview();renderConfigMeta();renderTasks();renderSessions();}
function showStage(value){stage=Math.max(0,Math.min(2,value));for(let i=0;i<3;i++)$("wizard-"+i).hidden=i!==stage;
  document.querySelectorAll("[data-wizard-step]").forEach(b=>{if(Number(b.dataset.wizardStep)===stage)b.setAttribute("aria-current","step");else b.removeAttribute("aria-current");});
  $("wizard-next").hidden=stage===2;$("start").hidden=stage!==2;$("wizard-position").textContent=`${stage+1} / 3`;
  $("wizard-next").textContent=["下一步：任务与 Agent","下一步：确认开始"][stage]||"下一步";
  $("workflow-builder").querySelector(".drawer-scroll").scrollTop=0;renderReview();renderConfigMeta();persist();}
function showBuilder(value=stage){if(!config||lifecycleIntent)return;builderActive=true;sideClose();$("builder-message").hidden=true;renderConfig();showStage(value);if(!$("workflow-builder").open)$("workflow-builder").showModal();persist();}
function closeBuilder(){if(busy||lifecycleBusy)return;if(lifecycleIntent)return closeLifecycle();$("workflow-builder").close();persist();renderConfigMeta();}
$("collapse-builder").onclick=closeBuilder;
$("workflow-builder").addEventListener("cancel",e=>{if(lifecycleIntent){e.preventDefault();if(!lifecycleBusy)closeLifecycle();}else if(busy)e.preventDefault();});
$("workflow-builder").addEventListener("close",()=>{if(lifecycleIntent&&!lifecycleBusy)closeLifecycle(false);persist();renderConfigMeta();});
const outsideBuilder=e=>{const d=$("workflow-builder"),r=d.getBoundingClientRect();return e.target===d&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);};
$("workflow-builder").addEventListener("pointerdown",e=>{builderBackdropDown=outsideBuilder(e);});
$("workflow-builder").addEventListener("click",e=>{const close=builderBackdropDown&&outsideBuilder(e);builderBackdropDown=false;if(close&&!lifecycleIntent)closeBuilder();});
function draftNameKey(name){return String(name||"").normalize("NFKC").trim().replace(/\s+/gu," ").toLowerCase().replaceAll("ß","ss").replaceAll("ς","σ");}
function workflowNameIssue(){if(!config)return "";if(!config.name.trim())return "请填写工作流名称。";
  if(/[\p{Cc}\p{Cf}]/u.test(config.name.replace(/\s/gu,"")))return "名称不能包含不可见控制字符。";
  const clash=[...definitions,...allDraftDefinitions()].find(d=>d.id!==config.id&&draftNameKey(d.name)===draftNameKey(config.name));
  return clash?`名称已被「${clash.name}」使用，请换名或编辑已有工作流。`:"";}
function uniqueDraftName(base,id){base=String(base).normalize("NFKC").trim().replace(/\s+/gu," ").slice(0,80)||"我的工作流";
  const used=new Set([...definitions,...allDraftDefinitions()].filter(d=>d.id!==id).map(d=>draftNameKey(d.name)));let name=base,n=2;
  while(used.has(draftNameKey(name))){const suffix=` (${n++})`;name=base.slice(0,80-suffix.length)+suffix;}return name;}
function renderNameValidity(){const issue=workflowNameIssue();$("workflow-name").setAttribute("aria-invalid",String(!!issue));$("workflow-name-help").className="hint name-help"+(issue?" error":"");$("workflow-name-help").textContent=issue||"名称唯一；大小写、全半角及多余空格视为相同。";}
function configValid(){const nameIssue=workflowNameIssue();if(nameIssue)throw Error(nameIssue);if(!config.steps.length)throw Error("请添加至少一个任务。");const ids=new Set();for(const s of config.steps){if(!/^[a-z][a-z0-9-]{0,47}$/.test(s.id)||ids.has(s.id))throw Error("Task ID 必须唯一，以小写字母开头，只含小写字母、数字或短横线。");ids.add(s.id);if(!s.name.trim()||!s.prompt.trim())throw Error("请填写每个任务的名称和说明。");if(!s.model.trim())throw Error("请选择具体模型或填写 auto。");}}
$("wizard-prev").onclick=()=>{if(!busy)showStage(stage-1);};$("wizard-next").onclick=()=>{try{configValid();showStage(stage+1);}catch(e){notice(e.message,true);}};
document.querySelectorAll("[data-wizard-step]").forEach(b=>b.onclick=()=>{if(!busy)showStage(Number(b.dataset.wizardStep));});
$("new-task").onclick=()=>{newDefinition(false);showBuilder(0);};
$("empty-new-task").onclick=()=>{newDefinition(false);showBuilder(0);};
$("edit-workflow").onclick=()=>showBuilder(builderActive?stage:0);
$("drawer-add-step").onclick=addStep;$("back-to-goal").onclick=()=>showStage(0);
$("refresh-models").onclick=async()=>{try{catalog=await get("/api/catalogue");renderModelOptions();renderReview();notice("已重新读取各工具的原生候选；列表不代表账号一定有调用权限。");}catch(e){notice("候选刷新失败："+e.message,true);}};
for(const field of ["id","name","prompt","model"])$("step-"+field).oninput=e=>{config.steps[selected][field]=e.target.value;if(field==="model"){if(["auto","default",""].includes(e.target.value))config.steps[selected].effort="";renderModelOptions();}markDirty();};
$("step-tool").onchange=e=>{Object.assign(config.steps[selected],{tool:e.target.value,model:"auto",effort:""});markDirty();renderForm();};$("step-effort").onchange=e=>{config.steps[selected].effort=e.target.value;markDirty();};$("workflow-name").oninput=e=>{config.name=e.target.value;markDirty();};
$("requirements").oninput=()=>{inputTouched=true;builderActive=true;persist();renderConfigMeta();renderGeneration(generation);renderReview();};
$("chat-entry").onchange=()=>{inputTouched=true;persist();renderReview();};
$("definition").onchange=e=>{chooseWorkflow(e.target.value);showBuilder(0);};
function newDefinition(clone){
  if(!canNavigate())return;
  const value=clone&&config?{...copy(config),id:"flow-"+crypto.randomUUID().slice(0,8),revision:0,name:config.name+" · 副本"}:
    {schema:1,id:"flow-"+crypto.randomUUID().slice(0,8),revision:0,name:"我的工作流",steps:[{id:"first",name:"分析与实现",tool:"kiro",model:"auto",effort:"",prompt:"根据用户需求分析任务并完成实现，实际验证后交接。"}]};
  value.name=uniqueDraftName(value.name,value.id);
  if(activateEditor("draft:"+crypto.randomUUID(),value)){selected=0;markDirty();renderConfig();showBuilder(0);}
}
$("new-workflow").onclick=()=>newDefinition(false);$("copy-workflow").onclick=()=>newDefinition(true);
function fillSuggestedInput(value){$("requirements").value=value;suggestedInput=value;inputTouched=true;$("chat-entry").checked=false;}
function focusGoal(){$("template-picker").open=false;showStage(0);$("workflow-name").focus({preventScroll:true});$("workflow-name").scrollIntoView({block:"center"});}
function useTemplate(t){if(busy)return;++configSequence;
  const input=$("requirements").value.trim(),kept=!!input&&input!==suggestedInput.trim();
  const id=config.id,revision=config.revision;
  config={schema:1,id,revision,name:uniqueDraftName(t.name,id),steps:copy(t.steps)};
  if(!kept)fillSuggestedInput(t.input);
  selected=0;generation=null;markDirty();renderConfig();renderGeneration(null);focusGoal();
  notice("已将模板用于当前草稿。"+(kept?"保留了你手动填写的开发目标。":"已填入项目名称和示例目标。")+"当前运行保持原配置。");
}
function renderTemplates(){
  const search=$("template-search").value.trim().toLowerCase();
  const rows=templates.filter(t=>(templateCategory==="all"||t.category===templateCategory)&&
    (!search||[t.name,t.description,t.input,t.prerequisite,t.output,...t.steps.map(s=>toolLabels[s.tool])].join(" ").toLowerCase().includes(search)));
  $("template-total").textContent=`${templates.length} 个模板 · 应用于当前草稿`;
  $("template-count").textContent=rows.length?`显示 ${rows.length} 个模板；选择后可修改目标、任务及 Agent。`:"没有匹配的模板，请更换关键词或选择「全部」。";
  $("template-filters").replaceChildren(...Object.entries({all:"全部",...templateCategories}).map(([key,label])=>{
    const b=button(label,()=>{templateCategory=key;renderTemplates();$("template-filters").querySelector(`[data-category="${key}"]`).focus();});
    b.dataset.category=key;b.setAttribute("aria-pressed",String(templateCategory===key));return b;
  }));
  $("workflow-templates").replaceChildren(...rows.map(t=>{
    const b=button(undefined,()=>useTemplate(t),"template-card");b.dataset.template=t.id;
    b.append(el("span",`${t.steps.length} 项任务 · ${templateCategories[t.category]||"示例"}`,"template-meta"),
      el("strong",t.name),el("p",t.description),el("small","准备："+t.prerequisite,"template-prerequisite"),
      el("small","产物："+t.output,"template-output"),
      el("small",t.steps.map(s=>toolLabels[s.tool]).join(" → "),"template-route"),el("span","使用模板","template-action"));
    return b;
  }));
}
$("template-search").oninput=()=>{if($("template-search").value.trim())templateCategory="all";renderTemplates();};
$("reload").onclick=async()=>{try{await refreshDefinitions();await loadConfig(config.id,{discard:true});notice("已加载最新保存版本。");}catch(e){notice(e.message,true);}};
function download(content,name,type){const url=URL.createObjectURL(new Blob([content],{type})),a=el("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$("export").onclick=async()=>{try{configValid();const format=$("export-format").value,response=await post("/api/workflow/export",{config,format});download(response.content,config.id+"."+(format==="markdown"?"md":format),"text/plain;charset=utf-8");}catch(e){notice(e.message,true);}};
$("import-file").onchange=async e=>{if(busy)return;try{const file=e.target.files[0];if(!file)return;if(file.size>100000)throw Error("文件最大 100 KB。");busy=true;renderActions();const response=await post("/api/workflow/import",{content:await file.text(),format:file.name.split(".").at(-1).toLowerCase()});
  const value=response.config;value.name=uniqueDraftName(value.name,value.id);
  activateEditor("draft:"+crypto.randomUUID(),value,{internal:true});markDirty();renderConfig();showBuilder(1);notice("已导入为独立新草稿，请填写本次需求并确认任务与 Agent 后保存。");
}catch(error){notice(error.message,true);}finally{e.target.value="";busy=false;renderActions();}};
async function saveDefinition(){configValid();if(!dirty)return;config=await post("/api/config",{config,expected_revision:config.revision});delete drafts[config.id];dirty=false;persist();await refreshDefinitions();renderConfig();}
$("save-only").onclick=async()=>{if(busy)return;try{busy=true;renderActions();await saveDefinition();notice(`工作流已保存为 r${config.revision}，可以稍后开始。`);builderActive=true;}catch(e){notice(e.message,true);}finally{busy=false;renderConfig();}};
$("start").onclick=async()=>{if(busy)return;const chat=$("chat-entry").checked,input=$("requirements").value.trim();if(!chat&&input.length<3)return notice("请先填写需求，或选择在 KiroCrew 中输入。",true);
  try{busy=true;renderActions();catalog=await get("/api/catalogue");const issue=config.steps.map(modelIssue).find(Boolean);if(issue){showStage(1);throw Error(issue);}await saveDefinition();const mode=chat?"prepare":"start",signature=JSON.stringify([config.id,config.revision,mode,input]);if(!requests.has(signature))requests.set(signature,crypto.randomUUID());persist();
    const response=await post("/api/"+mode,{workflow_id:config.id,revision:config.revision,input,request_id:requests.get(signature)});
    const accepted=["accepted","prepared"].includes(response.status);
    if(accepted)requests.delete(signature);
    if(response.manifest){
      const manifest=response.manifest;builderActive=false;persist();
      runs=[manifest,...runs.filter(r=>r.id!==manifest.id)];
      const scope="run:"+manifest.id;
      if(!editorStore.read(scope))editorStore.write(scope,{config:manifest.workflow,input,requests:[...requests],generation:null,active:false,inputTouched:!chat});
      activateEditor(scope,manifest.workflow,{run:manifest.id,input,internal:true});
    }
    if(accepted){builderActive=false;stage=0;$("workflow-builder").close();notice(chat?"Session 已准备好，点击“打开 KiroCrew”输入需求。":"KiroCrew 已接收任务，进度与结果将在这里更新。");}
    else notice(`提交状态需要确认：${response.error||response.intake?.error||response.status}。请打开 KiroCrew 查看；重试相同请求不会创建重复任务。`,true);
    persist();await refreshSessions();await poll();
  }catch(e){notice("未能确认提交："+e.message+"。配置草稿与需求已保留。",true);}finally{busy=false;renderConfigMeta();}}
async function refreshRuntime(){const info=await get("/api/runtime");runtimeReady=(info.features||[]).includes("codex_effort_pair_v1");retryReady=(info.workbench_features||[]).includes("stage_retry_v1");sessionOpsReady=(info.workbench_features||[]).includes("session_operations_v1");WorkflowSessionOps.render(current(),sessionOpsReady);if(config)renderActions();}
async function exportSessions(ids){
  if(!ids.length)return;
  notice("正在通过 KiroCrew 原生接口导出对话、任务证据与开发文档…");
  try{
    const response=await fetch("/api/sessions/export",{method:"POST",headers:{"Content-Type":"application/json","X-Workflow-MVP":"1"},body:JSON.stringify({runs:ids})});
    if(!response.ok){const data=await response.json();throw Error(data.error||"导出失败");}
    download(await response.blob(),ids.length===1?ids[0]+".zip":"kirocrew-sessions.zip","application/zip");
    notice("已导出：原生对话包、工作流 YAML / Markdown、开发手册、Release 候选记录与 Feature 输入。");
  }catch(e){notice(e.message,true);}
}
$("select-visible").onchange=e=>{for(const id of visibleRuns){if(e.target.checked)selectedRuns.add(id);else selectedRuns.delete(id);}renderSessions();};
$("clear-selection").onclick=()=>{selectedRuns.clear();renderSessions();};
$("export-sessions").onclick=()=>exportSessions([...selectedRuns]);
$("export-current-session").onclick=()=>exportSessions(activeRun?[activeRun]:[]);
async function archiveSessions(ids){if(!ids.length)return;
  const archived=!ids.every(id=>runs.find(r=>r.id===id)?.session_meta?.archived);
  try{await post("/api/sessions/archive",{runs:ids,archived});selectedRuns.clear();await refreshSessions();
    notice(archived?"已移入工作台归档，原对话与产物保留，运行中的任务继续。":"Session 已恢复到工作流列表。");
  }catch(e){notice(e.message,true);}
}
$("archive-sessions").onclick=()=>archiveSessions([...selectedRuns]);
$("archive-current-session").onclick=()=>{if(activeRun)archiveSessions([activeRun]);$("session-menu").open=false;};
async function copyIdentity(value){try{await navigator.clipboard.writeText(value);notice("已复制："+value);}catch{notice("复制不可用，请从执行记录中选择 ID。",true);}}
$("copy-session-id").onclick=()=>{if(activeRun)copyIdentity(activeRun);$("session-menu").open=false;};
$("copy-task-id").onclick=()=>{if(activeRun&&selectedTask)copyIdentity(activeRun+"/"+selectedTask);};
$("new-session").onclick=()=>{const manifest=current()?.manifest;if(!manifest||!canNavigate())return;
  const definition=definitions.find(d=>d.id===manifest.workflow.id)||manifest.workflow;
  if(activateEditor("draft:"+crypto.randomUUID(),definition)){builderActive=true;persist();showBuilder(0);notice("新的 Session 草稿已建立，请填写它自己的需求。");}
};
function renderLifecycle(){
  $("workflow-delete-panel").hidden=!lifecycleIntent;
  $("workflow-builder").classList.toggle("confirming-lifecycle",!!lifecycleIntent);
  $("collapse-builder").disabled=busy||lifecycleBusy;
  $("collapse-builder").textContent=lifecycleIntent?"← 返回编辑":"收起编辑器";
  $("workflow-delete-cancel").disabled=lifecycleBusy;
  $("workflow-delete-submit").disabled=lifecycleBusy;
  if(lifecycleIntent){const {deleted,persisted}=lifecycleIntent;
    $("workflow-delete-submit").textContent=lifecycleBusy?(deleted?"正在删除…":"正在恢复…"):(deleted?(persisted?"确认删除":"删除草稿"):"确认恢复");
    $("workflow-delete-submit").classList.toggle("restore-submit",!deleted);
  }
}
function closeLifecycle(focus=true){if(lifecycleBusy)return;lifecycleIntent=null;renderLifecycle();
  const scroll=$("workflow-builder").querySelector(".drawer-scroll");scroll.scrollTop=lifecycleScroll;
  if(focus&&$("workflow-builder").open)$("delete-workflow").focus({preventScroll:true});
}
$("delete-workflow").onclick=()=>{if(busy||lifecycleBusy||lifecycleIntent||!config)return;
  lifecycleIntent=Object.freeze({id:config.id,name:config.name,revision:config.revision,persisted:config.revision>0,deleted:!deletedFlows[config.id]});
  lifecycleScroll=$("workflow-builder").querySelector(".drawer-scroll").scrollTop;
  const target=lifecycleIntent,count=runs.filter(r=>r.workflow.id===target.id).length;
  $("workflow-delete-title").textContent=target.deleted?(target.persisted?"删除这个工作流？":"删除这份草稿？"):"恢复这个工作流？";
  $("workflow-delete-name").textContent=target.name;
  $("workflow-delete-identity").textContent=target.id+(target.persisted?` · r${target.revision}`:" · 尚未保存");
  $("workflow-delete-description").textContent=target.deleted?(target.persisted?`工作流将移入“归档”。已有 ${count} 条 Session、版本与运行记录保留，可以恢复。未保存的配置草稿也保留。`:"仅删除当前浏览器中的这份未保存草稿。不会创建替代草稿，也不会影响已有工作流或 Session。"):"恢复后重新显示在工作流列表中，可以继续编辑或开始新的 Session。";
  $("workflow-delete-error").hidden=true;renderLifecycle();$("workflow-delete-cancel").focus({preventScroll:true});
};
$("workflow-delete-cancel").onclick=()=>closeLifecycle();
$("workflow-delete-submit").onclick=async()=>{const target=lifecycleIntent;if(!target||lifecycleBusy)return;
  lifecycleBusy=true;$("workflow-delete-error").hidden=true;renderLifecycle();
  let completed=false,refreshError="",fallback=null;
  try{
    if(target.persisted){
      await post("/api/workflow/lifecycle",{workflow_id:target.id,revision:target.revision,deleted:target.deleted});
      if(target.deleted)deletedFlows[target.id]={revision:target.revision};else delete deletedFlows[target.id];
      try{await refreshDefinitions();}catch(e){refreshError=" 列表刷新失败，请稍后重新加载："+e.message;}
    }else{
      editorStore.remove(editorScope);editorScope="";config=null;activeRun="";drafts={};dirty=false;selected=0;
      generation=null;requests=new Map();builderActive=false;$("requirements").value="";inputTouched=false;
      fallback=definitions.find(d=>!deletedFlows[d.id])||definitions[0];
      $("tasks-list").replaceChildren();$("config-label").textContent="尚未选择工作流";$("config-revision").textContent="—";
      $("editor-context").textContent="选择工作流或 Session";$("edit-workflow").disabled=true;
    }
    completed=true;
  }catch(e){$("workflow-delete-error").textContent="未能确认操作完成："+e.message+"。可以重试，或取消返回编辑。";$("workflow-delete-error").hidden=false;}
  finally{lifecycleBusy=false;renderLifecycle();}
  if(completed){closeLifecycle(false);if(fallback)chooseWorkflow(fallback.id);persist();if(config)renderConfig();else {definitionOptions();renderSessions();renderBoard();$("workflow-builder").close();}
    notice((target.deleted?(target.persisted?"工作流已移入归档，历史 Session 和版本保留，可在归档中恢复。":"未保存草稿已删除，没有新建替代草稿。"):"工作流已恢复。")+refreshError,!!refreshError);
    if(config&&$("workflow-builder").open)$("delete-workflow").focus({preventScroll:true});
  }
};
const generationPolling=new Set();
function receiveGeneration(scope,id,patch){
  try{
    if(!editorStore.updateGeneration(scope,id,patch))return;
    if(editorScope===scope&&generation?.id===id){generation=editorStore.read(scope).generation;renderGeneration(generation);}
  }catch(e){if(scope===editorScope)notice("生成结果暂时无法保存："+e.message,true);}
}
function renderGeneration(data){
  generationBusy=!!data&&["preparing","submitted","running","waiting","awaiting_approval"].includes(data.status);
  $("apply-generated").hidden=data?.status!=="ready"||!!data?.applied;$("generation-chat").hidden=!data?.id;
  if(data?.id)$("generation-chat").href="/crew-entry.html?design="+encodeURIComponent(data.id);
  const states={preparing:"正在创建 KiroCrew 生成会话…",submitted:"KiroCrew 已收到意图，等待生成草稿。",running:"Kiro CLI 正在分析意图并编排任务…",waiting:"等待 KiroCrew 返回草稿…",awaiting_approval:"需要在 KiroCrew 中授权："+(data?.permission_title||"工具操作"),ready:data?.applied?"已采用生成草稿，名称、目标和任务已填入向导，可继续编辑。":`草稿已生成：${data?.config?.name||""}，${data?.config?.steps?.length||0} 项任务。采用后自动填入名称和开发目标。`,invalid:"生成文件尚未通过格式校验："+(data?.error||""),uncertain:"提交结果待确认，请打开 KiroCrew 查看："+(data?.error||""),needs_attention:data?.error||"请打开 KiroCrew 查看生成结果。"};
  const stale=data?.intent&&data.intent.trim()!==$("requirements").value.trim();
  $("generation-status").textContent=data?(stale&&!data.applied?"需求已修改，旧生成结果不会覆盖当前输入。请按当前需求重新生成。":states[data.status]||data.error||"等待生成"):"使用上面的同一份需求生成任务；结果仅属于当前 Session 或草稿。";
  if(config)renderActions();
}
async function refreshGeneration(){
  if(!generation?.id)return;
  const id=generation.id,scope=editorScope,key=scope+"/"+id;
  if(generationPolling.has(key))return;generationPolling.add(key);
  try{const data=await get("/api/design?id="+encodeURIComponent(id));receiveGeneration(scope,id,data);}
  catch(e){if(editorScope===scope&&generation?.id===id){generationBusy=false;$("generation-status").textContent="生成状态暂时不可达："+e.message;if(config)renderActions();}}
  finally{generationPolling.delete(key);}
}
$("generate-workflow").onclick=async()=>{const intent=$("requirements").value.trim();
  if(intent.length<3)return notice("请先填写上面的开发需求。",true);
  if(generationBusy||busy)return;
  // Reuse an uncertain request for the same intent instead of duplicating chat.
  const request_id=generation?.intent===intent&&generation.status==="uncertain"?generation.request_id:crypto.randomUUID();
  const scope=editorScope,id="design-"+request_id.replaceAll("-","");
  generation={id,request_id,intent,status:"preparing"};persist();renderGeneration(generation);
  try{const data=await post("/api/design",{intent,request_id});receiveGeneration(scope,id,{...data,request_id,intent});
    if(editorScope===scope&&generation?.id===id)await refreshGeneration();
  }
  catch(e){receiveGeneration(scope,id,{status:"uncertain",error:e.message});if(editorScope===scope)notice("未能确认生成提交："+e.message,true);}
};
$("apply-generated").onclick=()=>{
  if(busy||generation?.status!=="ready"||!generation.config||generation.applied)return;
  const intent=generation.intent?.trim();
  if(!intent)return notice("生成记录缺少原始意图，请重新生成后再采用。",true);
  if(intent!==$("requirements").value.trim())return notice("需求已修改，请按当前需求重新生成。旧结果不会覆盖你的输入。",true);
  ++configSequence;config={...copy(generation.config),id:config.id,revision:config.revision};config.name=uniqueDraftName(config.name,config.id);
  fillSuggestedInput(intent);selected=0;generation.applied=true;
  markDirty();renderConfig();renderGeneration(generation);focusGoal();
  notice("已自动填入项目 / 工作流名称、开发目标和任务。请确认目标，再继续配置任务与 Agent。");
};
// Keyboard navigation supplements native dialog focus trapping and Escape.
document.querySelectorAll('[role="tablist"]').forEach(list=>list.addEventListener("keydown",e=>{if(!["ArrowLeft","ArrowRight"].includes(e.key))return;const tabs=[...list.querySelectorAll('[role="tab"]')],i=tabs.indexOf(document.activeElement);if(i<0)return;e.preventDefault();const next=tabs[(i+(e.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length];next.click();next.focus();}));
async function selectLocation(){
  const params=new URLSearchParams(location.search),run=params.get("run"),flow=params.get("workflow"),draft=params.get("draft");
  if(run&&runs.some(r=>r.id===run)){await chooseRun(run);return;}
  if(draft&&editorStore.read("draft:"+draft)){chooseDraft("draft:"+draft);return;}
  if(flow&&definitions.some(d=>d.id===flow)){chooseWorkflow(flow);return;}
  const first=runs.find(r=>!r.session_meta?.archived&&!r.session_meta?.closed&&!r.session_meta?.deleted&&!deletedFlows[r.workflow.id]);
  if(first){await chooseRun(first.id);return;}
  const pending=pendingEditors()[0];
  if(pending)chooseDraft(pending.scope);
  else if(definitions.length)chooseWorkflow(definitions[0].id);
  else newDefinition(false);
}
async function init(){try{
  [catalog,templates]=await Promise.all([get("/api/catalogue"),get("/workflow-templates.json")]);renderTemplates();
  await refreshDefinitions();
  try{editorStore.migrate(stored(LEGACY_STORAGE),definitions);}catch{notice("旧版草稿暂时无法迁移，原浏览器记录仍保留。",true);}
  await Promise.all([refreshRuntime(),refreshSessions()]);
  await selectLocation();renderBoard();renderConfigMeta();
  if(!runs.length)showBuilder(0);
}catch(e){notice("工作台加载失败："+e.message,true);}}
WorkflowSessionOps.init();
init();setInterval(poll,2000);setInterval(()=>{refreshSessions();refreshRuntime().catch(()=>{});refreshGeneration();},10000);
