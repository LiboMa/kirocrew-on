/* React console against the real HTTP/SQLite fixture (tests/mvp_browser_fixture.py on 8921). Never reaches a native Worker.
   Build first: npm --prefix web run build. */
async page => {
  const browser=await page.context().browser().browserType().launch({headless:true,channel:"chrome"});
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:"reduce"});
  const p=await context.newPage(),checks=[],errors=[],violations=[];
  p.on("pageerror",e=>errors.push(e.message));
  p.on("console",m=>{if(m.type()==="error"&&/Content Security Policy|Refused to/.test(m.text()))violations.push(m.text());});
  const check=(yes,message)=>{if(!yes)throw Error(message);checks.push(message);};
  const dialog=()=>p.getByRole("dialog");
  const next=()=>dialog().getByRole("button",{name:"下一步"}).click();
  // D67: the session cookie signs the console in; a token is kept in the tab only when the browser refuses the cookie.
  const api=async path=>p.evaluate(async path=>{const t=sessionStorage.getItem("octopus.token");
    return (await fetch("/v1"+path,{headers:t?{Authorization:"Bearer "+t}:{}})).json();},path);
  try {
    await p.goto("http://127.0.0.1:8921/");
    check(await p.getByRole("heading",{name:"进入开发空间"}).isVisible(),"/ 提供 React 控制台登录页");
    await p.getByLabel("用户名").fill("fixture-owner");
    await p.getByLabel("密码").fill("Fixture-password-123!");
    await p.getByRole("button",{name:/登录工作台/}).click();
    await p.waitForSelector("#session-list [data-run]");
    check(await p.locator("#session-list [data-run]").count()===2,"登录后列出当前空间的 Sessions");
    const cookie=(await context.cookies()).find(c=>c.name==="__Host-octopus_session");
    check(cookie?.httpOnly&&cookie.secure&&cookie.sameSite==="Strict"&&cookie.expires-Date.now()/1000>29*86400,
      "登录写入 HttpOnly 会话 Cookie，默认保持 30 天");
    check(await p.evaluate(()=>sessionStorage.getItem("octopus.token"))===null,"Cookie 生效时页面不保存 Token");
    const tab=await context.newPage();
    await tab.goto("http://127.0.0.1:8921/");
    await tab.waitForSelector("#session-list [data-run]");
    check(true,"新标签页凭会话 Cookie 保持登录");
    await tab.close();

    // Owner 2026-10-22: pin from a row's menu (it shows on top), then unpin; the admin page lists the environments
    await p.getByRole("button",{name:/^会话操作：/}).first().click();
    await p.getByRole("menuitem",{name:"置顶"}).click();
    await p.getByRole("region",{name:"置顶"}).waitFor();
    check(await p.getByRole("region",{name:"置顶"}).locator("[data-run]").count()===1,"会话列表行菜单置顶，置顶分组在列表顶部");
    await p.getByRole("region",{name:"置顶"}).getByRole("button",{name:/^会话操作：/}).click();
    await p.getByRole("menuitem",{name:"取消置顶"}).click();
    await p.getByRole("region",{name:"置顶"}).waitFor({state:"detached"});
    check(true,"取消置顶后置顶分组消失");
    // Owner 2026-10-24 (青岩): the navigation on the left groups 工作台, 管理 and 我的设置; its entries are links
    await p.getByRole("navigation",{name:"管理"}).getByRole("link",{name:"执行环境"}).click();
    await p.getByRole("region",{name:"执行环境列表"}).waitFor();
    check(new URL(p.url()).searchParams.get("view")==="admin","管理页面可打开执行环境，地址栏记下所在页面");
    check(await p.getByRole("navigation",{name:"管理"}).getByRole("link",{name:"执行环境"}).getAttribute("aria-current")==="page",
      "导航标出所在分区");
    await p.getByRole("navigation",{name:"工作台"}).getByRole("link",{name:"会话"}).click();
    await p.waitForSelector("#session-list [data-run]");
    await p.getByRole("button",{name:"深色模式"}).click();
    check(await p.evaluate(()=>document.documentElement.classList.contains("dark")&&localStorage.getItem("octopus.theme")==="dark"),
      "深色模式切换并记在浏览器里");
    await p.reload();
    await p.waitForSelector("#session-list [data-run]");
    check(await p.evaluate(()=>document.documentElement.classList.contains("dark")),"刷新后保持深色模式");
    await p.getByRole("button",{name:"浅色模式"}).click();

    // D56: the list is filtered by the server, 300 ms after typing stops
    const listed=async n=>p.waitForFunction(n=>document.querySelectorAll("#session-list [data-run]").length===n,n,{timeout:5000}).then(()=>true,()=>false);
    const firstId=await p.locator("#session-list [data-run]").first().getAttribute("data-run");
    await p.getByLabel("搜索会话").fill(firstId.slice(-10));
    check(await listed(1),"会话搜索按 ID 片段过滤");
    await p.getByLabel("搜索会话").fill("独立需求");
    check(await listed(2),"会话搜索按需求内容过滤");
    await p.getByLabel("搜索会话").fill("");
    await p.getByRole("button",{name:"已完成"}).click();
    check(await listed(0),"状态筛选：没有已完成的会话");
    await p.getByRole("button",{name:"全部",exact:true}).click();   // not the administrator's 「全部会话」
    await listed(2);

    await p.locator("#session-list [data-run]").first().click();
    await p.waitForSelector("#run-title");
    check(new URL(p.url()).searchParams.get("run")?.startsWith("run-"),"选中会话写入地址栏，可直接分享与刷新");
    check((await p.locator("#attention-detail").innerText()).includes("模拟交接缺失"),"会话详情说明暂停原因");
    await p.waitForFunction(()=>document.querySelector("#stream-status")?.textContent.includes("已连接"));
    check(true,"实时状态流已连接");
    check(await p.getByRole("button",{name:"续作",exact:true}).isEnabled(),"已暂停的会话可以续作");
    await p.reload();
    await p.waitForSelector("#run-title");
    check(true,"刷新后恢复登录与所选会话");

    await p.goto("http://127.0.0.1:8921/classic/");
    await p.waitForSelector("#app:not([hidden])");
    check(true,"经典控制台在 /classic/ 共用同一登录");
    await p.goto("http://127.0.0.1:8921/");
    await p.waitForSelector("#session-list [data-run]");

    await p.getByRole("button",{name:/新建开发/}).click();
    await dialog().getByLabel("这次要完成什么？").fill("修复登录失败的 Bug，并补充回归测试。");
    await dialog().getByLabel("代码来源").selectOption("new");
    await next();
    await dialog().getByRole("radio",{name:/导入已生成的工作流/}).check({force:true});
    const generated={name:"Claude Code 预生成 · 登录修复",steps:[{title:"定位问题",agent:"Kiro CLI",description:"复现并定位"},
      {name:"修复与测试",tool:"claude-code",instructions:"修复并补充回归测试",depends_on:["定位问题"]},{name:"独立审查",tool:"codex",prompt:"独立审查"}]};
    await dialog().getByLabel(/工作流内容/).fill("```json\n"+JSON.stringify(generated)+"\n```");
    await dialog().getByRole("button",{name:"解析并采用"}).click();
    await dialog().getByText(/已采用：Claude Code 预生成/).waitFor();
    check((await dialog().locator("#import-notes").innerText()).includes("depends_on"),"导入的规范化说明可见");
    await next();await next();
    await dialog().getByRole("button",{name:"确认任务并提交"}).click();
    await p.waitForFunction(name=>document.querySelector("#run-title")?.textContent===name,generated.name);
    const imported=await api("/runs/"+new URL(p.url()).searchParams.get("run"));
    check(imported.tasks.map(t=>t.spec.tool).join(",")==="kiro,claude,codex","导入的工作流按提交内容创建 Session（真实 API）");
    check(/没有可用 Worker|等待选择 Worker/.test(await p.locator("#worker-notice").innerText()),"无 Worker 时只登记需求，提示分配");   // the server's hint (81394f9)

    const importedId=new URL(p.url()).searchParams.get("run");
    await p.getByRole("button",{name:/新建开发/}).click();
    await dialog().getByLabel("这次要完成什么？").fill("快速验证一个小函数");
    await dialog().getByLabel("代码来源").selectOption("new");
    await next();
    await dialog().getByRole("radio",{name:/参考模板/}).check({force:true});
    await dialog().locator('[data-pick="template:proof"]').click();
    await dialog().getByText("已指定：最小实现与审查").waitFor();
    await next();await next();
    await dialog().getByRole("button",{name:"确认任务并提交"}).click();
    await p.waitForFunction(()=>!document.querySelector("#launch-wizard"));
    await p.waitForFunction(previous=>{const id=new URLSearchParams(location.search).get("run");return id&&id!==previous;},importedId);
    const fromTemplate=await api("/runs/"+new URL(p.url()).searchParams.get("run"));
    check(fromTemplate.selection?.template_id==="proof"||fromTemplate.tasks.length===2,"参考模板按所选模板创建 Session");

    await p.getByRole("tab",{name:/工作流/}).click();
    await p.locator("#flow-list [data-flow]").first().click();
    await dialog().getByLabel("工作流名称").fill("React 编辑后的流程");
    await dialog().getByRole("button",{name:"保存"}).click();
    await p.waitForFunction(()=>document.querySelector("#flow-list")?.textContent.includes("v2"));
    check((await p.locator("#flow-list").innerText()).includes("React 编辑后的流程"),"工作流编辑形成新版本");

    await p.getByRole("navigation",{name:"我的设置"}).getByRole("link",{name:/API 与 Coding Agent/}).click();
    check(await p.getByLabel("Token 名称",{exact:true}).count()===0,"Token 页上没有内嵌的输入框（PRD 2026-10-23）");
    await p.getByRole("button",{name:"新建 Token"}).click();
    const tokenDialog=p.getByRole("dialog",{name:"新建 Token"});
    await tokenDialog.getByLabel("Token 名称").fill("react-browser-client");
    await tokenDialog.getByRole("button",{name:"创建 Token"}).click();
    await p.locator(".secret").waitFor();
    const original=await p.locator(".secret").innerText();
    check(original.startsWith("oct_"),"API Token 在对话框里一次性显示");
    await p.getByRole("dialog",{name:"Token 已创建"}).getByRole("button",{name:"我已保存"}).click();
    await p.locator("[data-token-row]",{hasText:"react-browser-client"}).waitFor();
    check(await p.locator(".secret").count()===0,"关闭后不再显示 Token");
    // Owner 2026-10-24: regenerating keeps the entry; the old value stops working at once, the new one works
    const bearer=async raw=>p.evaluate(async raw=>(await fetch("/v1/me",{headers:{Authorization:"Bearer "+raw}})).status,raw);
    await p.getByRole("button",{name:"重新生成 Token：react-browser-client"}).click();
    await p.getByRole("dialog",{name:"重新生成 Token"}).getByRole("button",{name:"重新生成"}).click();
    await p.locator(".secret").waitFor();
    const renewed=await p.locator(".secret").innerText();
    await p.getByRole("dialog",{name:"Token 已重新生成"}).getByRole("button",{name:"我已保存"}).click();
    check(await p.locator("[data-token-row]",{hasText:"react-browser-client"}).count()===1,"重新生成后仍是同一条 Token");
    check(await bearer(renewed)===200&&await bearer(original)===401,"重新生成后新值可用、旧值立即失效");
    await p.getByRole("navigation",{name:"工作台"}).getByRole("link",{name:"会话"}).click();

    await p.setViewportSize({width:390,height:844});
    await p.waitForTimeout(200);
    check(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),"390 px 宽度无横向溢出");
    await p.getByRole("button",{name:"打开会话列表"}).click();
    check(await p.locator("#session-list:visible [data-run]").first().isVisible(),"窄屏以抽屉展示会话列表");   // the desktop sidebar stays mounted, hidden
    await p.keyboard.press("Escape");
    await p.getByRole("button",{name:"打开导航"}).click();
    await p.getByRole("dialog",{name:"导航"}).getByRole("navigation",{name:"我的设置"}).waitFor();
    check(true,"窄屏从「打开导航」打开左侧导航");
    await p.keyboard.press("Escape");
    await p.setViewportSize({width:1440,height:1000});

    await p.getByRole("button",{name:/账户/}).click();
    await p.getByRole("menuitem",{name:"退出登录"}).click();
    await p.getByRole("heading",{name:"进入开发空间"}).waitFor();
    check(await p.evaluate(()=>!sessionStorage.getItem("octopus.token")),"退出登录清除共享 Token");
    check(violations.length===0,"无 CSP 违规："+violations.join(" | "));
    check(errors.length===0,"无浏览器脚本异常："+errors.join(" | "));
    return {ok:true,passed:checks.length,checks,nativeCalls:0,mode:"React build + ephemeral HTTP/SQLite"};
  } finally {await context.close();await browser.close();}
}
