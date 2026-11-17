import {store,load} from '../stores/research.js';
import {destructions} from '../api/research.js';
import {table} from '../components/common/table.js';
import {destructionStatus} from '../components/common/status.js';
import {bindForm} from '../hooks/forms.js';
import {esc} from '../utils/format.js';

function certificateRows(cert){
  if(!cert)return '';
  const checks=cert.checks||{};
  return `<h2>销毁凭据 <small>不含原值 · 仅保留定位证明</small></h2>${table(['项目','凭据记录'],[
    ['批次',`#${cert.study_id} ${esc(cert.study_name)}`],
    ['源记录数',cert.source_key_count],
    ['源记录集合证明 (SHA256)',{html:esc(cert.source_key_set_sha256||'—')}],
    ['已删除源观测',cert.source_observations_removed],
    ['已清除结果运行',cert.runs_scrubbed],
    ['已删除共享留档',{html:esc((cert.artifacts_removed||[]).join(', ')||'无')}],
    ['已清除复核留档',`${cert.reviews_removed??0} 条 · 留痕改写 ${cert.review_audits_redacted??0} 条`],
    ['冻结输入已移除原值',cert.frozen_inputs_redacted?'是':'否'],
    ['结果与下载已清空',cert.results_cleared?'是':'否'],
    ['核对 · 剩余源观测',checks.source_observations_remaining],
    ['核对 · 含原值的冻结输入',{html:esc(JSON.stringify(checks.frozen_inputs_with_original_values))}],
    ['核对 · 残留结果',{html:esc(JSON.stringify(checks.results_remaining))}],
    ['核对 · 残留共享留档',{html:esc(JSON.stringify(checks.artifacts_remaining))}],
    ['核对 · 残留复核留档',{html:esc(JSON.stringify(checks.reviews_remaining))}],
    ['核对 · 残留复核留痕',{html:esc(JSON.stringify(checks.review_audit_entries_remaining))}],
    ['同名异批源记录保留',checks.other_batches_same_source_key_preserved],
    ['全部副本核对清空',checks.all_cleared?'是':'否'],
    ['核对时间 (UTC)',cert.verified_at],
  ])}`;
}

export async function render(id){
  let tasks=[];let detail=null;
  try{tasks=await destructions.list();}catch(e){tasks=[];}
  if(id){try{detail=await destructions.get(id);}catch(e){detail=null;}}
  const writable=localStorage.getItem('research-role')==='admin';
  const open=store.studies.filter(s=>!s.sealed);
  return `<div class="eyebrow">06 / AUTHORIZATION WITHDRAWAL</div><h1>销毁任务与凭据</h1><p>按${store.studies.length?'批次':''}与源记录编号发起销毁。批次先封存，随后清除源观测、冻结输入、结果与共享留档；只保留不含原值的销毁凭据。未受影响批次照常可用。</p><div class="method-note">发起后所有读取、排队、计算、复核与下载入口立即拒绝该批次。旧计算晚到不会写回；清理失败可从“等待清理/清理失败”重试，重复提交不会新增任务。</div>${writable?`<form id="destruction-form" class="worksheet"><label>受影响批次<select name="study_id">${open.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></label><label>源记录编号（每行一个，或逗号分隔）<textarea name="source_keys" required placeholder="FIELD-001&#10;FIELD-002"></textarea></label><p>提交即封存该批次全部入口；请确认这些编号只属于该批次。</p><button type="submit">封存并发起销毁 →</button></form>`:'<p class="method-note">仅负责人可发起销毁与恢复清理；观察员与分析师可查看任务状态与凭据。</p>'}<h2>任务队列 <small>待清理 → 清理中 → 已清理 / 失败</small></h2>${table(['任务','批次','状态','源记录数','发起人','更新时间','操作'],tasks.map(t=>[{html:`<a href="#destruction/${t.id}">#${t.id}</a>`},`#${t.study_id}`,{html:destructionStatus(t.status)},(t.source_keys||[]).length||(t.certificate?.source_key_count??'—'),esc(t.initiated_by),esc(t.updated_at),{html:`<a href="#destruction/${t.id}">查看状态 →</a>`}]))}${detail?`<h2>任务 #${detail.id} ${destructionStatus(detail.status)}</h2><p>批次 #${detail.study_id} · 指纹 ${esc(detail.fingerprint.slice(0,16))}… · 发起 ${esc(detail.created_at)}</p>${detail.error?`<div class="error">最近错误：${esc(detail.error)}</div>`:''}${writable&&detail.status!=='completed'?`<div class="toolbar"><button id="retry">恢复清理</button></div>`:''}${detail.certificate?certificateRows(detail.certificate):`<p class="method-note">尚未完成清理，暂无销毁凭据。状态会随清理进度更新。</p>`}<h2>状态轨迹</h2>${table(['状态','时间 (UTC)'],(detail.history||[]).map(h=>[destructionStatus(h.status),esc(h.at)]))}`:''}`;
}
export function bind(refresh){
  bindForm('destruction-form',async data=>{
    const study_id=+data.study_id;
    const source_keys=String(data.source_keys).split(/[\n,，]/).map(s=>s.trim()).filter(Boolean);
    if(!study_id)throw new Error('请先创建受影响批次');
    const t=await destructions.create({study_id,source_keys});
    await load();
    location.hash='destruction/'+t.id;
  });
  const retry=document.getElementById('retry');
  if(retry)retry.onclick=async()=>{try{const id=Number(location.hash.split('/')[1]);await destructions.retry(id);refresh();}catch(e){document.getElementById('notice').textContent=e.message;}};
}
