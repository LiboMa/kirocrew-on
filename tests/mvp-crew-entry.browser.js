/* UI navigation fixtures only. Native auth is verified separately, without recording its token. */
async page => {
  const browser=await page.context().browser().browserType().launch({headless:true,channel:"chrome"});
  const context=await browser.newContext({viewport:{width:1480,height:1050},reducedMotion:"reduce"});
  const p=await context.newPage(),checks=[],errors=[],calls=[],bindings=new Map();
  let delayed=false,release,fail=false;
  const check=(ok,message)=>{if(!ok)throw Error(message);checks.push(message);};
  const step=async(n,page=p)=>{await page.locator(`#modal [data-goto="${n}"]`).click();await page.waitForSelector(`#modal .wizard-page[data-page="${n}"]:not([hidden])`);};
  const method=async(value,page=p)=>page.locator(`#modal label.plan-method:has(input[value="${value}"])`).click();
  p.on("pageerror",e=>errors.push(e.message));
  const origin="http://localhost:5678";
  try {
    await context.route(origin+"/chat?**",route=>route.fulfill({contentType:"text/html",
      body:"<!doctype html><title>Fixture native chat</title><h1>Fixture native chat · no real Gateway</h1>"}));
    await p.route("**/v1/workers",async route=>{
      const me=await (await route.fetch({url:"http://127.0.0.1:8921/v1/me"})).json();
      await route.fulfill({json:[{id:"fixture-worker",name:"Fixture only",owner_id:me.id,enabled:true,
        capabilities:{isolation:"trusted-local",models:{kiro:["auto"],claude:["auto"],codex:["auto"]}}}]});
    });
    await p.route("**/v1/runs/*",async route=>{
      const response=await route.fetch(),data=await response.json();
      await route.fulfill({response,json:{...data,...bindings.get(data.id)}});
    });
    await p.route("**/v1/runs/*/crew-entry",async route=>{
      const rid=new URL(route.request().url()).pathname.split("/")[3];
      calls.push({rid,body:route.request().postDataJSON(),method:route.request().method()});
      const parent=bindings.get(rid)?.native_parent;
      if(delayed)await new Promise(resolve=>release=resolve);
      if(fail)return route.fulfill({status:503,json:{code:"worker_unavailable",error:"测试：Worker 暂时不可用"}});
      await route.fulfill({json:{native_parent:parent,origin,
        url:origin+"/chat?"+new URLSearchParams({sid:parent,token:"fixture-browser-link"})}});
    });
    await p.goto("http://127.0.0.1:8921/");
    await p.getByLabel("用户名",{exact:true}).fill("fixture-owner");
    await p.getByLabel("密码",{exact:true}).fill("Fixture-password-123!");
    await p.getByRole("button",{name:"登录工作台"}).click();
    await p.waitForSelector("#app:not([hidden])");
    const ids=await p.locator("[data-run]").evaluateAll(nodes=>nodes.map(n=>n.dataset.run));
    const [a,b]=ids;
    bindings.set(a,{worker_id:"fixture-worker",native_parent:null});
    bindings.set(b,{worker_id:"fixture-worker",native_parent:"existing-native-b"});
    const select=async id=>{
      await p.locator(`[data-run="${id}"]`).click();
      await p.waitForFunction(id=>document.querySelector("#run-id").textContent.startsWith(id),id);
      await p.waitForTimeout(40);
    };
    await select(a);
    check(await p.locator("#run-open-crew").isDisabled(),"未建立原生 Session 时禁用入口并显示原因");
    check((await p.locator("#crew-entry-hint").innerText()).includes("尚未建立"),"等待 Worker 的说明可见");
    bindings.set(a,{worker_id:"fixture-worker",native_parent:"existing-native-a"});
    await select(b);await select(a);
    delayed=true;
    const firstPopup=context.waitForEvent("page");
    await p.locator("#run-open-crew").click();
    const first=await firstPopup;
    while(!release)await p.waitForTimeout(20);
    check(await p.locator("#run-open-crew").isDisabled(),"认证过程中阻止重复打开");
    await select(b);release();delayed=false;
    await first.waitForURL(url=>url.origin===origin);
    check(new URL(first.url()).searchParams.get("sid")==="existing-native-a","切换到 B 时，迟到的 A 跳转仍对应 A 的原生会话");
    check((await p.locator("#run-id").innerText()).startsWith(b),"打开原生会话不改变主工作台所选 Session");
    check(await first.evaluate(()=>window.opener===null),"新标签页与工作台解除 opener 关联");
    await first.close();
    const secondPopup=context.waitForEvent("page");
    await p.locator("#run-open-crew").click();
    const second=await secondPopup;await second.waitForURL(url=>url.origin===origin);
    check(new URL(second.url()).searchParams.get("sid")==="existing-native-b","切换 Session 后点击打开正确的 B 会话");
    check(calls.every(c=>c.method==="POST"&&Object.keys(c.body).length===0),"入口只提交 Run ID，不由前端指定任意原生 Session 或 URL");
    await second.close();
    await p.waitForSelector("#run-open-crew:not([disabled])");

    fail=true;
    const errorPopup=context.waitForEvent("page");
    await p.locator("#run-open-crew").click();
    const errorTab=await errorPopup;
    await errorTab.waitForSelector("#crew-entry-status");
    await errorTab.waitForFunction(()=>document.querySelector("#crew-entry-status").textContent.includes("暂时不可用"));
    check(errorTab.url().endsWith("/crew-entry.html"),"Worker 故障保留可读错误页，不跳到错误地址");
    check((await errorTab.locator("#crew-entry-status").innerText()).includes("Session 保持不变"),"连接失败提供原地重试说明");
    await errorTab.close();fail=false;
    await p.waitForSelector("#run-open-crew:not([disabled])");
    const before=calls.length;
    await p.evaluate(()=>{window.fixtureOpen=window.open;window.open=()=>null;});
    await p.locator("#run-open-crew").click();
    check(calls.length===before,"弹窗被拦截时不申请无用的原生登录 Token");
    await p.evaluate(()=>{window.open=window.fixtureOpen;delete window.fixtureOpen;});
    bindings.set(b,{...bindings.get(b),lifecycle:"closed"});
    await select(a);await select(b);
    check(await p.locator("#run-open-crew").isDisabled(),"关闭的 Session 要先重新打开，入口不隐式恢复或重建会话");
    bindings.set(b,{...bindings.get(b),lifecycle:"open"});
    await select(a);await select(b);

    const design="run-recommend-fixture",parent="native-recommendation-fixture";
    bindings.set(design,{native_parent:parent});
    const job={id:design,native_parent:parent,status:"ready",version:1,input:"设计一条验证工作流",
      config:{name:"Fixture 推荐",steps:[{id:"code",name:"验证",tool:"kiro",model:"auto",effort:"",prompt:"测试推荐入口"}]}};
    await p.route("**/v1/designs",route=>route.fulfill({status:201,json:job}));
    await p.route("**/v1/designs/*",route=>route.fulfill({json:job}));
    await p.locator("#new-run").click();
    await p.locator("#new-input").fill(job.input);
    await p.locator("#new-source").selectOption("new");
    await step(1);await method("template");
    await p.locator("#new-workflow").selectOption("template:proof");
    await p.waitForFunction(()=>document.querySelector("#launch-0-prompt")?.value.includes("验证工作流"));
    await method("recommend");
    await p.locator("#recommend-start").click();
    await p.waitForSelector("#recommend-open:not([hidden])");
    const designPopup=context.waitForEvent("page");
    await p.locator("#recommend-open").click();
    const designTab=await designPopup;await designTab.waitForURL(url=>url.origin===origin);
    check(new URL(designTab.url()).searchParams.get("sid")===parent,"推荐窗口打开自己的生成 Session，不混用开发 Session");
    await designTab.close();await p.locator("#modal-close").click();
    const stored=await p.evaluate(()=>JSON.stringify({...sessionStorage,...localStorage}));
    check(!stored.includes("fixture-browser-link"),"原生登录链接不写入工作台 SessionStorage 或 LocalStorage");
    await p.route("**/crew.js",async route=>{await new Promise(resolve=>setTimeout(resolve,500));await route.continue();});
    await p.reload();
    await p.waitForSelector("#run-open-crew:not([disabled])");
    check((await p.locator("#run-id").innerText()).startsWith(b),"入口脚本加载较慢时，已登录刷新仍恢复正确 Session");
    await p.setViewportSize({width:390,height:844});
    check(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"手机宽度下新增入口不造成横向溢出");
    check(errors.length===0,"工作台无脚本异常");
    return {passed:checks.length,checks};
  } finally {await browser.close();}
}
