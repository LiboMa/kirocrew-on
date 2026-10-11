/* UI-only generation stubs; workflow saves use the real ephemeral HTTP/SQLite fixture. */
async page => {
  const browser=await page.context().browser().browserType().launch({headless:true,channel:"chrome"});
  const context=await browser.newContext({viewport:{width:1480,height:1100},reducedMotion:"reduce"});
  const p=await context.newPage(),checks=[],errors=[],requests=[];
  const jobs=new Map();let delayed=false,release;
  const check=(yes,message)=>{if(!yes)throw Error(message);checks.push(message);};
  const step=async(n,page=p)=>{await page.locator(`#modal [data-goto="${n}"]`).click();await page.waitForSelector(`#modal .wizard-page[data-page="${n}"]:not([hidden])`);};
  const method=async(value,page=p)=>page.locator(`#modal label.plan-method:has(input[value="${value}"])`).click();
  p.on("pageerror",e=>errors.push(e.message));
  try {
    await p.route("**/v1/workers",async route=>{
      const me=await (await route.fetch({url:"http://127.0.0.1:8921/v1/me"})).json();
      await route.fulfill({json:[{id:"fixture-generator",name:"测试生成 Worker（无真实执行）",owner_id:me.id,
        enabled:true,capabilities:{isolation:"trusted-local",workflow_design:true,models:{kiro:["auto"],claude:["auto"],codex:["auto"]}}}]});
    });
    await p.route("**/v1/designs",async route=>{
      const body=route.request().postDataJSON();requests.push(body);
      let job=[...jobs.values()].find(j=>j.request_id===body.request_id);
      if(!job){const id="run-"+String(jobs.size+1).padStart(32,"0");job={id,version:1,input:body.input,status:"running",
        request_id:body.request_id,native_task_id:"fixture-not-native",evidence:{actual_backend:"kiro",actual_model:null},
        config:{name:"退款流程 · AI 测试建议",steps:[
          {id:"refund",name:"实现退款幂等",tool:"claude",model:"auto",effort:"",prompt:"针对本次退款需求实现幂等键校验，覆盖重复请求，交付真实测试与源码。"},
          {id:"review",name:"退款安全审查",tool:"codex",model:"auto",effort:"",prompt:"检查退款金额边界与重复请求安全，独立复跑退款测试并交付审查记录。"}]}};jobs.set(id,job);}
      if(delayed)await new Promise(resolve=>release=resolve);
      await route.fulfill({status:201,json:{id:job.id,version:1,status:"preparing"}});
    });
    await p.route("**/v1/designs/*",route=>{
      const id=route.request().url().split("/").pop(),job=jobs.get(id);
      return route.fulfill({json:{...job,...(job.status!=="ready"?{config:undefined}:{})}});
    });
    await p.goto("http://127.0.0.1:8921/");
    await p.getByLabel("用户名",{exact:true}).fill("fixture-owner");
    await p.getByLabel("密码",{exact:true}).fill("Fixture-password-123!");
    await p.getByRole("button",{name:"登录工作台"}).click();
    await p.waitForSelector("#app:not([hidden])");
    await p.locator("#new-run").click();
    await p.locator("#new-input").fill("为订单退款接口增加幂等与金额边界校验");
    await p.locator("#new-source").selectOption("new");
    await step(1);
    check(await p.locator("#recommend-start").isVisible(),"新需求默认进入 KiroCrew 推荐");
    await method("template");
    await p.locator("#new-workflow").selectOption("template:proof");
    await p.waitForFunction(()=>document.querySelector("#launch-0-prompt")?.value.includes("最少依赖"));
    check((await p.locator("#launch-0-prompt").inputValue()).includes("最少依赖"),"选择模板后立即可查看模板预设提示词");
    const first=await p.locator("#launch-0-prompt").inputValue();
    await p.locator("#new-workflow").selectOption("template:bugfix");
    await p.waitForFunction(()=>document.querySelector("#launch-0-prompt").value!==""&&document.querySelector("#launch-tasks").textContent.includes("复现"));
    check(await p.locator("#launch-0-prompt").inputValue()!==first,"切换模板立即更新对应 Agent 提示词");
    await p.waitForFunction(()=>document.querySelector("#launch-0-prompt").value.includes("订单退款"));
    check((await p.locator("#launch-0-prompt").inputValue()).includes("订单退款"),"预设提示词动态带入本次需求");
    await method("recommend");
    await p.locator("#recommend-start").click();
    await p.waitForFunction(()=>document.querySelector("#recommend-native").textContent.includes("fixture-not-native"));
    check(requests.length===1 && requests[0].steps[0].prompt.includes("订单退款"),"生成请求携带需求和当前提示词基线");
    await step(0);
    await p.locator("#new-input").fill("改为设计会议议程");
    await p.waitForFunction(()=>document.querySelector("#launch-0-prompt").value.includes("会议议程"));
    await step(1);
    jobs.values().next().value.status="ready";
    await p.waitForSelector("#recommend-apply:not([hidden])");
    check(await p.locator("#recommend-apply").isDisabled(),"需求变化后旧推荐不能覆盖新草稿");
    await p.locator("#modal-close").click();

    await p.locator("#new-flow").click();
    await p.locator("#recommend-flow").click();
    check((await p.locator("#modal-title").innerText()).includes("设计工作流"),"新增模板入口可以交给 KiroCrew 推荐");
    await p.locator("#new-input").fill("设计订单退款的开发与独立审查流程");
    await step(1);
    check(await p.locator("#recommend-start").isVisible(),"工作流模式下的推荐入口直接进入 KiroCrew 推荐");
    delayed=true;
    await p.locator("#recommend-start").click();
    await p.waitForFunction(()=>document.querySelector("#recommend-status").textContent.includes("提交"));
    while(!release)await p.waitForTimeout(20);
    await p.locator("#modal-close").click();
    release();delayed=false;await p.waitForTimeout(200);
    await p.locator("#new-flow").click();await p.locator("#recommend-flow").click();
    await p.waitForFunction(()=>document.querySelector("#recommend-native").textContent.includes("fixture-not-native"));
    check(requests.length===2,"关闭弹窗期间的迟到回执恢复到原草稿，不重复生成");
    const second=[...jobs.values()][1];second.status="ready";
    await step(1);
    await p.waitForSelector("#recommend-apply:not([hidden]):not([disabled])");
    check((await p.locator("#launch-0-prompt").inputValue()).includes("阅读源码"),"生成完成后不会自动替换预设");
    await p.locator("#recommend-apply").click();
    check((await p.locator("#launch-0-prompt").inputValue()).includes("幂等键校验"),"采用建议后填入 Agent 生成的具体提示词");
    check(await p.locator("#new-name").inputValue()==="退款流程 · AI 测试建议","采用建议自动填入工作流名称");
    await step(2);
    await p.locator("#launch-tasks details").first().locator("summary").click();
    await p.locator("#launch-0-prompt").fill("用户修改：实现退款幂等，并额外验证并发请求。");
    await step(0);
    await p.locator("#new-name").fill("用户修改的退款工作流");
    await step(3);
    await p.locator("#modal-submit").click();
    await p.waitForFunction(()=>!document.querySelector("#modal").open);
    await p.waitForFunction(()=>document.querySelector("#flow-list").textContent.includes("用户修改的退款工作流"));
    const flows=await p.evaluate(async()=>{
      const r=await fetch("/v1/workflows",{headers:{Authorization:"Bearer "+sessionStorage.getItem("octopus.token")}});
      return r.json();
    });
    check(flows.find(f=>f.name==="用户修改的退款工作流").steps[0].prompt.startsWith("用户修改"),"用户编辑后的建议通过真实 API 保存为工作流");
    await p.locator("#new-run").click();
    await p.setViewportSize({width:390,height:844});
    check(await p.locator("#modal").evaluate(n=>n.scrollWidth<=n.clientWidth+1),"推荐控件在窄屏无横向溢出");
    await p.setViewportSize({width:1480,height:1100});
    await p.screenshot({path:"/Users/malibo/MyDev/Kiro-Family-Spaces/experiments/kirocrew-on/state/browser/recommendation-ui.png"});
    check(errors.length===0,"推荐流程无浏览器脚本错误");
    return {ok:true,checks,nativeCalls:0,mode:"generation HTTP stubs + real workflow persistence; not native evidence"};
  } finally {if(release)release();await context.close();await browser.close();}
}
