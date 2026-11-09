// Offline Chromium proof of the real pane using Hermes's genuine SDK controls.
// No installation, backend settings, microphone, or recordings are touched.
// HERMES_SOURCE and CHROMIUM may override the existing local toolchain paths.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, writeFileSync, mkdtempSync, readdirSync } from 'node:fs'
import { join, resolve, basename, extname } from 'node:path'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { fileURLToPath } from 'node:url'

const repo = resolve(fileURLToPath(new URL('..', import.meta.url)))
const hermes = process.env.HERMES_SOURCE || '/home/jace/.hermes/hermes-agent'
const desktop = join(hermes, 'apps/desktop')
const require = createRequire(join(desktop, 'package.json'))
const { build } = require('esbuild')
const proof = mkdtempSync(join(process.env.TMPDIR, 'unified-target-proof-'))
const chromium = process.env.CHROMIUM || '/home/jace/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome'
const src = join(desktop, 'src')
const sdkFile = join(proof, 'sdk.jsx')
const entry = join(proof, 'entry.jsx')
writeFileSync(sdkFile, `
import {useSyncExternalStore} from 'react'
export * from '${src}/components/ui/select'
export * from '${src}/components/ui/button'
export * from '${src}/components/ui/input'
export * from '${src}/components/ui/badge'
export * from '${src}/components/ui/switch'
export {cn} from '${src}/lib/utils'
const listeners=new Set();let version=0
const emit=()=>{version++;for(const f of listeners)f()}
window.queryData={'capture-host':{ok:true,capture_mode:'browser',host:'',backend_host:'hermes-host',recent_hosts:['hermes-host','framework','audio-laptop']},status:{},config:{},recordings:{items:[]}}
export const useQuery=q=>{useSyncExternalStore(f=>{listeners.add(f);return()=>listeners.delete(f)},()=>version);return{data:window.queryData[q.queryKey[1]]||{}}}
window.qc={setQueryData:(key,fn)=>{window.queryData[key[1]]=typeof fn==='function'?fn(window.queryData[key[1]]):fn;emit()},invalidateQueries:()=>{}}
export const useQueryClient=()=>window.qc
export const StatusDot = () => null
const atom = value => ({get:()=>value,listen:()=>()=>{}})
export const host = {state:{connectionId:atom('offline-proof'),profile:atom('offline-proof')},notify:n=>window.notices.push(n)}
`)
writeFileSync(entry, `
import {createRoot} from 'react-dom/client'
import {nousTheme} from '${src}/themes/presets'
import plugin from '${repo}/desktop/plugin.js'
window.requests=[]; window.notices=[]
const qc=window.qc
window.fixture={ok:true,capture_mode:'browser',host:'',backend_host:'hermes-host',recent_hosts:['hermes-host','framework','audio-laptop']}
let recording=false
window.theme=mode=>{
 const c=mode==='dark'?nousTheme.darkColors:nousTheme.colors
 document.documentElement.classList.toggle('dark',mode==='dark')
 const mapping={'--theme-foreground':c.foreground,'--theme-primary':c.primary,'--theme-secondary':c.secondary,'--theme-accent-soft':c.accent,'--theme-midground':c.midground||c.ring,'--theme-warm':c.primary,'--theme-background-seed':c.background,'--theme-sidebar-seed':c.sidebarBackground||c.background,'--theme-card-seed':c.card,'--theme-elevated-seed':c.popover,'--theme-bubble-seed':c.userBubble||c.popover,'--dt-primary-foreground':c.primaryForeground,'--dt-border':c.border,'--dt-input':c.input,'--dt-ring':c.ring,'--dt-muted':c.muted}
 for(const[k,v]of Object.entries(mapping)) document.documentElement.style.setProperty(k,v)
}
window.theme('dark')
window.setRecording=value=>{recording=value;qc.setQueryData(['call-recorder','status'],{recording})}
const contributions=[]
plugin.register({register:c=>contributions.push(c),storage:{get:()=>true,set(){}},onDispose(){},rest:async(path,options={})=>{
 if(options.method==='POST'){
  window.requests.push({path,body:options.body})
  if(path==='/capture-host') window.fixture={...window.fixture,host:options.body.host,capture_mode:'computer'}
  else if(path==='/capture-mode') window.fixture={...window.fixture,capture_mode:options.body.capture_mode}
  else throw Error('Forbidden proof write: '+path)
 }
 if(path==='/capture-host'||path==='/capture-mode')return window.fixture
 if(path==='/status')return{recording}
 if(path==='/recordings')return{items:[]}
 return{}
}})
createRoot(document.getElementById('root')).render(contributions.find(c=>c.id==='call-recorder-pane').render())
`)
const icons=join(proof,'icons.jsx')
writeFileSync(icons,`export const Loader2=()=>null`)
await build({entryPoints:[entry],outfile:join(proof,'bundle.js'),bundle:true,format:'esm',jsx:'automatic',nodePaths:[join(hermes,'node_modules')],alias:{'@/lib/icons':icons,'@':src,'@hermes/plugin-sdk':sdkFile},define:{'process.env.NODE_ENV':'"production"'},logLevel:'warning'})
const cssDir = join(desktop,'dist-webapp/assets')
const cssName = readdirSync(cssDir).find(n=>/^main-.*\.css$/.test(n))
assert(cssName, 'existing production Hermes desktop CSS must be available')
const css = readFileSync(join(cssDir,cssName))
const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/desktop.css"><style>body{margin:0;background:var(--dt-background);color:var(--ui-text-primary);font-family:system-ui}#root{height:700px;width:760px}</style><div id="root"></div><script type="module" src="/bundle.js"></script>`
const assets=new Set(readdirSync(cssDir))
const server=createServer((req,res)=>{
 const name=req.url,asset=basename(name)
 if(assets.has(asset)){
  res.setHeader('Content-Type',extname(asset)==='.woff2'?'font/woff2':extname(asset)==='.ttf'?'font/ttf':'application/octet-stream')
  res.end(readFileSync(join(cssDir,asset)));return
 }
 res.setHeader('Content-Type',name==='/bundle.js'?'text/javascript':name==='/desktop.css'?'text/css':'text/html')
 res.end(name==='/bundle.js'?readFileSync(join(proof,'bundle.js')):name==='/desktop.css'?css:html)
})
await new Promise(r=>server.listen(0,'127.0.0.1',r))
const chrome=spawn(chromium,['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port=0','--user-data-dir='+join(proof,'chrome-profile'),'--window-size=900,760','about:blank'],{stdio:['ignore','ignore','pipe']})
let stderr=''
chrome.stderr.on('data',chunk=>{stderr+=chunk})
const wait=async(fn)=>{for(let i=0;i<200;i++){const result=await fn();if(result)return result;await new Promise(r=>setTimeout(r,50))}throw Error('Timed out waiting for browser readiness')}
let socket
const summary={proof,repo,sdkSource:join(src,'components/ui/select.tsx'),cssSource:join(cssDir,cssName),mocks:['capture-host data and REST','query cache adapter','host identity and notices','status dot and loading icon'],themes:[],screenshots:[]}
try{
 const endpoint=await wait(()=>stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1])
 socket=new WebSocket(endpoint)
 await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j})
 let id=0;const pending=new Map()
 socket.onmessage=event=>{const msg=JSON.parse(event.data);if(msg.id){const p=pending.get(msg.id);pending.delete(msg.id);msg.error?p.reject(Error(JSON.stringify(msg.error))):p.resolve(msg.result)}}
 const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});socket.send(JSON.stringify({id:n,method,params,...(sessionId?{sessionId}:{})}))})
 const {targetInfos}=await send('Target.getTargets')
 const target=targetInfos.find(t=>t.type==='page')
 assert(target,'Chromium page exists')
 const {sessionId}=await send('Target.attachToTarget',{targetId:target.targetId,flatten:true})
 const cdp=(m,p)=>send(m,p,sessionId)
 const evaluate=async expression=>{const result=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value}
 const key=async(key,code=key)=>{await cdp('Input.dispatchKeyEvent',{type:'keyDown',key,code});await cdp('Input.dispatchKeyEvent',{type:'keyUp',key,code})}
 const shot=async name=>{const path=join(proof,name+'.png');const {data}=await cdp('Page.captureScreenshot',{format:'png'});writeFileSync(path,Buffer.from(data,'base64'));summary.screenshots.push(path)}
 await cdp('Emulation.setDeviceMetricsOverride',{width:900,height:760,deviceScaleFactor:1,mobile:false})
 await cdp('Page.navigate',{url:'http://127.0.0.1:'+server.address().port})
 await wait(()=>evaluate(`!!document.querySelector('[role="combobox"]') && !document.querySelector('[role="combobox"]').disabled`))
 const expected=['This device’s microphone','System audio + mic on hermes-host (this backend)','System audio + mic on framework','System audio + mic on audio-laptop']
 const contrastScript=`(()=>{
  const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const cx=canvas.getContext('2d');
  const rgba=color=>{cx.clearRect(0,0,1,1);cx.fillStyle=color;cx.fillRect(0,0,1,1);return [...cx.getImageData(0,0,1,1).data].map((v,i)=>i===3?v/255:v)};
  const blend=(a,b)=>a.slice(0,3).map((v,i)=>v*a[3]+b[i]*(1-a[3])).concat(1);
  const bg=el=>{if(!el)return[255,255,255,1];return blend(rgba(getComputedStyle(el).backgroundColor),bg(el.parentElement))};
  const lum=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
  return [...document.querySelectorAll('[role="combobox"],[role="option"],[data-capture-source]')].map(el=>{const background=bg(el),foreground=blend(rgba(getComputedStyle(el).color),background),a=lum(foreground),b=lum(background);return{text:el.textContent,foreground,background,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)}})
 })()`
 for(const theme of ['dark','light']){
  await evaluate(`window.theme('${theme}')`)
  assert.equal(await evaluate(`document.querySelectorAll('[role="combobox"][aria-label="Audio source"]').length`),1)
  assert.equal(await evaluate(`document.querySelectorAll('[aria-label="Capture machine"]').length`),0)
  await shot(theme+'-browser')
  const closedContrast=await evaluate(contrastScript)
  await evaluate(`document.querySelector('[role="combobox"]').focus()`)
  await key('ArrowDown')
  await wait(()=>evaluate(`document.querySelectorAll('[role="option"]').length===4`))
  const labels=await evaluate(`[...document.querySelectorAll('[role="option"]')].map(el=>el.textContent)`)
  assert.deepEqual(labels,expected)
  const openContrast=await evaluate(contrastScript)
  for(const item of [...closedContrast,...openContrast])assert(item.ratio>=4.5,theme+' contrast below 4.5: '+JSON.stringify(item))
  await shot(theme+'-options')
  // Real keyboard navigation chooses the backend entry, with no mouse click.
  await key('Home')
  await wait(()=>evaluate(`document.activeElement?.textContent==='This device’s microphone'`))
  await key('ArrowDown')
  await wait(()=>evaluate(`document.activeElement?.textContent==='System audio + mic on hermes-host (this backend)'`))
  await key('Enter')
  await wait(()=>evaluate(`document.querySelector('[data-capture-source]').textContent==='Microphone and system audio, saved on hermes-host.'`))
  assert.deepEqual(await evaluate('window.requests.at(-1)'),{path:'/capture-host',body:{host:''}})
  const computerContrast=await evaluate(contrastScript)
  for(const item of computerContrast)assert(item.ratio>=4.5,theme+' computer contrast below 4.5')
  await shot(theme+'-computer')
  await evaluate('window.setRecording(true)')
  await wait(()=>evaluate(`document.querySelector('[role="combobox"]').disabled`))
  assert.equal(await evaluate(`document.querySelector('[role="combobox"]').title`),'Stop recording before switching machines')
  await evaluate(`document.querySelector('[role="combobox"]').focus()`);await key('ArrowDown')
  assert.equal(await evaluate(`document.querySelectorAll('[role="option"]').length`),0)
  await shot(theme+'-recording')
  await evaluate('window.setRecording(false)')
  await wait(()=>evaluate(`!document.querySelector('[role="combobox"]').disabled`))
  await evaluate(`document.querySelector('[role="combobox"]').focus()`);await key('ArrowDown')
  await wait(()=>evaluate(`document.querySelectorAll('[role="option"]').length===4`))
  await key('Home')
  await wait(()=>evaluate(`document.activeElement?.textContent==='This device’s microphone'`))
  await key('Enter')
  await wait(()=>evaluate(`document.querySelector('[data-capture-source]').textContent.startsWith('One mixed microphone recording')`))
  assert.deepEqual(await evaluate('window.requests.at(-1)'),{path:'/capture-mode',body:{capture_mode:'browser'}})
  summary.themes.push({theme,labels,closedContrast,openContrast,computerContrast,singleControl:true,keyboardOpenSelect:true,recordingGuard:true})
 }
 summary.requests=await evaluate('window.requests')
 summary.ok=true
 writeFileSync(join(proof,'summary.json'),JSON.stringify(summary,null,2))
 console.log(JSON.stringify(summary,null,2))
}finally{socket?.close();chrome.kill();server.close()}
