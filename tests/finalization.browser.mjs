// Real runtime loader + SDK components + CSS + MediaRecorder + IndexedDB.
// Display share is instrumented (300Hz + canvas); mic is Chromium fake device.
import {createHash} from 'node:crypto'
import {spawn,execFileSync} from 'node:child_process'
import {mkdtempSync,readFileSync,writeFileSync,mkdirSync,rmSync} from 'node:fs'
import {join,resolve} from 'node:path'
import {setTimeout as sleep} from 'node:timers/promises'
const out=process.env.FINALIZATION_OUT||join(process.env.TMPDIR,'call-recorder-finalization-evidence');mkdirSync(out,{recursive:true})
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
window.readCalls=()=>new Promise((resolve,reject)=>{const r=indexedDB.open('call-recorder-local-library',1);r.onsuccess=()=>{const db=r.result;const q=db.transaction('calls').objectStore('calls').getAll();q.onsuccess=()=>{db.close();resolve(q.result)}};r.onerror=()=>reject(r.error)});
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
for(const mode of (process.env.FINALIZATION_MODES||'stop-disable,stop-unmount,active-unmount,error-mic,error-dual,repeated-stop').split(',')){
 await send('Page.navigate',{url:process.env.PROOF_URL||'http://127.0.0.1:4197/'});
 await until('!!document.querySelector("[data-recorder-pane]")');
 const before=await ev('(async()=> (await readCalls()).map(c=>c.id))()');
 await click('Grant microphone');
 if(mode==='error-mic') await click('Use microphone only');else await click('Grant call audio');
 await click('Record');await sleep(1600);
 await ev(`(()=>{
 const mode=${JSON.stringify(mode)};
 const stop=[...document.querySelectorAll('button')].find(b=>b.textContent==='Stop');
 if(mode.startsWith('error'))nativeRecorders[0].stream.removeTrack(nativeRecorders[0].stream.getTracks()[0]);
 else if(mode==='active-unmount')mountPane(false);
 else {stop.click();if(mode==='stop-disable')disposePlugin();if(mode==='stop-unmount')mountPane(false);if(mode==='repeated-stop'){stop.click();stop.click()}}
 })()`);
 if(!mode.startsWith('error'))check(mode+' immediately releases all acquired tracks',await ev('fixture.tracks.every(t=>t.readyState==="ended")'));
 let result;
 for(let i=0;i<100;i++){
 result=await ev(`(async()=>{
 const calls=(await readCalls()).filter(c=>!${JSON.stringify(before)}.includes(c.id));
 return {calls:calls.length,allTracksEnded:fixture.tracks.every(t=>t.readyState==='ended'),errors:proof.errors,
 recorders:await Promise.all(nativeRecorders.map(async(r,i)=>{const emitted=new Blob(r.proofChunks),stored=calls[0]?.tracks[i]?.blob;return {events:r.proofEvents,emitted:emitted.size,stored:stored?.size,emittedHash:await hashBlob(emitted),storedHash:stored?await hashBlob(stored):null}}))};})()`);
 if(result.calls)break;await sleep(100);
 }
 evidence.push({mode,...result});
 console.log(JSON.stringify({mode,...result},null,2));
 check(mode+' saves exactly one call',result.calls===1);
 check(mode+' terminal chunks stored byte-exact',result.recorders.every(r=>r.events.some(e=>e.event==='stop')&&r.emitted>0&&r.emitted===r.stored&&r.emittedHash===r.storedHash));
 check(mode+' releases all tracks and no renderer error',result.allTracksEnded&&result.errors.length===0);
 if(mode.startsWith('error'))check(mode+' native inactive error reproduced',result.recorders[0].events.some(e=>e.event==='error'&&e.name==='InvalidModificationError'&&e.state==='inactive'));
}
check('no backend API requests',requests.length===0);
writeFileSync(join(out,'finalization.json'),JSON.stringify({fixture:'real MediaRecorder and IDB, fake microphone, instrumented 300Hz display audio/canvas; actual SDK runtime loader',checks,evidence},null,2));
console.log(`Finalization browser checks passed: ${checks.length}`);
}finally{ws?.close();chrome.kill('SIGTERM');await sleep(200);rmSync(profile,{recursive:true,force:true});}
