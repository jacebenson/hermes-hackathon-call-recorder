// Real runtime loader + SDK components + CSS + MediaRecorder + IndexedDB.
// Display share is instrumented (300Hz + canvas); mic is Chromium fake device.
import {createHash} from 'node:crypto'
import {spawn,execFileSync} from 'node:child_process'
import {mkdtempSync,readFileSync,writeFileSync,mkdirSync,rmSync} from 'node:fs'
import {join,resolve} from 'node:path'
import {setTimeout as sleep} from 'node:timers/promises'
const out=process.env.CHUNKED_OUT||join(process.env.TMPDIR,'call-recorder-chunked-evidence');mkdirSync(out,{recursive:true})
const profile=mkdtempSync(join(process.env.TMPDIR,'call-recorder-profile-'))
const chrome=spawn(process.env.PROOF_CHROME||`${process.env.HOME}/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome`,['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-first-run','--disable-extensions','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required','--mute-audio','about:blank'],{stdio:['ignore','ignore','pipe']})
let errors='';chrome.stderr.on('data',d=>errors+=d)
const checks=[];const check=(name,value,detail='')=>{checks.push({name,ok:!!value,detail});console.log(`${value?'PASS':'FAIL'} ${name}${detail?' '+detail:''}`);if(!value)throw Error(name)}
let ws
try{
let port;for(let i=0;i<100&&!port;i++){await sleep(100);try{port=Number(readFileSync(join(profile,'DevToolsActivePort'),'utf8').split('\n')[0])}catch{}}
if(!port)throw Error(errors)
const pages=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json()
ws=new WebSocket(pages.find(p=>p.type==='page'&&p.url==='about:blank').webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r))
let n=0;const pending=new Map();ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result)}})
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++n;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})
const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.text+JSON.stringify(r.exceptionDetails.exception));return r.result.value}
const until=async(expr)=>{for(let i=0;i<100;i++){try{if(await ev(`Boolean(${expr})`))return}catch{}await sleep(100)}throw Error('Timeout '+expr+' '+await ev('JSON.stringify({body:document.body.innerText,proof:window.proof,html:document.documentElement.outerHTML.slice(0,500)})'))}
const click=async text=>{await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent===${JSON.stringify(text)});if(!b||b.disabled)throw Error('Unavailable '+${JSON.stringify(text)});b.click()})()`);await sleep(100)}
await send('Page.enable');await send('Runtime.enable');await send('Network.enable');ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown'||m.method==='Runtime.consoleAPICalled')console.log(JSON.stringify(m).slice(0,3000))})
const requests=[];ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.method==='Network.requestWillBeSent'&&m.params.request.url.includes('/api/'))requests.push(m.params.request.url)})
await send('Page.addScriptToEvaluateOnNewDocument',{source:`
window.fixture={tracks:[],contexts:[],mode:'audio',late:false};
const gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia=async opts=>{if(fixture.deny)throw new DOMException('fixture refused','NotAllowedError');const s=await gum(opts);fixture.tracks.push(...s.getTracks());if(fixture.late)await new Promise(r=>fixture.resolveLate=r);return s};
navigator.mediaDevices.getDisplayMedia=async()=>{const canvas=document.createElement('canvas');canvas.width=32;canvas.height=32;const v=canvas.captureStream(10);if(fixture.mode==='no-audio'){fixture.tracks.push(...v.getTracks());return v}const a=new AudioContext();fixture.contexts.push(a);const o=a.createOscillator();o.frequency.value=300;const gain=a.createGain();gain.gain.value=.18;const d=a.createMediaStreamDestination();o.connect(gain);gain.connect(d);o.start();await a.resume();const s=new MediaStream([...d.stream.getAudioTracks(),...v.getVideoTracks()]);fixture.tracks.push(...s.getTracks());return s};
window.readCalls=()=>new Promise((resolve,reject)=>{const r=indexedDB.open('call-recorder-chunks-v2',1);r.onsuccess=()=>{const db=r.result;const rows=[];const t=db.transaction(['sessions','chunks']);const q=t.objectStore('sessions').openCursor();q.onsuccess=()=>{const c=q.result;if(c){rows.push(c.value);c.continue()}};t.oncomplete=async()=>{for(const row of rows)for(const track of row.tracks){const parts=[];for(let n=0;n<track.chunks;n++){parts.push(await new Promise((res,rej)=>{const x=db.transaction('chunks').objectStore('chunks').get([row.id,track.key,n]);x.onsuccess=()=>res(x.result.blob);x.onerror=()=>rej(x.error)}))}track.blob=new Blob(parts,{type:track.mimeType})}db.close();resolve(rows)};};r.onerror=()=>reject(r.error)});
window.hashBlob=async b=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await b.arrayBuffer()))).map(v=>v.toString(16).padStart(2,'0')).join('');
`})

await send('Page.addScriptToEvaluateOnNewDocument',{source:`
window.nativeRecorders=[];
const MR=window.MediaRecorder;
window.MediaRecorder=class extends MR {
 constructor(...args){super(...args);nativeRecorders.push(this);this.proofChunks=[];this.proofEvents=[];
 this.addEventListener('dataavailable',e=>{this.proofChunks.push(e.data);this.proofEvents.push({event:'data',bytes:e.data.size,state:this.state})});
 this.addEventListener('error',e=>this.proofEvents.push({event:'error',name:e.error.name,state:this.state}));
 this.addEventListener('stop',()=>this.proofEvents.push({event:'stop',state:this.state}));}
};`});

await send('Page.addScriptToEvaluateOnNewDocument',{source:`
const create=URL.createObjectURL.bind(URL);
URL.createObjectURL=b=>{b.text().then(t=>{if(t.startsWith('// Generated by scripts/build-desktop.mjs')) window.artifactBlob=b});return create(b)};
`});
await send('Page.navigate',{url:process.env.PROOF_URL});
await until('!!document.querySelector("[data-recorder-pane]") && !!window.artifactBlob');
await ev(`(async()=>{window.artifactModule=await import(URL.createObjectURL(artifactBlob));window.boundary=artifactModule.createController(window)})()`);
// Create the older saved call through actual pane handlers.
await click('Grant microphone');await click('Use microphone only');await click('Record');await sleep(1300);await click('Stop');
await until('document.body.innerText.includes("Saved")');
await click('Release / reset inputs');await click('Grant microphone');await click('Grant call audio');await click('Record');await sleep(1500);
const activeId=await ev('(async()=> (await readCalls()).find(r=>r.status==="recording").id)()');
await click('Save title');
check('title-save refresh does not expose live call as recovered/deletable',await ev(`!document.querySelector('[data-call-id="${activeId}"]') && nativeRecorders.slice(-2).every(r=>r.state==='recording')`));
const attempt=`(async()=>{
const id=${JSON.stringify(activeId)}, before=(await readCalls()).find(r=>r.id===id);
const snap=async r=>Promise.all(r.tracks.map(async t=>({key:t.key,bytes:t.bytes,hash:await hashBlob(t.blob)})));
const a=await snap(before);let error='';try{await boundary.backend.remove(id)}catch(e){error=e.message}
const after=(await readCalls()).find(r=>r.id===id);const b=await Promise.all(after.tracks.map(async(t,i)=>({key:t.key,bytes:a[i].bytes,hash:await hashBlob(t.blob.slice(0,a[i].bytes))})));
return {id,error,before:a,afterPrefix:b,active:await boundary.backend.isActive(id)};
})()`;
const direct=await ev(attempt);console.log(JSON.stringify({direct}));
check('storage boundary refuses exact active ID and preserves both committed hashes',direct.active&&/active/.test(direct.error)&&JSON.stringify(direct.before)===JSON.stringify(direct.afterPrefix)&&direct.before.length===2&&direct.before.every(t=>t.bytes>0));
// Same-origin second tab is intentional: ownership must protect shared IDB across tabs.
const target=await send('Target.createTarget',{url:'about:blank'});
let tab;for(let i=0;i<100&&!tab;i++){const all=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();tab=all.find(t=>t.id===target.targetId);if(!tab)await sleep(50)}
const second=new WebSocket(tab.webSocketDebuggerUrl);await new Promise(r=>second.addEventListener('open',r));
let sid=0;const sp=new Map();second.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){sp.get(m.id)(m.result);sp.delete(m.id)}});
const se=expression=>new Promise((resolve,reject)=>{const id=++sid;sp.set(id,r=>{if(r.exceptionDetails){reject(Error(JSON.stringify(r)));return}resolve(r.result.value)});second.send(JSON.stringify({id,method:'Runtime.evaluate',params:{expression,awaitPromise:true,returnByValue:true}}))});
// Capture the exact artifact through the second tab's own loader realm.
const enableId=++sid;await new Promise(resolve=>{sp.set(enableId,resolve);second.send(JSON.stringify({id:enableId,method:'Page.enable'}))});
const injectId=++sid;
await new Promise(resolve=>{sp.set(injectId,resolve);second.send(JSON.stringify({id:injectId,method:'Page.addScriptToEvaluateOnNewDocument',params:{source:`const create=URL.createObjectURL.bind(URL);URL.createObjectURL=b=>{b.text().then(t=>{if(t.startsWith('// Generated by scripts/build-desktop.mjs'))window.artifactBlob=b});return create(b)};`}}))});
const reloadId=++sid;await new Promise(resolve=>{sp.set(reloadId,resolve);second.send(JSON.stringify({id:reloadId,method:'Page.navigate',params:{url:process.env.PROOF_URL}}))});
for(let i=0;i<100;i++){if(await se('!!window.artifactBlob && !!document.querySelector("[data-recorder-pane]")'))break;await sleep(100)}
await se(`(async()=>{window.module=await import(URL.createObjectURL(artifactBlob));window.peer=module.createController(window)})()`);
const peer=await se(`(async()=>{await peer.refresh();let error='';try{await peer.backend.remove(${JSON.stringify(activeId)})}catch(e){error=e.message}return {error,active:await peer.backend.isActive(${JSON.stringify(activeId)}),visible:peer.ui.calls.some(c=>c.id===${JSON.stringify(activeId)})}})()`);
console.log(JSON.stringify({peer}));check('other-tab controller hides and refuses deleting live session',peer.active&&!peer.visible&&/active/.test(peer.error));
await sleep(1400);await click('Stop');await until(`!!document.querySelector('[data-call-id="${activeId}"]')`);
const final=await ev(`(async()=>{const r=(await readCalls()).find(r=>r.id===${JSON.stringify(activeId)});return {status:r.status,active:await boundary.backend.isActive(r.id),tracks:await Promise.all(r.tracks.map(async(t,i)=>({key:t.key,bytes:t.bytes,storedHash:await hashBlob(t.blob),emittedHash:await hashBlob(new Blob(nativeRecorders.slice(-2)[i].proofChunks))}))),errors:proof.errors}})()`);
console.log(JSON.stringify({final}));check('both-track capture continues then stops byte-exact and ownership ends',final.status==='saved'&&!final.active&&final.tracks.length===2&&final.tracks.every((t,i)=>t.bytes>direct.before[i].bytes&&t.storedHash===t.emittedHash)&&final.errors.length===0);
await se(`peer.dispose()`);second.close();await send('Target.closeTarget',{targetId:target.targetId});
// Confirmed deletion is legal after actual ownership ends.
await ev(`(()=>{const c=document.querySelector('[data-call-id="${activeId}"]');[...c.querySelectorAll('button')].find(b=>b.textContent==='Delete this call').click()})()`);
await ev(`(()=>{const c=document.querySelector('[data-call-id="${activeId}"]');[...c.querySelectorAll('button')].find(b=>b.textContent==='Delete permanently').click()})()`);
await until(`!document.querySelector('[data-call-id="${activeId}"]')`);
check('confirmed deletion after stop removes target only',await ev(`(async()=>{const rows=await readCalls();return !rows.some(r=>r.id===${JSON.stringify(activeId)})&&rows.length===1})()`));
// Actual reload without graceful handlers abandons a persisted live session.
await click('Release / reset inputs');await click('Grant microphone');await click('Grant call audio');await click('Record');await sleep(1400);
const stale=await ev('(async()=>{const r=(await readCalls()).find(r=>r.status==="recording");return {id:r.id,tracks:await Promise.all(r.tracks.map(async t=>({bytes:t.bytes,hash:await hashBlob(t.blob)})))}})()');
await ev(`for(const name of ['beforeunload','pagehide'])window.addEventListener(name,e=>e.stopImmediatePropagation(),{capture:true});window.__oldDocument=true`);
await send('Page.reload');await until('!window.__oldDocument && document.body.innerText.includes("Recovered partial")');
const recovered=await ev(`(async()=>{const r=(await readCalls()).find(r=>r.id===${JSON.stringify(stale.id)});return Promise.all(r.tracks.map(async(t,i)=>hashBlob(t.blob.slice(0,${JSON.stringify(stale.tracks)}[i].bytes))))})()`);
check('abandoned reload releases lock and recovers both committed prefixes',recovered.every((h,i)=>h===stale.tracks[i].hash));
await ev(`(()=>{const c=document.querySelector('[data-call-id="${stale.id}"]');[...c.querySelectorAll('button')].find(b=>b.textContent==='Delete this call').click()})()`);
await ev(`(()=>{const c=document.querySelector('[data-call-id="${stale.id}"]');[...c.querySelectorAll('button')].find(b=>b.textContent==='Delete permanently').click()})()`);
await until(`!document.querySelector('[data-call-id="${stale.id}"]')`);
check('abandoned session is not forever locked and can be confirmed deleted',await ev(`(async()=>!(await readCalls()).some(r=>r.id===${JSON.stringify(stale.id)}))()`));
writeFileSync(join(out,'active-deletion.json'),JSON.stringify({checks,direct,peer,final,stale,recovered},null,2));
}finally{ws?.close();chrome.kill();await sleep(300);rmSync(profile,{recursive:true,force:true})}
