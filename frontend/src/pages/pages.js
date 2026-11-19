import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {pagesAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {esc,formObject} from '../utils/html.js';
import {action,message} from '../hooks/action.js';
import {REPAIR_STATUSES} from '../constants/enums.js';

const writable=()=>session.role!=='viewer';
const repairBadge=s=>`<span class="badge repair ${esc(s)}">${esc(REPAIR_STATUSES[s]||s)}</span>`;
const short=v=>esc(String(v).slice(0,16)+(String(v).length>16?'…':''));

export function pagesPage(){
 const pageKeys=[...new Set(store.pages.map(p=>p.page_key))].sort();
 return `<div class="section-title"><h2>节点页面健康摘要</h2><span>坏页未清零的备库不能当选</span></div>
 ${table(['节点','副本数','坏页数','坏页清单','切主候选就绪'],store.pageHealth.map(h=>[esc(h.node_name),h.page_total,h.bad_pages,esc(h.bad_page_keys.join('、')||'—'),h.page_ready?'<b class="ok">就绪</b>':'<b class="warn">受阻</b>']))}
 <div class="section-title"><h2>页面副本与坏页操作</h2><span>校验和失配即坏页：停读、停重写推进</span></div>
 ${table(['逻辑页','节点','版本','校验状态','校验和','操作'],store.pages.map(p=>[esc(p.page_key),esc(p.node_name),'v'+p.version,p.healthy?'<b class="ok">通过</b>':'<b class="warn">坏页</b>',short(p.checksum),writable()?(p.healthy?`<button class="secondary mini" data-act="corrupt" data-id="${p.id}">制造坏页</button>`:`<button class="mini" data-act="repair" data-id="${p.id}">发起补页</button>`):'—']))}
 <section class="split compact">
  <div><h2>整页重写与读探测</h2><p>整页重写只推进健康副本，坏页原地保留；读坏页会被停读并给出原因，其他页与其他节点照常读写。</p>
   ${writable()?`<form id="rewrite-form" class="stack"><label>逻辑页<select name="page_key">${pageKeys.map(k=>`<option>${esc(k)}</option>`).join('')}</select></label><button type="submit">整页重写（推进一个版本）</button></form>`:'<p class="readonly">重写需操作员或管理员。</p>'}
   <form id="probe-form" class="stack"><label>读探测：逻辑页<select name="page_key">${pageKeys.map(k=>`<option>${esc(k)}</option>`).join('')}</select></label><label>节点<select name="node_id">${store.nodes.map(n=>`<option value="${n.id}">${esc(n.name)}</option>`).join('')}</select></label><button type="submit" class="secondary">查看读到的版本</button></form>
   <div id="probe-result"></div>
  </div>
  <div><h2>补页规则</h2><p>补页只接受<b>校验通过且版本相同</b>的副本；写盘带版本 CAS，整页重写已发布的新版不会被旧页盖掉。</p>
   <p class="big-note">同键补页幂等：写盘中断后重试，发布次数不重复累计。</p>
   ${writable()?`<form id="repair-form" class="stack"><label>幂等补页键（可留空自动生成）<input name="repair_key" maxlength="64" placeholder="例如 drill-key-001"></label><label>坏页副本<select name="copy_id">${store.pages.filter(p=>!p.healthy).map(p=>`<option value="${p.id}">${esc(p.node_name)} / ${esc(p.page_key)} / v${p.version}</option>`).join('')}</select></label><button type="submit">登记同次补页</button></form>`:'<p class="readonly">补页操作需操作员或管理员。</p>'}
  </div>
 </section>
 <div class="section-title"><h2>补页任务</h2><button class="secondary" id="refresh-pages">刷新</button></div>
 ${table(['编号','坏页目标','同版本副本源','目标版本','状态','发布次数','尝试','受阻/中断原因','推进操作'],store.repairs.map(j=>[j.id,j.target?`${esc(j.target.node_name)} / ${esc(j.target.page_key)}`:'—',j.donor?`${esc(j.donor.node_name)} / v${j.donor.version}`:'未选定','v'+j.target_version,repairBadge(j.status),j.publish_count,j.attempts,esc(j.blocked_reason||'—'),writable()?`<button class="secondary mini" data-act="advance" data-id="${j.id}">推进补页</button> <button class="secondary mini" data-act="interrupt" data-id="${j.id}">模拟写盘中断</button>`:'—']))}`;
}

export function bindPages(render){
 document.querySelector('#refresh-pages')?.addEventListener('click',render);
 document.querySelectorAll('button[data-act]').forEach(btn=>btn.addEventListener('click',async()=>{
  btn.disabled=true;
  try{
   const id=Number(btn.dataset.id),act=btn.dataset.act;
   if(act==='corrupt')await pagesAPI.corrupt(id);
   if(act==='repair')await pagesAPI.startRepair(id);
   if(act==='advance')await pagesAPI.advance(id,false);
   if(act==='interrupt')await pagesAPI.advance(id,true);
   await render();
  }catch(e){message(e.message,true);}
 }));
 action(document.querySelector('#rewrite-form'),async f=>{const d=formObject(f);await pagesAPI.rewrite(d.page_key);await render();});
 action(document.querySelector('#repair-form'),async f=>{const d=formObject(f);if(!d.copy_id)throw new Error('当前没有坏页副本可供补页');await pagesAPI.startRepair(Number(d.copy_id),d.repair_key||undefined);await render();});
 const pf=document.querySelector('#probe-form');
 pf?.addEventListener('submit',async e=>{e.preventDefault();const box=document.querySelector('#probe-result');try{const d=formObject(pf);const r=await pagesAPI.probe(d.page_key,Number(d.node_id));box.innerHTML=`<p class="notice ${r.readable?'':'error'}">${esc(r.reason)}<br>校验和：${short(r.checksum)}</p>`;}catch(err){box.innerHTML=`<p class="notice error">${esc(err.message)}</p>`;}});
}
