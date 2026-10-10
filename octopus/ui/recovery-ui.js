"use strict";

function recoveryFields(value={enabled:true,max_cycles:2},prefix="recovery") {
  return `<div class="recovery-settings"><label class="check-label"><input type="checkbox" id="${prefix}-enabled"${value.enabled?" checked":""}>失败时由 Coordinator 自动诊断并安排修复</label>
    <label>最多自动纠错轮次<select id="${prefix}-limit">${[1,2,3,4,5].map(n=>`<option value="${n}"${n===value.max_cycles?" selected":""}>${n} 轮</option>`).join("")}</select></label>
    <p class="hint">同一工作区内返工，保留历史并重新审查。缺少项目输入、认证或权限时说明原因，等待处理。</p></div>`;
}
function readRecovery(prefix="recovery") {
  return {enabled:$(prefix+"-enabled").checked,max_cycles:Number($(prefix+"-limit").value)};
}
function renderRecovery(run) {
  const data=run.recovery,attention=run.attention,last=data?.latest;
  $("run-recover").hidden=run.purpose==="workflow_design";
  $("run-recover").disabled=activeStates.has(run.status)||run.lifecycle!=="open"||
    !["blocked","failed","stopped"].includes(run.status)||!run.native_parent||
    !run.tasks.some(t=>t.status!=="accepted"&&t.attempts?.length);
  $("run-attention").hidden=!attention;
  $("attention-title").textContent=attention?.title||"";
  $("attention-detail").textContent=attention?.detail||"";
  $("attention-action").textContent=[attention?.task_name&&`当前任务：${attention.task_name}`,attention?.action].filter(Boolean).join(" · ");
  $("recovery-policy").textContent=data?(data.policy.enabled?
    `自动纠错已开启 · 最多 ${data.policy.max_cycles} 轮`:"自动纠错未开启 · 可手动诊断"):"";
  $("recovery-history").hidden=!data?.history?.length;
  $("recovery-summary").textContent=run.status==="diagnosing"?`第 ${last?.number||1} 轮 · Coordinator 正在诊断`:
    last?.status==="applied"&&run.status!=="completed"?`第 ${last.number} 轮 · 已安排返工与复审`:`纠错记录 · ${data?.used||0} 轮`;
  $("recovery-records").innerHTML=(data?.history||[]).map(row=>`<article class="recovery-record">
    <strong>第 ${row.number} 轮 · ${esc(({pending:"等待协调者",running:"诊断中",waiting_approval:"诊断等待权限",dispatch_unknown:"派发待核实",applied:"已安排修复",blocked:"需要补充条件",cancelled:"已取消"})[row.status]||row.status)}</strong>
    <p>${esc(row.decision?.reason||row.error||"等待真实诊断结果")}</p>
    ${row.decision?.instructions?`<p>${esc(row.decision.instructions)}</p>`:""}
    <small>失败 Task：${esc(row.failed_task_id)}${row.decision?.target_task_id?" → 返工 Task："+esc(row.decision.target_task_id):""} · 原生诊断 Task：${esc(row.native_id||"待派发")}</small>
  </article>`).join("");
}
async function requestRecovery() {
  const run=state.run;if(!run)return;
  try{
    await command("/runs/"+encodeURIComponent(run.id)+"/recover",{expected_version:run.version});
    await refresh();if(state.selected===run.id)await selectRun(run.id);
    toast("Coordinator 已收到诊断请求，将在原工作区安排修复或说明缺失条件。");
  }catch(error){toast(error.message);}
}
