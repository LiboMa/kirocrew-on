/* Real HTTP/SQLite fixture. Never submits to a native Worker. */
async page => {
  const browser=await page.context().browser().browserType().launch({headless:true,channel:"chrome"});
  const context=await browser.newContext({viewport:{width:1480,height:1050},reducedMotion:"reduce"});
  const p=await context.newPage(),checks=[],errors=[];
  p.on("pageerror",e=>errors.push(e.message));
  const check=(yes,message)=>{if(!yes)throw Error(message);checks.push(message);};
  const step=async(n,page=p)=>{await page.locator(`#modal [data-goto="${n}"]`).click();await page.waitForSelector(`#modal .wizard-page[data-page="${n}"]:not([hidden])`);};
  const method=async(value,page=p)=>page.locator(`#modal label.plan-method:has(input[value="${value}"])`).click();
  const status=text=>p.waitForFunction(value=>document.querySelector("#import-status")?.textContent.includes(value),text);
  try {
    await p.goto("http://127.0.0.1:8921/classic/");
    await p.getByLabel("用户名",{exact:true}).fill("fixture-owner");
    await p.getByLabel("密码",{exact:true}).fill("Fixture-password-123!");
    await p.getByRole("button",{name:"登录工作台"}).click();
    await p.waitForSelector("#app:not([hidden])");
    await p.locator("#new-run").click();
    check(await p.locator("#wizard-back").isDisabled()&&await p.locator("#modal-submit").isHidden(),"第一步只有“下一步”，提交在最后一步");
    await p.locator("#new-input").fill("修复登录失败的 Bug，并补充回归测试。");
    await p.locator("#new-source").selectOption("new");
    await step(1);
    check(await p.locator(".plan-method").count()===3,"编排方式提供参考模板、KiroCrew 推荐与导入已生成的工作流");

    await method("template");
    await p.locator("#template-search").fill("cli");
    const visible=await p.locator(".template-card:not([hidden])").allInnerTexts();
    check(visible.length>0&&visible.every(text=>text.toLowerCase().includes("cli")),"模板搜索按名称、说明与 Agent 过滤");
    await p.locator("#template-search").fill("");
    await p.locator('.template-card[data-pick="template:proof"]').click();
    await p.waitForFunction(()=>document.querySelector("#match-name").textContent.includes("最小实现"));
    check(await p.locator("#new-workflow").inputValue()==="template:proof"&&
      await p.locator('.template-card[data-pick="template:proof"]').evaluate(n=>n.classList.contains("selected")),"点击模板卡片即选用该模板");

    await method("import");
    await p.locator("#wizard-next").click();
    await p.waitForFunction(()=>document.querySelector("#modal-error").textContent.includes("导入"));
    check(await p.locator('#modal .wizard-page[data-page="1"]').isVisible(),"选择导入但尚未采用时不能继续");
    await p.locator("#import-content").fill("```json\n[{\"name\":\"x\",\"tool\":\"gpt\",\"prompt\":\"p\"}]\n```");
    await p.locator("#import-apply").click();
    await status("解析失败");
    check((await p.locator("#import-status").innerText()).includes("Coding 工具"),"无法识别的工具给出中文原因，不替换当前任务");
    const generated={name:"登录 Bug 修复 · Claude Code 生成",steps:[
      {title:"定位问题",agent:"Kiro CLI",description:"复现登录失败并定位根因"},
      {name:"修复与测试",tool:"claude-code",instructions:"修复并补充回归测试",depends_on:["定位问题"]},
      {id:"review",name:"独立审查",tool:"codex",effort:"high",prompt:"独立审查修复与测试"}]};
    await p.locator("#import-content").fill("以下是 Claude Code 生成的工作流：\n\n```json\n"+JSON.stringify(generated,null,2)+"\n```\n");
    await p.locator("#import-apply").click();
    await status("已采用");
    check((await p.locator("#import-notes").innerText()).includes("depends_on"),"规范化说明逐条可见");
    check(await p.locator("#new-name").inputValue()===generated.name,"未手动命名时采用导入工作流的名称");

    await step(2);
    check(await p.locator("#launch-tasks details").count()===3,"导入的三个任务进入任务与 Agent 页");
    check(await p.locator("#launch-0-tool").inputValue()==="kiro"&&await p.locator("#launch-1-tool").inputValue()==="claude",
      "工具别名由代码规范化为可运行的 Agent");
    check((await p.locator("#launch-origin").innerText()).includes("导入"),"任务页说明任务来源");
    await p.locator("#launch-tasks details").nth(1).locator("summary").click();
    await p.locator("#launch-1-prompt").fill("修复并补充回归测试；额外覆盖会话过期场景。");
    await step(1);await step(2);
    check((await p.locator("#launch-1-prompt").inputValue()).includes("会话过期"),"返回上一步再回来不丢失已编辑的任务");

    await step(3);
    const summary=await p.locator("#launch-summary").innerText();
    check(summary.includes("导入已生成的工作流")&&summary.includes("独立审查 · Codex · auto · Effort high"),"确认页显示编排方式与每个任务的 Agent");
    await p.locator("#modal-submit").click();
    await p.waitForFunction(()=>!document.querySelector("#modal").open);
    await p.waitForFunction(name=>document.querySelector("#run-title").textContent===name,generated.name);
    const created=await p.evaluate(async()=>{
      const id=new URL(location.href).searchParams.get("run");
      return (await fetch("/v1/runs/"+id,{headers:{Authorization:"Bearer "+sessionStorage.getItem("octopus.token")}})).json();
    });
    check(created.tasks.map(t=>t.spec.tool).join(",")==="kiro,claude,codex"&&created.tasks[1].spec.prompt.includes("会话过期"),
      "按导入并编辑后的任务创建 Session（真实 API）");
    await p.locator("#new-run").click();
    await p.setViewportSize({width:390,height:844});
    check(await p.locator("#modal").evaluate(n=>n.scrollWidth<=n.clientWidth+1),"窄屏向导无横向溢出");
    await p.setViewportSize({width:1480,height:1050});
    check(errors.length===0,"向导无浏览器脚本异常");
    return {ok:true,checks,nativeCalls:0,mode:"ephemeral HTTP/SQLite"};
  } finally {await context.close();await browser.close();}
}
