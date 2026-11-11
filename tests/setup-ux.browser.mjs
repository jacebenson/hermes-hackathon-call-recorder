// Actual packaged runtime loader, React, SDK and CSS. Synthetic sharing only.
// No OS microphone: Chromium fake device. Does not touch a live Hermes client.
import {spawn} from 'node:child_process'
import {mkdtempSync,readFileSync,writeFileSync,mkdirSync,rmSync} from 'node:fs'
import {join} from 'node:path'
import {setTimeout as sleep} from 'node:timers/promises'
const out=process.env.SETUP_OUT||join(process.env.TMPDIR,'callrec-setup-ux');mkdirSync(out,{recursive:true})
const profile=mkdtempSync(join(process.env.TMPDIR,'callrec-setup-profile-'))
const chrome=spawn(process.env.PROOF_CHROME||'/usr/sbin/chromium',['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-first-run','--disable-extensions','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required','--mute-audio','about:blank'],{stdio:['ignore','ignore','pipe']})
let stderr='',ws;chrome.stderr.on('data',d=>stderr+=d)
const checks=[];const check=(name,ok)=>{checks.push({name,ok:!!ok});console.log(`${ok?'PASS':'FAIL'} ${name}`);if(!ok)throw Error(name)}
try {
 let port;for(let i=0;i<100&&!port;i++){await sleep(100);try{port=Number(readFileSync(join(profile,'DevToolsActivePort'),'utf8').split('\n')[0])}catch{}}
 if(!port)throw Error(stderr)
 const pages=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();ws=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r))
 let n=0;const pending=new Map();ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result)}})
 const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++n;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})
 const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
 const until=async expr=>{for(let i=0;i<100;i++){if(await ev(`Boolean(${expr})`))return;await sleep(100)}throw Error('Timeout '+expr+' '+await ev('document.body.innerText'))}
 const button=text=>`[...document.querySelectorAll('button')].find(b=>b.textContent===${JSON.stringify(text)})`
 const click=async text=>{await ev(`(()=>{const b=${button(text)};if(!b||b.disabled)throw Error('Unavailable ${text}');b.click()})()`);await sleep(100)}
 const shot=async name=>{writeFileSync(join(out,name+'.png'),Buffer.from((await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false})).data,'base64'))}
 await send('Page.enable');await send('Runtime.enable')
 await send('Page.addScriptToEvaluateOnNewDocument',{source:`
 window.fixture={unsupported:true,tracks:[],recorders:[]};
 const gum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
 navigator.mediaDevices.getUserMedia=async opts=>{const s=await gum(opts);fixture.tracks.push(...s.getTracks());return s};
 navigator.mediaDevices.getDisplayMedia=async()=>{if(fixture.unsupported)throw new DOMException('Not supported','NotSupportedError');const a=new AudioContext(),o=a.createOscillator(),d=a.createMediaStreamDestination();o.connect(d);o.start();await a.resume();const canvas=document.createElement('canvas');canvas.width=32;canvas.height=32;const s=new MediaStream([...d.stream.getTracks(),...canvas.captureStream().getTracks()]);fixture.tracks.push(...s.getTracks());return s};
 const MR=window.MediaRecorder;window.MediaRecorder=class extends MR{constructor(...args){super(...args);fixture.recorders.push(this)}};
 `})
 const reload=async()=>{await ev('window.__oldDocument=true');await send('Page.navigate',{url:process.env.PROOF_URL||'http://127.0.0.1:4198/'});await until('!window.__oldDocument && document.querySelector("[data-recorder-pane]")')}
 await reload();await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
 check('first run setup expanded',await ev('document.querySelector("button[aria-controls]").getAttribute("aria-expanded")==="true"'))
 check('verbose storage help initially collapsed',await ev('!document.querySelector("details").open'))
 // Native keyboard activation, not invoking the React handler directly.
 await ev('document.querySelector("button[aria-controls]").focus();window.__noReload="kept"')
 await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',text:'\r',windowsVirtualKeyCode:13});await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
 await until('document.querySelector("button[aria-controls]").getAttribute("aria-expanded")==="false"')
 check('keyboard collapse hides settings without navigation',await ev('document.querySelector("#call-recorder-setup").hidden && window.__noReload==="kept" && document.activeElement===document.querySelector("button[aria-controls]")'))
 check('collapsed hidden reset is not focusable',await ev('!'+button('Release / reset inputs')+'.checkVisibility()'))
 await reload();check('collapsed preference survives reload',await ev('document.querySelector("#call-recorder-setup").hidden'))
 await click('Grant microphone');await click('Grant call audio')
 check('NotSupportedError retains explicit explanation and does not downgrade',await ev(`document.querySelector('[role="alert"]').textContent.includes('Electron host must provide display-media sharing') && ${button('Record')}.disabled && !!${button('Use microphone only')} && fixture.recorders.length===0 && fixture.tracks[0].readyState==='live'`))
 check('collapsed failures and microphone-only action stay visible',await ev(`document.querySelector('[role="alert"]').checkVisibility() && ${button('Use microphone only')}.checkVisibility() && document.querySelector('#call-recorder-setup').hidden`))
 await shot('narrow-unsupported-collapsed')
 await click('Expand setup / settings');await shot('narrow-expanded-error')
 await ev('document.querySelector("[data-recorder-pane]").scrollTop=10000')
 check('primary controls and collapse remain reachable at scroll bottom',await ev(`(()=>{const b=document.querySelector('button[aria-controls]'),r=b.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight && b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)) && document.querySelector('output').checkVisibility()})()`))
 await shot('narrow-expanded-scroll-bottom')
 await click('Collapse setup / settings');await ev('document.querySelector("[data-recorder-pane]").scrollTop=0');await click('Use microphone only');await click('Record');await until('fixture.recorders.length===1 && fixture.recorders[0].state==="recording"');await sleep(1200)
 await click('Expand setup / settings');await click('Collapse setup / settings')
 check('collapse preserves active capture, source and timer',await ev(`fixture.tracks[0].readyState==='live' && fixture.recorders[0].state==='recording' && ${button('Stop')}.checkVisibility() && document.querySelector('output').textContent!=='0:00'`))
 await shot('narrow-recording-collapsed');await click('Stop');await until('document.querySelector("[data-save-state=saved]")')
 check('stop still commits and releases tracks',await ev('fixture.tracks.every(t=>t.readyState==="ended") && document.querySelectorAll("[data-call-id]").length===1'))
 await click('Expand setup / settings');await reload();check('expanded preference survives reload',await ev('!document.querySelector("#call-recorder-setup").hidden'))
 // Check the real two-input grant/start flow too. Ready may auto-collapse,
 // but the user can expand again without triggering a second collapse.
 await ev('fixture.unsupported=false');await click('Grant microphone');await click('Grant call audio');await until('document.querySelector("#call-recorder-setup").hidden')
 check('readiness auto-collapses without hiding input identity',await ev('document.body.innerText.includes("Microphone") && document.body.innerText.includes("Call audio") && fixture.tracks.every(t=>t.readyState==="live")'))
 await click('Expand setup / settings');await click('Record');await sleep(1200);await click('Collapse setup / settings')
 check('two-input collapse keeps both recorders and all tracks live',await ev('fixture.recorders.length===2 && fixture.recorders.every(r=>r.state==="recording") && fixture.tracks.every(t=>t.readyState==="live")'))
 await send('Emulation.setDeviceMetricsOverride',{width:1000,height:800,deviceScaleFactor:1,mobile:false});await shot('wide-dual-recording-collapsed')
 await click('Stop');await until('document.querySelector("[data-save-state=saved]")')
 check('dual-input stop releases both inputs and video',await ev('fixture.tracks.every(t=>t.readyState==="ended") && document.querySelectorAll("[data-call-id]").length===2'))
 await ev('localStorage.setItem("call-recorder.ui.v1", "{broken")');await reload();check('malformed UI preference safely expands setup',await ev('!document.querySelector("#call-recorder-setup").hidden'))
 await ev('localStorage.setItem("call-recorder.ui.v1", JSON.stringify({version:9,setupExpanded:false}))');await reload();check('unknown preference version safely expands setup',await ev('!document.querySelector("#call-recorder-setup").hidden'))
 check('no renderer errors',await ev('proof.errors.length===0'))
 check('no horizontal overflow at narrow width',await (async()=>{await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});return ev('document.documentElement.scrollWidth<=390')})())
 console.log(JSON.stringify(checks,null,2))
} finally {
 writeFileSync(join(out,'checks.json'),JSON.stringify(checks,null,2));ws?.close();chrome.kill('SIGTERM');await new Promise(r=>chrome.once('exit',r));rmSync(profile,{recursive:true,force:true})
}
