// Drives the packaged plugin inside the real desktop shell fixture: real runtime
// loader, real ChatSidebar, real route table, genuine SDK components, ThemeProvider
// and production CSS. Verifies the sidebar row and the /call-recorder page, then
// screenshots 390px and 1440px in dark and light while a call is recording.
import {spawn} from 'node:child_process'
import {mkdtempSync,readFileSync,writeFileSync,mkdirSync,rmSync} from 'node:fs'
import {join,resolve} from 'node:path'
import {setTimeout as sleep} from 'node:timers/promises'
const out=resolve(process.env.PROOF_EVIDENCE||join(process.env.TMPDIR,'call-recorder-sidebar-proof'));mkdirSync(out,{recursive:true})
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
const until=async(expr,ms=30000)=>{const end=Date.now()+ms;while(Date.now()<end){try{if(await ev(`Boolean(${expr})`))return}catch{}await sleep(100)}throw Error('Timeout '+expr+' '+await ev('JSON.stringify({hash:location.hash,body:document.body.innerText.slice(0,1500),proof:window.proof})'))}
await send('Page.enable');await send('Runtime.enable');await send('Network.enable')
ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')console.log('EXC',JSON.stringify(m.params).slice(0,2000))})
const requests=[];ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.method==='Network.requestWillBeSent'&&m.params.request.url.includes('/api/'))requests.push({url:m.params.request.url,method:m.params.request.method})})

// Live transcript grows one line per poll, so the tail is visibly live.
let stage='idle',polls=0
const transcript=[
 '[00:00:03] them> Can you see the transcript on the page yet?',
 '[00:00:07] you> Yes, it is scrolling in as we talk.',
 '[00:00:11] them> And the sidebar row opens the full page?',
 '[00:00:14] you> It does, and the pane is still in the layout.',
 '[00:00:19] them> Good. Let us ship it tonight.',
 '[00:00:23] you> Starting with the installer check.'
]
const status=()=>({ok:true,recording:stage==='recording',live:stage==='recording',elapsed:23,bytes:1048576,
  file:'/fixture/call-20260101-120000.mkv',monitor:'fixture-monitor',mic:'fixture-mic',transcriptions:[]})
await send('Fetch.enable',{patterns:[{urlPattern:'*/api/*',requestStage:'Request'}]})
ws.addEventListener('message',async e=>{
 const m=JSON.parse(e.data);if(m.method!=='Fetch.requestPaused')return
 const p=m.params,path=new URL(p.request.url).pathname
 let body={ok:true}
 if(path.endsWith('/profiles/active'))body={ok:true,current:'default',profile:'default'}
 else if(path.endsWith('/profiles'))body={ok:true,profiles:[{name:'default',display_name:'default',is_default:true}]}
 else if(path.endsWith('/sessions'))body={ok:true,sessions:[]}
 else if(path.endsWith('/capture-host'))body={ok:true,capture_mode:'computer',host:'fixture-capture-box',backend_host:'fixture-backend',recent_hosts:[]}
 else if(path.endsWith('/config'))body={ok:true,host:'fixture-capture-box',host_label:'fixture-capture-box',live:true,transcription:true,ready:true,transcription_detail:'tiny.en on cpu'}
 else if(path.endsWith('/start')){await sleep(400);stage='recording';body=status()}
 else if(path.endsWith('/stop')){stage='history';body={ok:true,file:'/fixture/call-20260101-120000.mkv',transcription:{file:'/fixture/call-20260101-120000.mkv',state:'ready',ok:true,engine:'live'}}}
 else if(path.endsWith('/status'))body=status()
 else if(path.endsWith('/recordings'))body={ok:true,items:stage==='history'?[{file:'/fixture/call-20260101-120000.mkv',name:'call-20260101-120000.mkv',title:'Fixture call',machine:'fixture-capture-box',bytes:1048576,duration:23,transcript:'/fixture/call-20260101-120000.mkv.transcript.md'}]:[]}
 else if(path.endsWith('/transcript/read'))body={ok:true,markdown:'# Fixture transcript\n**[00:00:03] them:** Can you see the transcript on the page yet?\n**[00:00:07] you:** Yes, it is scrolling in as we talk.'}
 else if(path.endsWith('/transcript')){polls++;body={ok:true,lines:transcript.slice(0,Math.min(transcript.length,2+polls)),text:transcript.slice(0,2+polls).join('\n')}}
 await send('Fetch.fulfillRequest',{requestId:p.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify(body)).toString('base64')})
})
await send('Page.addScriptToEvaluateOnNewDocument',{source:`
window.hermesDesktop={api:async req=>(await fetch(req.path,{method:req.method||'GET',headers:{'Content-Type':'application/json'},body:req.body?JSON.stringify(req.body):undefined})).json()};
navigator.mediaDevices.getUserMedia=()=>{throw Error('Physical microphone forbidden')};
navigator.mediaDevices.getDisplayMedia=()=>{throw Error('Screen capture forbidden')};
window.__timers=new Map();{const si=window.setInterval,ci=window.clearInterval;
 window.setInterval=(fn,ms,...a)=>{const id=si(fn,ms,...a);__timers.set(id,ms);return id};
 window.clearInterval=id=>{__timers.delete(id);return ci(id)}}
window.longTimers=()=>[...__timers.values()].filter(ms=>ms===1000).length;`})
await send('Page.navigate',{url:'http://127.0.0.1:4197/'})
await until("window.proof && window.proof.records && window.proof.records['call-recorder'] && window.proof.records['call-recorder'].status==='loaded'")
await until("[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Call recorder')")
check('real loader loaded the packaged plugin',await ev("proof.records['call-recorder'].status==='loaded'"))
check('route contribution registered (routes area)',await ev("proof.routes.some(r=>r.data&&r.data.path==='/call-recorder')"))
check('sidebar nav contribution registered (sidebar.nav area)',await ev("proof.nav.some(r=>r.data&&r.data.path==='/call-recorder'&&r.data.label==='Call recorder'&&r.data.codicon==='record')"))
const navRow=await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Call recorder');if(!b)return null;const r=b.getBoundingClientRect();return {label:b.textContent.trim(),name:b.getAttribute('aria-label'),tag:b.tagName,w:Math.round(r.width),h:Math.round(r.height),tour:b.querySelector('[data-tour]')?.getAttribute('data-tour'),icon:!!b.querySelector('.codicon,i[class*=codicon]'),html:b.outerHTML.slice(0,400)}})()`)
check('sidebar row exposes the accessible name Call recorder',navRow&&navRow.label==='Call recorder',JSON.stringify(navRow))
const shot=async name=>{const s=await send('Page.captureScreenshot',{format:'png'});writeFileSync(join(out,name+'.png'),Buffer.from(s.data,'base64'));return join(out,name+'.png')}
const viewport=async(w,h)=>{await send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:w<600});await sleep(400)}
await viewport(1440,900)
await shot('01-sidebar-idle-1440-dark')
// Click the row itself: the host's nav button calls host.navigate and the real
// router mounts the contributed page.
await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Call recorder');b.click()})()`)
await until("location.hash==='#/call-recorder'")
await until("!!document.querySelector('[data-recorder-surface=page]')")
check('clicking the sidebar row navigates to the page route',await ev("location.hash==='#/call-recorder'"))
check('contributed page mounted in the workspace',await ev("!!document.querySelector('[data-recorder-surface=page]')"))
check('page names itself Call recorder',await ev("[...document.querySelectorAll('[data-recorder-surface=page] h1')].some(h=>h.textContent.trim()==='Call recorder')"))
check('pane still mounted beside the page',await ev("!!document.querySelector('[data-recorder-surface=pane]')"))
check('nav row highlights on its own path',await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Call recorder');return b.className.includes('control-active')})()`))
await shot('02-page-idle-1440-dark')

// Both Record buttons clicked in ONE task, while /start is delayed 400ms: the
// shared command guard must let exactly one start through.
const before=requests.length
await ev(`(()=>{const page=document.querySelector('[data-recorder-surface=page]');const pane=document.querySelector('[data-recorder-surface=pane]');
 const b=[...page.querySelectorAll('button')].find(b=>b.textContent.trim()==='Record');
 const p=[...pane.querySelectorAll('button')].find(b=>b.textContent.trim()==='Record');
 if(!b||!p)throw Error('both Record buttons must be enabled while idle');
 b.click();p.click()})()`)
await until("[...document.querySelectorAll('[data-recorder-surface=page] button')].some(b=>b.textContent.trim()==='Stop')")
const starts=requests.filter(r=>r.url.includes('/start'))
check('two surfaces clicked, exactly one POST /start',starts.length===1,`starts=${starts.length} requests=${requests.length-before}`)
await sleep(5200)
const liveText=await ev(`(()=>{const p=[...document.querySelectorAll('[data-recorder-surface=page] [aria-label="Live transcript"]')];return p.map(s=>s.innerText)})()`)
check('page shows a live transcript panel',liveText.length===1)
check('live transcript text is readable on the page',/scrolling in as we talk|ship it tonight|installer check/.test(liveText[0]||''),JSON.stringify(liveText).slice(0,400))
check('live panel names the capture machine',(liveText[0]||'').includes('fixture-capture-box'))
check('one timer on the page',await ev("[...document.querySelectorAll('[data-recorder-surface=page]')].some(p=>[...p.querySelectorAll('*')].filter(n=>/^0?0:2[0-9]$/.test(n.textContent.trim())).length===1)"))
check('one Record/Stop control on the page',await ev(`(()=>{const p=document.querySelector('[data-recorder-surface=page]');
 const rec=[...p.querySelectorAll('button')].filter(b=>['Record','Stop'].includes(b.textContent.trim()));
 return rec.length===1&&rec[0].textContent.trim()==='Stop'})()`))
check('exactly one running 1s interval with pane and page mounted',await ev('longTimers()===1'),String(await ev('longTimers()')))
const statusCount=requests.filter(r=>r.url.includes('/status')).length
check('both surfaces share one status poll (React Query dedupe)',statusCount<=8,`/status requests in ~6.5s: ${statusCount}`)

// Four screenshots: the page with live transcript at 390 and 1440, dark and light.
await send('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false})
await ev("setTheme('dark')");await sleep(500)
await shot('03-page-live-1440-dark')
await ev("setTheme('light')");await sleep(500)
await shot('04-page-live-1440-light')
await viewport(390,844)
await ev("setTheme('dark')");await sleep(600)
await shot('05-page-live-390-dark')
await ev("setTheme('light')");await sleep(600)
await shot('06-page-live-390-light')
const sizes=await ev(`(()=>{const p=document.querySelector('[data-recorder-surface=page]');return {w:innerWidth,h:innerHeight,pageW:Math.round(p.getBoundingClientRect().width),overflowX:document.documentElement.scrollWidth>innerWidth+1,liveH:Math.round(document.querySelector('[data-recorder-surface=page] [aria-label="Live transcript"]').getBoundingClientRect().height),timer:document.querySelector('[data-recorder-surface=page]').innerText.slice(0,300)}})()`)
check('390px layout does not overflow horizontally',sizes.overflowX===false,JSON.stringify(sizes))
check('live panel keeps usable height at 390px',sizes.liveH>=120,String(sizes.liveH))
check('renderer has no uncaught errors',await ev('proof.errors.length===0'),String(await ev('JSON.stringify(proof.errors)')))
writeFileSync(join(out,'checks.json'),JSON.stringify({out,checks,sizes,requests:requests.length,fixtures:'REST fixture only; no physical capture or live ASR'},null,2))
console.log(JSON.stringify({out,checks},null,2))
}finally{ws?.close();chrome.kill('SIGTERM');await sleep(200);rmSync(profile,{recursive:true,force:true})}
