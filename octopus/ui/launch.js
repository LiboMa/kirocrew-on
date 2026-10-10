"use strict";

// One requirement input; drafts belong to a user, space and originating Session.
function launchComposer(seed={}) {
  const workflowMode=seed.mode==="workflow";
  const scope = [state.me.id, state.space || "personal", state.selected || "new",
    ...(workflowMode?["workflow",seed.workflow?.id||"new"]:[])].join("/");
  const key = "octopus.launch." + scope;
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(key) || "{}"); } catch (_) { /* fresh draft */ }
  const baseKey=JSON.stringify({workflow:seed.workflow,name:seed.name,steps:seed.steps});
  const initial=workflowMode && saved.baseKey===baseKey && saved.steps?.length?{}:seed;
  const explicit = !!(initial.workflow_id || initial.template_id || initial.steps);
  const sourceChoice = saved.sourceConfirmed || saved.source==="git" ? saved.source : "";
  const eligibleWorkers=state.workers.filter(w=>w.enabled&&((saved.space??state.space)?
    w.team_id===(saved.space??state.space):w.owner_id===state.me.id));
  const initialWorker=saved.worker || (eligibleWorkers.length===1?eligibleWorkers[0].id:"");
  let choice = initial.workflow_id ? "workflow:" + initial.workflow_id : initial.template_id ? "template:" + initial.template_id :
    initial.steps ? "custom" : saved.choice || "auto";
  let draft = structuredClone(initial.steps || saved.steps || []);
  let resolution = explicit ? null : saved.resolution || null;
  let nameTouched = !!saved.nameTouched || !!seed.workflow?.id, alive = true, sequence = 0, timer, submitted = false, recommender;
  const flows = state.flows.filter(f => f.role !== "viewer");
  const choices = [["auto", "自动匹配 · 按这次需求"], ["custom", "自定义 · 编辑下方任务列表"]];
  const available = new Set(["auto", "custom", ...flows.map(f=>"workflow:"+f.id), ...Object.keys(templates).map(id=>"template:"+id)]);
  if (!available.has(choice)) { choice="auto"; resolution=null; draft=[]; }
  const content = `
    <p class="hint">先选预设查看任务与提示词，也可让 KiroCrew 根据需求推荐；生成后采用并编辑，再${workflowMode?"保存工作流":"提交开发"}。</p>
    <label>编排方式<select id="plan-mode">${options([["recommend","Agent 推荐编排 → 审阅并采用（推荐）"],["manual","手动编排 / 直接采用预设任务"]],saved.planMode||(workflowMode||!explicit?"recommend":"manual"))}</select></label>
    <label class="intent-label">这次要完成什么？
      <textarea id="new-input" required minlength="3" maxlength="12000" rows="4" placeholder="例如：修复登录页刷新后状态丢失的 Bug，补充回归测试。也可以输入“使用我的代码审查工作流”。">${esc(initial.input ?? saved.input ?? "")}</textarea>
    </label>
    <div class="intent-examples" aria-label="需求示例">
      <button type="button" data-intent="修复登录页刷新后状态丢失的 Bug，并补充回归测试。">修复 Bug</button>
      <button type="button" data-intent="对现有代码进行安全审计，独立检查测试覆盖并形成审查报告。">安全审查</button>
      <button type="button" data-intent="快速验证一个小功能，完成最小实现与独立测试。">快速验证</button>
    </div>
    <div class="form-row">
      <label>${workflowMode?"工作流":"Session"}名称<input id="new-name" required maxlength="80" value="${esc(initial.name ?? saved.name ?? "")}" placeholder="根据需求自动填入，可修改"></label>
      <label>执行空间<select id="new-space">${options([["","我的私有空间"],...state.teams.map(t=>[t.id,t.name+" · Team 共同可见"])],saved.space ?? state.space)}</select></label>
    </div>
    <label>工作流选择<select id="new-workflow">${options(choices,choice)}
      <optgroup label="我的与共享的可运行工作流">${options(flows.map(f=>["workflow:"+f.id,`${f.name} · v${f.version}`]),choice)}</optgroup>
      <optgroup label="预定义模板 · ${Object.keys(templates).length} 个">${options(Object.entries(templates).map(([id,t])=>["template:"+id,t.name]),choice)}</optgroup>
    </select></label>
    <div class="match-result" aria-live="polite"><strong id="match-name">等待输入需求</strong><p id="match-reason">输入至少 3 个字符，自动显示工作流与任务。</p><small>即时模板 / 关键词匹配 · Chat 可通过 API/MCP 编排自定义任务</small></div>
    <div id="match-alternatives" class="intent-examples"></div>
    <section class="recommend-box">
      <div class="section-heading"><h2>KiroCrew 工作流建议</h2><span>后台 Agent 生成 · 用户采用</span></div>
      <div class="recommend-actions"><label>生成建议的 Worker<select id="recommend-worker"></select></label>
        <button type="button" id="recommend-start" class="primary">让 KiroCrew 推荐</button>
        <button type="button" id="recommend-apply" hidden>采用建议</button>
        <button type="button" id="recommend-stop" class="quiet" hidden>停止生成</button>
        <button type="button" id="recommend-open" class="quiet" hidden>在 KiroCrew 查看 ↗</button>
      </div>
      <p id="recommend-status" role="status"></p><p id="recommend-native" class="mono"></p>
      <details id="recommend-preview" hidden><summary>查看推荐的任务和 Agent 提示词</summary><pre id="recommend-preview-body"></pre></details>
      <small>模板预设始终可用。点击推荐会实际调用 Kiro Agent；关闭窗口后可重新打开查看，不会自动执行推荐工作流。</small>
    </section>
    <div class="section-heading"><h2>本次任务列表 <span id="launch-count"></span></h2><button type="button" id="launch-add">＋ 添加任务</button></div>
    <div id="launch-tasks"></div>
    <div class="form-row">
      <label ${workflowMode?"hidden":""}>代码来源<select id="new-source"${workflowMode?"":" required"}>${options([["","请选择目标源码，不能默认使用空项目"],["new","明确创建一个全新项目"],["git","开发已有 Git 仓库"]],workflowMode?"new":sourceChoice)}</select></label>
      <label>执行 Worker<select id="new-worker">${workerOptions(saved.space ?? state.space,initialWorker)}</select></label>
    </div>
    <div class="form-row" id="git-fields" hidden><label>Git URL<input id="new-url" value="${esc(saved.url || "")}" placeholder="https://…/project.git 或 git@…"></label><label>分支 / Tag / Commit（可选）<input id="new-ref" value="${esc(saved.ref || "")}" placeholder="默认分支"></label></div>
    ${workflowMode?"":recoveryFields(saved.recovery||{enabled:true,max_cycles:2},"launch-recovery")}
    <div class="notice">${workflowMode?"保存为可重复使用的工作流；已有工作流保存新版本，不影响正在运行的 Session。":"提交时保存本次需求和任务快照，不修改工作流库或已有 Session。未选择 Worker 时先登记需求，稍后分配。"}</div>`;

  const captureTasks = () => draft.map((s,i)=>({...s,name:$("launch-"+i+"-name").value,
    prompt:$("launch-"+i+"-prompt").value,...readAgent("launch-"+i)}));
  const persist = () => {
    if (!alive || submitted || !$("new-input")) return;
    const value = {input:$("new-input").value,name:$("new-name").value,nameTouched,choice:$("new-workflow").value,
      steps:captureTasks(),resolution,space:$("new-space").value,worker:$("new-worker").value,
      source:$("new-source").value,url:$("new-url").value,ref:$("new-ref").value,
      sourceConfirmed:!!$("new-source").value,planMode:$("plan-mode").value,
      recovery:workflowMode?undefined:readRecovery("launch-recovery"),
      generation:recommender?.snapshot() || saved.generation || null,baseKey};
    try { sessionStorage.setItem(key,JSON.stringify(value)); }
    catch (_) { $("modal-note").textContent="浏览器无法保存草稿；当前输入仍可提交。"; }
    recommender?.changed();
  };
  const sourceFields = () => {
    $("git-fields").hidden=$("new-source").value!=="git";
    $("new-url").required=$("new-source").value==="git";
  };
  const renderTasks = () => {
    $("launch-count").textContent=draft.length ? "· "+draft.length+" Tasks" : "";
    $("launch-tasks").innerHTML=draft.map((s,i)=>`
      <details class="launch-task" ${draft.length===1?"open":""}>
        <summary><span class="task-index">${i+1}</span><strong>${esc(s.name)}</strong><small>${esc(tools[s.tool])} · ${esc(s.model||"auto")}</small><span>配置</span></summary>
        <div class="launch-task-fields"><div class="step-top"><span class="mono">Task ID · ${esc(s.id)}</span>
          <button type="button" data-launch-up="${i}" ${i===0?"disabled":""}>上移</button>
          <button type="button" data-launch-remove="${i}" ${draft.length===1?"disabled":""}>移除</button></div>
          <label>任务名称<input id="launch-${i}-name" value="${esc(s.name)}" required maxlength="80"></label>
          <label>Agent 提示词 / 任务说明<textarea id="launch-${i}-prompt" required>${esc(s.prompt)}</textarea></label>
          ${agentFields(s,"launch-"+i,$("new-worker").value || $("recommend-worker").value)}
        </div>
      </details>`).join("");
    draft.forEach((s,i)=>bindAgent("launch-"+i,$("new-worker").value || $("recommend-worker").value));
  };
  const manual = () => {
    clearTimeout(timer);++sequence;choice="custom";$("new-workflow").value=choice;
    draft=captureTasks();resolution=null;
    if(!recommender?.snapshot()?.applied)$("plan-mode").value="manual";
    $("launch-tasks").querySelectorAll("summary").forEach((node,i)=>{
      node.querySelector("strong").textContent=draft[i].name;
      node.querySelector("small").textContent=tools[draft[i].tool]+" · "+draft[i].model;
    });
    $("match-name").textContent="自定义工作流 · 已保留你的任务配置";
    $("match-reason").textContent="修改需求不会覆盖这些任务；需要重新匹配时，手动选择“自动匹配”。";
    $("match-alternatives").replaceChildren();persist();
  };
  const selected = () => {
    const value=$("new-workflow").value;
    if(value==="custom")return {steps:captureTasks()};
    if(value.startsWith("workflow:"))return {workflow_id:value.slice(9)};
    if(value.startsWith("template:"))return {template_id:value.slice(9)};
    return {};
  };
  const match = async () => {
    clearTimeout(timer);
    const seq=++sequence,input=$("new-input").value.trim(),space=$("new-space").value,which=$("new-workflow").value;
    if(!nameTouched)$("new-name").value=input.split(/[\n。！？!?]/)[0].slice(0,60);
    if(which==="custom") { persist();return; }
    resolution=null;
    if(input.length<3) {
      const preset=which.startsWith("template:")?templates[which.slice(9)]:null;
      const flow=which.startsWith("workflow:")?flows.find(f=>f.id===which.slice(9)):null;
      draft=structuredClone(preset?.steps || flow?.steps || []);renderTasks();
      $("match-name").textContent=preset||flow?"已载入预设："+(preset||flow).name:"等待输入需求";
      $("match-reason").textContent="预设提示词已可编辑；填写需求后可以由后台 Kiro Agent 进一步推荐。";persist();return;
    }
    $("match-name").textContent=which==="auto"?"正在匹配工作流…":"正在读取选定工作流…";
    $("match-reason").textContent="匹配不会创建 Session 或派发 Coding 任务。";
    try {
      const result=await api("/workflows/resolve","POST",{input,...selected(),...(space?{team_id:space}:{})});
      if(!alive || seq!==sequence || $("new-input").value.trim()!==input ||
         $("new-space").value!==space || $("new-workflow").value!==which)return;
      resolution=result;draft=structuredClone(result.steps);
      if(!nameTouched)$("new-name").value=result.name;
      $("match-name").textContent=(which==="auto"?"已匹配：":"已指定：")+result.workflow_name;
      $("match-reason").textContent=result.reason;
      $("match-alternatives").innerHTML=(result.recommendations||[]).slice(1,4).map((r,i)=>
        `<button type="button" data-match-alternative="${i}">${esc(r.name)} · ${r.tasks} Tasks</button>`).join("");
      $("match-alternatives").querySelectorAll("button").forEach((b,i)=>b.onclick=()=>{
        const pick=result.recommendations[i+1].selection;
        $("new-workflow").value=pick.workflow_id?"workflow:"+pick.workflow_id:"template:"+pick.template_id;
        choice=$("new-workflow").value;match();
      });
      renderTasks();persist();
    } catch(error) {
      if(!alive || seq!==sequence)return;
      $("match-name").textContent="暂未得到匹配结果";$("match-reason").textContent=error.message;
      draft=[];renderTasks();persist();
    }
  };

  modal(workflowMode?"由需求设计工作流":"从需求开始构建任务",content,async()=>{
    const input=$("new-input").value.trim(),which=$("new-workflow").value;
    if(!draft.length || (which!=="custom" && (!resolution || resolution.input!==input)))
      throw Error("请等待当前需求匹配完成，或手动配置任务列表。");
    if($("plan-mode").value==="recommend"&&!recommender?.snapshot()?.applied)
      throw Error("请先让 KiroCrew 推荐并采用方案；也可以明确选择手动编排。");
    if(workflowMode) {
      const existing=seed.workflow;
      await api("/workflows"+(existing?.id?"/"+existing.id+"/versions":""),"POST",
        {name:$("new-name").value,steps:captureTasks(),...(existing?.id?{expected_version:existing.version}:{})});
      submitted=true;try{sessionStorage.removeItem(key);}catch(_){}
      closeModal();await refresh();toast("工作流已保存，可从工作流库重复使用。");return;
    }
    const selection=which==="custom"?{steps:captureTasks()}:resolution.selection;
    if(!$("new-source").value)throw Error("请明确选择新建项目或目标 Git 仓库，避免把空工作区当作已有项目。");
    const source=$("new-source").value==="git"?{type:"git",url:$("new-url").value.trim(),
      ...($("new-ref").value.trim()?{ref:$("new-ref").value.trim()}:{})}:{type:"new"};
    const body={...selection,input,name:$("new-name").value,source,recovery:readRecovery("launch-recovery"),
      ...($("new-space").value?{team_id:$("new-space").value}:{}),...($("new-worker").value?{worker_id:$("new-worker").value}:{})};
    const run=await command("/runs",body);
    submitted=true;try{sessionStorage.removeItem(key);}catch(_){}
    closeModal();await refresh();await selectRun(run.id);toast("需求与工作流已提交，任务快照已保存。");
  },{submit:workflowMode?"保存工作流":"确认任务并提交",kicker:"需求 → 工作流 → 任务",note:"草稿仅属于当前用户、开发空间与来源 Session。"});
  // The cleanup runs before another modal replaces the DOM, including Escape.
  modalCleanup=()=>{persist();recommender?.close();alive=false;++sequence;clearTimeout(timer);};
  sourceFields();renderTasks();
  if(seed.steps){$("match-name").textContent="使用当前任务列表的副本";$("match-reason").textContent="编辑此草稿不修改原 Session。";}
  $("new-input").oninput=()=>{
    ++sequence;clearTimeout(timer);
    if(!nameTouched)$("new-name").value=$("new-input").value.trim().split(/[\n。！？!?]/)[0].slice(0,60);
    if($("new-workflow").value!=="custom") {resolution=null;timer=setTimeout(match,350);}
    if(!$("new-source").value) {
      const url=$("new-input").value.match(/https:\/\/(?:github\.com|gitlab\.com)\/[\w.-]+\/[\w.-]+(?:\.git)?/);
      if(url){$("new-source").value="git";$("new-url").value=url[0];sourceFields();}
    }
    persist();
  };
  $("new-name").oninput=()=>{nameTouched=true;persist();};
  $("new-workflow").onchange=()=>{
    choice=$("new-workflow").value;
    if(choice==="custom") {
      if(!draft.length){draft=templateCopy("proof");renderTasks();}
      manual();
    } else match();
  };
  $("new-space").onchange=()=>{$("new-worker").innerHTML=workerOptions($("new-space").value);draft=captureTasks();renderTasks();match();};
  $("new-worker").onchange=()=>{draft=captureTasks();renderTasks();persist();};
  $("new-source").onchange=()=>{sourceFields();persist();};
  $("plan-mode").onchange=persist;
  if(!workflowMode){$("launch-recovery-enabled").onchange=persist;$("launch-recovery-limit").onchange=persist;}
  ["new-url","new-ref"].forEach(id=>$(id).oninput=persist);
  $("launch-tasks").addEventListener("input",manual);
  $("launch-tasks").addEventListener("change",manual);
  $("launch-tasks").onclick=e=>{
    const up=e.target.closest("[data-launch-up]"),remove=e.target.closest("[data-launch-remove]");
    if(!up&&!remove)return;
    manual();
    if(remove)draft.splice(Number(remove.dataset.launchRemove),1);
    if(up){const i=Number(up.dataset.launchUp);[draft[i-1],draft[i]]=[draft[i],draft[i-1]];}
    renderTasks();persist();
  };
  $("launch-add").onclick=()=>{
    manual();
    if(draft.length>=8){toast("最多配置 8 个顺序任务。");return;}
    draft.push({id:"task-"+crypto.randomUUID().slice(0,8),name:"新任务",tool:"claude",model:"auto",effort:"",prompt:""});
    renderTasks();$("launch-tasks").lastElementChild.open=true;persist();
  };
  $("modal-body").querySelectorAll("[data-intent]").forEach(b=>b.onclick=()=>{
    $("new-input").value=b.dataset.intent;$("new-input").dispatchEvent(new Event("input"));
    $("new-input").focus();
  });
  recommender=workflowRecommendation({
    saved:saved.generation,
    read:()=>({input:$("new-input").value,steps:captureTasks(),team_id:$("new-space").value,
      ready:draft.length>0 && ($("new-workflow").value==="custom" || resolution?.input===$("new-input").value.trim())}),
    apply:config=>{
      ++sequence;clearTimeout(timer);draft=config.steps;choice="custom";$("new-workflow").value=choice;resolution=null;
      if(!nameTouched)$("new-name").value=config.name;
      renderTasks();$("match-name").textContent="已采用 KiroCrew 建议："+config.name;
      $("match-reason").textContent="每个 Task 的提示词已更新，可以继续手动调整。";
      $("match-alternatives").replaceChildren();
    },
    persist,
    persistClosed:job=>{
      try {
        const stored=JSON.parse(sessionStorage.getItem(key)||"{}");
        if(stored.generation?.request?.request_id===job.request.request_id){
          stored.generation=job;sessionStorage.setItem(key,JSON.stringify(stored));
        }
      } catch(_) { /* The native generation run remains accessible in Sessions. */ }
    }
  });
  renderTasks();
  if(choice!=="custom")match();
  else persist();
}
