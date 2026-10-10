/* Isolated browser + synthetic APIs; never cancels/deletes an actual user Session. */
async (page,{artifactRoot}={})=>{
  const browser=await page.context().browser().browserType().launch({headless:true,channel:'chrome'});
  const ctx=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion:'reduce'}),p=await ctx.newPage();
  const errors=[],calls=[],results=[];
  p.on('pageerror',e=>errors.push(String(e)));
  const check=(x,m)=>{if(!x)throw Error(m);};
  const step={id:'code',name:'编码',tool:'claude',model:'auto',effort:'',prompt:'实现并测试'},review={...step,id:'review',name:'审查',tool:'codex'};
  const config={schema:1,id:'development',name:'开发流程',revision:1,steps:[step,review]};
  const runs=['a','b'].map(id=>({id:'workflow-operations-'+id,slot:'workflow-operations-'+id,run:'/fixture/workflow-operations-'+id,created_at:1700000000,workflow:config,session_meta:{}}));
  const status={a:'active',b:'active'},goals={a:'A 原始需求',b:'B 原始需求'},running={a:false,b:false};
  let failDelete=false,uncertain=false,hold=false,release,backup=false;
  await ctx.route('**/api/**',async route=>{
    const req=route.request(),u=new URL(req.url()),path=u.pathname,body=req.method()==='POST'?req.postDataJSON():null;
    if(body)calls.push({path,body});
    let data;
    if(path==='/api/catalogue')data=Object.fromEntries(['kiro','claude','codex','opencode'].map(t=>[t,{models:['auto'],advertised_models:[],efforts:['']} ]));
    else if(path==='/api/runtime')data={features:['codex_effort_pair_v1'],workbench_features:['stage_retry_v1','session_operations_v1']};
    else if(path==='/api/library')data={definitions:[config],deleted:{}};
    else if(path==='/api/runs')data=runs.map(r=>({...r,session_meta:{closed:status[r.id.at(-1)]==='closed',deleted:status[r.id.at(-1)]==='deleted'}}));
    else if(path==='/api/state'){
      const m=runs.find(r=>r.id===u.searchParams.get('run')),id=m.id.at(-1);
      data={manifest:m,connected:true,at:Date.now()/1000,parent_running:running[id],approvals:[],session_control:{status:status[id],backup:backup?'operations/test/session-backup.zip':null},status:status[id]==='active'?'failed':status[id],input:{text:goals[id]},steps:config.steps.map((s,i)=>({...s,status:i===0?(running[id]?'running':'failed'):'pending',task:i===0?{id:'deadbeef',done:!running[id],error:running[id]?null:'fixture'}:null}))};
    }else if(path==='/api/session/continue'){
      if(hold){hold=false;await new Promise(r=>release=r);}
      goals[body.run.at(-1)]=body.input;data={status:uncertain?'uncertain':'accepted',run:body.run,error:uncertain?'timeout fixture':null};
    }else if(path==='/api/session/control'){
      const id=body.run.at(-1),action=body.action;
      if(action==='delete'&&failDelete)data={status:'failed',error:'备份失败，原生对话尚未删除'};
      else{status[id]={stop:'stopped',close:'closed',reopen:'stopped',delete:'deleted'}[action];running[id]=false;backup=['close','delete'].includes(action);data={status:'completed',session_status:status[id]};}
    }else {errors.push(path);return route.fulfill({status:500,json:{error:'Unexpected fixture API'}});}
    await route.fulfill({json:data});
  });
  const openAction=async action=>{await p.locator('#session-menu').evaluate(n=>n.open=true);await p.locator('#session-'+action).click();};
  const select=async id=>{await p.evaluate(async id=>{await chooseRun(id);},'workflow-operations-'+id);};
  const capture=async name=>{if(artifactRoot)await p.screenshot({path:artifactRoot+'/'+name+'.png',animations:'disabled'});};
  try{
    await p.goto('http://127.0.0.1:8920/workflow.html?run=workflow-operations-a');await p.waitForFunction(()=>document.querySelector('#continue-session').disabled===false);
    await p.locator('#continue-session').click();check(await p.locator('#session-continue-input').inputValue()==='A 原始需求','current Session goal seeded');
    await p.locator('#session-continue-input').fill('A 继续修复新的开发需求');await p.locator('#session-continue-prompt').fill('A 修复与真实验证');
    await p.locator('#session-operation-cancel').click();await select('b');await p.locator('#continue-session').click();check(await p.locator('#session-continue-input').inputValue()==='B 原始需求','B cannot inherit A continuation');
    await p.locator('#session-operation-cancel').click();await select('a');await p.locator('#continue-session').click();check(await p.locator('#session-continue-input').inputValue()==='A 继续修复新的开发需求','A draft restored');
    await capture('continue-desktop');
    uncertain=true;await p.locator('#session-operation-submit').click();await p.waitForFunction(()=>document.querySelector('#session-operation-message').textContent.includes('待确认'));
    const request=calls.at(-1).body;check(request.run==='workflow-operations-a','existing Run ID');check(request.input==='A 继续修复新的开发需求','updated input sent');
    uncertain=false;hold=true;await p.locator('#session-operation-submit').click();await p.waitForFunction(()=>document.querySelector('#session-operation-submit').disabled);
    check(await p.locator('#session-operation-cancel').isDisabled(),'cannot close inflight operation');check(await p.locator('#session-continue-input').isDisabled(),'input frozen during submission');
    check(calls.at(-1).body.request_id===request.request_id,'uncertain retry uses original id');release();await p.waitForFunction(()=>!document.querySelector('#session-operation').open);
    check(!calls.some(c=>['/api/start','/api/prepare'].includes(c.path)),'never creates new Run');
    results.push('原 Session 续作、A/B 草稿隔离、超时复用请求 ID、提交期间禁止改写，不调用新建接口');

    uncertain=true;await p.locator('#continue-session').click();await p.locator('#session-operation-submit').click();
    await p.waitForFunction(()=>document.querySelector('#session-operation-message').textContent.includes('待确认'));
    const interruptedRequest=calls.at(-1).body.request_id;await p.locator('#session-operation-cancel').click();
    running.a=true;await p.evaluate(()=>poll());check(await p.locator('#continue-session').isDisabled(),'running Session cannot continue');
    await openAction('stop');check((await p.locator('#session-operation-description').textContent()).includes('子任务'),'stop scope explicit');await p.locator('#session-operation-submit').click();await p.waitForFunction(()=>!document.querySelector('#session-operation').open);check((await p.locator('#status').textContent())==='已停止','confirmed stop shown');check(!await p.locator('#continue-session').isDisabled(),'stopped Session can resume');
    uncertain=false;await p.locator('#continue-session').click();await p.locator('#session-operation-submit').click();await p.waitForFunction(()=>!document.querySelector('#session-operation').open);
    check(calls.at(-1).body.request_id!==interruptedRequest,'explicit stop requires a fresh continuation request');
    results.push('运行时禁止直接续作；强制停止确认后显示已停止，再允许原地续作');

    await openAction('close');await p.locator('#session-operation-submit').click();await p.waitForFunction(()=>!document.querySelector('#session-operation').open);check(await p.locator('#continue-session').isHidden(),'closed Session requires reopen');check((await p.locator('#status').textContent())==='已关闭','closed state');
    await openAction('reopen');await p.locator('#session-operation-submit').click();await p.waitForFunction(()=>!document.querySelector('#session-operation').open);check(!await p.locator('#continue-session').isHidden(),'reopen same Session');
    results.push('关闭、重新打开与备份入口保持同一 Session 身份');

    await openAction('delete');check(await p.locator('#session-operation-submit').isDisabled(),'delete requires ID');await p.locator('#session-delete-confirm').fill('workflow-operations-b');check(await p.locator('#session-operation-submit').isDisabled(),'wrong ID cannot delete');
    await p.locator('#session-delete-confirm').fill('workflow-operations-a');failDelete=true;await p.locator('#session-operation-submit').click();await p.waitForFunction(()=>document.querySelector('#session-operation-message').textContent.includes('备份失败'));check(await p.locator('#session-operation').evaluate(n=>n.open),'failure remains visible');
    await p.evaluate(()=>refreshSessions());check(await p.locator('#session-operation').evaluate(n=>n.open),'poll cannot dismiss confirmation');
    await p.setViewportSize({width:390,height:844});await capture('delete-mobile');check(await p.locator('#session-operation').evaluate(n=>n.scrollWidth<=n.clientWidth+1),'mobile dialog no overflow');
    failDelete=false;await p.locator('#session-operation-submit').click();await p.waitForFunction(()=>!document.querySelector('#session-operation').open);await p.setViewportSize({width:1440,height:1050});
    await p.locator('[data-session-filter="deleted"]').click();check(await p.locator('[data-run="workflow-operations-a"]').count()===1,'deleted audit row accessible');check(await p.locator('[data-run="workflow-operations-b"]').count()===0,'other Session not deleted');
    check(errors.length===0,JSON.stringify(errors));results.push('删除输入完整 ID；备份失败保留面板与原生数据；已删除视图保留审计；窄屏通过');
    return {ok:true,results,apiWrites:calls.map(c=>c.path),nativeCalls:0};
  }finally{await browser.close();}
}
