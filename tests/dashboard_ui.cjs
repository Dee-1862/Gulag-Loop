// Run with jsdom available on NODE_PATH; see MD files/DASHBOARD.md.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function harness(t){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'dashboard/index.html'),'utf8'),{url:'http://localhost/?mode=demo&experiment=old#flow',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,requests=[];
 w.setInterval=()=>0;
 w.HTMLMediaElement.prototype.pause=function(){};w.HTMLMediaElement.prototype.load=function(){};w.HTMLMediaElement.prototype.play=async function(){};
 w.URL.createObjectURL=()=> 'blob:test-frame';w.URL.revokeObjectURL=()=>{};
 // Deliberately ignore abort: late completions must still be rejected by the UI.
 w.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));
 w.eval(fs.readFileSync(path.join(root,'dashboard/app.js'),'utf8')+';window.diagramTestData={nodes:flowNodes,edges:flowEdges};');
 t.after(()=>w.close());
 const get=id=>d.getElementById(id),click=id=>get(id).click();
 const respond=(prefix,data,{status=200}={})=>{const i=requests.findIndex(r=>r.url.startsWith(prefix));assert.ok(i>=0,'Expected request '+prefix);const request=requests.splice(i,1)[0];request.resolve({ok:status<400,status,json:async()=>data,blob:async()=>new w.Blob(['image'])});return request;};
 const change=(id,value)=>{get(id).value=value;get(id).dispatchEvent(new w.Event('change'));};
 const start=async()=>{respond('/api/config',{default_mode:'live'});await settle();};
 return {w,d,requests,get,click,respond,change,start};
}
function data(id='a',mode='live'){
 return {mode,connected:true,experiment:{id,map:'kino',generation:3},updated_at:'2026-10-11T12:00:00Z',counts:{jobs:5,runs:4,playbooks:5,llm_calls:4},jobs:{queued:1,done:4},agents:[{persona:'Bold',state:'fallback',generation:3}],workers:[]};
}
test('dashboard is Gulag Loop and always requests unified live history, even from an old demo link',async t=>{
 const h=harness(t);await h.start();
 assert.match(h.d.title,/Gulag Loop/);assert.equal(h.get('mode'),null);assert.equal(h.get('experiment'),null);
 assert.equal(h.w.location.search,'');assert.equal(h.requests.some(r=>r.url.startsWith('/api/experiments')),false);
 const request=h.requests.find(r=>r.url.startsWith('/api/snapshot'));
 assert.equal(request.url,'/api/snapshot?mode=live');
 h.respond('/api/snapshot',{...data('all'),experiment_count:3});await settle();
 assert.equal(h.get('experiment-name').textContent,'All run history');assert.equal(h.get('experiment-count').textContent,'3');
});
test('pause rejects in-flight responses and manual refresh still works',async t=>{
 const h=harness(t);await h.start();h.click('pause');
 h.respond('/api/snapshot',data('late'));await settle();
 assert.notEqual(h.get('experiment-name').textContent,'late');
 h.click('refresh');
 h.respond('/api/snapshot',data());await settle();
 assert.equal(h.get('experiment-name').textContent,'All run history');
 assert.match(h.get('connection').textContent,/PAUSED/);
 assert.match(h.get('last-refresh').textContent,/paused/);
});
test('frame request uses the active worker experiment and rejects a late frame after refresh',async t=>{
 const h=harness(t);await h.start();
 h.respond('/api/snapshot',{...data(),workers:[{experiment_id:'origin-a',identity:'worker-a',job_id:'job-a',state:'running',elapsed_s:240}]});await settle();
 const frame=h.requests.find(r=>r.url.startsWith('/api/frame'));
 assert.ok(frame.url.includes('experiment=origin-a'));assert.ok(frame.url.includes('worker=worker-a'));assert.ok(frame.url.includes('job=job-a'));
 assert.equal(h.get('elapsed').textContent,'240s elapsed');
 h.click('pause');h.click('pause');h.respond('/api/snapshot',data());await settle();h.respond('/api/frame',{});await settle();
 assert.equal(h.get('frame').hidden,true);
});
test('missing frame is not labeled live; fallback summary is visible',async t=>{
 const h=harness(t);await h.start();
 h.respond('/api/snapshot',{...data(),workers:[{identity:'worker-a',state:'running'}]});await settle();
 h.respond('/api/frame',{}, {status:204});await settle();
 assert.equal(h.get('frame').hidden,true);assert.equal(h.get('frame-label').textContent,'NO LIVE FRAME');
 assert.match(h.get('agents').textContent,/fallback/);
});
test('all diagram arrows meet their source and destination; interactions survive refresh',async t=>{
 const h=harness(t);await h.start();h.respond('/api/snapshot',data());await settle();
 const {nodes,edges}=h.w.diagramTestData;
 function endpoints(svgPath){let x,y,start;for(const command of svgPath.match(/[MHVC][^MHVC]*/g)){const values=command.slice(1).trim().split(/[ ,]+/).map(Number);if(command[0]==='M'){[x,y]=values;start=[x,y];}else if(command[0]==='H')x=values[0];else if(command[0]==='V')y=values[0];else [x,y]=values.slice(-2);}return [start,[x,y]];}
 function boundary([x,y],n){return ((x===n.x||x===n.x+n.w)&&y>=n.y&&y<=n.y+90)||((y===n.y||y===n.y+90)&&x>=n.x&&x<=n.x+n.w);}
 for(const edge of edges){const [start,end]=endpoints(edge.path);assert.ok(boundary(start,nodes.find(n=>n.id===edge.from)),edge.id+' source');assert.ok(boundary(end,nodes.find(n=>n.id===edge.to)),edge.id+' destination');}
 for(const item of h.d.querySelectorAll('[data-node],[data-edge]')){item.dispatchEvent(new h.w.MouseEvent('click'));assert.doesNotThrow(()=>JSON.parse(h.get('inspect-payload').textContent));}
 h.d.querySelector('[data-node="Defender"]').dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Enter'}));
 h.w.eval('render('+JSON.stringify(data())+')');assert.equal(h.get('inspect-title').textContent,'Defender');
 assert.equal(nodes.length,6);assert.equal(edges.length,5);assert.equal(edges.filter(e=>e.from==='mongo').length,4);
 assert.equal(h.d.querySelectorAll('.graph-layer').length,3);
 h.click('tab-overview');assert.equal(h.get('view-flow').hidden,true);
 h.get('tab-overview').dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'ArrowRight'}));assert.equal(h.get('view-flow').hidden,false);
 h.w.eval('render({connected:false,mode:"live"})');assert.equal(h.get('inspect-state').textContent,'NO DATA');
});
test('MongoDB records show saved results without live workers and preserve inspection choices',async t=>{
 const h=harness(t);await h.start();
 const snapshot={...data(),latest_run:{job_id:'job-1',points:0,survival_s:44.7,fitness:223.5,completed_at:'2026-10-11T20:10:00Z'},records:{
  runs:[{_id:'run-1',job_id:'job-1',strategy_id:'baseline',points:0,survival_s:44.7,fitness:223.5,diagnostics:{time_no_target_pct:97.6,overshoot_rate:0,loop_fps:16.1,det_count_avg:.02}}],
  strategies:[{_id:'child',persona:'Bold',generation:1,parent_ids:['baseline'],params:{aim_gain:.7},rationale:'<img src=x onerror=alert(1)>',evaluations:0},{_id:'baseline',persona:'Baseline',generation:0,parent_ids:[],params:{aim_gain:.8,det_conf:.35,aim_y_offset:.15,strafe_mode:'none'},rationale:'Starting settings.',evaluations:1,mean_fitness:223.5}],
  jobs:[{job_id:'job-1',status:'done',strategy_id:'baseline'},{job_id:'job-2',status:'queued',strategy_id:'child'}],calls:[]}};
 h.respond('/api/snapshot',snapshot);await settle();h.click('tab-records');
 assert.equal(h.get('view-records').hidden,false);assert.equal(h.get('view-overview').hidden,true);assert.equal(h.w.location.hash,'#records');
 assert.equal(h.get('saved-points').textContent,'0');assert.equal(h.get('saved-survival').textContent,'44.7s');
 assert.match(h.get('saved-diagnostics').textContent,/97.6%/);assert.match(h.get('saved-diagnostics').textContent,/0%/);
 assert.equal(h.get('saved-rationale').querySelector('img'),null);
 assert.match(h.get('saved-calls').textContent,/No Luminal calls/);
 h.get('saved-parents').querySelector('button').click();assert.equal(h.get('record-strategy').value,JSON.stringify(['','baseline']));
 assert.match(h.get('saved-params').textContent,/0.8/);assert.match(h.get('saved-params').textContent,/0.35/);assert.match(h.get('saved-params').textContent,/0.15/);assert.match(h.get('saved-diagnostics').textContent,/0.02/);
 h.change('record-job-filter','queued');assert.equal(h.get('saved-jobs').querySelectorAll('tr').length,1);assert.match(h.get('saved-jobs').textContent,/job-2/);
 h.w.eval('render('+JSON.stringify(snapshot)+')');assert.equal(h.get('record-strategy').value,JSON.stringify(['','baseline']));
 assert.equal(h.get('record-job-filter').value,'queued');
 h.get('tab-flow').dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'End'}));assert.equal(h.get('tab-records').getAttribute('aria-selected'),'true');
 h.w.eval('render({connected:false,mode:"live"})');assert.equal(h.get('saved-run-content').hidden,true);assert.equal(h.get('saved-strategy-content').hidden,true);
 assert.equal(h.get('record-run').options.length,1);
});

test('flow shows exact parent differences, escaped rationales, and experiment-scoped stdout',async t=>{
 const h=harness(t);await h.start();
 const proposal={_id:'child',persona:'Defender',generation:2,parent_ids:['parent'],parent_params:{aim_gain:.8,strafe_mode:'none'},params:{aim_gain:.72,strafe_mode:'alternate'},rationale:'<img src=x onerror=alert(1)> survive longer',evaluations:0};
 h.respond('/api/snapshot',{...data(),agents:[{persona:'Defender',experiment_id:'a',state:'complete',generation:2,proposal}]});await settle();
 h.w.eval('showControl('+JSON.stringify({experiment:'a',log_tail:['[12:01] queue empty: asking Luminal for generation 2','[12:02] stored in MongoDB','[12:03] capture backend ready']})+')');
 h.d.querySelector('[data-node="Defender"]').dispatchEvent(new h.w.MouseEvent('click'));
 assert.match(h.get('inspect-insight').textContent,/0.8/);assert.match(h.get('inspect-insight').textContent,/0.72/);
 assert.equal(h.get('inspect-insight').querySelector('img'),null);assert.match(h.get('inspect-console').textContent,/asking Luminal/);
 assert.doesNotMatch(h.get('inspect-console').textContent,/capture backend/);assert.match(h.get('console-source').textContent,/STDOUT/);
 h.w.eval('showControl('+JSON.stringify({experiment:'other',log_tail:['private other run']})+')');
 assert.doesNotMatch(h.get('inspect-console').textContent,/private other run/);assert.match(h.get('console-source').textContent,/NO CAPTURED/);

});
test('recordings with the same job name retain their original experiment',async t=>{
 const h=harness(t);await h.start();
 h.respond('/api/snapshot',{...data(),footage:[{experiment_id:'a',job_id:'same-job',points:10},{experiment_id:'b',job_id:'same-job',points:20}]});await settle();
 const videos=h.d.querySelectorAll('.recording-item');videos[1].click();
 assert.match(h.get('recording').getAttribute('src'),/experiment=b/);assert.match(h.get('recording').getAttribute('src'),/job=same-job/);
 h.w.eval('render({connected:false,mode:"live"})');assert.equal(h.get('recording').hidden,true);assert.equal(h.get('recording').hasAttribute('src'),false);
});
test('all fifty recordings are available and older playback survives a new arrival',async t=>{
 const h=harness(t);await h.start();
 const footage=Array.from({length:50},(_,i)=>({experiment_id:`experiment-${i%6}`,job_id:`job-${i}`,points:i,created_at:'2026-10-11T12:00:00Z'}));
 h.respond('/api/snapshot',{...data('all'),footage});await settle();
 assert.equal(h.d.querySelectorAll('.recording-item').length,50);
 assert.match(h.get('recordings-count').textContent,/50 SAVED/);
 h.d.querySelectorAll('.recording-item')[49].click();
 const src=h.get('recording').getAttribute('src');assert.match(src,/job=job-49/);
 h.w.eval('render('+JSON.stringify({...data('all'),footage:[{experiment_id:'new',job_id:'new'},...footage]})+')');
 assert.equal(h.d.querySelectorAll('.recording-item').length,51);
 assert.equal(h.get('recording').getAttribute('src'),src);
 assert.equal(h.d.querySelector('.recording-item.selected').dataset.job,JSON.stringify(['experiment-1','job-49']));
});
test('all three layer backgrounds are interactive',async t=>{
 const h=harness(t);await h.start();h.respond('/api/snapshot',data());await settle();
 const layer=h.d.querySelector('[data-layer="agents"]');layer.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Enter'}));
 assert.equal(h.get('inspect-title').textContent,'Four Luminal agents');assert.equal(h.d.querySelectorAll('[data-insight-agent]').length,4);
 h.d.querySelector('[data-insight-agent="Explorer"]').click();assert.equal(h.get('inspect-title').textContent,'Explorer');

});

test('combined run history pages older records without losing new arrivals or mixing duplicate IDs',async t=>{
 const h=harness(t);await h.start();
 const recent={experiment_id:'b',_id:'same',job_id:'same',points:22,fitness:100,completed_at:'2026-10-11T12:00:00Z'};
 h.respond('/api/snapshot',{...data('all'),counts:{runs:3},records:{runs:[recent]}});await settle();
 assert.match(h.get('run-history').textContent,/b/);h.click('history-more');
 const old={experiment_id:'a',_id:'same',job_id:'same',points:11,fitness:50,completed_at:'2026-10-10T12:00:00Z'};
 h.respond('/api/history',{runs:[old],total:3});await settle();
 const newer={...recent,_id:'new',job_id:'new',completed_at:'2026-10-12T12:00:00Z'};
 h.w.eval('render('+JSON.stringify({...data('all'),counts:{runs:3},records:{runs:[newer,recent]}})+')');
 assert.equal(h.get('run-history').querySelectorAll('button').length,3);assert.equal(h.get('history-more').hidden,true);
 h.get('run-history').querySelectorAll('button')[2].click();assert.equal(h.get('saved-points').textContent,'11');
 assert.match(h.get('saved-run-meta').textContent,/a/);
});
