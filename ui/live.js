const $ = id => document.getElementById(id);
const labels = {ready:'等待 App 发起',awaiting_input:'等待输入需求',pending:'待执行',running:'执行中',checking:'等待阶段验收',
  passed:'验收通过',delivered:'四阶段已通过',failed:'执行失败',blocked:'验收未通过',awaiting_approval:'等待审批'};
const descriptions = {
  plan:'Kiro CLI 将用户需求转成接口规格、开发计划和验收测试；通过规划检查后锁定。',
  code:'Claude Code 实现核心模块。宿主脚本运行测试，生成真实 diff 并记录候选哈希。',
  review:'Codex 审查同一核心候选、真实 diff 和测试证据。高风险问题或失败测试会阻止交付。',
  deliver:'OpenCode 在已审查核心上制作 Web 前端。核心与测试必须保持一致，浏览器验收另行记录。'
};
let state, selected, manuallySelected = false;
let runId = new URLSearchParams(location.search).get('run') || '';
const scoped = path => `/r/${encodeURIComponent(state.manifest.id)}${path}`;
function badge(node, status){ node.className = `badge ${status}`; node.textContent = labels[status] || status; }
function el(tag, text, className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function renderStage(stage, index){
  const card=el('button',undefined,`stage${selected===stage.id?' selected':''}`);
  card.type='button'; card.setAttribute('aria-pressed',String(selected===stage.id));
  const head=el('div',undefined,'stage-head'), tag=el('span');badge(tag,stage.status);
  head.append(el('span',String(index+1).padStart(2,'0'),'stage-index'),tag);
  const taskId=stage.task?.id||stage.gate.task_id;
  card.append(head,el('h3',stage.title),el('div',stage.tool,'tool'),
    el('div',taskId?`${stage.task?'TASK':'已记录任务'} ${taskId}`:'等待原生任务','task'));
  card.onclick=()=>{selected=stage.id;manuallySelected=true;render(state);};
  return card;
}
function render(data){
  state=data;
  const list=$('run-select'), options=data.runs||[];
  const optionsKey=JSON.stringify(options);
  if(list.dataset.options!==optionsKey){
    list.replaceChildren(...options.map(r=>{const o=el('option',r.title+' · '+r.id);o.value=r.id;return o;}));
    list.dataset.options=optionsKey;
  }
  $('run-content').hidden=!data.manifest;
  if(!data.manifest){$('connection').textContent='等待新需求';$('app-link').hidden=true;return;}
  list.value=data.manifest.id;
  $('app-link').hidden=false;
  if(!runId){runId=data.manifest.id;history.replaceState(null,'',`/?run=${encodeURIComponent(runId)}${location.hash}`);}
  if(!manuallySelected) selected=(data.stages.find(s=>['running','checking','blocked','failed','awaiting_approval'].includes(s.status))
      || [...data.stages].reverse().find(s=>s.status==='passed') || data.stages[0]).id;
  $('title').textContent=data.manifest.title;$('run-id').textContent=data.manifest.id;
  $('app-link').href=`http://localhost:5476/chat?sid=${encodeURIComponent(data.manifest.slot)}`;
  $('product-title').textContent=data.manifest.title;
  $('preview-title').textContent='打开'+data.manifest.title;
  $('request-record').hidden=!data.requirements;
  $('request-text').textContent=data.requirements||'';
  $('preview-link').href=`http://localhost:${location.port}${scoped('/preview/index.html')}`;
  document.querySelectorAll('.report-actions a').forEach(a=>{
    const path=a.getAttribute('href').split('/files/')[1]; if(path)a.href=scoped('/files/'+path);
  });
  badge($('overall'),data.status);$('passed').textContent=data.passed_count;
  const age = data.last_success_at ? Math.max(0,Math.round(Date.now()/1000-data.last_success_at)) : null;
  const connected=data.connected && age!==null && age<12;
  $('connection').textContent=connected?'Gateway 已连接':'连接中断 / 状态可能滞后';
  $('connection-dot').className=connected?'live':'';
  $('updated').textContent=age===null?'等待连接':`最近成功读取：${age} 秒前`;
  const warnings=[];
  if(!connected)warnings.push('暂时无法读取最新状态。下方保留最后一次记录，不代表任务仍在运行。');
  if(data.approvals.length || data.stages.some(s=>s.status==='awaiting_approval'))
    warnings.push('等待原生审批：'+(data.approvals.map(a=>a.tool||a.title||'工具操作').join('；')||'子任务工具操作')+
      '。请在 KiroCrew App 批准具体操作；聊天输入“approved”不会放行。');
  if(data.status==='blocked')warnings.push('阶段未通过；查看任务输出与验收原因，下一阶段不应启动。');
  $('warning').hidden=!warnings.length;$('warning').textContent=warnings.join(' ');
  $('stages').replaceChildren(...data.stages.map(renderStage));
  const stage=data.stages.find(s=>s.id===selected);
  $('detail-title').textContent=`${stage.tool} · ${stage.title}`;badge($('detail-status'),stage.status);
  $('detail-description').textContent=descriptions[stage.id];$('agent').textContent=stage.agent;
  $('task-id').textContent=stage.task?.id||stage.gate.task_id||'—';
  $('backend').textContent=stage.route?.actual_backend?
    `${stage.route.actual_backend} · ${stage.route.provider_type||'AcpProvider'}`:'等待实际 factory 记录';
  $('last-tool').textContent=stage.task?.last_tool||(
    stage.task?.elapsed!==undefined?`${stage.task.elapsed} 秒`:'—');
  $('gate').className='gate'+(stage.gate.passed?' pass':stage.gate.error?' fail':'');
  $('gate').textContent=stage.gate.passed?'已核验：原生任务完成、父会话与实际后端匹配，阶段交付物通过验收。':
    stage.gate.error||'等待工作者完成，然后核验任务归属、后端与交付证据。';
  $('result').textContent=stage.task?.result||stage.task?.error||stage.task?.last_tool||
    (!stage.task&&stage.gate.native?.result?`已保存的执行结果：\n${stage.gate.native.result}`:'')||
    (stage.task?'原生任务已登记，正在等待工具输出。':'尚未创建本阶段任务。请从 KiroCrew App 发起本次 Pipeline。');
  $('evidence').textContent=Object.keys(stage.gate).length?JSON.stringify({
    task_id:stage.gate.task_id,native_session_id:stage.route?.native_session_id,candidate_sha256:stage.gate.candidate_sha256,
    tests:stage.gate.tests,review:stage.gate.review,route:stage.route,artifacts:stage.gate.artifacts
  },null,2):'尚未产生';
  $('preview-placeholder').hidden=data.preview_ready;$('preview-link').hidden=!data.preview_ready;
  $('browser-state').textContent=data.browser_check.passed?'浏览器验收：已通过功能与注入输入验证':
    '浏览器验收：尚未通过或未执行。Codex 的前序审查仅覆盖核心候选。';
  $('report-link').style.opacity=data.files.includes('report.html')?'1':'.45';
  $('files').replaceChildren(...data.files.map(path=>{
    const a=el('a',path.replace('workspace/',''));a.href=scoped(`/files/${path}`);a.target='_blank';a.rel='noopener';return a;
  }));
  $('parent-state').textContent=`父会话：${data.parent_running?'处理中':'空闲 / 等待完成事件'}`;
}
async function poll(){
  const requested=runId;
  try{const response=await fetch('/state.json'+(requested?`?run=${encodeURIComponent(requested)}`:''),{cache:'no-store'});if(!response.ok)throw Error(response.status);
    const data=await response.json();if(requested===runId)render(data);
  }catch{if(state)render({...state,connected:false});else{$('connection').textContent='看板服务连接失败';}}
  setTimeout(poll,2000);
}
$('run-select').onchange=()=>{runId=$('run-select').value;manuallySelected=false;history.replaceState(null,'',`/?run=${encodeURIComponent(runId)}`);};
$('fill-example').onclick=()=>{
  $('request-title').value='会议成本计算器';
  $('requirements').value='做一个会议成本计算器。输入参会人数、每人时薪和会议分钟数，计算总成本（元，保留两位小数）和总人时（保留两位小数）。参会人数必须为正整数，时薪和分钟数必须是有限的非负数字，空值、负数或非数字要提示错误。支持载入示例（6 人、150 元/小时、30 分钟）和清空，界面中文、手机可用。';
  $('requirements').focus();
};
let sending=false, lastSubmission;
async function intake(mode){
  if(sending)return;
  if(mode==='send'&&!$('intake-form').reportValidity())return;
  sending=true;
  $('send-request').disabled=$('app-intake').disabled=true;
  const feedback=$('intake-feedback');feedback.hidden=false;
  feedback.replaceChildren(el('span',mode==='send'?'正在将需求发送到 KiroCrew…':'正在准备 App 对话…'));
  const input={mode,requirements:$('requirements').value,title:$('request-title').value};
  const fingerprint=JSON.stringify(input);
  if(!lastSubmission||lastSubmission.fingerprint!==fingerprint)
    lastSubmission={fingerprint,body:{...input,request_id:crypto.randomUUID()}};
  // Re-clicking unchanged input retries the receipt, never dispatches a second run.
  const body=lastSubmission.body;
  try{
    const response=await fetch('/intake',{method:'POST',headers:{'Content-Type':'application/json','X-Pipeline-Intake':'1'},body:JSON.stringify(body)});
    const result=await response.json();if(!response.ok||!result.run)throw Error(result.error||'提交状态不确定，请检查 App 后再操作。');
    runId=result.run;manuallySelected=false;history.replaceState(null,'',`/?run=${encodeURIComponent(runId)}#new`);
    const accepted=result.intake.status==='accepted', prepared=result.intake.status==='prepared';
    feedback.replaceChildren(el('span',accepted?'需求已进入 KiroCrew App。流程将在这条对话中执行。':
      prepared?'App 对话已准备好。打开后直接输入你的开发需求。':'发送结果尚未确认，请打开 App 检查，避免重复发送。'));
    const a=el('a','打开这条 App 对话','button primary');a.href=result.app_url;a.target='_blank';a.rel='noopener';feedback.append(a);
    if(result.intake.error)feedback.append(el('p',result.intake.error));
    render(await (await fetch(`/state.json?run=${encodeURIComponent(runId)}`)).json());
  }catch(error){feedback.replaceChildren(el('span',`${error.message} 若已创建会话，数据会保留。`));}
  finally{sending=false;$('send-request').disabled=$('app-intake').disabled=false;}
}
$('intake-form').onsubmit=event=>{event.preventDefault();intake('send');};
$('app-intake').onclick=()=>intake('app');
poll();
