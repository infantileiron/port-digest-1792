import {state} from '../stores/session.js';
import {esc} from '../utils/html.js';
import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {RECON_BATCH_STATES,RECON_FINDINGS,RECON_DISPOSITIONS} from '../constants/enums.js';

const findingLabel=f=>f?RECON_FINDINGS[f]||f:'档证一致';
const dispositionLabel=d=>RECON_DISPOSITIONS[d]||d;
const batchLabel=b=>RECON_BATCH_STATES[b.state]||b.state;

function evidenceLine(it){
 const e=it.evidence||{};const parts=[];
 if(e.reason)parts.push(e.reason);
 if(e.belongs_to_receipt)parts.push('档案本属于回执 #'+e.belongs_to_receipt);
 if(e.quarantined_to)parts.push('原件隔离至 '+e.quarantined_to);
 if(e.resume_note)parts.push(e.resume_note);
 if(Array.isArray(e.diffs)&&e.diffs.length)parts.push('字段差异 '+e.diffs.map(d=>d.field).join('、'));
 return parts.join('；')||'—';
}

function totalsLine(t){
 if(!t)return '';
 return `回执 ${t.receipts} · 扫描文件 ${t.artifacts_scanned} · 缺档 ${t.missing} · 坏档 ${t.corrupt} · 串档 ${t.crossed} · 矛盾 ${t.contradiction} · 孤立 ${t.orphan} ｜ 一致 ${t.ok} · 补档 ${t.repaired} · 留存 ${t.held} · 隔离 ${t.quarantined}`;
}

function detailPanel(){
 const d=state.reconDetail;
 if(!d)return '<p class="notice">选择左侧批次查看逐项处理依据。差异先落库再动文件，崩溃续跑不重复补档或隔离。</p>';
 const b=d.batch;
 const rows=d.items.map(it=>[
  it.artifact_name||'—',
  it.receipt_id?('#'+it.receipt_id):'无回执',
  findingLabel(it.finding),
  dispositionLabel(it.disposition),
  evidenceLine(it)
 ]);
 return `<h2>对账批次 #${b.id} ${badge(batchLabel(b))}</h2>
 <p class="eyebrow">发起 ${esc(b.created_at)} · 开始 ${esc(b.started_at||'—')} · 结批 ${esc(b.finished_at||'—')}</p>
 <p>${esc(totalsLine(b.totals))}</p>
 ${b.state==='held'?'<p class="notice">存在来源自相矛盾项，批次留存：矛盾回执不补档、不删档，待台账核清后再发起下一批次。</p>':''}
 ${table(['共享文件','回执','差异分类','处置','处理依据'],rows)}
 <details><summary class="eyebrow">查看完整证据 JSON（字段级差异/隔离去向/恢复备注）</summary>
 <pre class="scroll">${esc(JSON.stringify(d.items,null,2))}</pre></details>`;
}

export function render(){
 const isAdmin=state.user.role==='admin';
 const open=state.reconBatches.find(b=>['pending','scanning','held'].includes(b.state));
 return `<div class="split"><section class="panel">${detailPanel()}</section>
 <aside class="side"><h2>归档对账批次</h2>
 ${isAdmin?`<form id="recon-start"><button class="primary" ${open?'disabled':''}>${open?'存在未结批次，先核清':'发起对账批次'}</button></form>
 <p>按回执冻结来源重算延误统计并校验共享目录；可信回执缺/坏/串档自动补档，矛盾留存，孤立文件隔离。</p>`
 :'<p class="notice">观察员可查看批次与处理依据；发起对账仅负责人可操作。</p>'}
 <h2>历史批次</h2>
 ${state.reconBatches.map(b=>`<button class="study" data-batch="${b.id}">#${b.id} ${esc(batchLabel(b))}<br>${esc(totalsLine(b.totals))}</button>`).join('')||'<p>尚无对账批次。</p>'}
 </aside></div>`;
}
