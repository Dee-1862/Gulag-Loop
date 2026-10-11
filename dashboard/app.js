const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>v==null?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:1});
const roles={Bold:['↗','Larger changes. Faster exploration.'],Tuner:['≋','Small adjustments. Precise control.'],Defender:['◇','Survival first. Create space.'],Explorer:['⌖','New targets. Different movement.']};
let snapshot=null,paused=false,loading=false,filter='all',blobURL=null,selectedPersona=null;
function params(){return new URLSearchParams({mode:$('mode').value,experiment:$('experiment').value});}
function badge(id,text,active){$(id).textContent=text;$(id).parentElement.classList.toggle('active',Boolean(active));}
function ageLabel(value){if(!value)return '—';const seconds=Math.max(0,Math.round((Date.now()-new Date(value).getTime())/1000));return seconds<60?`${seconds}s ago`:`${Math.floor(seconds/60)}m ago`;}
function showNotice(text){$('notice').hidden=!text;$('notice').textContent=text||'';}
function agents(items=[]){
 $('agents').innerHTML=Object.entries(roles).map(([name,[icon,role]])=>{const a=items.find(x=>x.persona===name)||{state:'waiting'};return `<button class="agent-card ${esc(a.state)}" data-persona="${name}" aria-label="Inspect ${name} agent"><div class="agent-top"><span class="agent-icon">${icon}</span><span class="agent-state">${esc(a.state)}</span></div><h3>${name}</h3><p>${role}</p><div class="agent-meta"><span>${a.latency_ms!=null?(a.latency_ms/1000).toFixed(1)+'s last call':'Awaiting call'}</span><b>${a.generation!=null?'GEN '+String(a.generation).padStart(2,'0'):'—'} ↗</b></div></button>`}).join('');
 document.querySelectorAll('[data-persona]').forEach(b=>b.addEventListener('click',()=>openAgent(b.dataset.persona)));
}
function openAgent(name){selectedPersona=name;const a=snapshot?.agents?.find(x=>x.persona===name);$('detail-title').textContent=name;$('detail-state').textContent=`${a?.state||'waiting'} · ${a?.tokens!=null?fmt(a.tokens)+' tokens in last recorded attempt':'No usage recorded'}`;$('detail-rationale').textContent=a?.rationale||'No saved proposal yet. A rationale appears after the orchestrator persists this agent’s result.';$('detail-changes').innerHTML=a?.changed_params?.length?a.changed_params.map(k=>`<code>${esc(k)}</code>`).join(''):'No changed settings recorded.';if(!$('detail').open)$('detail').showModal();}
function drawChart(rows=[]){
 if(!rows.length){$('chart').innerHTML='<p class="empty">Scores appear after evaluations complete.</p>';return;}
 const w=590,h=165,left=44,right=12,top=10,bottom=28;const maximum=Math.max(100,...rows.map(r=>r.best))*1.12;
 const x=i=>left+i*(w-left-right)/Math.max(1,rows.length-1),y=v=>h-bottom-(v/maximum)*(h-top-bottom);
 const path=key=>rows.map((r,i)=>(i?'L':'M')+x(i)+','+y(r[key])).join(' ');
 const grid=[0,.5,1].map(k=>`<line x1="${left}" x2="${w-right}" y1="${y(k*maximum)}" y2="${y(k*maximum)}" stroke="#29313b" stroke-dasharray="3 4"/><text x="${left-9}" y="${y(k*maximum)+3}" text-anchor="end" fill="#768595" font-size="9">${Math.round(k*maximum)}</text>`).join('');
 const labels=rows.filter((r,i)=>i%Math.max(1,Math.ceil(rows.length/9))===0).map(r=>`<text x="${x(rows.indexOf(r))}" y="${h-6}" text-anchor="middle" fill="#768595" font-size="9">G${esc(r.generation)}</text>`).join('');
 $('chart').innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Best and mean run fitness by generation"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#c4ed91" stop-opacity=".13"/><stop offset="100%" stop-color="#c4ed91" stop-opacity="0"/></linearGradient></defs>${grid}<path d="${path('best')} L${x(rows.length-1)},${y(0)} L${x(0)},${y(0)} Z" fill="url(#fill)"/><path d="${path('mean')}" stroke="#83ced8" stroke-width="1.5" fill="none" stroke-dasharray="4 4"/><path d="${path('best')}" stroke="#c4ed91" stroke-width="2" fill="none"/>${rows.map((r,i)=>`<circle cx="${x(i)}" cy="${y(r.best)}" r="3" fill="#c4ed91"><title>Generation ${esc(r.generation)}: best ${fmt(r.best)}, mean ${fmt(r.mean)}</title></circle>`).join('')}${labels}</svg>`;
}
function events(){let list=snapshot?.events||[];if(filter==='mongo')list=list.filter(e=>e.kind!=='llm');if(filter==='llm')list=list.filter(e=>e.kind==='llm');$('events').innerHTML=list.length?list.slice(0,7).map(e=>`<div class="event-row"><span><i class="event-dot ${esc(e.kind)}"></i>${esc(e.title)}</span><span>${esc(e.detail)}</span><span>${esc(e.collection)}</span><span title="${esc(e.at)}">${ageLabel(e.at)}</span></div>`).join(''):'<p class="empty">No matching activity recorded.</p>';}
function clearFrame(){if(blobURL){URL.revokeObjectURL(blobURL);blobURL=null;}$('frame').hidden=true;$('frame-empty').hidden=false;$('frame').removeAttribute('src');}
async function updateFrame(data){
 const active=data.workers?.find(w=>w.state==='running');
 if(data.mode==='demo'){$('frame').src='/demo-frame.jpg';$('frame').hidden=false;$('frame-empty').hidden=true;$('frame-label').textContent='SAMPLE CAPTURE';return;}
 $('frame-label').textContent=active?'LIVE · 2s CAPTURE':'NO LIVE FRAME';
 if(!active){clearFrame();return;}
 try{const r=await fetch('/api/frame?'+params());if(!r.ok||r.status===204){clearFrame();return;}const b=await r.blob();if(blobURL)URL.revokeObjectURL(blobURL);blobURL=URL.createObjectURL(b);$('frame').src=blobURL;$('frame').hidden=false;$('frame-empty').hidden=true;}catch{clearFrame();}
}
function render(data){snapshot=data;const ready=data.connected&&data.experiment;const demo=data.mode==='demo';
 $('connection').className='pill '+(demo?'demo':ready?'connected':'');$('connection').innerHTML=`<i></i>${paused?'PAUSED':demo?'DEMO PREVIEW':ready?'BACKEND CONNECTED':'AWAITING BACKEND'}`;
 showNotice(data.error||(demo?'DEMO PREVIEW — synthetic pipeline data with a recorded sample frame. No game, database writes, or model calls are running.':''));
 const e=data.experiment||{};$('experiment-name').textContent=e.id||'No experiment';$('map-name').textContent=e.map?'MAP / '+e.map.toUpperCase():'Select an experiment when MongoDB is connected';$('generation').innerHTML=e.generation!=null?`${String(e.generation).padStart(2,'0')}<span> / evolving</span>`:'—';$('fitness').textContent=fmt(data.leaderboard?.[0]?.mean_fitness);$('runs-count').textContent=fmt(data.counts?.runs);$('queue-count').textContent=data.jobs?`${data.jobs.queued||0} queued · ${data.jobs.running||0} running`:'No queue data yet';
 const worker=data.workers?.find(w=>w.state==='running')||data.workers?.[0];const running=worker?.state==='running';
 badge('vision-status',running?'PLAYING':worker?.state?.toUpperCase()||'WAITING',running);badge('mongo-status',ready?'CONNECTED':'OFFLINE',ready);
 const active=data.agents?.filter(a=>['running','retrying'].includes(a.state)).length||0;badge('luminal-status',active?'GENERATING':data.agents?.some(a=>a.state==='complete')?'READY':'WAITING',active);$('agent-count').textContent=active+' / 4 ACTIVE';
 $('worker-name').textContent=worker?.worker||worker?.identity||'NO WORKER CONNECTED';$('frame-age').textContent=worker?.updated_at?ageLabel(worker.updated_at):'—';$('fps').textContent=fmt(running?worker?.fps:null);$('targets').textContent=fmt(running?worker?.detections:null);$('points').textContent=fmt(worker?.points);$('worker-state').textContent=worker?.state||'Waiting';$('action').textContent=running?worker.action||'PLAYING':'STANDBY';$('elapsed').textContent=worker?.elapsed_s!=null?Math.round(worker.elapsed_s)+' / 180s':'— / 180s';
 agents(data.agents);if($('detail').open&&selectedPersona)openAgent(selectedPersona);
 $('model-name').textContent=data.model?.split('/').pop()||'No model recorded';$('calls-count').textContent=(data.counts?.llm_calls??'—')+' calls recorded';drawChart(data.chart);$('sample-note').textContent=data.runs_sampled!=null?`${data.runs_sampled} recent evaluations (max 500)`:'No results yet';
 $('collections').innerHTML=['jobs','runs','playbooks','llm_calls'].map(name=>`<div class="collection"><span class="collection-icon">▱</span><div><strong>${fmt(data.counts?.[name])}</strong><small>${name}</small></div><span>${ready?'↗':'—'}</span></div>`).join('');
 $('db-updated').textContent=data.events?.[0]?.at?'Latest activity '+ageLabel(data.events[0].at):'No activity recorded';$('last-refresh').textContent='Snapshot '+new Date().toLocaleTimeString()+' · '+(demo?'demo data':'polls every 2 seconds');events();
}
async function refresh(){if(loading)return;loading=true;$('refresh').disabled=true;try{const response=await fetch('/api/snapshot?'+params());const data=await response.json();render(data);await updateFrame(data);}catch{render({connected:false,mode:$('mode').value,error:'Dashboard connection lost. Check that the local server is running.'});clearFrame();}finally{loading=false;$('refresh').disabled=false;}}
async function loadExperiments(){const previous=$('experiment').value;try{const r=await fetch('/api/experiments?'+params());const d=await r.json();$('experiment').innerHTML=(d.experiments||[]).map(e=>`<option value="${esc(e.id)}">${esc(e.id)}</option>`).join('')||'<option value="">No experiments found</option>';if([...$('experiment').options].some(o=>o.value===previous))$('experiment').value=previous;}catch{$('experiment').innerHTML='<option value="">Database unavailable</option>';}}
$('pause').addEventListener('click',()=>{paused=!paused;$('pause').textContent=paused?'▶ Resume':'Ⅱ Pause';if(snapshot)render(snapshot);if(!paused)refresh();});
$('refresh').addEventListener('click',async()=>{await loadExperiments();refresh();});
$('mode').addEventListener('change',async()=>{clearFrame();await loadExperiments();refresh();});$('experiment').addEventListener('change',()=>{clearFrame();refresh();});
$('close-detail').addEventListener('click',()=>$('detail').close());
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b===button));events();}));
setInterval(()=>{$('clock').textContent=new Date().toLocaleTimeString()+' LOCAL';if(!paused)refresh();},2000);
(async()=>{agents();try{const c=await(await fetch('/api/config')).json();$('mode').value=new URLSearchParams(location.search).get('mode')||c.default_mode;}catch{}await loadExperiments();await refresh();})();
