// Run with jsdom available on NODE_PATH; see MD files/DASHBOARD.md.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function harness(t){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'dashboard/index.html'),'utf8'),{url:'http://localhost/?mode=live#flow',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,requests=[];
 w.setInterval=()=>0;
 w.URL.createObjectURL=()=> 'blob:test-frame';w.URL.revokeObjectURL=()=>{};
 // Deliberately ignore abort: late completions must still be rejected by the UI.
 w.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));
 w.eval(fs.readFileSync(path.join(root,'dashboard/app.js'),'utf8')+';window.diagramTestData={nodes:flowNodes,edges:flowEdges};');
 t.after(()=>w.close());
 const get=id=>d.getElementById(id),click=id=>get(id).click();
 const respond=(prefix,data,{status=200}={})=>{const i=requests.findIndex(r=>r.url.startsWith(prefix));assert.ok(i>=0,'Expected request '+prefix);const request=requests.splice(i,1)[0];request.resolve({ok:status<400,status,json:async()=>data,blob:async()=>new w.Blob(['image'])});return request;};
 const change=(id,value)=>{get(id).value=value;get(id).dispatchEvent(new w.Event('change'));};
 const start=async()=>{respond('/api/config',{default_mode:'live'});await settle();respond('/api/experiments',{experiments:[{id:'a'},{id:'b'}]});await settle();};
 return {w,d,requests,get,click,respond,change,start};
}
function data(id='a',mode='live'){
 return {mode,connected:true,experiment:{id,map:'kino',generation:3},updated_at:'2026-10-11T12:00:00Z',counts:{jobs:5,runs:4,playbooks:5,llm_calls:4},jobs:{queued:1,done:4},agents:[{persona:'Bold',state:'fallback',generation:3}],workers:[]};
}
test('late snapshot cannot replace a newly selected data source',async t=>{
 const h=harness(t);await h.start();
 h.change('mode','demo');
 h.respond('/api/snapshot',data('old-live'));await settle();
 assert.notEqual(h.get('experiment-name').textContent,'old-live');
 h.respond('/api/experiments',{experiments:[{id:'demo-kino'}]});await settle();
 h.respond('/api/snapshot',data('demo-kino','demo'));await settle();
 assert.equal(h.get('experiment-name').textContent,'demo-kino');
 assert.equal(h.get('mongo-status').textContent,'DEMO');
 assert.equal(h.get('frame-label').textContent,'SAMPLE CAPTURE');
 assert.match(h.w.location.search,/mode=demo/);
});
test('pause rejects in-flight responses and manual refresh still works',async t=>{
 const h=harness(t);await h.start();h.click('pause');
 h.respond('/api/snapshot',data('late'));await settle();
 assert.notEqual(h.get('experiment-name').textContent,'late');
 h.click('refresh');h.respond('/api/experiments',{experiments:[{id:'a'}]});await settle();
 h.respond('/api/snapshot',data());await settle();
 assert.equal(h.get('experiment-name').textContent,'a');
 assert.equal(h.get('db-live').textContent,'PAUSED');
 assert.match(h.get('last-refresh').textContent,/paused/);
});
test('late frame cannot replace demo image; frame request identifies worker and job',async t=>{
 const h=harness(t);await h.start();
 h.respond('/api/snapshot',{...data(),workers:[{identity:'worker-a',job_id:'job-a',state:'running',elapsed_s:240}]});await settle();
 const frame=h.requests.find(r=>r.url.startsWith('/api/frame'));
 assert.ok(frame.url.includes('worker=worker-a'));assert.ok(frame.url.includes('job=job-a'));
 assert.equal(h.get('elapsed').textContent,'240s elapsed');
 h.change('mode','demo');h.respond('/api/experiments',{experiments:[{id:'demo-kino'}]});await settle();
 h.respond('/api/snapshot',data('demo-kino','demo'));await settle();
 h.respond('/api/frame',{});await settle();
 assert.equal(h.get('frame').getAttribute('src'),'/demo-frame.jpg');
});
test('late experiment list and startup config cannot overwrite a user selection',async t=>{
 const h=harness(t);
 h.change('mode','demo');
 h.respond('/api/config',{default_mode:'live'});await settle();
 assert.equal(h.get('mode').value,'demo');
 h.change('mode','live');
 h.respond('/api/experiments',{experiments:[{id:'wrong-demo'}]});await settle();
 assert.notEqual(h.get('experiment').value,'wrong-demo');
 h.respond('/api/experiments',{}, {status:503});await settle();
 assert.match(h.get('experiment').textContent,/Database unavailable/);
 h.respond('/api/snapshot',{connected:true,mode:'live',error:'Select an experiment'});await settle();
 assert.equal(h.get('mongo-status').textContent,'CONNECTED');
});
test('missing frame is not labeled live; fallback summary is visible',async t=>{
 const h=harness(t);await h.start();
 h.respond('/api/snapshot',{...data(),workers:[{identity:'worker-a',state:'running'}]});await settle();
 h.respond('/api/frame',{}, {status:204});await settle();
 assert.equal(h.get('frame').hidden,true);assert.equal(h.get('frame-label').textContent,'NO LIVE FRAME');
 assert.equal(h.get('luminal-status').textContent,'FALLBACK');
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
 h.click('graph-zoom-in');assert.equal(h.get('graph-zoom-label').textContent,'120%');h.click('graph-fit');assert.equal(h.get('backend-graph').getAttribute('viewBox'),'0 0 1200 730');
 h.click('tab-overview');assert.equal(h.get('view-flow').hidden,true);
 h.get('tab-overview').dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'ArrowRight'}));assert.equal(h.get('view-flow').hidden,false);
 h.click('tour-next');assert.equal(h.get('inspect-title').textContent,'Vision pipeline');
 h.w.eval('render({connected:false,mode:"live"})');assert.equal(h.get('inspect-state').textContent,'NO TELEMETRY');
});
