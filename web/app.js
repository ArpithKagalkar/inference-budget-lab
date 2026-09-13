'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = x => `${(Number(x || 0) * 100).toFixed(1)}%`;
const money = (x, currency='$') => `${currency}${Number(x || 0).toFixed(3)}`;
const ms = x => `${Math.round(Number(x || 0)).toLocaleString()} ms`;
const colors = {strong:'#8277d0', economy:'#dba35d', adaptive:'#087f71'};
let currentRun = null;
let historyRows = [];

async function api(path, options={}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The request failed');
  return data;
}
function jsonPost(path, body) { return api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); }
function toast(message) { $('toast').textContent=message; $('toast').classList.remove('hidden'); setTimeout(()=>$('toast').classList.add('hidden'),6000); }

const titles = {overview:'Overview', opportunities:'Opportunities', lab:'Experiment lab', workflows:'Verified workflows', requests:'Request explorer', history:'Run history', methodology:'Methodology'};
function showView(view) {
  document.querySelectorAll('.view').forEach(el=>el.classList.add('hidden'));
  $(view === 'overview' ? 'overview' : `${view}-view`).classList.remove('hidden');
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active', el.dataset.view === view));
  $('view-title').textContent=titles[view];
  if(view!=='lab') $('mode-badge').textContent='● LOCAL WORKSPACE';
  if(view==='overview') refreshOps();
  if(view==='opportunities') refreshOpportunities();
  if(view==='workflows') refreshWorkflows();
  if(view==='history') refreshHistory();
  if(view==='requests') renderRequests();
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>showView(button.dataset.view)));

function findingLabel(type) { return {oversized_model:'MODEL ROUTING', repeated_prompt:'PROMPT REUSE', excessive_rag:'RAG CONTEXT'}[type] || type; }
function findingCard(row, compact=false) {
  const evidence=Object.entries(row.evidence || {}).slice(0,compact?3:6).map(([key,value])=>`<span><b>${escapeHTML(key.replaceAll('_',' '))}</b>${typeof value==='number'?Number(value).toLocaleString(undefined,{maximumFractionDigits:3}):escapeHTML(value)}</span>`).join('');
  const action=row.eligible_for_experiment?`<button class="primary action-primary" data-experiment="${escapeHTML(row.id)}">Run guarded experiment</button>`:'<span class="advisory">Advisory · separate quality test required</span>';
  return `<article class="panel opportunity-card"><div class="opportunity-top"><span class="pill">${findingLabel(row.type)}</span><span class="confidence ${row.confidence}">${escapeHTML(row.confidence)} confidence</span></div><h2>${escapeHTML(row.title)}</h2><p class="endpoint">${escapeHTML(row.endpoint)}</p><div class="impact"><strong>${money(row.estimated_savings)}</strong><span>estimated potential in this trace window</span></div><div class="evidence-grid">${evidence}</div>${compact?'':`<details><summary>Evidence limitations</summary><ul>${row.limitations.map(item=>`<li>${escapeHTML(item)}</li>`).join('')}</ul></details>${action}`}</article>`;
}
function bindOpportunityActions() {
  document.querySelectorAll('[data-experiment]').forEach(button=>button.addEventListener('click',async()=>{
    button.disabled=true; button.textContent='Running…';
    try {
      const workflow=await jsonPost(`/api/opportunities/${button.dataset.experiment}/experiments`, {mode:'simulation', requests:80, validation_requests:120, seed:42, rps:4, concurrency:8, quality_min:.90, latency_slo_ms:1800, traffic:'steady'});
      toast(`Workflow ${workflow.id} finished: ${workflow.state}`); await refreshWorkflows(); showView('workflows');
    } catch(error) { toast(error.message); button.disabled=false; button.textContent='Run guarded experiment'; }
  }));
}
async function refreshOps() {
  try {
    const [summary, opportunities]=await Promise.all([api('/api/summary'),api('/api/opportunities')]);
    const symbol=summary.currency==='USD'?'$':`${summary.currency} `;
    $('ops-metrics').innerHTML=`<article class="metric"><label>OBSERVED SPEND</label><strong>${money(summary.observed_cost,symbol)}</strong><small>Latest imported trace window</small></article><article class="metric"><label>POTENTIAL SAVINGS</label><strong>${money(summary.potential_savings,symbol)}</strong><small>Estimated · not yet verified</small></article><article class="metric"><label>TRACES ANALYSED</label><strong>${summary.trace_count.toLocaleString()}</strong><small>${summary.batch_count} trace batch${summary.batch_count===1?'':'es'}</small></article><article class="metric"><label>OPPORTUNITIES</label><strong>${summary.opportunity_count}</strong><small>${summary.workflow_count} verification workflows</small></article>`;
    $('overview-opportunities').innerHTML=opportunities.length?opportunities.slice(0,3).map(row=>findingCard(row,true)).join(''):'<div class="empty-state">Load traces and run an audit to identify opportunities.</div>';
  } catch(error) { toast(error.message); }
}
async function loadDemo() {
  try {
    $('load-demo').disabled=true; $('load-demo').textContent='Preparing workspace…';
    const batch=await jsonPost('/api/trace-batches',{source:'demo',count:900,seed:42});
    await jsonPost('/api/audits',{batch_id:batch.id});
    toast('Loaded 900 explicitly synthetic production-shaped traces.');
    await refreshOps(); await refreshOpportunities();
  } catch(error) { toast(error.message); }
  finally { $('load-demo').disabled=false; $('load-demo').textContent='Load demo workspace'; }
}
$('load-demo').addEventListener('click',loadDemo);
async function runAudit() {
  try { await jsonPost('/api/audits',{}); await refreshOps(); await refreshOpportunities(); toast('Cost audit complete.'); }
  catch(error) { toast(error.message); }
}
$('run-audit').addEventListener('click',runAudit); $('overview-audit').addEventListener('click',runAudit);
async function refreshOpportunities() {
  try { const rows=await api('/api/opportunities'); $('opportunity-list').innerHTML=rows.length?rows.map(row=>findingCard(row)).join(''):'<div class="panel empty-state">No opportunities yet. Load a trace workspace first.</div>'; bindOpportunityActions(); }
  catch(error) { toast(error.message); }
}

function eventTimeline(events) { return `<div class="timeline">${events.map(event=>`<div class="timeline-row ${event.status}"><i></i><div><b>${escapeHTML(event.type.replaceAll('_',' '))}</b><span>${new Date(event.created*1000).toLocaleString()}</span></div></div>`).join('')}</div>`; }
function workflowCard(row) {
  let action='';
  if(row.state==='VERIFIED') action=`<button class="secondary" data-preview="${row.id}">Generate PR preview</button>`;
  if(row.state==='PR_PREVIEWED') action=`<button class="primary action-primary" data-create-pr="${row.id}">Create external draft PR</button>`;
  if(row.state==='PR_CREATED'||row.state==='PARTIAL') action=`<button class="secondary" data-notify="${row.id}">Stage Slack notification</button>`;
  const pr=row.pr?.url?`<a href="${escapeHTML(row.pr.url)}" target="_blank" rel="noreferrer">Open verified draft PR ↗</a>`:'';
  return `<article class="panel workflow-card"><div class="workflow-heading"><div><span class="pill">${escapeHTML(row.state)}</span><h2>Workflow ${escapeHTML(row.id)}</h2><p>Opportunity ${escapeHTML(row.opportunity_id)} · Experiment ${escapeHTML(row.experiment_id||'pending')}</p></div><div class="workflow-actions">${pr}${action}</div></div>${eventTimeline(row.events||[])}</article>`;
}
async function refreshWorkflows() {
  try {
    const rows=await api('/api/workflows');
    const hydrated=await Promise.all(rows.map(row=>api(`/api/workflows/${row.id}`)));
    $('workflow-list').innerHTML=hydrated.length?hydrated.map(workflowCard).join(''):'<div class="panel empty-state">Start an eligible opportunity experiment to create a workflow.</div>';
    document.querySelectorAll('[data-preview]').forEach(button=>button.addEventListener('click',async()=>{try{await jsonPost(`/api/workflows/${button.dataset.preview}/pr-preview`,{});await refreshWorkflows();}catch(error){toast(error.message);}}));
    document.querySelectorAll('[data-create-pr]').forEach(button=>button.addEventListener('click',async()=>{if(!window.confirm('Create a real public draft PR in the allowlisted demo repository? Nothing will be merged or deployed.'))return;try{await jsonPost(`/api/workflows/${button.dataset.createPr}/create-pr`,{confirm:true});await refreshWorkflows();toast('Draft PR created and read back from GitHub.');}catch(error){toast(error.message);await refreshWorkflows();}}));
    document.querySelectorAll('[data-notify]').forEach(button=>button.addEventListener('click',async()=>{try{await jsonPost(`/api/workflows/${button.dataset.notify}/notify`,{delivery:'staged'});await refreshWorkflows();toast('Slack-equivalent notification staged locally.');}catch(error){toast(error.message);}}));
  } catch(error) { toast(error.message); }
}

function updateCallCount(){
  $('request-count').textContent=$('requests').value; $('validation-count').textContent=$('validation_requests').value;
  const live=$('mode').value==='live'; $('paid-fields').classList.toggle('hidden',!live);
  $('call-count').textContent=live?`${2*Number($('validation_requests').value)+3*Number($('requests').value)} live calls · provider charges may apply`:'3 policies · fixed evaluation schema';
}
for(const id of ['requests','validation_requests','mode']) $(id).addEventListener('change',updateCallCount);
function configFromForm() {
  const config={mode:$('mode').value,traffic:$('traffic').value};
  for(const key of ['requests','validation_requests','seed','rps','concurrency','quality_min','latency_slo_ms']) config[key]=Number($(key).value);
  config.quality_min/=100;
  if(config.mode==='live') Object.assign(config,{budget_cap:Number($('budget_cap').value),budget_currency:'USD',paid_confirmation:$('paid_confirmation').value,estimated_input_tokens:2000,max_output_tokens:150});
  return config;
}
function syncForm(config) { for(const [key,value] of Object.entries(config)) if($(key)&&key!=='paid_confirmation') $(key).value=key==='quality_min'?Math.round(value*100):value; updateCallCount(); }
function setBusy(busy) { $('run-button').disabled=busy; $('run-button').textContent=busy?'Experiment running…':'▶ Run experiment'; $('run-form').querySelectorAll('input,select').forEach(el=>el.disabled=busy); }
$('preflight-button').addEventListener('click',async()=>{try{const config=configFromForm();delete config.paid_confirmation;const result=await jsonPost('/api/provider-preflight',config);$('preflight-result').textContent=`${result.call_count} calls · maximum ${result.currency} ${result.maximum_cost.toFixed(4)} · type ${result.required_confirmation}`;$('paid_confirmation').placeholder=result.required_confirmation;}catch(error){toast(error.message);}});
$('run-form').addEventListener('submit',async event=>{event.preventDefault();setBusy(true);$('job-status').textContent='Starting experiment…';try{const job=await jsonPost('/api/runs',configFromForm());localStorage.setItem('inferenceops-job',job.id);await pollJob(job.id);}catch(error){toast(error.message);$('job-status').textContent=error.message;setBusy(false);}});
async function pollJob(id) {
  try { const job=await api(`/api/jobs/${id}`); $('job-status').textContent=job.message; if(job.status==='running'){setTimeout(()=>pollJob(id),800);return;} localStorage.removeItem('inferenceops-job');setBusy(false);if(job.status==='failed')throw new Error(job.message);await loadRun(id);await refreshHistory(); }
  catch(error){try{await loadRun(id);$('job-status').textContent='Loaded saved experiment.';}catch{toast(error.message);$('job-status').textContent='Connection interrupted. Check run history before retrying live work.';}localStorage.removeItem('inferenceops-job');setBusy(false);}
}
async function loadRun(id){currentRun=await api(`/api/runs/${id}`);syncForm(currentRun.config);renderRun();}
function renderRun(){
  const run=currentRun,cfg=run.config,m=run.results.find(x=>x.id==='adaptive').metrics,sim=cfg.mode==='simulation';
  $('mode-badge').textContent=sim?'● SIMULATION':'● LIVE ENDPOINTS';
  $('mode-notice').innerHTML=sim?'<strong>Simulation evidence.</strong> Synthetic tickets, modeled latency, and illustrative prices do not prove real model savings.':'<strong>Live endpoint measurements.</strong> Provider usage and latency are measured; inspect dataset provenance before making a production claim.';
  $('run-label').textContent=`${run.id} · seed ${cfg.seed}`;
  $('metrics').innerHTML=`<article class="metric"><label>COST REDUCTION</label><strong>${m.savings===null?'—':pct(m.savings)}</strong><small class="${m.savings>0?'good':'bad'}">Against always strong</small></article><article class="metric"><label>EXACT-MATCH QUALITY</label><strong>${pct(m.quality)}</strong><small>${pct(m.quality_ci[0])}–${pct(m.quality_ci[1])} Wilson interval</small></article><article class="metric"><label>P95 LATENCY</label><strong>${Math.round(m.p95_ms).toLocaleString()}<em>ms</em></strong><small class="${m.p95_ms<=cfg.latency_slo_ms?'good':'bad'}">SLO ${ms(cfg.latency_slo_ms)}</small></article>`;
  $('policy-table').innerHTML=run.results.map(row=>{const x=row.metrics;return `<tr class="${row.id==='adaptive'?'highlight':''}"><td><i class="dot ${row.id}"></i>${row.name}</td><td>${x.cost_known?money(x.cost_per_1k):'Incomplete'}</td><td>${pct(x.quality)}</td><td>${ms(x.p95_ms)}</td><td><span class="tag ${x.feasible?'':'fail'}">${x.feasible?'✓ Pass':'× Outside target'}</span></td></tr>`;}).join('');
  $('decision').className=`decision ${run.accepted?'accepted':''}`;$('decision').textContent=run.accepted?`Accepted on held-out test. Threshold ${run.threshold}.`:'No adaptive policy satisfied every constraint with complete cost accounting.';
  $('calibration-count-pill').textContent=`${cfg.validation_requests} FIXTURES`;
  $('calibration-table').innerHTML=run.calibration.map(row=>`<tr><td>${row.threshold}</td><td>${row.cost_known?money(row.cost_per_1k):'Incomplete'}</td><td>${pct(row.quality)}</td><td>${ms(row.p95_ms)}</td><td><span class="tag ${row.feasible&&row.cost_known?'':'fail'}">${row.feasible&&row.cost_known?'Eligible':'Rejected'}</span></td></tr>`).join('');
  const adaptive=run.results.find(row=>row.id==='adaptive');const economy=adaptive.metrics.economy_share;
  $('routing').innerHTML=`<svg class="routing-chart" viewBox="0 0 100 12" preserveAspectRatio="none" role="img" aria-label="${pct(economy)} economy and ${pct(1-economy)} strong"><rect width="100" height="12" rx="3" fill="#8277d0"/><rect width="${economy*100}" height="12" rx="3" fill="#dba35d"/></svg><div class="bar-labels"><span><b>${pct(economy)}</b> economy</span><span><b>${pct(1-economy)}</b> strong</span></div>`;
  $('segments').innerHTML=Object.entries(adaptive.metrics.quality_by_difficulty||{}).map(([level,x])=>`<div class="segment"><span>${level}</span><svg width="100%"><rect width="100%" height="6" rx="3" fill="#edf2f1"/><rect width="${x.quality*100}%" height="6" rx="3" fill="#70b59f"/></svg><b>${pct(x.quality)}</b></div>`).join('');
  $('accounting-note').textContent=run.budget?`Budget ${run.budget.currency} ${run.budget.spent.toFixed(4)} spent of ${run.budget.cap.toFixed(2)} reserved cap.`:'Calibration spending is reported separately; incomplete usage is never treated as free.';
  renderChart(run);renderRequests();$('export-button').disabled=false;$('csv-button').disabled=false;
}
function renderChart(run){
  const width=650,height=285,left=64,right=100,top=20,bottom=52,plotW=width-left-right,plotH=height-top-bottom;
  const maxCost=Math.max(...run.results.map(r=>r.metrics.cost_per_1k),.01)*1.12,minQ=Math.max(0,Math.floor((Math.min(run.config.quality_min,...run.results.map(r=>r.metrics.quality))-.08)*10)/10);
  const x=v=>left+v/maxCost*plotW,y=v=>top+(1-v)/(1-minQ)*plotH;let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Cost versus exact-match quality"><rect x="${left}" y="${top}" width="${plotW}" height="${Math.max(0,y(run.config.quality_min)-top)}" fill="#f1f8f5"/>`;
  for(let i=0;i<=4;i++){const q=minQ+(1-minQ)*i/4;svg+=`<line x1="${left}" x2="${width-right}" y1="${y(q)}" y2="${y(q)}" stroke="#e8eeed"/><text x="${left-12}" y="${y(q)+4}" text-anchor="end">${Math.round(q*100)}%</text>`;}
  for(let i=0;i<=4;i++){const cost=maxCost*i/4;svg+=`<line x1="${x(cost)}" x2="${x(cost)}" y1="${top}" y2="${height-bottom}" stroke="#f0f3f4"/><text x="${x(cost)}" y="${height-bottom+22}" text-anchor="middle">$${cost.toFixed(2)}</text>`;}
  svg+=`<line x1="${left}" x2="${width-right}" y1="${y(run.config.quality_min)}" y2="${y(run.config.quality_min)}" stroke="#80b19e" stroke-dasharray="5 5"/>`;
  for(const row of run.results){const m=row.metrics,cx=x(m.cost_per_1k),cy=y(m.quality);svg+=`<g><title>${row.name}</title><circle cx="${cx}" cy="${cy}" r="7" fill="${colors[row.id]}" stroke="white" stroke-width="2"/><text class="point-label" x="${cx+12}" y="${cy-10}">${row.id}</text></g>`;}
  svg+=`<text class="chart-title" x="${left+plotW/2}" y="${height-6}" text-anchor="middle">SERVING COST PER 1,000 REQUESTS →</text></svg>`;$('chart').classList.remove('empty-chart');$('chart').innerHTML=svg;
}
function renderRequests(){if(!currentRun)return;const policy=currentRun.results.find(r=>r.id===$('inspect-policy').value),outcome=$('inspect-outcome').value,search=$('inspect-search').value.toLowerCase();const rows=policy.records.filter(r=>(r.text||'').toLowerCase().includes(search)&&(outcome==='all'||(outcome==='failed'&&!r.correct)||(outcome==='slo'&&r.latency_ms>currentRun.config.latency_slo_ms)));$('request-list').innerHTML=`<p class="request-count">${rows.length} matching requests · showing first ${Math.min(100,rows.length)}</p>`+rows.slice(0,100).map(r=>`<article class="panel request-card"><div class="request-top"><strong>${escapeHTML(r.id)} · ${escapeHTML(r.difficulty||'unsegmented')} · ${escapeHTML(r.model)}</strong><span class="tag ${r.correct?'':'fail'}">${r.correct?'✓ Exact match':'× Quality failure'}</span></div><p>${escapeHTML(r.text)}</p><div class="request-meta"><span>${ms(r.latency_ms)}</span><span>${r.cost_known?`$${r.cost.toFixed(6)}`:'Cost unavailable'}</span><span>${r.input_tokens+r.output_tokens} tokens</span></div><details><summary>Compare output and labels</summary><div class="json-grid"><pre>${escapeHTML(JSON.stringify(r.output,null,2))}</pre><pre>${escapeHTML(JSON.stringify(r.expected,null,2))}</pre></div></details></article>`).join('');}
for(const id of ['inspect-policy','inspect-outcome','inspect-search']) $(id).addEventListener(id==='inspect-search'?'input':'change',renderRequests);
async function refreshHistory(){try{historyRows=await api('/api/runs');$('history-list').innerHTML=historyRows.length?historyRows.map(run=>`<article class="panel history-card"><div><h2>${escapeHTML(run.id)} <span class="pill">${run.config.mode.toUpperCase()}</span></h2><p>${new Date(run.created*1000).toLocaleString()} · ${run.config.requests} requests · seed ${run.config.seed}</p></div><div><span class="tag ${run.accepted?'':'fail'}">${run.accepted?'Accepted':'Outside constraints'}</span><button class="secondary" data-load="${run.id}">Open run ↗</button></div></article>`).join(''):'<div class="panel empty-state">No saved experiments yet.</div>';document.querySelectorAll('[data-load]').forEach(button=>button.addEventListener('click',async()=>{try{await loadRun(button.dataset.load);showView('lab');}catch(error){toast(error.message);}}));}catch(error){toast(error.message);}}
$('export-button').addEventListener('click',()=>{if(!currentRun)return;const url=URL.createObjectURL(new Blob([JSON.stringify(currentRun,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`inferenceops-${currentRun.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('csv-button').addEventListener('click',()=>{if(currentRun)window.location.href=`/api/runs/${currentRun.id}/csv`;});
async function init(){try{const health=await api('/api/health');if(health.live_ready){const option=$('mode').querySelector('[value="live"]');option.disabled=false;option.textContent=health.paid_provider?'Live endpoints · confirmation required':'Live local endpoints';}await Promise.all([refreshHistory(),refreshOps()]);const pending=localStorage.getItem('inferenceops-job');if(pending){setBusy(true);pollJob(pending);}else if(historyRows.length){await loadRun(historyRows[0].id);$('mode-badge').textContent='● LOCAL WORKSPACE';}}catch(error){toast(`Cannot connect to the local engine: ${error.message}`);}}
init();
