// Real runtime loader + SDK components + CSS + MediaRecorder + IndexedDB.
// Display share is instrumented (300Hz + canvas); mic is Chromium fake device.
import {createHash} from 'node:crypto'
import {spawn,execFileSync} from 'node:child_process'
import {mkdtempSync,readFileSync,writeFileSync,mkdirSync,rmSync} from 'node:fs'
import {join,resolve} from 'node:path'
import {setTimeout as sleep} from 'node:timers/promises'
const out=resolve(process.env.PROOF_EVIDENCE||join(process.env.TMPDIR,'native-sdk-proof'));mkdirSync(out,{recursive:true})
const profile=mkdtempSync(join(process.env.TMPDIR,'call-recorder-profile-'))
const chrome=spawn(process.env.PROOF_CHROME||`${process.env.HOME}/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome`,['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-first-run','--disable-extensions','--autoplay-policy=no-user-gesture-required','--mute-audio','about:blank'],{stdio:['ignore','ignore','pipe']})
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

let stage='idle'
const file='/fixture/call-20260101-120000.mkv'
const status=()=>({ok:true,recording:stage==='recording',live:stage==='recording',elapsed:12,bytes:30000,file,monitor:'fixture-monitor',mic:'fixture-mic',transcriptions:[]})
await send('Fetch.enable',{patterns:[{urlPattern:'*/api/*',requestStage:'Request'}]})
ws.addEventListener('message',async e=>{
 const m=JSON.parse(e.data);if(m.method!=='Fetch.requestPaused')return
 const p=m.params,path=new URL(p.request.url).pathname
 let body={ok:true}
 // The fixture now mounts the real sidebar, which reads the backend's profile
 // and session lists on boot. Answer them so the shell can render.
 if(path.endsWith('/profiles/active'))body={ok:true,current:'default',profile:'default'}
 else if(path.endsWith('/profiles'))body={ok:true,profiles:[{name:'default',display_name:'default',is_default:true}]}
 else if(path.endsWith('/sessions'))body={ok:true,sessions:[]}
 if(path.endsWith('/capture-host'))body={ok:true,capture_mode:'computer',host:'fixture-capture-box',backend_host:'fixture-backend',recent_hosts:[]}
 else if(path.endsWith('/config'))body={ok:true,host:'fixture-capture-box',host_label:'fixture-capture-box',live:true,transcription:true,ready:true}
 else if(path.endsWith('/start')){stage='recording';body=status()}
 else if(path.endsWith('/stop')){stage='history';body={ok:true,file,transcription:{file,state:'ready',ok:true,engine:'live'}}}
 else if(path.endsWith('/status'))body=status()
 else if(path.endsWith('/recordings'))body={ok:true,items:stage==='history'?[{file,name:'call-20260101-120000.mkv',title:'Synthetic fixture call',machine:'fixture-capture-box',bytes:30000,duration:12,transcript:file.replace('.mkv','.transcript.md')}]:[]}
 else if(path.endsWith('/transcript/read'))body={ok:true,markdown:'# Fixture transcript\n[00:00:01] you> Synthetic transcript only'}
 else if(path.endsWith('/transcript'))body={ok:true,lines:['[00:00:01] you> Synthetic live speech'],text:'[00:00:01] you> Synthetic live speech'}
 await send('Fetch.fulfillRequest',{requestId:p.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify(body)).toString('base64')})
})
await send('Page.addScriptToEvaluateOnNewDocument',{source:`window.hermesDesktop={api:async req=>(await fetch(req.path,{method:req.method||'GET',headers:{'Content-Type':'application/json'},body:req.body?JSON.stringify(req.body):undefined})).json()};navigator.mediaDevices.getUserMedia=()=>{throw Error('Physical microphone forbidden')};navigator.mediaDevices.getDisplayMedia=()=>{throw Error('Screen capture forbidden')}`})
await send('Page.navigate',{url:'http://127.0.0.1:4197/'})
// A desktop window: the fixture's layout pane sits beside the workspace, as it
// does in the real app once the pane is docked. Navigation clears the override.
await until('window.proof && document.querySelector("[data-recorder-surface]")')
await send('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false})
await sleep(300)
await until("document.body.innerText.includes('fixture-capture-box')")
check('real loader loaded native entrypoint',await ev("proof.records['call-recorder'].status==='loaded'"))
check('native idle names actual fixture capture machine',await ev("document.body.innerText.includes('fixture-capture-box')"))
const shot=async name=>{const s=await send('Page.captureScreenshot',{format:'png'});writeFileSync(join(out,name+'.png'),Buffer.from(s.data,'base64'))}
await shot('native-idle')
await click('Record')
await until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Stop'&&!b.disabled)")
check('Record routes to native start',requests.some(p=>p.endsWith('/start')))
await shot('native-recording')
await ev(`document.querySelector('[aria-label="Toggle live transcript"]')?.click()`)
await sleep(300)
await shot('native-live')
await click('Stop')
await until("document.body.innerText.includes('Synthetic fixture call')")
check('Stop routes to native stop and history',requests.some(p=>p.endsWith('/stop')))
await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Synthetic fixture call'));b.click()})()`)
await until("document.body.innerText.includes('Synthetic transcript only')")
check('library reads native transcript route',requests.some(p=>p.includes('/transcript/read')))
await shot('native-transcript-history')
check('no capture/upload API boundary crossed',!requests.some(p=>p.includes('/browser-audio')))
check('renderer has no uncaught errors',await ev('proof.errors.length===0'))
console.log(JSON.stringify({out,checks,requests},null,2))
}finally{ws?.close();chrome.kill('SIGTERM');await sleep(200);rmSync(profile,{recursive:true,force:true});writeFileSync(join(out,'checks.json'),JSON.stringify({fixtures:'REST fixture only; no physical capture, live ASR or Electron window tested',checks},null,2))}
