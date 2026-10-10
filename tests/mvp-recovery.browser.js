/* UI recovery responses are substitutes; source/manual submission uses the real fixture API. */
async page => {
  const browser=await page.context().browser().browserType().launch({headless:true,channel:"chrome"});
  const context=await browser.newContext({viewport:{width:1480,height:1050},reducedMotion:"reduce"});
  const p=await context.newPage(),checks=[],errors=[],requests=[];
  const check=(yes,message)=>{if(!yes)throw Error(message);checks.push(message);};
  let status="blocked",selected,closing=false;
  p.on("pageerror",e=>errors.push(e.message));
  try {
    await p.route(/\/v1\/runs\/run-[a-f0-9]+$/,async route=>{
      try {
      const r=await route.fetch(),run=await r.json();
      if(!selected||run.id===selected){
        run.status=status;run.native_parent="octopus-"+run.id;run.worker_id="fixture-only";
        run.error="原生任务已停止";
        run.attention={title:status==="diagnosing"?"Coordinator 正在诊断":"规划任务已停止",
          detail:"原生任务已结束但未提供停止原因；无法判断是谁停止。后续编码和审查尚未开始。",
          task_name:run.tasks[0].spec.name,action:"确认项目来源后配置并原地重跑，或请求协调者诊断。"};
        run.recovery={policy:{enabled:true,max_cycles:2},used:status==="diagnosing"?1:0,
          latest:status==="diagnosing"?{number:1,status:"running"}:null,history:status==="diagnosing"?[
            {number:1,status:"running",failed_task_id:run.tasks[0].id,native_id:"fixture-diagnosis"}]:[]};
      }
      await route.fulfill({json:run});
      } catch(error) {if(!closing)throw error;}
    });
    await p.route("**/v1/runs/*/recover",async route=>{
      requests.push({url:route.request().url(),body:route.request().postDataJSON()});
      status="diagnosing";
      await route.fulfill({status:200,json:{status:"accepted",id:selected,version:2}});
    });
    await p.goto("http://127.0.0.1:8921/");
    await p.getByLabel("用户名",{exact:true}).fill("fixture-owner");
    await p.getByLabel("密码",{exact:true}).fill("Fixture-password-123!");
    await p.getByRole("button",{name:"登录工作台"}).click();
    await p.waitForSelector("#session-list [data-run]");
    selected=await p.locator("#session-list [data-run]").first().getAttribute("data-run");
    await p.locator(`[data-run="${selected}"]`).click();
    await p.waitForSelector("#run-attention:not([hidden])");
    check((await p.locator("#run-status").innerText()).includes("规划任务已停止"),"状态直接说明停止的任务，不再笼统写需要处理");
    check((await p.locator("#attention-detail").innerText()).includes("无法判断是谁停止"),"没有原生证据时不猜测停止原因");
    check((await p.locator("#attention-action").innerText()).includes("确认项目来源"),"阻塞卡明确给出下一步");
    check(await p.locator("#legacy-entry").getAttribute("href")==="http://127.0.0.1:8917/","新版保留独立旧版入口");
    await p.locator("#run-continue").click();
    check(await p.locator("#continue-recovery-enabled").isChecked(),"原地续作可以配置自动纠错");
    await p.locator("#continue-recovery-limit").selectOption("3");
    check(await p.locator("#continue-tool").count()===1,"续作保留工具、模型、Effort 配置");
    await p.locator("#modal-close").click();
    await p.locator("#run-recover").click();
    await p.waitForFunction(()=>document.querySelector("#run-status").textContent.includes("协调者"));
    check(requests.length===1&&requests[0].url.endsWith("/"+selected+"/recover"),"诊断操作提交到当前 Session，不建立新运行");
    check(requests[0].body.expected_version===1&&requests[0].body.request_id,"诊断请求携带版本与幂等 ID");
    check(await p.locator("#run-recover").isDisabled(),"诊断期间不能重复安排诊断");
    check((await p.locator("#recovery-records").textContent()).includes("fixture-diagnosis"),"可以查看原生诊断 Task 与纠错记录");
    await p.setViewportSize({width:390,height:844});
    check(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),"窄屏旧入口与状态卡不造成页面横向溢出");
    await p.setViewportSize({width:1480,height:1050});
    await p.screenshot({path:"/Users/malibo/MyDev/Kiro-Family-Spaces/kirocrew-octopus/evidence/mvp-1.0.0/recovery-ui.png"});

    await p.locator("#new-run").click();
    check(await p.locator("#plan-mode").inputValue()==="recommend","新需求默认引导 Agent 推荐、审阅和采用");
    check(await p.locator("#new-source").inputValue()==="","不默认使用空项目，必须确认代码来源");
    await p.locator("#new-input").fill("快速验证一个加法小函数");
    await p.waitForSelector("#launch-0-prompt",{state:"attached"});
    await p.locator("#new-source").selectOption("new");
    await p.locator("#modal-submit").click();
    await p.waitForFunction(()=>document.querySelector("#modal-error").textContent.includes("采用"));
    check(await p.locator("#modal").evaluate(n=>n.open),"Agent 编排必须采用后才能提交，不能静默退回预设");
    await p.locator("#plan-mode").selectOption("manual");
    await p.locator("#new-source").selectOption("");
    await p.locator("#new-input").fill("修复 https://github.com/example/project.git 的登录 Bug");
    await p.waitForFunction(()=>document.querySelector("#new-source").value==="git");
    check(await p.locator("#new-url").inputValue()==="https://github.com/example/project.git","需求中的明确 Git URL 自动填入可编辑的代码来源");
    await p.locator("#new-source").selectOption("new");
    await p.locator("#launch-recovery-limit").selectOption("3");
    await p.locator("#new-name").fill("手动编排与源码确认测试");
    await p.waitForFunction(()=>document.querySelector("#match-name").textContent.includes("Bug 修复"));
    await p.locator("#modal-submit").click();
    await p.waitForFunction(()=>!document.querySelector("#modal").open);
    await p.waitForFunction(()=>document.querySelector("#run-title").textContent==="手动编排与源码确认测试");
    const created=await p.evaluate(async()=>{
      const id=new URL(location.href).searchParams.get("run");
      return (await fetch("/v1/runs/"+id,{headers:{Authorization:"Bearer "+sessionStorage.getItem("octopus.token")}})).json();
    });
    check(created.source.type==="new"&&created.recovery.policy.max_cycles===3,"用户明确选择的来源与纠错策略通过真实 API 保存");
    check(errors.length===0,"完整交互无浏览器脚本错误");
    return {ok:true,checks,nativeCalls:0,mode:"UI diagnosis substitutes + real fixture submission"};
  } catch(error) {
    console.error("Recovery browser checks completed:",checks,
      "modal:",await p.locator("#modal-error").textContent(),
      "run:",await p.locator("#run-title").textContent());
    throw error;
  } finally {closing=true;await context.close();await browser.close();}
}
