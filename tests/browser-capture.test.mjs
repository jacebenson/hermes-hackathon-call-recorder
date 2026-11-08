import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
const source = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
assert(source.includes('export function createBrowserCapture'), 'browser capture controller must exist')
const code = source.slice(source.indexOf('export function createBrowserCapture'), source.indexOf('\nfunction useBrowserCapture')).replace('export function', 'function') + '\ncreateBrowserCapture'
let acquired = 0, stopped = 0, uploads = 0, resolveMedia
const tracks = [{stop(){stopped++}, addEventListener(){}}]
class Recorder {
  static isTypeSupported(t){return t === 'audio/webm;codecs=opus'}
  constructor(){this.state='inactive'; this.mimeType='audio/webm;codecs=opus'}
  start(){this.state='recording'}
  stop(){this.state='inactive'; this.ondataavailable?.({data:new Blob(['test'], {type:this.mimeType})}); this.onstop?.()}
}
const env = {isSecureContext:true, navigator:{mediaDevices:{getUserMedia:()=>{acquired++; return Promise.resolve({getTracks:()=>tracks})}}}, MediaRecorder:Recorder, Blob, setTimeout, clearTimeout, Date}
const make = vm.runInNewContext(code, {...env})
const ctx={rest:async(path, opts)=>{uploads++; assert.equal(path,'/browser-audio'); assert(opts.upload.bytes instanceof ArrayBuffer); return {ok:true,transcription:{state:'unavailable'}}}}
let capture=make(ctx,env)
assert.equal(acquired,0)
const a=capture.start(); const b=capture.start(); await Promise.all([a,b]); assert.equal(acquired,1)
assert.equal(capture.get().phase,'recording')
await capture.stop(); assert.equal(stopped,1); assert.equal(uploads,1); assert.equal(capture.get().phase,'saved')
// Cancel while permission is pending must stop late tracks, without recording/upload.
env.navigator.mediaDevices.getUserMedia=()=>new Promise(r=>{resolveMedia=r})
capture=make(ctx,env); const pending=capture.start(); capture.cancel(); resolveMedia({getTracks:()=>tracks}); await pending
assert.equal(stopped,2); assert.equal(uploads,1)
// Permission, insecure and API failures have different actionable messages.
for (const [override, word] of [[{isSecureContext:false},'HTTPS'],[{MediaRecorder:undefined},'supported'],[{navigator:{mediaDevices:{getUserMedia:async()=>{throw {name:'NotAllowedError'}}}}},'permission']]) {
 capture=make(ctx,{...env,...override}); await capture.start(); assert.equal(capture.get().phase,'error'); assert(capture.get().error.includes(word),capture.get().error)
}
// Recorder construction/start failure releases acquired tracks.
env.navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>tracks})
class Broken extends Recorder {start(){throw Error('device failed')}}
capture=make(ctx,{...env,MediaRecorder:Broken}); await capture.start(); assert.equal(capture.get().phase,'error'); assert.equal(stopped,3)
// Upload errors never retry against another gateway or restart the microphone.
capture=make({rest:async()=>{throw Error('offline')}},env); await capture.start(); await capture.stop(); assert.equal(stopped,4); assert.equal(capture.get().phase,'error')
console.log('Browser capture lifecycle checks passed')
