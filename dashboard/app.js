const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>v==null?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:1});
const roles={Bold:['↗','Larger changes. Faster exploration.'],Tuner:['≋','Small adjustments. Precise control.'],Defender:['◇','Survival first. Create space.'],Explorer:['⌖','New targets. Different movement.']};
let snapshotRequest=null,listRequest=null,requestVersion=0,listVersion=0,listing=false,receivedAt=null;
let snapshot=null,paused=false,loading=false,blobURL=null,selectedPersona=null,controlEnabled=false;
function params(){return new URLSearchParams({mode:$('mode').value,experiment:$('experiment').value});}
function ageLabel(value){if(!value||!Number.isFinite(new Date(value).getTime()))return '—';const seconds=Math.max(0,Math.round((Date.now()-new Date(value).getTime())/1000));return seconds<60?`${seconds}s ago`:`${Math.floor(seconds/60)}m ago`;}
function showNotice(text){$('notice').hidden=!text;$('notice').textContent=text||'';}
function agents(items=[]){
 $('agents').innerHTML=Object.entries(roles).map(([name,[icon,role]])=>{const a=items.find(x=>x.persona===name)||{state:'waiting'};return `<button class="agent-card ${esc(a.state)}" data-persona="${name}" aria-label="Inspect ${name} agent"><div class="agent-top"><span class="agent-icon">${icon}</span><span class="agent-state">${esc(a.state)}</span></div><h3>${name}</h3><p>${role}</p><div class="agent-meta"><span>${a.latency_ms!=null?(a.latency_ms/1000).toFixed(1)+'s last call':'Awaiting call'}</span><b>${a.generation!=null?'GEN '+String(a.generation).padStart(2,'0'):'—'} ↗</b></div></button>`}).join('');
 document.querySelectorAll('[data-persona]').forEach(b=>b.addEventListener('click',()=>openAgent(b.dataset.persona)));
}
function openAgent(name){selectedPersona=name;const a=snapshot?.agents?.find(x=>x.persona===name);$('detail-title').textContent=name;$('detail-state').textContent=`${a?.state||'waiting'} · ${a?.tokens!=null?fmt(a.tokens)+' tokens in last recorded attempt':'No usage recorded'}`;$('detail-rationale').textContent=proposalFor(name)?.rationale||'No saved proposal yet. A rationale appears after the orchestrator persists this agent’s result.';$('detail-changes').innerHTML=changesHTML(proposalFor(name));if(!$('detail').open)$('detail').showModal();}
function clearFrame(){if(blobURL){URL.revokeObjectURL(blobURL);blobURL=null;}$('frame').hidden=true;$('frame-empty').hidden=false;$('frame').removeAttribute('src');}
function cancelSnapshot(){requestVersion++;snapshotRequest?.abort();snapshotRequest=null;loading=false;$('refresh').disabled=false;}
function cancelListing(){listVersion++;listRequest?.abort();listRequest=null;listing=false;}
function currentRequest(version,query){return version===requestVersion&&query.toString()===params().toString();}
let rawView=false;
async function updateFrame(data,query,version,signal){
 if(!data.workers?.some(w=>w.state==='running'&&w.stream))$('stream-view').hidden=true;
 const active=data.workers?.find(w=>w.state==='running');
 // A worker started with --stream (bot/livestream.py) advertises a local MJPEG URL in its heartbeat:
 // show the HUD live (~10-15 fps) instead of the 2 s snapshots.
 const stream=data.mode!=='demo'&&typeof active?.stream==='string'&&/^http:\/\/127\.0\.0\.1:\d+\//.test(active.stream)?active.stream:null;
 if(stream){
  if(blobURL){URL.revokeObjectURL(blobURL);blobURL=null;}
  const want=stream+(rawView?'?view=raw':'');
  if($('frame').getAttribute('src')!==want)$('frame').src=want;
  $('stream-view').hidden=false;$('stream-view').textContent=rawView?'RAW':'HUD';
  $('frame').hidden=false;$('frame-empty').hidden=true;$('frame-label').textContent=rawView?'LIVE STREAM · RAW GAME':'LIVE STREAM · HUD';return;
 }
 if(data.mode==='demo'){clearFrame();$('frame').src='/demo-frame.jpg';$('frame').hidden=false;$('frame-empty').hidden=true;$('frame-label').textContent='SAMPLE CAPTURE';return;}
 $('frame-label').textContent='NO LIVE FRAME';
 if(!active){clearFrame();return;}
 const frameQuery=new URLSearchParams(query);frameQuery.set('worker',active.identity||active.worker||'');
 if(active.job_id)frameQuery.set('job',active.job_id);
 try{
  const r=await fetch('/api/frame?'+frameQuery,{signal});
  if(!currentRequest(version,query))return;
  if(!r.ok||r.status===204){clearFrame();return;}
  const b=await r.blob();if(!currentRequest(version,query))return;
  clearFrame();blobURL=URL.createObjectURL(b);$('frame').src=blobURL;$('frame').hidden=false;$('frame-empty').hidden=true;$('frame-label').textContent='LIVE · 2s CAPTURE';
 }catch{if(currentRequest(version,query)){clearFrame();$('frame-label').textContent='FRAME UNAVAILABLE';}}
}
function render(data){snapshot=data;const connected=Boolean(data.connected),demo=data.mode==='demo';
 $('connection').className='pill '+(demo?'demo':connected?'connected':'');$('connection').innerHTML=`<i></i>${paused?'UPDATES PAUSED':demo?'DEMO PREVIEW':connected?'BACKEND CONNECTED':'AWAITING BACKEND'}`;
 $('control-box').hidden=demo||!controlEnabled;
 showNotice(data.error||(demo?'DEMO PREVIEW — synthetic data and a sample game frame. No game, database writes or model calls are running.':''));
 const e=data.experiment||{};$('experiment-name').textContent=e.id||'No experiment';$('map-name').textContent=e.map?e.map.toUpperCase():'Waiting for data';$('generation').textContent=e.generation??'—';$('runs-count').textContent=fmt(data.counts?.runs);
 const worker=data.workers?.find(w=>w.state==='running')||data.workers?.[0],running=worker?.state==='running';
 const active=data.agents?.filter(a=>['running','retrying'].includes(a.state)).length||0,saved=data.agents?.filter(a=>a.proposal||a.rationale).length||0;$('agent-count').textContent=active?active+' / 4 THINKING':saved?saved+' / 4 SAVED':'AWAITING PROPOSALS';
 $('worker-name').textContent=worker?.worker||worker?.identity||'NO WORKER CONNECTED';$('frame-age').textContent=worker?.updated_at?ageLabel(worker.updated_at):'—';$('fps').textContent=fmt(running?worker?.fps:null);$('targets').textContent=fmt(running?worker?.detections:null);$('points').textContent=fmt(worker?.points);$('worker-state').textContent=worker?.state||'Waiting';$('action').textContent=running?worker.action||'PLAYING':'STANDBY';$('elapsed').textContent=worker?.elapsed_s!=null?Math.round(worker.elapsed_s)+'s elapsed':'— elapsed';
 agents(data.agents);if($('detail').open&&selectedPersona)openAgent(selectedPersona);
 $('model-name').textContent=data.model?.split('/').pop()||'No model recorded';const stamp=data.updated_at||receivedAt;$('last-refresh').textContent=(stamp?'Snapshot '+new Date(stamp).toLocaleTimeString():'No snapshot received')+' · '+(paused?'updates paused':demo?'demo data':'updates every 2s');
 renderGraph();renderRecords();renderRecordings(data,params());
}
async function refresh(){
 if(loading||listing)return;
 const version=++requestVersion,query=params(),controller=new AbortController();snapshotRequest=controller;
 loading=true;$('refresh').disabled=true;
 const timeout=setTimeout(()=>controller.abort(),12000);
 try{
  const response=await fetch('/api/snapshot?'+query,{signal:controller.signal});const data=await response.json();
  if(!currentRequest(version,query))return;
  receivedAt=Date.now();render(data);await updateFrame(data,query,version,controller.signal);
 }catch{
  if(currentRequest(version,query)){receivedAt=null;render({connected:false,mode:query.get('mode'),error:'Dashboard connection lost or timed out. Check that the local server is running.'});clearFrame();$('frame-label').textContent='NO LIVE FRAME';}
 }finally{
  clearTimeout(timeout);if(version===requestVersion){loading=false;snapshotRequest=null;$('refresh').disabled=false;}
 }
}
async function loadExperiments(){
 const version=++listVersion,mode=$('mode').value,previous=$('experiment').value;
 listRequest?.abort();const controller=new AbortController();listRequest=controller;listing=true;
 const timeout=setTimeout(()=>controller.abort(),12000);
 try{
  const r=await fetch('/api/experiments?mode='+encodeURIComponent(mode),{signal:controller.signal});
  if(!r.ok)throw new Error('Experiments unavailable');const d=await r.json();
  if(version!==listVersion||mode!==$('mode').value)return false;
  $('experiment').innerHTML=(d.experiments||[]).map(e=>`<option value="${esc(e.id)}">${esc(e.id)}</option>`).join('')||'<option value="">No experiments found</option>';
  if([...$('experiment').options].some(o=>o.value===previous))$('experiment').value=previous;
  return true;
 }catch{
  if(version!==listVersion||mode!==$('mode').value)return false;
  $('experiment').innerHTML='<option value="">Database unavailable</option>';return true;
 }finally{clearTimeout(timeout);if(version===listVersion){listing=false;listRequest=null;}}
}
function clearSelection(){cancelSnapshot();cancelListing();clearFrame();receivedAt=null;$('frame-label').textContent='NO LIVE FRAME';render({connected:false,mode:$('mode').value,error:'Loading selected data source…'});}
$('pause').addEventListener('click',()=>{paused=!paused;if(paused){cancelSnapshot();cancelListing();}$('pause').textContent=paused?'▶ Resume updates':'Ⅱ Pause updates';if(snapshot)render(snapshot);if(!paused)reloadSelection();});
async function reloadSelection(){cancelSnapshot();if(await loadExperiments())await refresh();}
$('refresh').addEventListener('click',reloadSelection);
$('mode').addEventListener('change',async()=>{clearSelection();$('experiment').innerHTML='<option value="">Loading experiments…</option>';const url=new URL(location.href);url.searchParams.set('mode',$('mode').value);history.replaceState(null,'',url.pathname+url.search+url.hash);await reloadSelection();});
$('experiment').addEventListener('change',()=>{clearSelection();refresh();});
$('close-detail').addEventListener('click',()=>$('detail').close());
setInterval(()=>{if(!paused&&!listing)refresh();},2000);
(async()=>{
 agents();listing=true;
 const requested=new URLSearchParams(location.search).get('mode');
 if(['live','demo'].includes(requested))$('mode').value=requested;
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
 try{const c=await(await fetch('/api/config',{signal:controller.signal})).json();if(listVersion===0&&!['live','demo'].includes(requested))$('mode').value=c.default_mode==='demo'?'demo':'live';if(c.control)enableControl();}catch{}finally{clearTimeout(timeout);}
 // A user's selection or pause takes precedence over a delayed startup response.
 if(listVersion!==0||paused)return;
 listing=false;if(await loadExperiments()){if(!paused)await refresh();}
})();

// Three conceptual layers. Both arrowheads show the implemented return path,
// not measured network traffic. The orchestrator performs the MongoDB reads/writes.
const flowNodes=[
 {id:'vision',title:'Vision model',sub:'See targets · control the game',lane:'01 / VISION & GAME',x:170,y:55,w:420,description:'The vision model detects targets while the controller aims, fires and moves. At the end of a round, the worker saves the score, survival time and diagnostics in MongoDB.',reads:'Game frames and the saved settings for the current evaluation.',writes:'Game actions, live telemetry and completed round results.',source:'vision/pipeline.py · bot/controller.py · bot/worker.py'},
 {id:'mongo',title:'MongoDB memory',sub:'Results in · next strategy out',lane:'02 / SHARED MEMORY',x:170,y:230,w:420,description:'MongoDB holds the evidence from each round. The orchestrator reads it to rank parent strategies, asks the four agents for new ideas, then saves validated settings and queues their next evaluations.',reads:'Completed runs and validated agent proposals.',writes:'Parent settings and diagnostics for the agents; jobs and settings for the worker.',source:'mongo_orchestrator.py · generation.py'},
 ...Object.keys(roles).map((name,i)=>({id:name,title:name,sub:{Bold:'Bigger changes',Tuner:'Fine adjustments',Defender:'Survive longer',Explorer:'Try new tactics'}[name],lane:'03 / LUMINAL AGENTS',x:26+i*185,y:467,w:153,description:roles[name][1]+' This agent receives a parent strategy and its game diagnostics, then proposes settings and explains why they may help the next round.',reads:'Assigned parent settings, round diagnostics and generation history.',writes:'A full set of validated bot settings and a rationale, or an explicitly marked fallback.',source:'generation.py · luminal_client.py · strategy.py'}))
];
const flowEdges=[
 {id:'round-trip',from:'vision',to:'mongo',path:'M380 145 V230',description:'After a round, results go to MongoDB. Before the next round, the worker claims a queued evaluation and loads its saved settings.'},
 ...Object.keys(roles).map((name,i)=>({id:'agent-'+name,from:'mongo',to:name,path:`M${230+i*100} 320 V${352+i*15} H${102.5+i*185} V467`,description:`The orchestrator sends parent settings and diagnostics to ${name}. Its validated proposal returns to MongoDB, ready to test in the game.`}))
];
let flowSelection={kind:'node',id:'vision'};
function flowNode(id){return id==='agents'?{id:'agents',title:'Four Luminal agents',lane:'03 / FOUR PERSPECTIVES',description:'Four prompt personas examine the round from different angles. The orchestrator collects their proposals, validates the settings, and saves them in MongoDB for evaluation.',reads:'Ranked parent strategies, game diagnostics and generation history.',writes:'Four child strategies with explanations, call records and next evaluation jobs.',source:'generation.py · luminal_client.py · mongo_orchestrator.py'}:flowNodes.find(n=>n.id===id);}
function flowWorker(){return snapshot?.workers?.find(w=>w.state==='running')||snapshot?.workers?.[0];}
function flowStatus(id){
 if(!snapshot?.connected||!snapshot?.experiment)return 'no data';
 if(id==='vision')return flowWorker()?.state||'no live worker';
 if(id==='agents')return snapshot.agents?.some(a=>['running','retrying'].includes(a.state))?'generating':'saved context';
 if(id==='mongo')return snapshot.mode==='demo'?'demo memory':'connected';
 return snapshot.agents?.find(a=>a.persona===id)?.state||'waiting';
}
function proposalFor(name){
 const a=snapshot?.agents?.find(a=>a.persona===name);
 if(a?.proposal)return a.proposal;
 return snapshot?.records?.strategies?.find(s=>s.persona===name)||null;
}
function changesFor(strategy){
 if(!strategy)return [];
 const parent=strategy.parent_params||snapshot?.records?.strategies?.find(s=>String(s._id)===String(strategy.parent_ids?.[0]))?.params;
 const fields=parent?Object.keys(strategy.params||{}).filter(k=>strategy.params[k]!==parent[k]):strategy.changed_params||[];
 return fields.map(key=>({key,before:parent?.[key],after:strategy.params?.[key]}));
}
function changesHTML(strategy){
 const changes=changesFor(strategy);
 if(!changes.length)return '<p class="record-footnote">'+(strategy?.parent_ids?.length?'No parameter differences are available for this saved proposal.':'Starting settings · no parent comparison.')+'</p>';
 return '<table class="change-table"><thead><tr><th>Setting</th><th>Parent</th><th>Proposed</th></tr></thead><tbody>'+changes.map(c=>`<tr><td>${esc(parameterLabels[c.key]||c.key)}</td><td>${esc(c.before==null?'Not available':recordValue(c.before))}</td><td>${esc(recordValue(c.after))}</td></tr>`).join('')+'</tbody></table>';
}
function flowSelect(kind,id){flowSelection={kind,id};renderGraph();}
function stageLines(id){
 const s=typeof controlState==='undefined'?{}:controlState;
 if(snapshot?.mode==='demo'||!snapshot?.experiment||s.experiment!==snapshot.experiment.id||s.external)return [];
 return (s.log_tail||[]).filter(line=>id==='vision'?/detector|capture|calib|gpu|points|survival|job \d|game|fps|SCAN|ENGAGE|RETREAT/i.test(line):id==='mongo'?/mongo|stored|queue|generation.*children/i.test(line):/luminal|generation.*children|propos/i.test(line)).slice(-12);
}
function renderGraph(){
 const edge=flowSelection.kind==='edge'?flowEdges.find(e=>e.id===flowSelection.id):null;
 const id=edge?(edge.to==='mongo'?'mongo':edge.to):flowSelection.id,node=flowNode(id);
 document.querySelectorAll('[data-layer]').forEach(g=>g.setAttribute('aria-pressed',String(g.dataset.layer===id)));
 document.querySelectorAll('[data-node]').forEach(g=>{const state=flowStatus(g.dataset.node);g.querySelector('.graph-node-state').textContent=state.toUpperCase();g.classList.toggle('selected',g.dataset.node===id);g.setAttribute('aria-pressed',String(g.dataset.node===id));});
 document.querySelectorAll('[data-edge]').forEach(g=>{const e=flowEdges.find(e=>e.id===g.dataset.edge);g.classList.toggle('selected',edge?.id===e.id);g.classList.toggle('related',e.from===id||e.to===id||(id==='agents'&&e.from==='mongo'));g.setAttribute('aria-pressed',String(edge?.id===e.id));});
 const ready=snapshot?.connected&&snapshot?.experiment,demo=snapshot?.mode==='demo';
 $('graph-source').textContent=(demo?'DEMO DATA':ready?'LIVE BACKEND':'WAITING FOR DATA')+(paused?' · UPDATES PAUSED':'');
 $('inspect-category').textContent=node.lane;$('inspect-title').textContent=edge?`${flowNode(edge.from).title} ↔ ${node.title}`:node.title;
 $('inspect-state').textContent=flowStatus(id).toUpperCase();$('inspect-description').textContent=edge?.description||node.description;
 const w=flowWorker(),run=snapshot?.latest_run,a=snapshot?.agents?.find(a=>a.persona===id),proposal=roles[id]?proposalFor(id):null;
 const metrics=id==='agents'?[['Personas',4],['Saved proposals',snapshot?.agents?.filter(a=>a.proposal).length||0],['Model calls',fmt(snapshot?.counts?.llm_calls)]]:id==='vision'?[['Worker',w?.state||'Offline'],['Targets',w?.state==='running'?fmt(w.detections):'—'],['Last survival',run?.survival_s!=null?fmt(run.survival_s)+'s':'—']]:id==='mongo'?[['Saved runs',fmt(snapshot?.counts?.runs)],['Strategies',fmt(snapshot?.counts?.playbooks)],['Queued',fmt(snapshot?.jobs?.queued??(ready?0:null))]]:[['Proposal gen.',proposal?.generation??'—'],['Last call',a?.latency_ms!=null?fmt(a.latency_ms/1000)+'s':'—'],['Evaluations',proposal?.evaluations??'—']];
 $('inspect-evidence').innerHTML=metrics.map(([k,v])=>`<div class="evidence-item"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
 if(id==='agents'){
  $('inspect-insight').innerHTML=Object.keys(roles).map(name=>{const p=proposalFor(name);return `<button class="layer-agent-insight" data-insight-agent="${name}"><strong>${name} ↗</strong><span>${esc(p?.rationale||roles[name][1])}</span></button>`;}).join('');
  document.querySelectorAll('[data-insight-agent]').forEach(b=>b.addEventListener('click',()=>flowSelect('node',b.dataset.insightAgent)));
 }else if(roles[id]){
  const fallback=proposal?.fallback;
  $('inspect-insight').innerHTML=`<div class="insight-callout"><h3>${fallback?'Fallback strategy':proposal?'Suggested for the next round':'Waiting for a proposal'}</h3><p>${esc(proposal?.rationale||'This agent has not saved a proposal for this experiment yet.')}</p></div>${proposal?changesHTML(proposal):''}<p class="record-footnote">${proposal?`Saved generation ${esc(proposal.generation??'—')}. ${fallback?'Source: '+esc(proposal.fallback_source||'fallback')+'. ':''}`:''}${proposal?.evaluations?`${fmt(proposal.mean_fitness)} mean fitness across ${fmt(proposal.evaluations)} recent evaluations. A proposal alone does not establish improvement.`:'These are proposed changes; evaluate them in the game to measure their effect.'}</p>`;
 }else if(id==='vision'){
  const summary=run?.diagnostics?.last_5s_summary;
  $('inspect-insight').innerHTML=`<div class="insight-callout"><h3>What feeds the next strategy</h3><p>${esc(summary|| (run?`${fmt(run.points)} points and ${fmt(run.survival_s)} seconds survived in the latest saved round. Its diagnostics help the agents identify what to change.`:'After a round completes, aim accuracy, target visibility and survival diagnostics give the agents evidence to work with.'))}</p></div>`;
 }else{
  $('inspect-insight').innerHTML=`<div class="insight-callout"><h3>Memory makes the loop possible</h3><p>${ready?`${fmt(snapshot.counts?.runs)} saved evaluations inform parent selection. ${fmt(snapshot.jobs?.queued||0)} evaluations are queued and ${fmt(snapshot.jobs?.running||0)} are running.`:'Saved results, strategies and queued evaluations appear when an experiment is selected.'}</p><p>The orchestrator validates all four proposals before saving a new generation. The worker then tests those settings.</p></div>`;
 }
 const lines=stageLines(id);$('console-source').textContent=demo?'DEMO · NO PROCESS':lines.length?'CAPTURED STDOUT':'NO CAPTURED OUTPUT';
 $('inspect-console').textContent=lines.join('\n')||'No matching console lines captured for this stage.';
 $('console-note').textContent=demo?'Demo mode uses synthetic records. No game process is running.':(roles[id]||id==='agents')?'Generation-level output from the local worker. Per-agent outcomes are shown in saved backend events below.':'Actual stdout from a worker launched by this dashboard for the selected experiment. Remote or externally started workers may not provide console output.';
 const events=(snapshot?.events||[]).filter(e=>id==='vision'?e.kind==='run':id==='mongo'?e.kind!=='llm':e.kind==='llm'&&(id==='agents'||e.title?.startsWith(id)));
 $('inspect-events').textContent=(demo?'[SYNTHETIC DEMO EVENTS]\n':'[SAVED RECORDS · NOT STDOUT]\n')+(events.slice(0,8).map(e=>`${e.at||'Time not recorded'}  ${e.title}\n${e.detail} · ${e.collection}`).join('\n\n')||'No matching saved events.');
 $('inspect-contract').innerHTML=[['Receives',node.reads],['Produces',node.writes],['Code',node.source]].map(([k,v])=>`<div class="contract-row"><strong>${esc(k)}</strong><span>${esc(v)}</span></div>`).join('');
 $('inspect-payload').textContent=JSON.stringify(id==='vision'?w||{}:id==='mongo'?snapshot?.counts||{}:id==='agents'?snapshot?.agents||[]:proposal||a||{},null,2);
}
function showFlowTab(view,updateHash=true){
 for(const name of ['overview','flow','records']){const active=name===view;$('view-'+name).hidden=!active;$('tab-'+name).setAttribute('aria-selected',String(active));$('tab-'+name).tabIndex=active?0:-1;}
 if(updateHash)history.replaceState(null,'',location.pathname+location.search+(view==='overview'?'':'#'+view));
}
function initFlow(){
 const svg=$('backend-graph');
 svg.innerHTML=`<defs>${[['muted','#78959e'],['active','#c4ed91']].map(([name,color])=>`<marker id="arrow-${name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="${color}"/></marker>`).join('')}</defs>`+
 [[10,0,740,163,'01 / VISION & GAME'],[10,175,740,162,'02 / MONGODB MEMORY'],[10,422,740,162,'03 / FOUR LUMINAL AGENTS']].map(([x,y,w,h,label])=>`<rect class="graph-layer" data-layer="${y===0?'vision':y===175?'mongo':'agents'}" role="button" tabindex="0" aria-label="Inspect ${label}" x="${x}" y="${y}" width="${w}" height="${h}" rx="15"/><text class="layer-title" x="${x+20}" y="${y===422?580:y+28}">${label}</text>`).join('')+
 flowEdges.map(e=>`<g class="graph-edge" data-edge="${e.id}" role="button" tabindex="0" aria-label="Inspect ${esc(flowNode(e.from).title)} and ${esc(flowNode(e.to).title)} data exchange"><path class="edge-hit" d="${e.path}"/><path class="edge-line" d="${e.path}"/></g>`).join('')+
 flowNodes.map(n=>`<g class="graph-node" data-node="${n.id}" transform="translate(${n.x},${n.y})" role="button" tabindex="0" aria-label="Inspect ${esc(n.title)}"><rect width="${n.w}" height="90" rx="10"/><text class="graph-node-title" x="15" y="29">${n.title}</text><text class="graph-node-subtitle" x="15" y="49">${n.sub}</text><circle class="node-indicator" cx="19" cy="71" r="3"/><text class="graph-node-state" x="29" y="74">WAITING</text></g>`).join('');
 svg.querySelectorAll('[data-layer]').forEach(g=>{g.addEventListener('click',()=>flowSelect('node',g.dataset.layer));g.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();flowSelect('node',g.dataset.layer);}});});
 for(const kind of ['node','edge'])svg.querySelectorAll(`[data-${kind}]`).forEach(g=>{g.addEventListener('click',()=>flowSelect(kind,g.dataset[kind]));g.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();flowSelect(kind,g.dataset[kind]);}});});
 document.querySelectorAll('[data-view]').forEach(b=>{b.addEventListener('click',()=>showFlowTab(b.dataset.view));b.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const views=['overview','flow','records'],index=views.indexOf(b.dataset.view),view=e.key==='Home'?'overview':e.key==='End'?'records':views[(index+(e.key==='ArrowRight'?1:2))%3];showFlowTab(view);$('tab-'+view).focus();}});});
 window.addEventListener('hashchange',()=>showFlowTab(['flow','records'].includes(location.hash.slice(1))?location.hash.slice(1):'overview',false));
 showFlowTab(['flow','records'].includes(location.hash.slice(1))?location.hash.slice(1):'overview',false);
}
initFlow();

// Saved records share the experiment-scoped snapshot and never issue writes.
let recordScope='';
const parameterLabels={det_conf:'Detection confidence',aim_gain:'Aim gain',aim_max_step:'Max aim step',aim_y_offset:'Aim height offset',fire_radius_px:'Fire radius (px)',burst_ms:'Burst duration (ms)',retreat_box_h:'Retreat box height',strafe_mode:'Strafe mode',strafe_period_s:'Strafe period (s)',reload_when_clear_at:'Reload threshold',scan_turn_px:'Scan turn (px)',target_rule:'Target rule'};
const diagnosticLabels={overshoot_rate:'Aim overshoot',time_no_target_pct:'Time without a target',det_count_avg:'Average detections',loop_fps:'Average loop FPS',shots_fired:'Shots fired',hits_proxy:'Score increase events',close_calls:'Close calls',retreat_frames:'Retreat frames',stuck_events:'Stuck events',pointer_lock_losses:'Pointer lock losses',death_time_s:'Death time (s)',kills_est:'Estimated kills',headshot_kills_est:'Estimated headshot kills',body_kills_est:'Estimated body kills',melee_kills_est:'Estimated melee kills',non_lethal_hits_est:'Estimated non-lethal hits',shots_without_target:'Shots without a target',avg_frames_to_lock:'Frames to target lock'};
function recordTime(value){const d=new Date(value);return value&&Number.isFinite(d.getTime())?d.toLocaleString():'Time not recorded';}
function shortId(value){const text=String(value||'—');return text.length>25?text.slice(0,12)+'…'+text.slice(-7):text;}
function recordValue(value){return value==null?'—':typeof value==='number'?String(value):typeof value==='object'?JSON.stringify(value):String(value);}
function runId(run){return String(run._id||run.job_id||'');}
function syncRecordSelect(id,rows,key,label,empty){
 const select=$(id),previous=select.value;
 const html=rows.map(r=>`<option value="${esc(key(r))}">${esc(label(r))}</option>`).join('')||`<option value="">${esc(empty)}</option>`;
 if(select.innerHTML!==html){select.innerHTML=html;if(rows.some(r=>key(r)===previous))select.value=previous;}
 if(!rows.some(r=>key(r)===select.value))select.value=rows.length?key(rows[0]):'';
 return rows.find(r=>key(r)===select.value)||null;
}
function strategyLink(id,records,label){
 if(!id)return '<span>Not recorded</span>';
 const found=records.strategies?.some(s=>String(s._id)===String(id));
 return found?`<button class="record-link" data-record-strategy="${esc(id)}" title="${esc(id)}">${esc(label||shortId(id))} ↗</button>`:`<span class="record-meta" title="${esc(id)}">${esc(label||shortId(id))} · outside recent strategy window</span>`;
}
function renderRecords(){
 const data=snapshot||{},records=data.records||{},runs=records.runs||[],strategies=records.strategies||[],jobs=records.jobs||[],calls=records.calls||[];
 const scope=(data.mode||'live')+':'+(data.experiment?.id||'');
 if(scope!==recordScope){$('record-run').value='';$('record-strategy').value='';recordScope=scope;}
 const demo=data.mode==='demo',ready=data.connected&&data.experiment;
 $('records-source').textContent=(demo?'DEMO RECORDS':ready?'MONGODB · READ ONLY':'NO EXPERIMENT DATA')+(paused?' · PAUSED':'');
 $('records-meta').textContent=ready?`${data.experiment.id} · ${data.experiment.map} · Generation ${data.experiment.generation}${data.experiment.started_at?' · Started '+recordTime(data.experiment.started_at):''}`:'Select a connected experiment to inspect its saved records.';
 $('record-total-runs').textContent=fmt(data.counts?.runs);$('record-total-strategies').textContent=fmt(data.counts?.playbooks);$('record-queue-summary').textContent=`${fmt(data.jobs?.queued||0)} queued · ${fmt(data.jobs?.running||0)} running`;
 const run=syncRecordSelect('record-run',runs,runId,r=>`G${r.generation??'—'} · ${fmt(r.fitness)} fitness · ${shortId(runId(r))}`,'No saved runs');
 $('saved-run-empty').hidden=Boolean(run);$('saved-run-content').hidden=!run;
 if(run){
  $('saved-points').textContent=fmt(run.points);$('saved-survival').textContent=fmt(run.survival_s)+'s';$('saved-fitness').textContent=fmt(run.fitness);
  $('saved-run-meta').textContent=`${run.worker||'Worker not recorded'} · ${recordTime(run.completed_at)} · ${runId(run)}`;
  $('saved-run-strategy').innerHTML=strategyLink(run.strategy_id,records,'Inspect run strategy');
  const diagnostics=run.diagnostics||{};
  $('saved-diagnostics').innerHTML=['time_no_target_pct','det_count_avg','loop_fps','overshoot_rate','shots_fired','hits_proxy'].map(key=>{
   const raw=diagnostics[key];let value=recordValue(raw);
   if(raw!=null&&typeof raw==='number'){if(key==='overshoot_rate')value=fmt(raw*100)+'%';else if(key==='time_no_target_pct')value=fmt(raw)+'%';}
   return `<div class="diagnostic"><small>${esc(diagnosticLabels[key])}</small><strong>${esc(value)}</strong></div>`;
  }).join('');
  $('saved-summary').textContent=diagnostics.last_5s_summary||'No final-seconds summary was saved for this run.';
  $('saved-all-diagnostics').innerHTML=Object.entries(diagnostics).filter(([k])=>k!=='last_5s_summary').map(([key,value])=>`<div><dt title="${esc(key)}">${esc(diagnosticLabels[key]||key.replaceAll('_',' '))}</dt><dd>${esc(recordValue(value))}${key==='overshoot_rate'?' (fraction)':key==='time_no_target_pct'?'%':''}</dd></div>`).join('')||'<div><dt>No diagnostics saved</dt></div>';
 }
 const strategy=syncRecordSelect('record-strategy',strategies,s=>String(s._id),s=>`${s.persona||'Strategy'} · G${s.generation??'—'} · ${shortId(s._id)}`,'No saved strategies');
 $('saved-strategy-empty').hidden=Boolean(strategy);$('saved-strategy-content').hidden=!strategy;
 if(strategy){
  $('saved-strategy-source').textContent=strategy.fallback?'FALLBACK · '+(strategy.fallback_source||'source not recorded').replaceAll('_',' ').toUpperCase():strategy.persona==='Baseline'?'HAND-WRITTEN BASELINE':strategy.model?'MODEL PROPOSAL':'SAVED STRATEGY';
  $('saved-strategy-score').textContent=strategy.evaluations?`${fmt(strategy.mean_fitness)} mean fitness · ${fmt(strategy.evaluations)} evaluation${strategy.evaluations===1?'':'s'}`:'Not evaluated in the recent run sample';
  $('saved-strategy-id').textContent=String(strategy._id);$('saved-changes').innerHTML=changesHTML(strategy);$('saved-rationale').textContent=strategy.rationale||'No rationale saved.';
  $('saved-params').innerHTML=Object.entries(strategy.params||{}).map(([key,value])=>`<div class="strategy-param ${strategy.changed_params?.includes(key)?'changed':''}"><span title="${esc(key)}">${esc(parameterLabels[key]||key)}</span><strong>${esc(recordValue(value))}</strong></div>`).join('')||'<p class="record-meta">No settings saved.</p>';
  $('saved-parents').innerHTML=strategy.parent_ids?.length?strategy.parent_ids.map(id=>strategyLink(id,records)).join(''):'<span>No parents recorded · baseline or root strategy</span>';
 }
 const statuses=data.jobs||{},unfinished=Object.entries(statuses).filter(([status])=>status!=='done').reduce((n,[,count])=>n+count,0),total=Object.values(statuses).reduce((n,count)=>n+count,0);
 $('queue-readiness').textContent=!ready?'Queue data is unavailable.':!total?'No evaluation jobs recorded yet.':unfinished?`${unfinished} evaluation${unfinished===1?' remains':'s remain'} before the next generation can be proposed (${statuses.queued||0} queued, ${statuses.running||0} running).`:(data.counts?.runs||0)>0?'All recorded evaluations are complete. The orchestrator can propose the next generation.':'Jobs are complete; waiting for saved run results.';
 const jobFilter=$('record-job-filter').value,visibleJobs=jobs.filter(j=>jobFilter==='all'||j.status===jobFilter);
 $('saved-jobs').innerHTML=visibleJobs.map(job=>{
  const savedRun=runs.find(r=>r.job_id===job.job_id),at=job.completed_at||job.claimed_at||job.created_at;
  const jobLabel=savedRun?`<button class="record-link" data-record-run="${esc(runId(savedRun))}" title="${esc(job.job_id)}">${esc(shortId(job.job_id))} ↗</button>`:`<span title="${esc(job.job_id)}">${esc(shortId(job.job_id))}</span>`;
  return `<tr><td><span class="record-state ${esc(job.status)}">${esc(job.status||'unknown')}</span></td><td>${jobLabel}<small>${esc((job.kind||'evaluation').replaceAll('_',' '))}</small></td><td>${esc(job.generation??'—')}</td><td>${strategyLink(job.strategy_id,records)}</td><td>${esc(job.worker||'Unclaimed')}</td><td title="${esc(recordTime(at))}">${esc(ageLabel(at))}</td></tr>`;
 }).join('')||'<tr><td colspan="6" class="empty">No matching jobs in the recent records.</td></tr>';
 $('saved-jobs-note').textContent=`Showing ${visibleJobs.length} matching jobs from the latest ${jobs.length} returned (maximum 50). Queue totals above cover the entire experiment. Select a completed job to inspect its saved run.`;
 $('saved-calls-count').textContent=`${data.counts?.llm_calls??0} RECORDED`;
 $('saved-calls').innerHTML=calls.map(call=>`<tr><td>${esc(call.persona||'Unknown')}<small>${esc(call.model||'Model not recorded')}</small></td><td>${esc(call.generation??'—')}</td><td><span class="record-state ${call.ok?'validated':'failed'}">${call.ok?'Validated':'Failed'}</span>${call.error_type?`<small>${esc(call.error_type)}</small>`:''}</td><td>${esc(call.attempt??'—')}</td><td>${call.latency_ms!=null?fmt(call.latency_ms/1000)+'s':'—'}</td><td>${fmt(call.tokens?.total_tokens)}</td><td title="${esc(recordTime(call.created_at))}">${esc(ageLabel(call.created_at))}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">No Luminal calls saved for this experiment yet.</td></tr>';
}
$('record-run').addEventListener('change',renderRecords);$('record-strategy').addEventListener('change',renderRecords);$('record-job-filter').addEventListener('change',renderRecords);
$('view-records').addEventListener('click',event=>{
 const strategy=event.target.closest('[data-record-strategy]'),run=event.target.closest('[data-record-run]');
 if(strategy){$('record-strategy').value=strategy.dataset.recordStrategy;renderRecords();$('record-strategy').focus({preventScroll:true});}
 if(run){$('record-run').value=run.dataset.recordRun;renderRecords();$('record-run').focus();$('record-run').scrollIntoView?.({block:'center',behavior:'smooth'});}
});
// Episode recordings: MP4s the bot publisher stores in GridFS ('footage'), streamed by /api/video with Range support.
let recordingKey='',recordingJob='';
function renderRecordings(data,query){
 const items=data.footage||[],personas={};(data.records?.strategies||[]).forEach(s=>personas[String(s._id)]=s.persona);
 $('recordings-count').textContent=`${items.length} SAVED`;
 const key=(query.get('mode')||'live')+':'+(query.get('experiment')||'')+'|'+items.map(f=>f.job_id).join(',');
 if(key===recordingKey)return;
 const sameExperiment=recordingKey.split('|')[0]===key.split('|')[0];recordingKey=key;
 if(!sameExperiment||!items.some(f=>f.job_id===recordingJob)){ // a different experiment: never keep its old video
  recordingJob='';const v=$('recording');v.pause();v.removeAttribute('src');v.load();v.hidden=true;$('recording-empty').hidden=false;
  $('recording-caption').textContent='Recordings appear when a worker publishes footage for this experiment.';}
 if(!items.length){$('recordings-list').innerHTML='<p class="empty">No recordings for this experiment yet.</p>';return;}
 $('recordings-list').innerHTML=items.map(f=>{const who=personas[String(f.strategy_id)]||String(f.strategy_id||'').split(':').pop();
  return `<button class="recording-item${f.job_id===recordingJob?' selected':''}" data-job="${esc(f.job_id)}"><span><b>${esc(who)}</b><small>gen ${esc(f.generation??0)} · ${esc(String(f.job_id).slice(0,8))}</small></span><span>${fmt(f.points)} pts · ${esc(f.kills??'—')} kills</span><span>${fmt(f.survival_s)}s · fit ${fmt(f.fitness)}</span></button>`;}).join('');
 document.querySelectorAll('.recording-item').forEach(b=>b.addEventListener('click',()=>{
  recordingJob=b.dataset.job;document.querySelectorAll('.recording-item').forEach(x=>x.classList.toggle('selected',x===b));
  const q=new URLSearchParams({mode:query.get('mode')||'live',experiment:query.get('experiment')||'',job:recordingJob});
  const v=$('recording');v.src='/api/video?'+q;v.hidden=false;$('recording-empty').hidden=true;v.play().catch(()=>{});
  $('recording-caption').textContent=`Job ${recordingJob} · annotated HUD view recorded by the vision worker`;}));
}

// Fullscreen game view: the whole viewport (stream + overlays) fills the screen; Esc exits.
$('fullscreen')?.addEventListener('click',()=>{const v=$('viewport');if(document.fullscreenElement)document.exitFullscreen();else v?.requestFullscreen?.();});
$('stream-view')?.addEventListener('click',()=>{rawView=!rawView;const f=$('frame'),s=f.getAttribute('src')||'';if(s.includes('/stream.mjpg')){f.src=s.split('?')[0]+(rawView?'?view=raw':'');}$('stream-view').textContent=rawView?'RAW':'HUD';$('frame-label').textContent=rawView?'LIVE STREAM · RAW GAME':'LIVE STREAM · HUD';});

// ---- Local game control (dashboard/control.py): only shown when the server allows it ----
let controlTimer=null;
async function controlCall(path,body){
 const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Control':'1'},body:JSON.stringify(body||{})});
 const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||('HTTP '+r.status));return d;
}
let controlState={};
renderGraph();renderRecords();
async function stopIfRunning(target){
 // One bot at a time (RAM): offer to stop the current one before starting another.
 if(!controlState.running)return true;
 if(controlState.experiment===target){alert(`The bot is already running on ${target}. Select it in the dropdown to watch.`);return false;}
 if(!confirm(`The bot is running on ${controlState.experiment||'another experiment'}.
Stop it and start ${target}?`))return false;
 showControl(await controlCall('/api/control/stop'));return true;
}
function showControl(s){controlState=s||{};
 const box=$('control-status');box.hidden=false;
 const tail=(s.log_tail||[]).slice(-1)[0]||'';
 box.textContent=s.running?`● Bot running on ${s.experiment} (pid ${s.pid})${tail?' · '+tail.replace(/^\[[^\]]*\]\s*/,'').slice(0,110):''}`
  :s.experiment?`Bot stopped${s.exit_code!=null?' (exit '+s.exit_code+')':''} · ${s.experiment}`:'Bot idle · pick an experiment and press ▶ Run bot';
 renderGraph();box.classList.toggle('running',!!s.running);$('run-bot').disabled=false;$('stop-bot').disabled=!s.running;
}
async function pollControl(){try{showControl(await(await fetch('/api/control/status')).json());}catch{}}
function enableControl(){
 if(controlTimer)return;controlEnabled=true;$('control-box').hidden=$('mode').value==='demo';pollControl();controlTimer=setInterval(pollControl,3000);
 $('run-bot').addEventListener('click',async()=>{if($('mode').value==='demo')return;const exp=$('experiment').value;if(!exp){alert('Select an experiment first, or press + New run.');return;}
  try{if(!await stopIfRunning(exp))return;showControl({running:true,experiment:exp,pid:'…'});showControl(await controlCall('/api/control/start',{experiment:exp,generations:2}));}catch(e){alert('Could not start: '+e.message);pollControl();}});
 $('stop-bot').addEventListener('click',async()=>{if($('mode').value==='demo')return;try{showControl(await controlCall('/api/control/stop'));}catch(e){alert('Could not stop: '+e.message);}});
 $('new-run').addEventListener('click',async()=>{if($('mode').value==='demo')return;const d=new Date(),p=n=>String(n).padStart(2,'0');
  const name=prompt('Name for the new experiment:',`exp-kino-${p(d.getMonth()+1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`);if(!name)return;
  try{if(!await stopIfRunning(name.trim()))return;showControl(await controlCall('/api/control/start',{experiment:name.trim(),generations:2}));
   const o=document.createElement('option');o.value=name.trim();o.textContent=name.trim();$('experiment').appendChild(o);$('experiment').value=name.trim();$('experiment').dispatchEvent(new Event('change'));}
  catch(e){alert('Could not start: '+e.message);}});
}
