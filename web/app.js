'use strict';
const $ = id => document.getElementById(id);
let currentRun = null;
let historyRows = [];
let activeJob = null;
const colors = {strong:'#8277d0',economy:'#dba35d',adaptive:'#087f71'};
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = x => `${(x*100).toFixed(1)}%`;
const money = x => `$${x.toFixed(3)}`;
const ms = x => `${Math.round(x).toLocaleString()} ms`;
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The request failed');
  return data;
}
function toast(message) { $('toast').textContent=message; $('toast').classList.remove('hidden'); setTimeout(()=>$('toast').classList.add('hidden'),6000); }
function showView(view) {
  document.querySelectorAll('.view').forEach(el=>el.classList.add('hidden'));
  $(view === 'overview' ? 'overview' : `${view}-view`).classList.remove('hidden');
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.view === view));
  $('view-title').textContent={overview:'Experiment lab',requests:'Request explorer',history:'Run history',methodology:'Methodology'}[view];
  if(view==='history') refreshHistory();
  if(view==='requests') renderRequests();
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>showView(button.dataset.view)));
function updateCallCount(){
  $('request-count').textContent=$('requests').value;
  $('validation-count').textContent=$('validation_requests').value;
  $('call-count').textContent=$('mode').value==='live'?`${2*Number($('validation_requests').value)+3*Number($('requests').value)} live calls · provider charges apply`:'3 policies · fixed evaluation schema';
}
$('requests').addEventListener('change',updateCallCount);
$('validation_requests').addEventListener('change',updateCallCount);
$('mode').addEventListener('change',updateCallCount);
function configFromForm() {
  const config={mode:$('mode').value, traffic:$('traffic').value};
  for(const key of ['requests','validation_requests','seed','rps','concurrency','quality_min','latency_slo_ms']) config[key]=Number($(key).value);
  config.quality_min /= 100;
  return config;
}
function syncForm(config) {
  for(const [key,value] of Object.entries(config)) if($(key)) $(key).value=key==='quality_min' ? Math.round(value*100) : value;
  updateCallCount();
}
function setBusy(busy) {
  $('run-button').disabled=busy;
  $('run-button').textContent=busy?'Experiment running…':'▶ Run experiment';
  $('run-form').querySelectorAll('input,select').forEach(el=>el.disabled=busy);
}
$('run-form').addEventListener('submit',async event=>{
  event.preventDefault();
  setBusy(true);
  $('job-status').textContent='Starting experiment…';
  try {
    const job=await api('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(configFromForm())});
    activeJob=job.id;
    localStorage.setItem('ibl-job',job.id);
    await pollJob(job.id);
  } catch(error) { toast(error.message); $('job-status').textContent=error.message; setBusy(false); }
});
async function pollJob(id) {
  try {
    const job=await api(`/api/jobs/${id}`);
    $('job-status').textContent=job.message;
    if(job.status==='running') { setTimeout(()=>pollJob(id),800); return; }
    localStorage.removeItem('ibl-job'); activeJob=null; setBusy(false);
    if(job.status==='failed') throw new Error(job.message);
    await loadRun(id);
    refreshHistory();
  } catch(error) {
    // A completed run persists even if its in-memory job was lost on restart.
    try { await loadRun(id); $('job-status').textContent='Loaded saved experiment.'; }
    catch { toast(error.message); $('job-status').textContent='Connection interrupted. Check run history before retrying live work.'; }
    localStorage.removeItem('ibl-job'); activeJob=null; setBusy(false);
  }
}
async function loadRun(id) { currentRun=await api(`/api/runs/${id}`); syncForm(currentRun.config); render(); }
function render() {
  const run=currentRun, cfg=run.config, m=run.results.find(x=>x.id==='adaptive').metrics;
  const sim=cfg.mode==='simulation';
  $('mode-badge').textContent=sim?'● SIMULATION':'● LIVE ENDPOINTS';
  $('mode-notice').innerHTML=sim?'<strong>A transparent starting point.</strong> Simulation uses synthetic tickets, modeled latency, and illustrative token prices. It makes no claims about real model performance.':'<strong>Live endpoint measurements.</strong> Latency and provider token usage are measured. Tickets remain synthetic. Configured token prices exclude self-hosted infrastructure cost.';
  $('run-label').textContent=`${run.id} · seed ${cfg.seed} · ${cfg.requests} requests`;
  const savings=m.savings === null || !m.cost_known ? '—' : pct(m.savings);
  $('metrics').innerHTML=`<article class="metric"><label>COST REDUCTION</label><strong>${savings}</strong><small>${m.cost_known?`${money(m.cost_per_1k)} / 1k · vs. always strong`:'Incomplete provider usage'}</small></article><article class="metric"><label>EXACT-MATCH QUALITY</label><strong>${pct(m.quality)}</strong><small class="${m.quality>=cfg.quality_min?'good':'bad'}">${m.quality>=cfg.quality_min?'✓':'×'} Target ≥ ${pct(cfg.quality_min)}</small></article><article class="metric"><label>P95 LATENCY</label><strong>${Math.round(m.p95_ms).toLocaleString()}<em>ms</em></strong><small class="${m.p95_ms<=cfg.latency_slo_ms?'good':'bad'}">${m.p95_ms<=cfg.latency_slo_ms?'✓':'×'} SLO ≤ ${ms(cfg.latency_slo_ms)}</small></article>`;
  $('policy-table').innerHTML=run.results.map(row=>{const x=row.metrics;return `<tr class="${row.id==='adaptive'?'highlight':''}"><td><i class="dot ${row.id}"></i>${row.name}</td><td>${x.cost_known?money(x.cost_per_1k):'Incomplete'}</td><td title="95% Wilson interval: ${pct(x.quality_ci[0])} – ${pct(x.quality_ci[1])}">${pct(x.quality)}</td><td>${ms(x.p95_ms)}</td><td><span class="tag ${x.feasible?'':'fail'}">${x.feasible?'✓ Pass':'× Outside target'}</span></td></tr>`;}).join('');
  $('decision').className=`decision ${run.accepted?'accepted':''}`;
  $('decision').textContent=run.accepted?`✓ Adaptive policy meets both test targets. Threshold ${run.threshold} was selected on validation data. Quality 95% CI: ${pct(m.quality_ci[0])}–${pct(m.quality_ci[1])}. This is a sample result, not a guarantee.`:!run.validation_feasible?'No validation candidate met all requirements with complete costs. The strong-model fallback is shown; this configuration is not accepted.':`Adaptive policy is not accepted: ${!m.cost_known?'provider cost accounting is incomplete':'held-out quality or latency misses the target'}. Try a different operating point; retain failed experiments as evidence.`;
  renderChart(run);
  $('calibration-count-pill').textContent=`${run.dataset.validation_count} FIXTURES`;
  $('calibration-table').innerHTML=run.calibration.map(c=>`<tr class="${c.threshold===run.threshold?'highlight':''}"><td>${c.threshold}${c.threshold===run.threshold?' · selected':''}</td><td>${c.cost_known?money(c.cost_per_1k):'Incomplete'}</td><td>${pct(c.quality)}</td><td>${ms(c.p95_ms)}</td><td><span class="tag ${c.feasible&&c.cost_known?'':'fail'}">${c.feasible&&c.cost_known?'Yes':'No'}</span></td></tr>`).join('');
  const basis=sim?'illustrative token prices':Object.values(run.providers||{}).map(p=>p.cost_basis).join(' / ');
  $('accounting-note').textContent=`Benchmark serving cost: $${run.benchmark_cost.toFixed(4)} (${basis}). ${sim?'Simulation calibration makes no API calls.':`Calibration cost: $${run.calibration_cost.toFixed(4)}${run.calibration_cost_known?'':' (incomplete)'}. Candidate latency above is a replay estimate.`} Adaptive error rate: ${pct(m.error_rate)} · latency violations: ${pct(m.slo_violations)} · throughput: ${m.throughput.toFixed(2)} req/s.`;
  $('routing').innerHTML=`<svg class="bar" width="100%" role="img" aria-label="${pct(m.economy_share)} economy, ${pct(1-m.economy_share)} strong"><rect width="100%" height="12" fill="${colors.strong}"/><rect width="${m.economy_share*100}%" height="12" fill="${colors.adaptive}"/></svg><div class="bar-labels"><span>Economy <b>${pct(m.economy_share)}</b></span><span>Strong <b>${pct(1-m.economy_share)}</b></span></div>`;
  $('segments').innerHTML=Object.entries(m.quality_by_difficulty).map(([level,x])=>`<div class="segment"><span>${level}</span><svg width="100%" role="img" aria-label="${level}: ${pct(x.quality)} from ${x.n} requests"><rect width="100%" height="6" rx="3" fill="#edf2f1"/><rect width="${x.quality*100}%" height="6" rx="3" fill="#70b59f"/></svg><b>${pct(x.quality)}</b></div>`).join('');
  $('export-button').disabled=false; $('csv-button').disabled=false;
  renderRequests();
}
function renderChart(run) {
  const width=650,height=285,left=64,right=100,top=20,bottom=52;
  const plotW=width-left-right,plotH=height-top-bottom;
  const maxCost=Math.max(...run.results.map(r=>r.metrics.cost_per_1k),.01)*1.12;
  const minQ=Math.max(0,Math.floor((Math.min(run.config.quality_min,...run.results.map(r=>r.metrics.quality))-.08)*10)/10);
  const x=v=>left+v/maxCost*plotW, y=v=>top+(1-v)/(1-minQ)*plotH;
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Cost versus exact-match quality. Dashed horizontal line is the quality threshold. Crosses mark latency SLO failures."><rect x="${left}" y="${top}" width="${plotW}" height="${Math.max(0,y(run.config.quality_min)-top)}" fill="#f1f8f5"/>`;
  for(let i=0;i<=4;i++){const q=minQ+(1-minQ)*i/4;svg+=`<line x1="${left}" x2="${width-right}" y1="${y(q)}" y2="${y(q)}" stroke="#e8eeed"/><text x="${left-12}" y="${y(q)+4}" text-anchor="end">${Math.round(q*100)}%</text>`;}
  for(let i=0;i<=4;i++){const cost=maxCost*i/4;svg+=`<line x1="${x(cost)}" x2="${x(cost)}" y1="${top}" y2="${height-bottom}" stroke="#f0f3f4"/><text x="${x(cost)}" y="${height-bottom+22}" text-anchor="middle">$${cost.toFixed(2)}</text>`;}
  svg+=`<line x1="${left}" x2="${width-right}" y1="${y(run.config.quality_min)}" y2="${y(run.config.quality_min)}" stroke="#80b19e" stroke-dasharray="5 5"/><text class="target-label" x="${width-right+8}" y="${y(run.config.quality_min)+4}">${pct(run.config.quality_min)} target</text>`;
  for(const row of run.results){const m=row.metrics,cx=x(m.cost_per_1k),cy=y(m.quality),offset=row.id==='adaptive'?-18:22;
    svg+=`<g><title>${row.name}: quality ${pct(m.quality)}, cost ${money(m.cost_per_1k)}/1k, p95 ${ms(m.p95_ms)}. Quality CI ${pct(m.quality_ci[0])}–${pct(m.quality_ci[1])}</title><line x1="${cx}" x2="${cx}" y1="${y(Math.min(1,m.quality_ci[1]))}" y2="${y(Math.max(minQ,m.quality_ci[0]))}" stroke="${colors[row.id]}" opacity=".4" stroke-width="2"/><circle cx="${cx}" cy="${cy}" r="12" fill="${colors[row.id]}" opacity=".10"/><circle cx="${cx}" cy="${cy}" r="6" fill="${colors[row.id]}" stroke="white" stroke-width="2"/>${m.p95_ms>run.config.latency_slo_ms?`<path d="M${cx-4},${cy-4}l8,8m-8,0l8,-8" stroke="#fff" stroke-width="1.5"/>`:''}<text class="point-label" x="${cx+13}" y="${Math.min(height-bottom-5,Math.max(14,cy+offset))}">${row.id==='adaptive'?'Adaptive':row.id==='strong'?'Strong':'Economy'}</text></g>`;
  }
  svg+=`<text class="chart-title" x="${left+plotW/2}" y="${height-6}" text-anchor="middle">SERVING COST PER 1,000 REQUESTS →</text><text class="chart-title" transform="translate(15,${top+plotH/2}) rotate(-90)" text-anchor="middle">EXACT-MATCH QUALITY →</text></svg>`;
  $('chart').classList.remove('empty-chart'); $('chart').innerHTML=svg;
}
function renderRequests() {
  if(!currentRun)return;
  const policy=currentRun.results.find(r=>r.id===$('inspect-policy').value);
  const outcome=$('inspect-outcome').value,search=$('inspect-search').value.toLowerCase();
  const rows=policy.records.filter(r=>r.text.toLowerCase().includes(search)&&(outcome==='all'||(outcome==='failed'&&!r.correct)||(outcome==='slo'&&r.latency_ms>currentRun.config.latency_slo_ms)));
  $('request-list').innerHTML=`<p class="request-count">${rows.length} matching requests · showing first ${Math.min(100,rows.length)} · full data available in exports</p>`+rows.slice(0,100).map(r=>`<article class="panel request-card"><div class="request-top"><strong>${escapeHTML(r.id)} · ${r.difficulty} · ${r.model}</strong><span class="tag ${r.correct?'':'fail'}">${r.correct?'✓ Exact match':'× Quality failure'}</span></div><p>${escapeHTML(r.text)}</p><div class="request-meta"><span>End-to-end ${ms(r.latency_ms)}</span><span>Queue ${ms(r.queue_ms)}</span><span>Service ${ms(r.service_ms)}</span><span>${r.cost_known?`$${r.cost.toFixed(6)}`:'Cost unavailable'}</span><span>${r.input_tokens+r.output_tokens} tokens</span>${r.error?`<span>${escapeHTML(r.error)}</span>`:''}</div><details><summary>Compare prediction with expected labels</summary><div class="json-grid"><div><label>MODEL OUTPUT</label><pre>${escapeHTML(JSON.stringify(r.output,null,2))}</pre></div><div><label>EXPECTED LABELS</label><pre>${escapeHTML(JSON.stringify(r.expected,null,2))}</pre></div></div></details></article>`).join('')+(rows.length?'':'<div class="panel empty-state">No requests match these filters.</div>');
}
for(const id of ['inspect-policy','inspect-outcome','inspect-search'])$(id).addEventListener(id==='inspect-search'?'input':'change',renderRequests);
async function refreshHistory() {
  try {
    historyRows=await api('/api/runs');
    $('history-list').innerHTML=historyRows.length?historyRows.map(run=>`<article class="panel history-card"><div><h2>${escapeHTML(run.id)} <span class="pill">${run.config.mode.toUpperCase()}</span></h2><p>${new Date(run.created*1000).toLocaleString()} · ${run.config.requests} requests · ${run.config.rps} rps · ${run.config.traffic} · seed ${run.config.seed}</p></div><div><span class="tag ${run.accepted?'':'fail'}">${run.accepted?'Accepted':'Outside constraints'}</span><button class="secondary" data-load="${run.id}">Open run ↗</button></div></article>`).join(''):'<div class="panel empty-state">No saved experiments yet. Your first run starts in the experiment lab.</div>';
    document.querySelectorAll('[data-load]').forEach(button=>button.addEventListener('click',async()=>{try{await loadRun(button.dataset.load);showView('overview');}catch(error){toast(error.message);}}));
  }catch(error){toast(error.message);}
}
$('export-button').addEventListener('click',()=>{
  if(!currentRun)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(currentRun,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download=`inference-lab-${currentRun.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('csv-button').addEventListener('click',()=>{if(currentRun)window.location.href=`/api/runs/${currentRun.id}/csv`;});
async function init(){
  try{
    const health=await api('/api/health');
    if(health.live_ready){const option=$('mode').querySelector('[value="live"]');option.disabled=false;option.textContent='Live endpoints · paid API calls';}
    await refreshHistory();
    const pending=localStorage.getItem('ibl-job');
    if(pending){activeJob=pending;setBusy(true);pollJob(pending);}
    else if(historyRows.length)await loadRun(historyRows[0].id);
  }catch(error){toast(`Cannot connect to the local engine: ${error.message}`);}
}
init();
