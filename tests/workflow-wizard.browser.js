/* Playwright run-code; all APIs mocked, no native tasks or user storage touched. */
async (page, {artifactRoot} = {}) => {
  const browser=await page.context().browser().browserType().launch({headless:true,channel:"chrome"});
  const context=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion:"reduce"});
  const p=await context.newPage(),errors=[],calls=[],results=[];
  const assert=(ok,message)=>{if(!ok)throw Error(message);};
  const equal=(a,b,message)=>assert(a===b,`${message}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`);
  const value=id=>p.locator("#"+id).inputValue();
  const capture=async name=>{if(artifactRoot)await p.screenshot({path:`${artifactRoot}/${name}.png`,animations:"disabled"});};
  const step={id:"code",name:"实现",tool:"claude",model:"auto",effort:"",prompt:"按目标完成实现与验证。"};
  const baseline={schema:1,id:"development",revision:2,name:"开发流程",steps:[step,{...step,id:"review",name:"审查",tool:"codex"}]};
  const definitions=[baseline],jobs=new Map();
  const runs=[
    {id:"workflow-fixture-a",created_at:1700000000,workflow:{...baseline,revision:1},input:"Session A 原始需求",session_meta:{}},
    {id:"workflow-fixture-b",created_at:1700000100,workflow:baseline,input:"Session B 原始需求",session_meta:{}}
  ];
  let holdDesign=false,releaseDesign,ready=false,projectRecord=null,stageStatuses=null,retryResult=null;
  p.on("pageerror",e=>errors.push(String(e)));
  // Emulate the older running server: helper scripts have no standalone route.
  await context.route("**/workflow-progress.js",route=>route.fulfill({status:404,body:"not found"}));
  await context.route("**/workflow-editor-store.js",route=>route.fulfill({status:404,body:"not found"}));
  await context.route("**/api/**",async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const body=request.method()==="POST"?request.postDataJSON():null;
    if(body)calls.push({path,body});
    let data;
    if(path==="/api/catalogue")data=Object.fromEntries(["kiro","claude","codex","opencode"].map(tool=>[tool,{models:["auto"],advertised_models:[],observed_models:[],efforts:[""]}]));
    else if(path==="/api/runtime")data={features:["codex_effort_pair_v1"],workbench_features:["stage_retry_v1","session_editor_v3"]};
    else if(path==="/api/library")data={definitions,deleted:{}};
    else if(path==="/api/runs")data=runs;
    else if(path==="/api/config"&&!body)data=definitions.find(d=>d.id===url.searchParams.get("id"));
    else if(path==="/api/config"&&body){data={...body.config,revision:body.expected_revision+1};const i=definitions.findIndex(d=>d.id===data.id);if(i<0)definitions.push(data);else definitions[i]=data;}
    else if(path==="/api/state"){
      const manifest=runs.find(r=>r.id===url.searchParams.get("run"));
      data={manifest,project:projectRecord,retry:retryResult,status:stageStatuses?.includes("failed")?"failed":"ready",at:Date.now()/1000,connected:true,approvals:[],input:{text:manifest.input||""},steps:manifest.workflow.steps.map((s,i)=>({...s,status:stageStatuses?.[i]||"pending"}))};
    }else if(path==="/api/design"&&body){
      const id="design-"+body.request_id.replaceAll("-","");
      data={id,request_id:body.request_id,intent:body.intent,status:"submitted"};jobs.set(id,data);
      if(holdDesign){holdDesign=false;await new Promise(resolve=>releaseDesign=resolve);}
    }else if(path==="/api/design"){
      data={...jobs.get(url.searchParams.get("id")),status:ready?"ready":"running"};
      if(ready)data.config={...baseline,id:"flow-generated-fixture",revision:0,name:"生成的开发工作流"};
    }else if(path==="/api/start"||path==="/api/prepare"){
      const manifest={id:"workflow-fixture-new-"+runs.length,run:"/fixture/runs/new",created_at:Date.now()/1000,development_contract:2,workflow:definitions.find(d=>d.id===body.workflow_id),input:body.input,session_meta:{}};
      runs.unshift(manifest);data={status:path==="/api/start"?"accepted":"prepared",manifest};
    }else if(path==="/api/retry"){
      stageStatuses=["accepted","pending"];retryResult={ok:true,...body,invalidated:[],next_hint:"浏览器夹具续作指令"};data=retryResult;
    }else{errors.push(`Unexpected API: ${request.method()} ${path}`);return route.fulfill({status:500,json:{error:"Unexpected API"}});}
    await route.fulfill({json:data});
  });
  const state=()=>p.evaluate(()=>({scope:editorScope,config,record:editorStore.read(editorScope)}));
  async function openGoal(){await p.locator("#edit-workflow").click();await p.locator('[data-wizard-step="0"]').click();await p.locator("#template-picker").evaluate(n=>n.open=false);}
  async function selectRun(id){if(await p.locator("#workflow-builder").evaluate(n=>n.open))await p.locator("#collapse-builder").click();await p.locator(`[data-run="${id}"]`).click();}
  async function selectTemplate(id){await p.locator("#template-picker").evaluate(n=>n.open=true);await p.locator("#template-search").fill("");await p.locator('[data-category="all"]').click();await p.locator(`[data-template="${id}"]`).click();}
  try{
    await p.goto("http://127.0.0.1:8920/workflow.html?run=workflow-fixture-a");
    await p.waitForFunction(()=>document.querySelector("#connection").textContent.includes("已连接"));
    equal(await p.locator("[data-edit-workflow]").count(),1,"one central editing entry");
    equal(await p.locator("#workflow-intent,.flow-edit,#resume-builder,#focus-edit,#edit-requirement,#configure-workflow").count(),0,"obsolete inputs and editing entries removed");
    equal(await p.evaluate(()=>[...document.scripts].map(s=>new URL(s.src).pathname).join(",")),"/workflow-view.js,/workflow.js","existing static routes suffice");
    await openGoal();equal(await value("requirements"),"Session A 原始需求","seed A input");
    equal((await state()).config.revision,1,"A owns frozen revision 1");
    await p.locator("#requirements").fill("Session A 独立修改需求");await p.locator("#workflow-name").fill("A 的流程草稿");
    await p.locator('[data-wizard-step="1"]').click();await p.locator("#step-name").fill("A 独立任务");
    await selectRun("workflow-fixture-b");await openGoal();
    equal(await value("requirements"),"Session B 原始需求","B doesn't inherit A input");equal((await state()).config.revision,2,"B keeps revision 2");equal((await state()).config.steps[0].name,"实现","B doesn't inherit A tasks");
    await p.locator("#requirements").fill("Session B 独立修改需求");
    await p.reload();await p.waitForFunction(()=>document.querySelector("#connection").textContent.includes("已连接"));await openGoal();
    equal(await value("requirements"),"Session B 独立修改需求","explicit B URL survives reload");
    await selectRun("workflow-fixture-a");await openGoal();equal(await value("requirements"),"Session A 独立修改需求","A restored");equal((await state()).config.steps[0].name,"A 独立任务","A task draft restored");
    results.push("同一工作流的 A/B Session 各自保留需求、任务草稿、冻结版本；切换与刷新不混用");

    holdDesign=true;await p.locator("#generate-workflow").click();
    await p.waitForFunction(()=>document.querySelector("#generation-status").textContent.includes("创建"));
    await selectRun("workflow-fixture-b");await openGoal();releaseDesign();
    await p.waitForFunction(()=>JSON.parse(localStorage.getItem("kirocrew.workflow.editor.v3:run:workflow-fixture-a")).generation.status==="submitted");
    equal(await value("requirements"),"Session B 独立修改需求","late POST cannot replace B input");equal((await state()).record.generation,null,"late POST cannot attach A job to B");
    await selectRun("workflow-fixture-a");await openGoal();await p.evaluate(()=>refreshGeneration());
    await p.locator("#requirements").fill("A 在生成期间修改的新需求");ready=true;await p.evaluate(()=>refreshGeneration());
    assert(await p.locator("#apply-generated").isDisabled(),"stale result cannot be adopted");equal(await value("requirements"),"A 在生成期间修改的新需求","stale result preserves edited text");
    await p.locator("#generate-workflow").click();await p.waitForFunction(()=>document.querySelector("#apply-generated").disabled===false);await p.locator("#apply-generated").click();
    equal(await value("requirements"),"A 在生成期间修改的新需求","current intent uses same input");equal(await value("workflow-name"),"生成的开发工作流","generated name filled");equal((await state()).config.id,"development","adopt edits current workflow identity");
    equal((await state()).config.steps.length,2,"generated tasks adopted");
    assert(!calls.some(c=>["/api/start","/api/prepare"].includes(c.path)),"adoption never starts coding");
    results.push("生成请求返回后只更新发起 Session；变更需求使旧结果不可采用；当前需求自动填入名称和任务，不自动开始");

    await p.locator("#collapse-builder").click();await p.locator("#new-session").click();
    equal(await value("requirements"),"","new Session starts with blank input");equal((await state()).record.generation,null,"new Session has no previous generation");equal((await state()).record.requests.length,0,"new Session has no previous request IDs");
    equal((await state()).config.name,"开发流程","new Session uses saved config, not A unsaved draft");
    await p.locator("#requirements").fill("新 Session 单独的需求");const draftScope=(await state()).scope;
    await p.reload();await p.waitForFunction(()=>document.querySelector("#editor-context").textContent.includes("独立草稿"));await openGoal();
    equal((await state()).scope,draftScope,"draft URL survives reload");equal(await value("requirements"),"新 Session 单独的需求","new draft retains its own input");
    results.push("新 Session 使用已保存流程，需求/生成记录/幂等请求均重新开始；草稿有独立 URL 可刷新恢复");

    await p.locator("#new-workflow").click();await p.locator("#template-picker").evaluate(n=>n.open=false);
    const newDraft=(await state()).scope;
    const templates=await (await context.request.get("http://127.0.0.1:8920/workflow-templates.json")).json();
    for(const t of templates){await selectTemplate(t.id);equal(await value("requirements"),t.input,t.id+" input");equal(JSON.stringify((await state()).config.steps),JSON.stringify(t.steps),t.id+" tasks");equal((await state()).scope,newDraft,"templates remain scoped");}
    await p.locator("#requirements").fill("请保留我手动填写的需求");await selectTemplate("team-kanban");equal(await value("requirements"),"请保留我手动填写的需求","template preserves hand-written goal");
    await p.locator("#template-picker").evaluate(n=>n.open=true);await p.locator("#template-search").fill("CSV");equal(await p.locator(".template-card").count(),1,"cross-category search");await p.locator("#template-search").fill("不存在的模板关键词");equal(await p.locator(".template-card").count(),0,"no matches state");
    await selectTemplate("coding-smoke");await p.locator("#requirements").fill("测试同一需求创建两个独立 Session");
    await p.locator("#wizard-next").click();await p.locator("#wizard-next").click();await p.locator("#start").click();await p.waitForFunction(()=>!document.querySelector("#workflow-builder").open);
    let start=calls.filter(c=>c.path==="/api/start").at(-1);const firstRequest=start.body.request_id,firstRun=(await state()).scope.slice(4);
    equal(start.body.input,"测试同一需求创建两个独立 Session","submit receives scoped goal");equal((await state()).record.generation,null,"new Run doesn't inherit generation job");
    await p.locator("#new-session").click();equal(await value("requirements"),"","second Session blank");await p.locator("#requirements").fill("测试同一需求创建两个独立 Session");await p.locator("#wizard-next").click();await p.locator("#wizard-next").click();await p.locator("#start").click();await p.waitForFunction(()=>!document.querySelector("#workflow-builder").open);
    start=calls.filter(c=>c.path==="/api/start").at(-1);assert(start.body.request_id!==firstRequest,"different Session gets new idempotency key");assert((await state()).scope!=="run:"+firstRun,"two distinct Runs");
    results.push("全部 12 个模板、手写需求保护、搜索通过；两个相同需求的独立 Session 不共用请求 ID");

    await p.evaluate(()=>poll());equal(await p.locator("#project-status-title").textContent(),"等待项目准备","pending source");
    projectRecord={status:"preparing",kind:"git",source:"https://example.com/product.git",directory:"workspace/project"};await p.evaluate(()=>poll());equal(await p.locator("#project-status-title").textContent(),"正在准备项目源码","cloning");
    projectRecord={...projectRecord,status:"failed",error:"指定 ref 不存在 <script>alert(1)</script>"};await p.evaluate(()=>poll());equal(await p.locator("#project-status-note").textContent(),projectRecord.error,"error stays text");
    projectRecord={...projectRecord,status:"ready",error:undefined,commit:"a".repeat(40),branch:"octopus/run"};stageStatuses=["accepted","failed"];await p.evaluate(async()=>{await poll();selectTask(config.steps[1].id);});
    equal(await p.locator("#overall-percent").textContent(),"50%","progress based on accepted stages");assert(await p.locator('.session-cycle[aria-label*="50%"]').count()>0,"shared progress ring");
    await p.locator("#focus-retry").click();await p.locator("#copy-retry").waitFor();equal(await p.locator("#project-status-title").textContent(),"项目源码已就绪","retry retains source");await capture("session-scoped-workbench");
    results.push("源码准备状态、错误转义、真实基线展示、阶段进度与重试请求保留回归覆盖");

    await p.locator("#new-task").click();const deletedScope=(await state()).scope;await p.locator("#template-picker").evaluate(n=>n.open=false);await p.locator("#delete-workflow").click();await p.evaluate(()=>Promise.all([refreshSessions(),refreshRuntime()]));assert(await p.locator("#workflow-delete-panel").isVisible(),"delete panel doesn't flash away on poll");await p.locator("#workflow-delete-submit").click();
    assert(await p.evaluate(scope=>!editorStore.read(scope),deletedScope),"draft is removed without being recreated");
    await selectRun("workflow-fixture-b");await openGoal();equal(await value("requirements"),"Session B 独立修改需求","deleting new draft does not affect B");
    await p.setViewportSize({width:390,height:844});await capture("unified-editor-mobile");assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"no mobile page overflow");assert(await p.locator("#workflow-builder").evaluate(n=>n.scrollWidth<=n.clientWidth+1),"no mobile editor overflow");
    equal(errors.length,0,"no browser errors");results.push("删除确认不受轮询影响；不清理其他 Session 草稿；390px 窄屏和旧静态路由兼容通过");
    return {ok:true,results,apiWrites:calls.map(c=>c.path),nativeCalls:0,templates:templates.length};
  }catch(e){await capture("failure");throw Error(e.message+"\nBrowser errors: "+JSON.stringify(errors));}
  finally{await browser.close();}
}
