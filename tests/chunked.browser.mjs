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
const evidence=[];
for(const mode of (process.env.FINALIZATION_MODES||'stop-disable,stop-unmount,active-unmount,error-mic,error-dual,repeated-stop,long').split(',')){
 await ev('window.__oldDocument=true');
 await send('Page.navigate',{url:process.env.PROOF_URL||'http://127.0.0.1:4197/'});
 await until('!window.__oldDocument && !!document.querySelector("[data-recorder-pane]")');
 const before=await ev('(async()=> (await readCalls()).map(c=>c.id))()');
 await click('Grant microphone');
 if(mode==='error-mic') await click('Use microphone only');else await click('Grant call audio');
 await click('Record');await until('nativeRecorders.length > 0 && nativeRecorders[0].state === \"recording\"');await sleep(mode === 'long' ? 183000 : 1600);
 await ev(`(()=>{
 const mode=${JSON.stringify(mode)};
 const stop=[...document.querySelectorAll('button')].find(b=>b.textContent==='Stop');
 if(mode.startsWith('error'))nativeRecorders[0].stream.removeTrack(nativeRecorders[0].stream.getTracks()[0]);
 else if(mode==='active-unmount')mountPane(false);
 else {stop.click();if(mode==='stop-disable')disposePlugin();if(mode==='stop-unmount')mountPane(false);if(mode==='repeated-stop'){stop.click();stop.click()}}
 })()`);
 if(mode==='active-unmount')await until('fixture.tracks.every(t=>t.readyState==="ended")');
 if(!mode.startsWith('error'))check(mode+' releases all acquired tracks after teardown' ,await ev('fixture.tracks.every(t=>t.readyState==="ended")'));
 let result;
 for(let i=0;i<100;i++){
 result=await ev(`(async()=>{
 const calls=(await readCalls()).filter(c=>!${JSON.stringify(before)}.includes(c.id));
 return {calls:calls.length,statuses:calls.map(c=>c.status),durations:calls.map(c=>c.sessionDurationMs),allTracksEnded:fixture.tracks.every(t=>t.readyState==='ended'),errors:proof.errors,
 recorders:await Promise.all(nativeRecorders.map(async(r,i)=>{const emitted=new Blob(r.proofChunks),stored=calls[0]?.tracks[i]?.blob;return {events:r.proofEvents,emitted:emitted.size,stored:stored?.size,emittedHash:await hashBlob(emitted),storedHash:stored?await hashBlob(stored):null}}))};})()`);
 if(result.calls && result.statuses.every(s=>s!=='recording') && result.recorders.every(r=>r.events.some(e=>e.event==='stop')))break;await sleep(100);
 }
 evidence.push({mode,...result});
 console.log(JSON.stringify({mode,...result},null,2));
 check(mode+' saves exactly one call',result.calls===1);
 if(mode==='long')check('actual elapsed capture exceeds former three-minute cap',result.durations[0]>180000,JSON.stringify(result.durations));
 check(mode+' terminal chunks stored byte-exact',result.recorders.every(r=>r.events.some(e=>e.event==='stop')&&r.emitted>0&&r.emitted===r.stored&&r.emittedHash===r.storedHash));
 check(mode+' releases all tracks and no renderer error',result.allTracksEnded&&result.errors.length===0);
 if(mode.startsWith('error'))check(mode+' native inactive error reproduced',result.recorders[0].events.some(e=>e.event==='error'&&e.name==='InvalidModificationError'&&e.state==='inactive'));
}

const beforeReload=await ev('(async()=>{const rows=await readCalls();return Promise.all(rows.map(async r=>({id:r.id,status:r.status,hashes:await Promise.all(r.tracks.map(t=>hashBlob(t.blob)))})))})()');
await ev('window.__oldDocument=true');await send('Page.reload');await until('!window.__oldDocument && !!document.querySelector("[data-recorder-pane]")');
const afterReload=await ev('(async()=>{const rows=await readCalls();return Promise.all(rows.map(async r=>({id:r.id,status:r.status,hashes:await Promise.all(r.tracks.map(t=>hashBlob(t.blob)))})))})()');
check('all persisted track hashes survive reload',JSON.stringify(beforeReload)===JSON.stringify(afterReload));
// Only the next document omits graceful cleanup. Other lifecycle cases keep
// genuine handlers. A late same-target listener cannot suppress earlier ones.
const abandonment=await send('Page.addScriptToEvaluateOnNewDocument',{source:`
window.abandonmentSuppressed=[];
const add=window.addEventListener.bind(window);
window.addEventListener=(type,fn,opts)=>{if(type==='pagehide'||type==='beforeunload'){abandonmentSuppressed.push(type);return}return add(type,fn,opts)};
`});
await ev('window.__oldDocument=true');await send('Page.reload');
await until('!window.__oldDocument && !!document.querySelector("[data-recorder-pane]")');
await send('Page.removeScriptToEvaluateOnNewDocument',{identifier:abandonment.identifier});
await ev(`if(!['pagehide','beforeunload'].every(t=>abandonmentSuppressed.includes(t)))throw Error('Abandonment handlers were not suppressed before plugin load')`);
await click('Grant microphone');await click('Use microphone only');await click('Record');await sleep(2400);
const interrupted=await ev('(async()=>{const r=(await readCalls()).find(r=>r.status==="recording");return {id:r.id,bytes:r.tracks[0].bytes,hash:await hashBlob(r.tracks[0].blob)}})()');
// Suppress graceful-unload callbacks to deterministically model an interrupted
// document, then perform a real reload. This is not a process-crash proof.
await ev('window.__oldDocument=true');
await send('Page.reload');await until('!window.__oldDocument && !!document.querySelector("[data-recorder-pane]")');
await until('document.body.innerText.includes("Recovered partial")');
const recovered=await ev(`(async()=>{const r=(await readCalls()).find(r=>r.id===${JSON.stringify(interrupted.id)});return {id:r.id,status:r.status,active:await navigator.locks.request('call-recorder-chunks-v2:capture:'+r.id,{ifAvailable:true},lock=>!lock),bytes:r.tracks[0].bytes,hash:await hashBlob(r.tracks[0].blob),prefixHash:await hashBlob(r.tracks[0].blob.slice(0,${interrupted.bytes}))}})()`);
check('actual reload recovers committed chunks only, labelled partial',recovered.status==='recording' && !recovered.active && interrupted.bytes>0 && recovered.bytes>=interrupted.bytes && recovered.prefixHash===interrupted.hash);
// Real IDB API failure, not a fake Saved response. Previously committed chunks survive.
await click('Grant microphone');await click('Use microphone only');await click('Record');await sleep(1400);
await ev(`window.originalAdd=IDBObjectStore.prototype.add;IDBObjectStore.prototype.add=function(...args){if(this.name==='chunks')throw new DOMException('fixture quota exhausted','QuotaExceededError');return originalAdd.apply(this,args)}`);
await until('document.body.innerText.includes("Partial, not fully saved")');
check('quota failure has no unhandled renderer exception',await ev('proof.errors.length===0'));
check('quota failure stops capture and preserves committed prefix',await ev('(async()=>{const r=(await readCalls()).at(-1);return fixture.tracks.every(t=>t.readyState==="ended") && (await readCalls()).some(r=>r.status==="partial"&&r.tracks[0].bytes>0) && document.body.innerText.includes("Local write failed")})()'));
await ev('IDBObjectStore.prototype.add=originalAdd');
// Load only one selected track on demand and prove actual playback advances.
await click('Load playback / download');await until('!!document.querySelector("audio")');
await ev('document.querySelector("audio").play()');await sleep(500);
check('selected committed audio plays',await ev('document.querySelector("audio").currentTime>0'));
const exportProof=await ev(`(async()=>{
 const card=document.querySelector('[data-call-id]');const id=card.dataset.callId;
 const source=(await readCalls()).find(r=>r.id===id).tracks[0].blob;
 const fallback=await (await fetch(card.querySelector('a[data-role^="download-"]').href)).blob();
 window.exported=[];window.exportDone=false;
 window.showSaveFilePicker=async()=>({createWritable:async()=>({write:async b=>exported.push(b),close:async()=>{exportDone=true},abort:async()=>{}})});
 return {stored:await hashBlob(source),download:await hashBlob(fallback)};
})()`);
await click('Export track to file');await until('window.exportDone');
exportProof.streamed=await ev('hashBlob(new Blob(exported))');
check('fallback download and chunk-streamed export match persisted track SHA256',exportProof.stored===exportProof.download && exportProof.stored===exportProof.streamed);
console.log(JSON.stringify({exportProof,interrupted,recovered}));
check('no backend API requests',requests.length===0);
writeFileSync(join(out,'chunked-browser.json'),JSON.stringify({fixture:'real MediaRecorder and IDB, fake microphone, instrumented 300Hz display audio/canvas; actual SDK runtime loader',checks,evidence,exportProof,interrupted,recovered},null,2));
console.log(`Finalization browser checks passed: ${checks.length}`);
}finally{ws?.close();chrome.kill('SIGTERM');await sleep(200);rmSync(profile,{recursive:true,force:true});}
