// Lifecycle checks for recorder-core.mjs with fake media devices.
// These prove controller behaviour (grants, cleanup, caps, interruption) with
// instrumented fakes. They prove nothing about a real browser or real devices;
// tests/browser-proof.mjs covers the real MediaRecorder path separately.
import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import { createCallRecorder } from '../../desktop/src/recorder-core.mjs'

const results = []
const check = (name, fn) => {
  try { fn(); results.push({ name, ok: true }) }
  catch (error) { results.push({ name, ok: false, detail: error?.message || String(error) }) }
}
const checkAsync = async (name, fn) => {
  try { await fn(); results.push({ name, ok: true }) }
  catch (error) { results.push({ name, ok: false, detail: error?.message || String(error) }) }
}

let idCounter = 0
class FakeTrack {
  constructor(kind, label = '') {
    this.kind = kind; this.label = label; this.readyState = 'live'; this.id = `t${++idCounter}`
    this.stopped = false; this.listeners = new Map()
  }
  addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(fn) }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn) }
  dispatch(type) { for (const fn of this.listeners.get(type) || []) fn({ type }) }
  stop() { this.stopped = true; this.readyState = 'ended' }
}
class FakeStream {
  constructor(tracks) { this.tracks = tracks }
  getTracks() { return [...this.tracks] }
  getAudioTracks() { return this.tracks.filter((t) => t.kind === 'audio') }
  getVideoTracks() { return this.tracks.filter((t) => t.kind === 'video') }
}
const allTracks = []
const streamWith = (...kinds) => {
  const tracks = kinds.map((k) => new FakeTrack(k, k === 'audio' ? 'Fake microphone' : 'Fake screen'))
  allTracks.push(...tracks)
  return new FakeStream(tracks)
}

class FakeRecorder {
  static instances = []
  static isTypeSupported(type) { return type === 'audio/webm;codecs=opus' }
  constructor(stream, options) {
    this.stream = stream; this.mimeType = options?.mimeType; this.state = 'inactive'
    this.chunks = []; this.onstop = null; this.ondataavailable = null; this.onerror = null
    this.error = null
    FakeRecorder.instances.push(this)
  }
  start() {
    if (this.error) throw new Error(this.error)
    this.state = 'recording'
  }
  emit(bytes = 512) {
    this.chunks.push(bytes)
    this.ondataavailable?.({ data: new Blob([new Uint8Array(bytes)], { type: this.mimeType }) })
  }
  stop() {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    this.emit(256)
    queueMicrotask(() => this.onstop?.())
  }
}

const baseEnv = (overrides = {}) => {
  const env = {
    isSecureContext: true,
    performance,
    navigator: {
      mediaDevices: {
        getUserMedia: async () => streamWith('audio'),
        getDisplayMedia: async () => streamWith('video', 'audio')
      }
    },
    MediaRecorder: FakeRecorder,
    MediaStream: FakeStream,
    Blob, URL, setTimeout, clearTimeout, setInterval, clearInterval,
    probeDuration: async () => 1.25,
    ...overrides
  }
  return env
}
const build = (env, limits) => {
  FakeRecorder.instances = []
  return createCallRecorder({ env, limits })
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const run = async () => {
  // 1. Nothing is acquired before an explicit grant.
  const env1 = baseEnv()
  const c1 = build(env1)
  check('idle state acquires no tracks and records nothing', () => {
    assert.equal(c1.state.phase, 'idle')
    assert.equal(FakeRecorder.instances.length, 0)
    assert.equal(c1.streams().micStream, null)
    assert.match(c1.state.message, /Grant the microphone/)
  })

  // 2. Microphone permission refusal.
  const env2 = baseEnv()
  env2.navigator.mediaDevices.getUserMedia = async () => { const e = new Error('denied'); e.name = 'NotAllowedError'; throw e }
  const c2 = build(env2)
  await checkAsync('microphone permission refusal is reported and captures nothing', async () => {
    assert.equal(await c2.grantMic(), false)
    assert.equal(c2.state.phase, 'error')
    assert.equal(c2.state.mic.status, 'denied')
    assert.match(c2.state.error, /permission was refused/i)
    assert.equal(FakeRecorder.instances.length, 0)
  })

  // 3. Display share with no audio track: whole share released, explicit choice required.
  const env3 = baseEnv()
  let sharedStream
  env3.navigator.mediaDevices.getDisplayMedia = async () => { sharedStream = streamWith('video'); return sharedStream }
  const c3 = build(env3)
  await checkAsync('display share without audio releases the share and refuses to start silently', async () => {
    await c3.grantMic()
    assert.equal(await c3.grantCallAudio(), false)
    assert.equal(c3.state.call.status, 'no-audio')
    assert.match(c3.state.message, /no shared-audio track/i)
    assert(sharedStream.getTracks().every((t) => t.stopped), 'share tracks must be stopped')
    assert.equal(c3.state.phase, 'idle')
    assert.equal(await c3.start(), false, 'start must not proceed without an explicit choice')
    assert.equal(FakeRecorder.instances.length, 0)
    assert.equal(c3.state.error, '', 'refusal is not an error state, it asks for a choice')
    assert.equal(c3.chooseMicOnly(), true)
    assert.equal(c3.state.micOnly, true)
    assert.equal(await c3.start(), true)
    assert.equal(FakeRecorder.instances.length, 1, 'mic-only capture uses one recorder')
    assert.equal(FakeRecorder.instances[0].stream.getVideoTracks().length, 0)
    await c3.stop('user')
    assert(c3.streams().micStream === null)
    assert(c3.streams().callStream === null)
  })

  // 4. Partial acquisition: call grant fails after mic is held.
  const env4 = baseEnv()
  env4.navigator.mediaDevices.getDisplayMedia = async () => { const e = new Error('cancelled'); e.name = 'NotAllowedError'; throw e }
  const c4 = build(env4)
  await checkAsync('failed call-audio grant after mic keeps mic usable and blocks start', async () => {
    assert.equal(await c4.grantMic(), true)
    assert.equal(await c4.grantCallAudio(), false)
    assert.equal(c4.state.call.status, 'denied')
    assert.match(c4.state.error, /cancelled or refused/i)
    assert.equal(c4.state.phase, 'error')
    assert.equal(await c4.start(), false)
    assert.equal(FakeRecorder.instances.length, 0)
    c4.reset()
    assert.equal(FakeRecorder.instances.length, 0)
  })

  // 5. Happy path: two separate recorders, audio-only call stream, timing metadata.
  const env5 = baseEnv()
  const c5 = build(env5)
  await checkAsync('two-input capture keeps separate recorders, audio-only call stream and timing', async () => {
    await c5.grantMic()
    await c5.grantCallAudio()
    assert.equal(c5.state.phase, 'ready')
    assert.equal(c5.state.call.hasVideo, true)
    assert.equal(await c5.start(), true)
    assert.equal(FakeRecorder.instances.length, 2)
    const [micRec, callRec] = FakeRecorder.instances
    assert.equal(micRec.stream.getTracks().length, 1)
    assert.equal(callRec.stream.getTracks().length, 1, 'display video must never reach a recorder')
    assert.equal(callRec.stream.getVideoTracks().length, 0)
    assert.equal(micRec.stream.getTracks()[0], c5.streams().micStream.getAudioTracks()[0])
    micRec.emit(1024); callRec.emit(2048)
    assert.equal(c5.state.totalBytes, 3072)
    assert.equal(await c5.stop('user'), true)
    assert.equal(c5.state.phase, 'done')
    assert.equal(c5.state.files.length, 3, 'two audio files plus timing metadata')
    const audioFiles = c5.state.files.filter((f) => f.key !== 'timing')
    assert.deepEqual(audioFiles.map((f) => f.key), ['mic', 'call-audio'])
    assert.match(audioFiles[0].filename, /^mic-.*\.webm$/)
    assert.match(audioFiles[1].filename, /^call-audio-.*\.webm$/)
    assert.equal(c5.state.timing.tracks.length, 2)
    assert.equal(c5.state.timing.videoPersisted, false)
    assert.equal(c5.state.timing.mimeType, 'audio/webm;codecs=opus')
    assert.equal(c5.state.timing.capture.callAudio.separateRecorder, true)
    assert.equal(c5.state.timing.tracks[1].containerDurationMs, 1250)
    assert.equal(c5.state.saving, false)
    const every = [...c5.streams().micStream?.getTracks?.() || [], ...(env5.__lastCallStream?.getTracks?.() || [])]
    for (const t of every) assert(t.stopped, 'all tracks released on stop')
  })

  // 6. All acquired tracks, video included, stop after a normal stop.
  const env6 = baseEnv()
  let display
  env6.navigator.mediaDevices.getDisplayMedia = async () => { display = streamWith('video', 'audio'); return display }
  const c6 = build(env6)
  await checkAsync('stop releases microphone and shared video tracks', async () => {
    await c6.grantMic(); await c6.grantCallAudio(); await c6.start()
    const mic = c6.streams().micStream
    await c6.stop('user')
    assert(mic.getTracks().every((t) => t.stopped), 'mic tracks stopped')
    assert(display.getTracks().every((t) => t.stopped), 'share audio and video stopped')
    assert.equal(c6.state.phase, 'done')
  })

  // 7. Double start is a no-op.
  const env7 = baseEnv()
  const c7 = build(env7)
  await checkAsync('double start does not create extra recorders', async () => {
    await c7.grantMic(); await c7.chooseMicOnly()
    const [a, b] = await Promise.all([c7.start(), c7.start()])
    assert.equal(a, true); assert.equal(b, false)
    assert.equal(FakeRecorder.instances.length, 1)
    assert.equal(c7.state.phase, 'recording')
    assert.equal(await c7.start(), false)
    assert.equal(FakeRecorder.instances.length, 1)
    await c7.stop('user')
  })

  // 8. Ended track mid-capture interrupts, finalizes and keeps chunks.
  const env8 = baseEnv()
  let display8
  env8.navigator.mediaDevices.getDisplayMedia = async () => { display8 = streamWith('video', 'audio'); return display8 }
  const c8 = build(env8)
  await checkAsync('ended call-audio track interrupts and preserves recorded chunks', async () => {
    await c8.grantMic(); await c8.grantCallAudio(); await c8.start()
    FakeRecorder.instances[0].emit(700)
    FakeRecorder.instances[1].emit(900)
    display8.getAudioTracks()[0].dispatch('ended')
    await sleep(10)
    assert.equal(c8.state.interrupted, 'call-ended')
    assert.equal(c8.state.phase, 'partial')
    assert.equal(c8.state.files.filter((f) => f.key !== 'timing').length, 2)
    assert.match(c8.state.message, /share ended/i)
    assert(c8.streams().micStream === null && c8.streams().callStream === null)
    assert(display8.getTracks().every((t) => t.stopped))
  })

  // 9. Mic track ended mid-capture.
  const env9 = baseEnv()
  const c9 = build(env9)
  await checkAsync('ended microphone track interrupts both inputs', async () => {
    await c9.grantMic(); await c9.chooseMicOnly(); await c9.start()
    const mic = c9.streams().micStream
    FakeRecorder.instances[0].emit(400)
    mic.getAudioTracks()[0].dispatch('ended')
    await sleep(10)
    assert.equal(c9.state.interrupted, 'mic-ended')
    assert.equal(c9.state.phase, 'partial')
    assert.equal(c9.state.files.filter((f) => f.key !== 'timing').length, 1)
  })

  // 10. Byte cap auto-stops and preserves what exists.
  const env10 = baseEnv()
  const c10 = build(env10, { maxDurationMs: 180000, maxBytes: 1500 })
  await checkAsync('byte cap stops capture and preserves existing chunks', async () => {
    await c10.grantMic(); await c10.chooseMicOnly(); await c10.start()
    FakeRecorder.instances[0].emit(400)
    FakeRecorder.instances[0].emit(1600)
    await sleep(40)
    assert.equal(c10.state.phase, 'partial')
    assert.match(c10.state.message, /in-memory cap/i)
    assert(c10.state.files.length >= 2, 'audio plus timing kept')
    assert(c10.state.files.find((f) => f.key === 'mic').bytes > 0)
  })

  // 11. Duration cap auto-stops.
  const env11 = baseEnv()
  const c11 = build(env11, { maxDurationMs: 120, maxBytes: 24 * 1024 * 1024 })
  await checkAsync('duration cap stops capture on its own', async () => {
    await c11.grantMic(); await c11.chooseMicOnly(); await c11.start()
    FakeRecorder.instances[0].emit(300)
    await sleep(260)
    assert.equal(c11.state.phase, 'done')
    assert.equal(c11.state.files.filter((f) => f.key !== 'timing').length, 1)
    assert.equal(c11.state.timing.stopReason, 'duration-cap')
  })

  // 12. Settings changes are refused while capturing.
  const env12 = baseEnv()
  const c12 = build(env12)
  await checkAsync('grants, mic-only switch and reset are locked during capture', async () => {
    await c12.grantMic(); await c12.chooseMicOnly(); await c12.start()
    assert.equal(await c12.grantMic(), false)
    assert.equal(await c12.grantCallAudio(), false)
    assert.equal(c12.chooseMicOnly(), false)
    assert.equal(c12.reset(), false)
    assert.equal(c12.state.phase, 'recording')
    await c12.stop('user')
  })

  // 13. Late permission result after teardown is stopped, not recorded.
  const env13 = baseEnv()
  let resolveMic
  env13.navigator.mediaDevices.getUserMedia = () => new Promise((r) => { resolveMic = r })
  const c13 = build(env13)
  await checkAsync('teardown during a pending permission stops the late stream', async () => {
    const pending = c13.grantMic()
    c13.releaseForUnload()
    const late = streamWith('audio')
    resolveMic(late)
    await pending
    assert(late.getTracks().every((t) => t.stopped), 'late tracks released')
    assert.equal(FakeRecorder.instances.length, 0)
  })

  // 14. Recorder construction failure releases every acquired track.
  const env14 = baseEnv()
  const c14 = build(env14)
  class BrokenRecorder extends FakeRecorder { constructor() { super(); throw new Error('device failed') } }
  env14.MediaRecorder = BrokenRecorder
  let display14
  env14.navigator.mediaDevices.getDisplayMedia = async () => { display14 = streamWith('video', 'audio'); return display14 }
  const c14b = createCallRecorder({ env: env14 })
  await checkAsync('recorder construction failure releases all tracks with no files', async () => {
    await c14b.grantMic(); await c14b.grantCallAudio(); await c14b.start()
    assert.equal(c14b.state.phase, 'error')
    assert.match(c14b.state.error, /Could not start audio recording/)
    assert(display14.getTracks().every((t) => t.stopped))
    assert.equal(c14b.state.files.length, 0)
  })

  // 15. Mic-only timing is honest about the missing input.
  const env15 = baseEnv()
  const c15 = build(env15)
  await checkAsync('microphone-only timing marks call audio as not captured', async () => {
    await c15.grantMic(); await c15.chooseMicOnly(); await c15.start()
    FakeRecorder.instances[0].emit(500)
    await c15.stop('user')
    assert.equal(c15.state.timing.capture.callAudio.source, 'not captured')
    assert.equal(c15.state.timing.tracks.length, 1)
    assert.equal(c15.state.files.filter((f) => f.key !== 'timing').length, 1)
  })

  await checkAsync('Stop plus teardown drains both delayed encoders and repeated stop joins completion', async () => {
    class Delayed extends FakeRecorder {
      stop() { if (this.state === 'inactive') return; this.state = 'inactive'; setTimeout(() => { this.emit(321); this.onstop?.() }, this === FakeRecorder.instances[0] ? 5 : 25) }
    }
    const c = build(baseEnv({ MediaRecorder: Delayed }))
    await c.grantMic(); await c.grantCallAudio(); await c.start()
    const tracks = [...c.streams().micStream.getTracks(), ...c.streams().callStream.getTracks()]
    const first = c.stop(), repeated = c.stop(), disposal = c.teardown()
    assert(tracks.every(t => t.stopped), 'privacy release must not wait for final chunks')
    assert.equal(c.state.files.length, 0)
    assert.deepEqual(await Promise.all([first, repeated, disposal]), [true, true, true])
    assert.deepEqual(c.state.files.filter(f => f.key !== 'timing').map(f => f.bytes), [321, 321])
    assert.equal(await c.grantMic(), false, 'disposed controllers cannot reacquire')
  })

  await checkAsync('inactive native error retains final data until terminal stop', async () => {
    const c = build(baseEnv())
    await c.grantMic(); c.chooseMicOnly(); await c.start()
    const r = FakeRecorder.instances[0]
    r.emit(17); r.state = 'inactive'; r.onerror({ error: new Error('encoder failed') })
    await sleep(2)
    assert.equal(c.state.phase, 'stopping')
    r.emit(31); r.onstop()
    await c.stop()
    assert.equal(c.state.files.find(f => f.key === 'mic').bytes, 48)
    assert.equal(c.state.phase, 'partial')
    assert.equal(c.state.timing.finalizationComplete, true)
  })

  await checkAsync('broken encoder deadline preserves received chunks with honest incomplete error', async () => {
    class Hung extends FakeRecorder { stop() { this.state = 'inactive' } }
    const c = build(baseEnv({ MediaRecorder: Hung, setTimeout: (fn, ms) => setTimeout(fn, ms === 5000 ? 20 : ms) }))
    await c.grantMic(); c.chooseMicOnly(); await c.start()
    const r = FakeRecorder.instances[0]; r.emit(23)
    const pending = c.stop()
    assert(r.stream.getTracks().every(t => t.stopped))
    await pending
    assert.equal(c.state.phase, 'partial')
    assert.equal(c.state.files.find(f => f.key === 'mic').bytes, 23)
    assert.equal(c.state.timing.finalizationComplete, false)
    assert.match(c.state.error, /timed out.*incomplete/)
    assert.equal(r.ondataavailable, null)
  })

  const failed = results.filter((r) => !r.ok)
  const summary = { suite: 'recorder-core fakes', total: results.length, passed: results.length - failed.length, failed: failed.length, results }
  writeFileSync(new URL('./checks-core.json', import.meta.url), JSON.stringify(summary, null, 2))
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : ' :: ' + r.detail}`)
  console.log(`\n${summary.passed}/${summary.total} lifecycle checks passed`)
  process.exitCode = failed.length ? 1 : 0
}

run().catch((error) => { console.error('suite crashed:', error); process.exitCode = 1 })
