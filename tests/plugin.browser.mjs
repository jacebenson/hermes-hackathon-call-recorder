// Real runtime loader + SDK components + CSS + MediaRecorder + IndexedDB.
// Display share is instrumented (300Hz + canvas); mic is Chromium fake device.
import {createHash} from 'node:crypto'
import {spawn,execFileSync} from 'node:child_process'
import {mkdtempSync,readFileSync,writeFileSync,mkdirSync,rmSync} from 'node:fs'
import {join,resolve} from 'node:path'
import {setTimeout as sleep} from 'node:timers/promises'
const out=resolve('docs/evidence');mkdirSync(out,{recursive:true})
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
await send('Page.navigate',{url:'http://127.0.0.1:4197/'})
await until('!!document.querySelector("[data-recorder-pane]")')
const loader=await ev('proof');writeFileSync(join(out,'runtime-loader.json'),JSON.stringify(loader,null,2))
check('actual loader registered call-recorder',loader.records['call-recorder'].status==='loaded',JSON.stringify(loader.contributions))
check('one named pane only',loader.contributions.length===1&&loader.contributions[0].title==='Call recorder')
await click('Grant microphone');await click('Grant call audio');await click('Record');await sleep(2400)
const identity=await ev('backendSwitch()');check('real SDK remote/profile identity switch during capture',identity.connection==='fixture-remote-b'&&identity.profile==='fixture-profile-b'&&await ev('[...document.querySelectorAll("button")].some(b=>b.textContent==="Stop")'));
check('actual nonzero source levels',await ev('[...document.querySelectorAll("meter")].every(m=>m.value > -80)'))
await click('Stop');await until('document.querySelector("[data-save-state=saved]")')
const before=await ev(`(async()=>{const [c]=await readCalls();return{metadata:{...c,tracks:c.tracks.map(({blob,...t})=>t)},hashes:await Promise.all(c.tracks.map(t=>hashBlob(t.blob)))}})()`)
check('atomic call contains two distinct blobs and timing',before.metadata.tracks.length===2&&before.hashes[0]!==before.hashes[1]&&before.metadata.timing.videoPersisted===false)
check('stop releases audio and video',await ev('fixture.tracks.every(t=>t.readyState==="ended")'))
await send('Page.reload');await until('document.querySelectorAll("audio").length===2')
const after=await ev('(async()=>{const[c]=await readCalls();return await Promise.all(c.tracks.map(t=>hashBlob(t.blob)))})()')
check('blob hashes identical after reload',JSON.stringify(before.hashes)===JSON.stringify(after));writeFileSync(join(out,'blob-hashes.json'),JSON.stringify({before:before.hashes,after},null,2))
await ev('Promise.all([...document.querySelectorAll("audio")].map(a=>a.play()))');await sleep(650)
check('both real audio players advance',await ev('[...document.querySelectorAll("audio")].every(a=>a.currentTime>.25)'))
const exports=await ev(`Promise.all([...document.querySelectorAll('a[data-role^="download-"]')].map(async a=>({name:a.download,bytes:Array.from(new Uint8Array(await(await fetch(a.href)).arrayBuffer()))})))`)
check('downloaded bytes equal reloaded stored Blob hashes',JSON.stringify(exports.map(f=>createHash('sha256').update(Buffer.from(f.bytes)).digest('hex')))===JSON.stringify(after));
for(const f of exports){const path=join(out,f.name.startsWith('mic-')?'fixture-mic.webm':'fixture-call-audio.webm');writeFileSync(path,Buffer.from(f.bytes));const streams=JSON.parse(execFileSync('ffprobe',['-v','quiet','-show_streams','-of','json',path])).streams;check(`${f.name} audio only`,streams.length===1&&streams[0].codec_type==='audio');const pcm=execFileSync('ffmpeg',['-v','error','-i',path,'-f','s16le','-ac','1','-ar','8000','pipe:1']);let energy=0;for(let i=0;i<pcm.length;i+=2)energy+=pcm.readInt16LE(i)**2;check(`${f.name} decoded non-silent`,energy>0)}
await ev(`(()=>{const i=document.querySelector('input[aria-label="Call title"]');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'Fixture renamed');i.dispatchEvent(new Event('input',{bubbles:true}))})()`);await click('Save title');await sleep(150)
check('rename persisted',await ev('(async()=> (await readCalls())[0].title==="Fixture renamed")()'))
const metadata=await ev(`(async()=>JSON.parse(await(await fetch(document.querySelector('a[data-role="metadata"]').href)).text()))()`)
check('metadata export names saved call and provenance',metadata.title==='Fixture renamed'&&metadata.provenance.backend==='none')
for(const theme of ['dark','light'])for(const width of [390,1440]){await ev(`setTheme('${theme}')`);await send('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});await sleep(250);check(`${theme} ${width} no horizontal overflow`,await ev('document.documentElement.scrollWidth<=innerWidth'));await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});check(`${theme} ${width} keyboard focus`,await ev('document.activeElement!==document.body'));const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});writeFileSync(join(out,`${theme}-${width}.png`),Buffer.from(shot.data,'base64'))}
await click('Delete this call');await click('Cancel');check('delete cancellation preserves call',await ev('(async()=> (await readCalls()).length===1)()'))
// Quota failure must keep immediate exports and retry.
await click('Grant microphone');await ev('fixture.mode="no-audio"');await click('Grant call audio');check('no-audio share explicit, released video',await ev('document.body.innerText.includes("no shared-audio track")&&fixture.tracks.filter(t=>t.kind==="video").every(t=>t.readyState==="ended")'))
await click('Use microphone only');await ev('window.originalPut=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(){throw new DOMException("fixture quota","QuotaExceededError")}');await click('Record');await sleep(1200);await click('Stop');await until('document.querySelector("[data-save-state=failed]")')
check('quota failure keeps immediate downloads',await ev('document.querySelectorAll("a[download]").length>=5'))
check('quota is atomic no partial record',await ev('(async()=> (await readCalls()).length===1)()'))
await ev('IDBObjectStore.prototype.put=originalPut');await click('Retry save');await until('document.querySelector("[data-save-state=saved]")');check('retry saves explicit mic-only call',await ev('(async()=> (await readCalls()).some(c=>c.micOnly&&c.tracks.length===1))()'))
await click('Release / reset inputs');await ev('fixture.deny=true');await click('Grant microphone');check('permission denial visible',await ev('document.body.innerText.includes("Microphone permission was refused")'))
check('denial saves nothing',await ev('(async()=> (await readCalls()).length===2)()'));await ev('fixture.deny=false')
await click('Grant microphone');await ev('fixture.mode="audio"');await click('Grant call audio');await click('Record');await sleep(300);await ev('mountPane(false)');await sleep(200);check('pane unmount releases granted tracks',await ev('fixture.tracks.every(t=>t.readyState==="ended")'));await ev('mountPane(true)');await sleep(200)
await ev('fixture.late=true');await click('Grant microphone');await until('!!fixture.resolveLate');await ev('disposePlugin();fixture.resolveLate()');await sleep(200);check('disable late permission return releases tracks',await ev('fixture.tracks.every(t=>t.readyState==="ended")'))
check('disable preserves saved data',await ev('(async()=> (await readCalls()).length===2)()'))
await send('Page.reload');await until('document.querySelectorAll("audio").length===3');await click('Grant microphone');await click('Grant call audio');await click('Record');await sleep(300);await ev('window.dispatchEvent(new PageTransitionEvent("pagehide"))');await sleep(100);check('client pagehide releases tracks',await ev('fixture.tracks.every(t=>t.readyState==="ended")'))
check('no recorder backend requests',requests.length===0,JSON.stringify(requests))
await send('Page.reload');await until('document.querySelectorAll("article").length===2');await ev('backendSwitch()');await click('Delete this call');await click('Delete permanently');await sleep(200);check('confirmed delete scopes one call',await ev('(async()=> (await readCalls()).length===1)()'))
const survivor=await ev('(async()=>{const[c]=await readCalls();return {id:c.id,hashes:await Promise.all(c.tracks.map(t=>hashBlob(t.blob)))}})()');check('delete leaves survivor exact Blob content',survivor.id===before.metadata.id&&JSON.stringify(survivor.hashes)===JSON.stringify(before.hashes));
check('renderer no uncaught errors',await ev('proof.errors.length===0'))
writeFileSync(join(out,'checks.json'),JSON.stringify({displayShare:'instrumented 300Hz WebAudio + canvas; real screen audio and Electron unverified',checks},null,2))
console.log(`Browser checks passed: ${checks.length}`)
}finally{ws?.close();chrome.kill('SIGTERM');await sleep(200);rmSync(profile,{recursive:true,force:true});writeFileSync(join(out,'checks.json'),JSON.stringify({displayShare:'instrumented; native Electron and real screen-audio unverified',checks},null,2))}
