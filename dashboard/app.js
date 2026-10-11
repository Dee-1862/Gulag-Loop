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
 $('db-updated').textContent=data.events?.[0]?.at?'Latest activity '+ageLabel(data.events[0].at):'No activity recorded';$('last-refresh').textContent='Snapshot '+new Date().toLocaleTimeString()+' · '+(demo?'demo data':'polls every 2 seconds');events();renderGraph();
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

// The map describes code paths. Status comes only from the existing snapshot;
// selecting an arrow never claims that a network transfer is happening now.
const flowNodes=[
 {id:'game',title:'Game session',sub:'Pixels in · controls out',lane:'On device',x:55,y:110,w:230,description:'The worker opens the game and starts a fresh evaluation episode.',reads:'Mouse and keyboard actions from the controller.',writes:'Game frames and HUD values for the vision pipeline.',source:'bot/worker.py',shape:{frame:'<captured image>',map:'<map identifier>'}},
 {id:'vision',title:'Vision pipeline',sub:'Capture → detect → track',lane:'On device',x:55,y:280,w:230,description:'The local vision model detects and tracks targets. HUD reading supplies the score used to evaluate the run.',reads:'Captured game frames; detection settings from the current strategy.',writes:'Tracked targets, target positions and HUD observations.',source:'vision/pipeline.py · vision/hud.py',shape:{targets:['<tracked detection>'],points:'<HUD score>'}},
 {id:'worker',title:'Controller / worker',sub:'Aim · fire · move · evaluate',lane:'On device',x:55,y:460,w:230,description:'The worker claims a job, loads its strategy, and uses vision observations to control the game. It submits the episode result when the run finishes.',reads:'A claimed evaluation job, strategy parameters and vision observations.',writes:'Game inputs, a completed run, and periodic dashboard telemetry.',source:'bot/worker.py · bot/controller.py',shape:{job_id:'<claimed job>',params:'<validated Strategy>',action:'<current control action>'}},
 {id:'jobs',title:'Evaluation queue',sub:'jobs · claimed atomically',lane:'MongoDB',x:415,y:110,w:230,description:'MongoDB holds pending evaluation jobs. A worker claims a job atomically and retrieves the matching strategy before playing.',reads:'Jobs created by the orchestrator.',writes:'Claimed job and strategy parameters to the worker; job status updates.',source:'mongo_orchestrator.py',shape:{job_id:'<job identifier>',strategy_id:'<playbook identifier>',generation:'<integer>',status:'queued → running → done'}},
 {id:'runs',title:'Run results',sub:'runs · fitness + diagnostics',lane:'MongoDB',x:415,y:310,w:230,description:'Completed episodes are saved with points, survival time and diagnostics. Fitness is points plus five times survival seconds.',reads:'The worker’s completed episode result.',writes:'Evaluation history used to rank parent strategies.',source:'mongo_orchestrator.py · evolution.py',shape:{job_id:'<job identifier>',points:'<number>',survival_s:'<seconds>',fitness:'points + 5 × survival_s',diagnostics:{}}},
 {id:'storage',title:'Strategy memory',sub:'playbooks · llm_calls · history',lane:'MongoDB',x:415,y:550,w:230,description:'The orchestrator commits the four child strategies, call logs, generation history and next jobs in one transaction. Saved strategies become candidates for future parent selection.',reads:'Validated children or parent fallbacks, plus model call records.',writes:'Saved strategy parameters, rationales, call logs and generation history.',source:'mongo_orchestrator.py',shape:{playbooks:'<four child strategy documents>',llm_calls:'<attempt records>',generations:'<champion history>'}},
 {id:'context',title:'Build generation',sub:'Rank parents · assign personas',lane:'Orchestration',x:870,y:110,w:245,description:'After all evaluations finish, the orchestrator ranks strategies by mean fitness and builds context from up to two parents. Tuner and Defender use the champion; Bold and Explorer use the runner-up when available.',reads:'Saved run diagnostics, parent parameters and generation history.',writes:'A generation request with parent context for four Luminal personas.',source:'evolution.py · generation.py',shape:{generation:'<next generation>',parents:[{strategy_id:'<ranked parent>',params:'<Strategy>',diagnostics:{},mean_fitness:'<number>',evaluation_count:'<integer>'}],history:[]}},
 ...Object.keys(roles).map((name,i)=>({id:name,title:name,sub:roles[name][1].split('. ')[0],lane:'Luminal',x:i%2?1000:785,y:i<2?300:415,w:175,description:`${roles[name][1]} This is one of four prompt personas using the configured Luminal model. Each proposes a complete set of bot settings and a rationale.`,reads:'Assigned parent, diagnostics, fitness, history and reference parents.',writes:'A strategy proposal and rationale; request progress and usage records.',source:'generation.py · luminal_client.py',shape:{strategy:'<complete Strategy object>',rationale:'<why these settings may help>'}})),
 {id:'validate',title:'Validate / fallback',sub:'Check settings · retain safe parent',lane:'Orchestration',x:870,y:600,w:245,description:'Each model response is parsed and checked against the strategy contract inside the Luminal client. Invalid responses can be retried; exhausted attempts fall back to the parent strategy. This node groups checks performed within each agent call.',reads:'A returned proposal or provider error.',writes:'A validated child strategy, or a parent fallback, with attempt logs.',source:'luminal_client.py · strategy.py',shape:{params:'<validated Strategy>',fallback:'<boolean>',rationale:'<explanation>',llm_calls:'<attempt logs>'}}
];
const flowEdges=[
 {id:'pixels',from:'game',to:'vision',label:'frames',path:'M170 200 V280',x:170,y:240,payload:{frame:'<captured game image>'},description:'Captured images are passed to detection and HUD reading on the game machine.'},
 {id:'targets',from:'vision',to:'worker',label:'targets',path:'M170 370 V460',x:170,y:415,payload:{targets:['<tracked target>'],points:'<HUD score>'},description:'Vision observations guide target selection, aiming and movement.'},
 {id:'inputs',from:'worker',to:'game',label:'controls',path:'M55 505 H30 V155 H55',x:72,y:665,payload:{actions:['<mouse movement>','<fire / movement / reload>']},description:'The controller sends mouse and keyboard inputs back to the game. This fast local loop runs throughout an episode.'},
 {id:'claim',from:'jobs',to:'worker',label:'claim + params',path:'M415 155 H345 V505 H285',x:345,y:220,payload:{job_id:'<claimed job>',strategy_id:'<strategy>',params:'<loaded Strategy>'},description:'The worker claims a queued job atomically and loads its saved strategy. This is a worker read, not a database-initiated push.'},
 {id:'result',from:'worker',to:'runs',label:'save result',path:'M285 530 C390 530 350 355 415 355',x:351,y:453,payload:{job_id:'<job>',points:'<number>',survival_s:'<seconds>',diagnostics:{}},description:'After an episode, the worker submits its result. The orchestrator persists it and marks the job done.'},
 {id:'rank',from:'runs',to:'context',label:'ranked results',path:'M645 355 H720 V140 H870',x:720,y:237,payload:{parents:'<up to two parents ranked by mean fitness>',diagnostics:'<aggregated run diagnostics>'},description:'Once evaluations finish, the orchestrator ranks evaluated strategies and aggregates their diagnostics.'},
 {id:'memory',from:'storage',to:'context',label:'parent context',path:'M645 585 H747 V175 H870',x:747,y:540,payload:{params:'<parent settings>',history:'<prior champions>'},description:'Saved parent settings and generation history are included in the next generation request.'},
 ...Object.keys(roles).map((name,i)=>({id:'prompt-'+name,from:'context',to:name,label:'',path:i<2?`M${i?1045:930} 200 V245 H${i?1087:872} V300`:`M${i?1115:870} 170 H${i?1188:770} V460 H${i?1175:785}`,payload:{persona:name,parent:'<assigned parent>',diagnostics:{},reference_parents:[],history:[]},description:`The orchestrator submits a ${name} prompt with the assigned parent and shared evaluation context. Calls can run concurrently.`})),
 ...Object.keys(roles).map((name,i)=>({id:'proposal-'+name,from:name,to:'validate',label:'',path:i<2?`M${i?1115:835} 390 V395 H${i?1196:762} V570 H${i?1050:935} V600`:`M${i?1087:872} 505 V550 H${i?1025:960} V600`,payload:{strategy:'<complete settings>',rationale:'<proposal rationale>'},description:`The ${name} response is parsed and validated. Its own client call handles retries and fallback before generation results are collected.`})),
 {id:'commit',from:'validate',to:'storage',label:'commit generation',path:'M870 645 H700 V615 H645',x:748,y:674,payload:{strategies:'<four validated children or fallbacks>',llm_calls:'<attempt records>'},description:'The orchestrator saves all generation outputs and next jobs in an atomic MongoDB transaction.'},
 {id:'enqueue',from:'storage',to:'jobs',label:'queue next 5',path:'M445 550 H395 V85 H530 V110',x:530,y:75,payload:{child_evaluations:4,champion_reevaluation:1},description:'As part of the same generation transaction, the orchestrator creates one evaluation per child and one champion reevaluation. The database does not generate these jobs itself.'}
];
const flowTour=['game','vision','worker','runs','context','Bold','validate','storage','jobs','worker'];
let flowSelection={kind:'node',id:'game'},tourIndex=0,graphZoom=1,graphBox={x:0,y:0,w:1200,h:730},graphDrag=null;
function flowNode(id){return flowNodes.find(n=>n.id===id);}
function flowWorker(){return snapshot?.workers?.find(w=>w.state==='running')||snapshot?.workers?.[0];}
function flowStatus(id){
 if(!snapshot?.connected||!snapshot?.experiment)return 'no telemetry';
 if(['game','vision','worker'].includes(id))return flowWorker()?.state||'waiting';
 if(roles[id])return snapshot.agents?.find(a=>a.persona===id)?.state||'waiting';
 if(id==='jobs')return `${snapshot.jobs?.queued??0} queued`;
 if(id==='runs')return `${snapshot.counts?.runs??0} saved`;
 if(id==='storage')return `${snapshot.counts?.playbooks??0} strategies`;
 return 'code path';
}
function flowObserved(id){
 if(!snapshot?.connected||!snapshot?.experiment)return null;
 if(['game','vision','worker'].includes(id)){
  const w=flowWorker();if(!w)return null;
  return Object.fromEntries(['worker','identity','state','job_id','fps','detections','action','points','elapsed_s','updated_at'].filter(k=>w[k]!=null).map(k=>[k,w[k]]));
 }
 if(roles[id])return snapshot.agents?.find(a=>a.persona===id)||null;
 if(id==='runs')return snapshot.latest_run||null;
 if(id==='jobs')return snapshot.jobs||null;
 if(id==='storage')return snapshot.counts||null;
 return null;
}
function flowSelect(kind,id){flowSelection={kind,id};$('payload-copy').textContent='Copy JSON';renderGraph();}
function renderGraph(){
 const selected=flowSelection.kind==='node'?flowSelection.id:null;
 const edge=flowEdges.find(e=>flowSelection.kind==='edge'&&e.id===flowSelection.id);
 const relatedEdges=flowEdges.filter(e=>edge?e===edge:e.from===selected||e.to===selected);
 const relatedNodes=new Set(relatedEdges.flatMap(e=>[e.from,e.to]));
 document.querySelectorAll('[data-node]').forEach(g=>{
  const id=g.dataset.node,state=flowStatus(id);g.dataset.state=state;
  g.querySelector('.graph-node-state').textContent=state.toUpperCase();
  g.classList.toggle('selected',id===selected);g.classList.toggle('related',id!==selected&&relatedNodes.has(id));g.classList.toggle('dimmed',id!==selected&&!relatedNodes.has(id));g.setAttribute('aria-pressed',String(id===selected));
 });
 document.querySelectorAll('[data-edge]').forEach(g=>{const match=relatedEdges.some(e=>e.id===g.dataset.edge);g.classList.toggle('selected',edge?.id===g.dataset.edge);g.classList.toggle('related',match);g.classList.toggle('dimmed',!match);g.setAttribute('aria-pressed',String(edge?.id===g.dataset.edge));});
 $('graph-source').textContent=(snapshot?.mode==='demo'?'DEMO DATA':snapshot?.connected&&snapshot?.experiment?'LIVE SNAPSHOT':'ARCHITECTURE · NO TELEMETRY')+(paused?' · PAUSED':'');
 const node=flowNode(selected);const observed=node?flowObserved(node.id):null;
 $('inspect-category').textContent=edge?'DATA CONNECTION':node.lane;
 $('inspect-title').textContent=edge?`${flowNode(edge.from).title} → ${flowNode(edge.to).title}`:node.title;
 $('inspect-state').textContent=edge?'CODE PATH':flowStatus(node.id).toUpperCase();
 $('inspect-description').textContent=(edge||node).description;
 const contract=edge?[['From',flowNode(edge.from).title],['To',flowNode(edge.to).title]]:[['Receives',node.reads],['Produces',node.writes],['Implemented in',node.source]];
 $('inspect-contract').innerHTML=contract.map(([k,v])=>`<div class="contract-row"><strong>${esc(k)}</strong><${k==='Implemented in'?'code':'span'}>${esc(v)}</${k==='Implemented in'?'code':'span'}></div>`).join('');
 const evidence=edge?[['Source status',flowStatus(edge.from)],['Destination status',flowStatus(edge.to)]]:observed?Object.entries(observed).filter(([,v])=>typeof v!=='object').slice(0,6):[['Telemetry','No stage-specific record']];
 $('inspect-evidence').innerHTML=evidence.map(([k,v])=>`<div class="evidence-item"><span>${esc(k.replaceAll('_',' '))}</span><b>${esc(String(v).slice(0,90))}</b></div>`).join('');
 $('payload-title').textContent=observed?(snapshot?.mode==='demo'?'Demo snapshot':'Observed snapshot'):'Illustrative payload';
 $('inspect-payload').textContent=JSON.stringify(observed||edge?.payload||node.shape,null,2);
 $('inspect-note').textContent=observed?'Snapshot fields may describe a previous completed run or attempt. This view contains no credentials or raw frames.':'This is a simplified data contract, not a captured request. Arrows show implemented code paths; selection does not indicate a transfer in progress.';
}
function graphView(){const svg=$('backend-graph');svg.setAttribute('viewBox',`${graphBox.x} ${graphBox.y} ${graphBox.w} ${graphBox.h}`);$('graph-zoom-label').textContent=Math.round(graphZoom*100)+'%';}
function graphScale(factor){const previous=graphZoom;graphZoom=Math.max(.6,Math.min(2.5,graphZoom*factor));const w=1200/graphZoom,h=730/graphZoom;graphBox={x:graphBox.x+(graphBox.w-w)/2,y:graphBox.y+(graphBox.h-h)/2,w,h};if(previous!==graphZoom)graphView();}
function showFlowTab(view,updateHash=true){
 for(const name of ['overview','flow']){const active=name===view;$('view-'+name).hidden=!active;$('tab-'+name).setAttribute('aria-selected',String(active));$('tab-'+name).tabIndex=active?0:-1;}
 if(updateHash)history.replaceState(null,'',location.pathname+location.search+(view==='flow'?'#flow':''));
}
function flowTourStep(delta){tourIndex=(tourIndex+delta+flowTour.length)%flowTour.length;const id=flowTour[tourIndex];flowSelect('node',id);$('tour-progress').textContent=`${tourIndex+1} / ${flowTour.length}`;$('tour-caption').textContent=flowNode(id).description;}
function initFlow(){
 const svg=$('backend-graph');
 svg.innerHTML=`<defs><marker id="arrow-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#465666"/></marker><marker id="arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#c4ed91"/></marker></defs>`+
 [[15,300,'01 / GAME + VISION'],[385,290,'02 / MONGODB'],[755,445,'03 / LUMINAL + ORCHESTRATION']].map(([x,w,title])=>`<rect class="graph-lane" x="${x}" y="35" width="${w}" height="675" rx="14"/><text class="graph-lane-title" x="${x+18}" y="60">${title}</text>`).join('')+
 flowEdges.map(e=>`<g class="graph-edge" data-edge="${e.id}" role="button" tabindex="0" aria-label="Inspect ${esc(flowNode(e.from).title)} to ${esc(flowNode(e.to).title)}"><path class="edge-hit" d="${e.path}"/><path class="edge-line" d="${e.path}"/>${e.label?`<rect class="edge-label-bg" x="${e.x-e.label.length*3.2-9}" y="${e.y-10}" width="${e.label.length*6.4+18}" height="20" rx="10"/><text x="${e.x}" y="${e.y+3}" text-anchor="middle">${e.label}</text>`:''}</g>`).join('')+
 flowNodes.map((n,i)=>`<g class="graph-node" data-node="${n.id}" transform="translate(${n.x},${n.y})" role="button" tabindex="0" aria-label="Inspect ${esc(n.title)}"><rect width="${n.w}" height="90" rx="10"/><text class="graph-node-number" x="${n.w-25}" y="20">${String(i+1).padStart(2,'0')}</text><text class="graph-node-title" x="15" y="30">${esc(n.title)}</text><text class="graph-node-subtitle" x="15" y="49">${esc(n.sub)}</text><circle class="node-indicator" cx="19" cy="70" r="3"/><text class="graph-node-state" x="29" y="73">WAITING</text></g>`).join('');
 for(const [attribute,kind] of [['node','node'],['edge','edge']])svg.querySelectorAll(`[data-${attribute}]`).forEach(g=>{g.addEventListener('click',()=>flowSelect(kind,g.dataset[attribute]));g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();flowSelect(kind,g.dataset[attribute]);}});});
 document.querySelectorAll('[data-view]').forEach(b=>{b.addEventListener('click',()=>showFlowTab(b.dataset.view));b.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const view=e.key==='Home'?'overview':e.key==='End'?'flow':b.dataset.view==='flow'?'overview':'flow';showFlowTab(view);$('tab-'+view).focus();}});});
 window.addEventListener('hashchange',()=>showFlowTab(location.hash==='#flow'?'flow':'overview',false));
 $('graph-zoom-in').addEventListener('click',()=>graphScale(1.2));$('graph-zoom-out').addEventListener('click',()=>graphScale(1/1.2));$('graph-fit').addEventListener('click',()=>{graphZoom=1;graphBox={x:0,y:0,w:1200,h:730};graphView();});
 const point=e=>{const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());};
 svg.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('[data-node],[data-edge]'))return;graphDrag=point(e);svg.setPointerCapture(e.pointerId);svg.classList.add('dragging');});
 svg.addEventListener('pointermove',e=>{if(!graphDrag)return;const p=point(e);graphBox.x+=graphDrag.x-p.x;graphBox.y+=graphDrag.y-p.y;graphView();});
 const release=()=>{graphDrag=null;svg.classList.remove('dragging');};svg.addEventListener('pointerup',release);svg.addEventListener('pointercancel',release);svg.addEventListener('lostpointercapture',release);
 $('tour-back').addEventListener('click',()=>flowTourStep(-1));$('tour-next').addEventListener('click',()=>flowTourStep(1));
 $('payload-copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('inspect-payload').textContent);$('payload-copy').textContent='Copied';}catch{$('payload-copy').textContent='Select text to copy';}});
 showFlowTab(location.hash==='#flow'?'flow':'overview',false);flowTourStep(0);
}
initFlow();
