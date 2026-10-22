"use strict";

const LAUNCH_STEPS = [["need", "需求"], ["plan", "编排方式"], ["tasks", "任务与 Agent"], ["confirm", "确认"]];
const PLAN_METHODS = [
  ["template", "参考模板", "按需求自动匹配，或选择模板 / 我的工作流"],
  ["recommend", "KiroCrew 推荐", "由 Kiro Agent 设计工作流，并为每个 Task 匹配 Prompt"],
  ["import", "导入已生成的工作流", "粘贴或上传 Agent 预先生成的工作流，按提交内容运行"],
];

// A wizard over one requirement; drafts belong to a user, space and originating Session.
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
  // New requirements still default to the KiroCrew recommendation; an explicit selection starts from the template view.
  let method = PLAN_METHODS.some(m=>m[0]===saved.method) ? saved.method : explicit && !workflowMode ? "template" : "recommend";
  let imported = explicit ? null : saved.imported || null, step = 0;
  const flows = state.flows.filter(f => f.role !== "viewer");
  const choices = [["auto", "自动匹配 · 按这次需求"], ["custom", "自定义 · 编辑任务列表"]];
  const available = new Set(["auto", "custom", ...flows.map(f=>"workflow:"+f.id), ...Object.keys(templates).map(id=>"template:"+id)]);
  if (!available.has(choice)) { choice="auto"; resolution=null; draft=[]; }
  const chain = steps => steps.map(s=>tools[s.tool]||s.tool).join(" → ");
  const cards = [["auto","按需求自动匹配","根据这次需求的关键词，从模板与我的工作流中选出最合适的一个。",""],
    ...flows.map(f=>["workflow:"+f.id,f.name,`我的 / 共享工作流 · v${f.version}`,chain(f.steps||[])]),
    ...Object.entries(templates).map(([id,t])=>["template:"+id,t.name,t.description||"",chain(t.steps)])];
  const content = `
    <ol class="wizard-steps">${LAUNCH_STEPS.map(([id,label],i)=>
      `<li data-wizard-step="${i}"><button type="button" data-goto="${i}"><span>${i+1}</span>${label}</button></li>`).join("")}</ol>
    <section class="wizard-page" data-page="0">
      <p class="hint">先说明这次要完成什么，再确认源码与执行位置。下一步选择编排方式。</p>
      <label class="intent-label">这次要完成什么？
        <textarea id="new-input" required minlength="3" maxlength="12000" rows="5" placeholder="例如：修复登录页刷新后状态丢失的 Bug，补充回归测试。也可以输入“使用我的代码审查工作流”。">${esc(initial.input ?? saved.input ?? "")}</textarea>
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
      <div class="form-row">
        <label ${workflowMode?"hidden":""}>代码来源<select id="new-source"${workflowMode?"":" required"}>${options([["","请选择目标源码，不能默认使用空项目"],["new","明确创建一个全新项目"],["git","开发已有 Git 仓库"]],workflowMode?"new":sourceChoice)}</select></label>
        <label>执行 Worker<select id="new-worker">${workerOptions(saved.space ?? state.space,initialWorker)}</select></label>
      </div>
      <div class="form-row" id="git-fields" hidden><label>Git URL<input id="new-url" value="${esc(saved.url || "")}" placeholder="https://…/project.git 或 git@…"></label><label>分支 / Tag / Commit（可选）<input id="new-ref" value="${esc(saved.ref || "")}" placeholder="默认分支"></label></div>
    </section>
    <section class="wizard-page" data-page="1" hidden>
      <div class="plan-methods" role="radiogroup" aria-label="编排方式">${PLAN_METHODS.map(([value,title,detail])=>
        `<label class="plan-method"><input type="radio" name="plan-method" value="${value}"${value===method?" checked":""}><strong>${title}</strong><small>${detail}</small></label>`).join("")}</div>
      <div class="plan-panel" data-method="template">
        <div class="match-result" aria-live="polite"><strong id="match-name">等待输入需求</strong><p id="match-reason">输入至少 3 个字符，自动显示工作流与任务。</p><small>即时模板 / 关键词匹配 · 不调用模型</small></div>
        <div id="match-alternatives" class="intent-examples"></div>
        <div class="form-row"><label>工作流 / 模板<select id="new-workflow">${options(choices,choice)}
          <optgroup label="我的与共享的可运行工作流">${options(flows.map(f=>["workflow:"+f.id,`${f.name} · v${f.version}`]),choice)}</optgroup>
          <optgroup label="预定义模板 · ${Object.keys(templates).length} 个">${options(Object.entries(templates).map(([id,t])=>["template:"+id,t.name]),choice)}</optgroup>
        </select></label><label>搜索模板<input id="template-search" type="search" placeholder="例如：Bug、审查、Web、CLI"></label></div>
        <div id="template-cards" class="template-cards">${cards.map(([value,title,detail,agents])=>
          `<button type="button" class="template-card" data-pick="${esc(value)}" data-search="${esc((title+" "+detail+" "+agents).toLowerCase())}"><strong>${esc(title)}</strong><small>${esc(detail)}</small>${agents?`<span>${esc(agents)}</span>`:""}</button>`).join("")}</div>
      </div>
      <div class="plan-panel" data-method="recommend" hidden>
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
          <small>以当前匹配到的任务为基础，由真实 Kiro Agent 重新设计任务与提示词。关闭窗口后可重新打开查看；采用前不会执行。</small>
        </section>
      </div>
      <div class="plan-panel" data-method="import" hidden>
        <label>工作流内容（JSON / YAML / 含一个代码块的 Markdown）
          <textarea id="import-content" rows="9" maxlength="100000" placeholder="粘贴 Claude Code、Codex 或 Kiro 生成的工作流，例如 {&quot;name&quot;: &quot;…&quot;, &quot;steps&quot;: [{&quot;name&quot;: &quot;实现&quot;, &quot;tool&quot;: &quot;claude&quot;, &quot;prompt&quot;: &quot;…&quot;}]}">${esc(saved.importContent || "")}</textarea>
        </label>
        <div class="recommend-actions"><button type="button" id="import-file-pick">选择文件…</button>
          <input type="file" id="import-file" accept=".json,.yaml,.yml,.md,.markdown,.txt" hidden>
          <button type="button" id="import-apply" class="primary">解析并采用</button></div>
        <p id="import-status" role="status"></p><ul id="import-notes" class="import-notes"></ul>
        <small>由代码解析与规范化，不调用模型；兼容控制台导出与旧版 Workflow Studio。Coding 客户端也可经 MCP <span class="mono">octopus_start</span> 的 <span class="mono">document</span> 直接提交。</small>
      </div>
    </section>
    <section class="wizard-page" data-page="2" hidden>
      <div class="section-heading"><h2>本次任务列表 <span id="launch-count"></span></h2><button type="button" id="launch-add">＋ 添加任务</button></div>
      <p id="launch-origin" class="hint"></p>
      <div id="launch-tasks"></div>
    </section>
    <section class="wizard-page" data-page="3" hidden>
      <dl id="launch-summary" class="launch-summary"></dl>
      ${workflowMode?"":recoveryFields(saved.recovery||{enabled:true,max_cycles:2},"launch-recovery")}
      <div class="notice">${workflowMode?"保存为可重复使用的工作流；已有工作流保存新版本，不影响正在运行的 Session。":"提交时保存本次需求和任务快照，不修改工作流库或已有 Session。未选择 Worker 时先登记需求，稍后分配。"}</div>
    </section>`;

  const captureTasks = () => draft.map((s,i)=>({...s,name:$("launch-"+i+"-name").value,
    prompt:$("launch-"+i+"-prompt").value,...readAgent("launch-"+i)}));
  const persist = () => {
    if (!alive || submitted || !$("new-input")) return;
    const value = {input:$("new-input").value,name:$("new-name").value,nameTouched,choice:$("new-workflow").value,
      steps:captureTasks(),resolution,space:$("new-space").value,worker:$("new-worker").value,
      source:$("new-source").value,url:$("new-url").value,ref:$("new-ref").value,
      sourceConfirmed:!!$("new-source").value,method,imported,importContent:$("import-content").value,
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
  const markCards = () => $("template-cards").querySelectorAll("[data-pick]").forEach(card=>
    card.classList.toggle("selected",card.dataset.pick===$("new-workflow").value));
  const manual = () => {
    clearTimeout(timer);++sequence;choice="custom";$("new-workflow").value=choice;
    draft=captureTasks();resolution=null;
    $("launch-tasks").querySelectorAll("summary").forEach((node,i)=>{
      node.querySelector("strong").textContent=draft[i].name;
      node.querySelector("small").textContent=tools[draft[i].tool]+" · "+draft[i].model;
    });
    $("match-name").textContent="自定义工作流 · 已保留你的任务配置";
    $("match-reason").textContent="修改需求不会覆盖这些任务；需要重新匹配时，手动选择“自动匹配”。";
    $("match-alternatives").replaceChildren();markCards();persist();
  };
  const selected = () => {
    const value=$("new-workflow").value;
    if(value==="custom")return {steps:captureTasks()};
    if(value.startsWith("workflow:"))return {workflow_id:value.slice(9)};
    if(value.startsWith("template:"))return {template_id:value.slice(9)};
    return {};
  };
  const match = async () => {
    clearTimeout(timer);markCards();
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
  const showImport = () => {
    $("import-status").textContent=imported?`已采用：${imported.name} · ${imported.tasks} Tasks。可在下一步检查与修改。`:
      "粘贴或选择文件后点击“解析并采用”。";
    $("import-notes").innerHTML=(imported?.notes||[]).map(note=>`<li>${esc(note)}</li>`).join("");
  };
  const importNow = async () => {
    const text=$("import-content").value;
    if(!text.trim()){$("import-status").textContent="请先粘贴工作流内容或选择文件。";return;}
    $("import-status").textContent="正在解析…";$("import-notes").replaceChildren();
    const seq=++sequence;clearTimeout(timer);
    try {
      const result=await api("/workflows/parse","POST",{content:text});
      if(!alive || seq!==sequence)return;
      draft=result.steps;choice="custom";$("new-workflow").value=choice;resolution=null;
      imported={name:result.name,notes:result.notes,tasks:result.steps.length};
      if(!nameTouched && result.name!=="导入的工作流")$("new-name").value=result.name;
      $("match-name").textContent="已导入：" + result.name;
      $("match-reason").textContent="按提交的工作流运行；可在任务列表中继续修改。";
      $("match-alternatives").replaceChildren();
      renderTasks();markCards();showImport();persist();
    } catch(error) {
      if(!alive || seq!==sequence)return;
      $("import-status").textContent="解析失败：" + error.message;
    }
  };
  const origin = () => method==="import"&&imported ? `来自导入的工作流“${imported.name}”。` :
    recommender?.snapshot()?.applied ? "来自已采用的 KiroCrew 建议。" : $("match-name").textContent + "。";
  const summary = () => {
    const tasks=captureTasks(),source=$("new-source").value==="git"?
      `已有 Git 仓库 · ${$("new-url").value.trim()}${$("new-ref").value.trim()?" @ "+$("new-ref").value.trim():""}`:"全新项目";
    const rows=[["需求",$("new-input").value.trim()],[workflowMode?"工作流名称":"Session 名称",$("new-name").value],
      ...(workflowMode?[]:[["代码来源",source]]),["执行空间",$("new-space").selectedOptions[0]?.textContent||""],
      ["执行 Worker",$("new-worker").selectedOptions[0]?.textContent||"稍后分配"],
      ["编排方式",PLAN_METHODS.find(m=>m[0]===method)[1]]];
    $("launch-summary").innerHTML=rows.map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")+
      `<dt>任务（${tasks.length}）</dt><dd><ol>${tasks.map(t=>`<li><strong>${esc(t.name)}</strong> · ${esc(tools[t.tool]||t.tool)} · ${esc(t.model||"auto")}${t.effort?" · Effort "+esc(t.effort):""}</li>`).join("")}</ol></dd>`;
  };
  const pages = () => [...$("modal-body").querySelectorAll(".wizard-page")];
  const fail = (message, at=step) => { if(at!==step)show(at); $("modal-error").textContent=message; return false; };
  // Each page validates only its own visible controls; the form itself skips native validation of hidden pages.
  const valid = index => {
    if(index===0 && !workflowMode && !$("new-source").value)
      return fail("请明确选择新建项目或目标 Git 仓库，避免把空工作区当作已有项目。",0);
    const controls=[...pages()[index].querySelectorAll("input,textarea,select")].filter(node=>!node.closest("[hidden]")&&!node.disabled);
    const invalid=controls.find(node=>!node.checkValidity());
    if(invalid){ if(index!==step)show(index); invalid.closest("details")?.setAttribute("open",""); invalid.reportValidity(); return false; }
    const input=$("new-input").value.trim(),which=$("new-workflow").value;
    if(index===1) {
      if(method==="recommend" && !recommender?.snapshot()?.applied)
        return fail("请先让 KiroCrew 推荐并采用方案；也可以改用参考模板或导入已生成的工作流。",1);
      if(method==="import" && !imported) return fail("请先解析并采用导入的工作流。",1);
      if(!draft.length || (which!=="custom" && (!resolution || resolution.input!==input)))
        return fail("请等待当前需求匹配完成，或选择一个模板 / 工作流。",1);
    }
    if(index===2 && !captureTasks().length) return fail("至少需要一个任务。",2);
    return true;
  };
  const show = index => {
    if(step===2) draft=captureTasks();
    step=index;$("modal-error").textContent="";
    pages().forEach((page,i)=>page.hidden=i!==step);
    $("modal-body").querySelectorAll("[data-wizard-step]").forEach((item,i)=>{
      item.classList.toggle("current",i===step);item.classList.toggle("done",i<step);
    });
    $("wizard-back").disabled=step===0;$("wizard-next").hidden=step===LAUNCH_STEPS.length-1;
    $("modal-submit").hidden=step!==LAUNCH_STEPS.length-1;
    if(step===2){renderTasks();$("launch-origin").textContent=origin();}
    if(step===3)summary();
    $("modal-body").scrollTop=0;persist();
  };
  // "Next" right after editing the requirement waits for that requirement's match instead of failing on a stale one.
  const settle = async () => {
    if($("new-workflow").value!=="custom" && (!resolution || resolution.input!==$("new-input").value.trim())) await match();
  };
  const go = async index => {
    if(index>1) await settle();
    if(!alive) return;
    for(let i=step;i<index;i++) if(!valid(i)) return;
    show(index);
  };
  const showMethod = () => {
    $("modal-body").querySelectorAll(".plan-panel").forEach(panel=>panel.hidden=panel.dataset.method!==method);
    $("modal-body").querySelectorAll(".plan-method").forEach(label=>
      label.classList.toggle("selected",label.querySelector("input").value===method));
  };

  modal(workflowMode?"由需求设计工作流":"从需求开始构建任务",content,async()=>{
    if(step<LAUNCH_STEPS.length-1){ await go(step+1); return; }
    await settle();
    for(let i=0;i<LAUNCH_STEPS.length-1;i++) if(!valid(i)) return;
    const input=$("new-input").value.trim(),which=$("new-workflow").value;
    if(!draft.length || (which!=="custom" && (!resolution || resolution.input!==input)))
      throw Error("请等待当前需求匹配完成，或手动配置任务列表。");
    if(workflowMode) {
      const existing=seed.workflow;
      await api("/workflows"+(existing?.id?"/"+existing.id+"/versions":""),"POST",
        {name:$("new-name").value,steps:captureTasks(),...(existing?.id?{expected_version:existing.version}:{})});
      submitted=true;try{sessionStorage.removeItem(key);}catch(_){}
      closeModal();await refresh();toast("工作流已保存，可从工作流库重复使用。");return;
    }
    const selection=which==="custom"?{steps:captureTasks()}:resolution.selection;
    const source=$("new-source").value==="git"?{type:"git",url:$("new-url").value.trim(),
      ...($("new-ref").value.trim()?{ref:$("new-ref").value.trim()}:{})}:{type:"new"};
    const body={...selection,input,name:$("new-name").value,source,recovery:readRecovery("launch-recovery"),
      ...($("new-space").value?{team_id:$("new-space").value}:{}),...($("new-worker").value?{worker_id:$("new-worker").value}:{})};
    const run=await command("/runs",body);
    submitted=true;try{sessionStorage.removeItem(key);}catch(_){}
    closeModal();await refresh();await selectRun(run.id);toast("需求与工作流已提交，任务快照已保存。");
  },{submit:workflowMode?"保存工作流":"确认任务并提交",kicker:"发起开发 · 向导",note:"草稿仅属于当前用户、开发空间与来源 Session。"});
  const back=document.createElement("button"),next=document.createElement("button");
  back.type=next.type="button";back.id="wizard-back";next.id="wizard-next";next.className="primary";
  back.textContent="上一步";next.textContent="下一步";
  $("modal-submit").before(back,next);$("modal-form").noValidate=true;
  back.onclick=()=>show(Math.max(0,step-1));next.onclick=()=>go(step+1);
  // The cleanup runs before another modal replaces the DOM, including Escape.
  modalCleanup=()=>{persist();recommender?.close();alive=false;++sequence;clearTimeout(timer);
    back.remove();next.remove();$("modal-form").noValidate=false;};
  sourceFields();renderTasks();showMethod();showImport();
  if(seed.steps){$("match-name").textContent="使用当前任务列表的副本";$("match-reason").textContent="编辑此草稿不修改原 Session。";}
  $("modal-body").querySelectorAll("[data-goto]").forEach(button=>button.onclick=()=>{
    const index=Number(button.dataset.goto);index<=step?show(index):go(index);
  });
  $("modal-body").querySelectorAll('input[name="plan-method"]').forEach(radio=>radio.onchange=()=>{
    method=radio.value;$("modal-error").textContent="";showMethod();persist();
  });
  $("template-cards").onclick=e=>{
    const card=e.target.closest("[data-pick]");if(!card)return;
    $("new-workflow").value=card.dataset.pick;$("new-workflow").dispatchEvent(new Event("change"));
  };
  $("template-search").oninput=()=>{
    const terms=$("template-search").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    $("template-cards").querySelectorAll("[data-pick]").forEach(card=>
      card.hidden=!terms.every(term=>card.dataset.search.includes(term)));
  };
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
  if(!workflowMode){$("launch-recovery-enabled").onchange=persist;$("launch-recovery-limit").onchange=persist;}
  ["new-url","new-ref"].forEach(id=>$(id).oninput=persist);
  $("import-content").oninput=()=>{if(imported)$("import-status").textContent="内容已修改；重新“解析并采用”后才会替换当前任务。";persist();};
  $("import-apply").onclick=importNow;
  $("import-file-pick").onclick=()=>$("import-file").click();
  $("import-file").onchange=async()=>{
    const file=$("import-file").files[0];if(!file)return;
    if(file.size>100000){$("import-status").textContent="文件超过 100 KB。";return;}
    $("import-content").value=await file.text();$("import-file").value="";await importNow();
  };
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
    if(draft.length>=12){toast("最多配置 12 个顺序任务。");return;}
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
      renderTasks();markCards();$("match-name").textContent="已采用 KiroCrew 建议："+config.name;
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
  renderTasks();show(0);
  if(choice!=="custom")match();
  else {markCards();persist();}
}
