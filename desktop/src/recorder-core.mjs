// Client-local two-input call recorder core.
// Two independent MediaRecorders: one for the microphone track, one for the
// call-audio track returned by getDisplayMedia({audio:true}). The display video
// track is never attached to a recorder and is released with everything else.
//
// Framework-free and dependency-injected so it can run in a browser and in Node
// tests. No network, upload or transcription. Persist timeslices incrementally;
// ordinary stop reads metadata only and never assembles full track Blobs.

import { createChunkWriter } from './chunk-store.mjs'
import { newCallId } from './library.mjs'
export const LIMITS = Object.freeze({ maxDurationMs: Infinity, maxBytes: Infinity })

const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4']

const idlePhases = ['idle', 'error', 'done', 'partial']
const capturePhases = ['starting', 'recording', 'stopping', 'finalizing']

function extFor(mime) {
  if (/mp4/.test(mime)) return 'm4a'
  if (/ogg/.test(mime)) return 'ogg'
  return 'webm'
}

function clockStamp(date) {
  const p = (n, w = 2) => String(n).padStart(w, '0')
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}-${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`
}

function friendlyMediaError(error, input) {
  const name = error?.name || ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return input === 'mic'
      ? 'Microphone permission was refused. Allow microphone access for this page, then grant it again.'
      : 'Call audio sharing was cancelled or refused. Grant it again, or choose microphone only.'
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return input === 'mic'
      ? 'No microphone was found on this device.'
      : 'No call-audio source was offered by the browser.'
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return input === 'mic'
      ? 'The microphone is in use or unreadable by another application.'
      : 'The shared source could not be read. Check that the call app is playing audio.'
  }
  if (name === 'AbortError') return 'The browser aborted the capture request. Try again.'
  return `${input === 'mic' ? 'Microphone' : 'Call audio'} capture failed: ${error?.message || name || 'unknown error'}`
}

export function createCallRecorder({ env, storage, limits = LIMITS, now = () => env.performance.now(), onState } = {}) {
  const state = {
    phase: 'idle',
    mic: { status: 'none', label: '', live: false, level: 0 },
    call: { status: 'none', label: '', live: false, hasAudio: false, hasVideo: false, videoTracks: 0, level: 0 },
    micOnly: false,
    message: 'Grant the microphone, then optionally add call audio.',
    error: '',
    startedAtISO: '',
    elapsedMs: 0,
    remainingMs: limits.maxDurationMs,
    totalBytes: 0,
    bytesLimit: limits.maxBytes,
    durationLimitMs: limits.maxDurationMs,
    files: [],
    timing: null,
    interrupted: '',
    saving: false
  }
  const listeners = new Set()
  const publish = (patch = {}) => {
    Object.assign(state, patch)
    if (onState) onState(state)
    for (const fn of listeners) fn(state)
  }
  const emit = () => publish({})

  let micStream = null
  let callStream = null
  let micRecorder = null
  let callRecorder = null
  let writer = null
  let sessionId = null
  let bytes = 0
  let generation = 0
  let disposed = false
  const drains = new Map()
  let sessionStartMs = 0
  let sessionStartISO = ''
  const startedAt = { mic: 0, call: 0 }
  const stoppedAt = { mic: 0, call: 0 }
  const chunkCount = { mic: 0, call: 0 }
  let durationTimer = null
  let stopping = null
  let tickTimer = null
  let endedHandlers = []

  const clearTimer = () => {
    if (durationTimer) env.clearTimeout(durationTimer)
    if (tickTimer) env.clearInterval(tickTimer)
    durationTimer = null
    tickTimer = null
  }

  const stopTracks = (stream) => {
    if (!stream) return
    for (const track of stream.getTracks()) {
      try { track.stop() } catch { /* already ended */ }
    }
  }

  const detachEnded = () => {
    for (const [track, fn] of endedHandlers) {
      try { track.removeEventListener?.('ended', fn) } catch { /* ignore */ }
    }
    endedHandlers = []
  }

  const releaseAll = (reason = '') => {
    clearTimer()
    detachEnded()
    for (const recorder of [micRecorder, callRecorder]) {
      if (!recorder) continue
      recorder.onstop = null
      recorder.ondataavailable = null
      recorder.onerror = null
      if (recorder.state !== 'inactive') { try { recorder.stop() } catch { /* ignore */ } }
    }
    micRecorder = null
    callRecorder = null
    stopTracks(micStream)
    stopTracks(callStream)
    micStream = null
    callStream = null
    const ended = { ...state.mic, live: false, level: 0 }
    const callEnded = { ...state.call, live: false, level: 0 }
    publish({ mic: ended, call: callEnded, message: reason || state.message })
  }

  const guarded = (patch) => publish({ ...patch, saving: true })

  // ----- grant: microphone -------------------------------------------------
  const grantMic = async () => {
    if (disposed || !idlePhases.includes(state.phase)) return false
    if (!env.isSecureContext) {
      publish({ phase: 'error', error: 'Microphone access needs HTTPS or localhost. Open this page on a secure origin.' })
      return false
    }
    if (!env.navigator?.mediaDevices?.getUserMedia) {
      publish({ phase: 'error', error: 'This browser does not expose getUserMedia. Try a current Chrome, Firefox or Safari.' })
      return false
    }
    const current = generation
    publish({ phase: 'granting-mic', error: '', message: 'Waiting for microphone permission…' })
    let acquired
    try {
      acquired = await env.navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (error) {
      if (current !== generation) return false
      publish({ phase: 'error', mic: { ...state.mic, status: 'denied', live: false }, error: friendlyMediaError(error, 'mic') })
      return false
    }
    if (current !== generation) { stopTracks(acquired); return false }
    stopTracks(micStream)
    micStream = acquired
    const track = acquired.getAudioTracks?.()[0]
    if (!track) {
      stopTracks(acquired)
      micStream = null
      publish({ phase: 'error', mic: { ...state.mic, status: 'denied', live: false }, error: 'The microphone stream arrived without an audio track.' })
      return false
    }
    publish({
      phase: state.call.status === 'granted' ? 'ready' : 'idle',
      mic: { status: 'granted', label: track.label || 'Default microphone', live: track.readyState === 'live', level: 0 },
      error: '',
      message: state.call.status === 'granted' ? 'Microphone and call audio are both granted. Start recording when ready.' : 'Microphone granted. You can add call audio or record microphone only.'
    })
    return true
  }

  // ----- grant: call audio via getDisplayMedia ------------------------------
  const grantCallAudio = async () => {
    if (disposed || !idlePhases.includes(state.phase)) return false
    if (!env.navigator?.mediaDevices?.getDisplayMedia) {
      publish({ phase: 'error', error: 'This browser does not expose getDisplayMedia. Tab or window audio capture is unavailable here.' })
      return false
    }
    const current = generation
    publish({ phase: 'granting-call', error: '', message: 'Choose the call window or tab, and tick “Share audio” in the browser picker…' })
    let acquired
    try {
      // audio:true is required for call audio. video is requested only because
      // many browsers refuse an audio-only display request; the video track is
      // never recorded and never persisted.
      acquired = await env.navigator.mediaDevices.getDisplayMedia({ audio: true, video: true })
    } catch (error) {
      if (current !== generation) return false
      publish({ phase: 'error', call: { ...state.call, status: 'denied', hasAudio: false }, error: friendlyMediaError(error, 'call') })
      return false
    }
    if (current !== generation) { stopTracks(acquired); return false }
    const audio = acquired.getAudioTracks?.() || []
    const video = acquired.getVideoTracks?.() || []
    if (audio.length === 0) {
      // Nothing usable was shared. Release every track from this request.
      stopTracks(acquired)
      stopTracks(callStream)
      callStream = null
      publish({
        phase: 'idle',
        call: { status: 'no-audio', label: '', live: false, hasAudio: false, hasVideo: false, videoTracks: 0, level: 0 },
        micOnly: false,
        error: '',
        message: 'The browser returned no shared-audio track, so there is no call audio to record. Share a tab or window and tick “Share audio”, or explicitly choose microphone only below.'
      })
      return false
    }
    stopTracks(callStream)
    callStream = acquired
    const label = audio[0].label || 'Shared source audio'
    publish({
      phase: state.mic.status === 'granted' ? 'ready' : 'idle',
      call: { status: 'granted', label, live: audio[0].readyState === 'live', hasAudio: true, hasVideo: video.length > 0, videoTracks: video.length, level: 0 },
      error: '',
      micOnly: false,
      message: video.length
        ? 'Call audio granted from the shared source. Video is active only to keep sharing alive and is not recorded.'
        : 'Call audio granted. Only audio is recorded.'
    })
    return true
  }

  const chooseMicOnly = () => {
    if (disposed || !idlePhases.includes(state.phase)) return false
    if (state.mic.status !== 'granted') {
      publish({ error: 'Grant the microphone before choosing microphone only.' })
      return false
    }
    if (state.call.status === 'granted') {
      publish({ error: 'Call audio is already granted. Reset if you want to drop it.' })
      return false
    }
    publish({ phase: 'ready', micOnly: true, error: '', message: 'Microphone only, chosen explicitly. Call audio will not be recorded.' })
    return true
  }

  const clearMicOnly = () => {
    if (disposed || !idlePhases.includes(state.phase)) return false
    publish({ phase: state.call.status === 'granted' ? 'ready' : 'idle', micOnly: false, message: 'Microphone-only choice cleared. Call audio is still not granted.' })
    return true
  }

  // ----- start -------------------------------------------------------------
  const supportedMime = () => {
    const MR = env.MediaRecorder
    if (!MR?.isTypeSupported) return ''
    return MIME_CANDIDATES.find((type) => MR.isTypeSupported(type)) || ''
  }

  const start = async () => {
    if (disposed || state.phase !== 'ready') return false
    if (!state.mic.live) { publish({ phase: 'error', error: 'The microphone track is not live. Grant it again.' }); return false }
    const wantsCall = state.call.status === 'granted' && !state.micOnly
    if (!wantsCall && !state.micOnly) {
      publish({ error: 'Grant call audio, or choose microphone only explicitly.' })
      return false
    }
    if (wantsCall && !state.call.hasAudio) {
      publish({ error: 'No call-audio track is present. Choose microphone only or grant call audio again.' })
      return false
    }
    const mimeType = supportedMime()
    if (!mimeType) { publish({ phase: 'error', error: 'No supported audio recording format in this browser (tried WebM/Opus, Ogg/Opus, MP4).' }); return false }

    const current = ++generation
    publish({ phase: 'starting', error: '', files: [], timing: null, interrupted: '', totalBytes: 0, saving: false })
    if (!storage) { publish({ phase: 'error', error: 'Incremental local storage is required.' }); return false }
    bytes = 0
    chunkCount.mic = 0
    chunkCount.call = 0
    const options = { mimeType, audioBitsPerSecond: 64000 }
    let builtMic = null
    let builtCall = null
    try {
      builtMic = new env.MediaRecorder(new env.MediaStream(micStream.getAudioTracks()), options)
      if (wantsCall) {
        // Derived audio-only stream: the display video track never reaches a recorder.
        builtCall = new env.MediaRecorder(new env.MediaStream(callStream.getAudioTracks()), options)
      }
    } catch (error) {
      publish({ phase: 'error', error: `Could not start audio recording: ${error?.message || error}. All acquired inputs were released.` })
      releaseAll('')
      return false
    }
    micRecorder = builtMic
    callRecorder = builtCall
    const failIfCurrent = (message) => {
      if (current !== generation) return
      interrupt('recorder-error', message)
    }
    drains.clear()
    const wire = (recorder, key) => {
      // Inactive is not terminal: native errors set it before final data/stop.
      // Install once, before start, and retain these callbacks through disposal.
      let resolve
      const drain = { stopped: false, promise: new Promise(r => { resolve = r }) }
      drains.set(recorder, drain)
      recorder.onstop = () => {
        if (drain.stopped) return
        drain.stopped = true
        stoppedAt[key] = now() - sessionStartMs
        resolve()
      }
      recorder.ondataavailable = (event) => {
        if (current !== generation) return
        const data = event.data
        if (!data || !data.size) return
        bytes += data.size
        state.totalBytes = bytes
        writer.append(key === 'call' ? 'call-audio' : key, data, now() - sessionStartMs, startedAt[key])
        chunkCount[key]++
        emit()
      }
      recorder.onerror = (event) => failIfCurrent(`${key === 'mic' ? 'Microphone' : 'Call audio'} recording failed: ${event?.error?.message || 'recorder error'}. Capture stopped.`)
    }
    wire(micRecorder, 'mic')
    if (callRecorder) wire(callRecorder, 'call')

    sessionStartISO = new Date().toISOString()
    sessionStartMs = now()
    startedAt.mic = 0
    stoppedAt.mic = 0
    startedAt.call = 0
    stoppedAt.call = 0
    sessionId = newCallId()
    const trackInfo = (key, label, source) => ({ key, label, source, filename: `${key}-${sessionId}.${extFor(mimeType)}`, mimeType, bytes: 0, chunks: 0, startedOffsetMs: 0, stoppedOffsetMs: 0, captureWindowMs: 0 })
    try {
      await storage.create({ id: sessionId, schema: 'call-library-record/2', title: 'Untitled call', capturedAtISO: sessionStartISO, createdAtISO: sessionStartISO, status: 'recording', micOnly: !wantsCall, tracks: [trackInfo('mic', 'Microphone', state.mic.label), ...(wantsCall ? [trackInfo('call-audio', 'Call audio', state.call.label)] : [])], provenance: { origin: env.location?.origin || '', storage: 'IndexedDB chunks', backend: 'none', videoPersisted: false }, timing: null })
    } catch (e) { releaseAll(''); publish({ phase: 'error', error: `Local storage unavailable: ${e.message}` }); return false }
    writer = createChunkWriter({ store: storage, id: sessionId, onFailure: e => { publish({ error: `Local write failed: ${e.name}: ${e.message}` }); failIfCurrent('Local storage failed. Capture stopped. Reload to inspect any remaining local data; the tail may be missing.') } })
    if (disposed || current !== generation) { releaseAll(''); try { await storage.update(sessionId, { status: 'partial', interrupted: 'start-cancelled' }) } finally { await storage.endSession?.(sessionId) }; return false }
    try {
      micRecorder.start(1000)
      startedAt.mic = now() - sessionStartMs
      if (callRecorder) {
        callRecorder.start(1000)
        startedAt.call = now() - sessionStartMs
      }
    } catch (error) {
      publish({ phase: 'error', error: `Recording could not start: ${error?.message || error}. All acquired inputs were released.`, saving: false })
      releaseAll('')
      generation++
      await storage.endSession?.(sessionId)
      return false
    }

    endedHandlers.push([micStream.getAudioTracks()[0], () => {
      if (current === generation && capturePhases.includes(state.phase)) {
        interrupt('mic-ended', 'The microphone track ended. Capture stopped and whatever was recorded is kept below.')
      }
    }])
    for (const [track, fn] of endedHandlers) track.addEventListener?.('ended', fn)
    if (callRecorder) {
      const callTrack = callStream.getAudioTracks()[0]
      const onCallEnded = () => {
        if (current === generation && capturePhases.includes(state.phase)) {
          interrupt('call-ended', 'The shared call audio stopped (share ended). Capture stopped; recorded audio is kept below.')
        }
      }
      callTrack.addEventListener?.('ended', onCallEnded)
      endedHandlers.push([callTrack, onCallEnded])
    }
    // Video tracks may end without ending the audio; surface it, do not stop.
    for (const track of callStream?.getVideoTracks?.() || []) {
      const onVideoEnded = () => {
        if (current !== generation) return
        publish({ message: 'The shared video track ended. Audio capture is unaffected; video was never recorded.' })
      }
      track.addEventListener?.('ended', onVideoEnded)
      endedHandlers.push([track, onVideoEnded])
    }

    // No arbitrary total duration or byte cap.
    tickTimer = env.setInterval(() => {
      if (current !== generation || state.phase !== 'recording') return
      const elapsed = now() - sessionStartMs
      publish({ elapsedMs: elapsed, remainingMs: Math.max(0, limits.maxDurationMs - elapsed) })
    }, 500)
    publish({
      phase: 'recording',
      startedAtISO: sessionStartISO,
      elapsedMs: 0,
      remainingMs: limits.maxDurationMs,
      message: wantsCall
        ? 'Recording microphone and shared call audio as two separate tracks. Keep this tab open.'
        : 'Recording microphone only. Keep this tab open.'
    })
    return true
  }

  // ----- stop / finalize ---------------------------------------------------
  const probeDuration = async (blob) => {
    try {
      if (!env.probeDuration) return null
      const value = await env.probeDuration(blob)
      return Number.isFinite(value) ? Math.round(value * 1000) : null
    } catch { return null }
  }

  const finalizeRecorder = async (recorder, chunks, key, label, source, mimeFallback, timingKey) => {
    if (!recorder) return null
    const mime = recorder.mimeType || mimeFallback
    const saved = await storage.get(sessionId)
    if (!saved) throw new Error('Session is missing. Local recording data is unavailable; recovery cannot be promised.')
    const track = saved.tracks.find(t => t.key === key)
    if (!track?.bytes) return null
    const filename = `${key}-${clockStamp(new Date(sessionStartISO || Date.now()))}.${extFor(mime)}`
    const stop = stoppedAt[timingKey] || (now() - sessionStartMs)
    return {
      key,
      label,
      source: `${source}: ${track.source}`,
      filename,
      mime,
      bytes: track.bytes,
      startedOffsetMs: startedAt[timingKey],
      stoppedOffsetMs: stop,
      captureWindowMs: Math.max(0, stop - startedAt[timingKey]),
      durationMs: null,
      chunks: track.chunks
    }
  }

  const finish = async (reason) => {
    const current = generation
    const wantsCall = !!callRecorder
    const mics = micRecorder
    const calls = callRecorder
    publish({ phase: 'stopping', saving: true, message: reason === 'user' ? 'Stopping both inputs…' : state.message })
    clearTimer()
    detachEnded()
    const recorders = [mics, calls].filter(Boolean)
    // Request all stops before releasing inputs. Privacy release is synchronous,
    // even if an encoder is broken or takes seconds to deliver its final events.
    for (const recorder of recorders) {
      if (recorder.state !== 'inactive') { try { recorder.stop() } catch { /* await terminal event or deadline */ } }
    }
    stopTracks(micStream)
    stopTracks(callStream)
    let timeout
    const complete = await Promise.race([
      Promise.all(recorders.map(r => drains.get(r).promise)).then(() => true),
      new Promise(resolve => { timeout = env.setTimeout(() => resolve(false), 5000) })
    ])
    env.clearTimeout(timeout)
    // Freeze the received chunks only after stop, or after the bounded failure
    // deadline. A broken encoder's later events cannot mutate exported files.
    for (const recorder of recorders) {
      recorder.onstop = null; recorder.ondataavailable = null; recorder.onerror = null
    }
    if (!complete) publish({ interrupted: state.interrupted || 'drain-timeout', message: 'Encoder did not finish within 5 seconds. Received audio is kept, but final audio may be incomplete.' })
    micStream = null
    callStream = null
    micRecorder = null
    callRecorder = null
    if (current !== generation) return
    publish({ phase: 'finalizing' })
    const writeFailure = await writer.drain()
    if (writeFailure) publish({ interrupted: 'storage-failure', error: `Local write failed: ${writeFailure.message}. Reload to inspect any remaining committed chunks; the tail may be missing.` })
    const mime = mics?.mimeType || calls?.mimeType || ''
    const files = []
    const micFile = await finalizeRecorder(mics, null, 'mic', 'Microphone', 'getUserMedia audio track', mime, 'mic')
    if (micFile) files.push(micFile)
    const callFile = wantsCall ? await finalizeRecorder(calls, null, 'call-audio', 'Call audio', 'getDisplayMedia shared audio track', mime, 'call') : null
    if (callFile) files.push(callFile)
    if (current !== generation) {
      for (const file of files) if (file.url) env.URL.revokeObjectURL(file.url)
      return
    }

    const totalBytes = files.reduce((sum, f) => sum + f.bytes, 0)
    const trimmed = reason === 'duration-cap' || reason === 'limits'
    const timing = {
      schema: 'call-recorder-timing/1',
      captureBuffer: 'incremental IndexedDB chunks; bounded pending writes',
      sessionStartedISO: sessionStartISO,
      sessionDurationMs: now() - sessionStartMs,
      mimeType: mime,
      audioBitsPerSecond: 64000,
      capture: {
        microphone: { source: 'getUserMedia({audio:true})', label: state.mic.label, provenance: 'this device microphone', separateRecorder: true },
        callAudio: wantsCall
          ? { source: 'getDisplayMedia({audio:true,video:true})', label: state.call.label, provenance: 'shared tab/window audio track returned by the browser', separateRecorder: true, videoPersisted: false }
          : { source: 'not captured', provenance: 'user chose microphone only', separateRecorder: false }
      },
      tracks: files.map((f) => ({
        key: f.key, label: f.label, source: f.source, filename: f.filename, mimeType: f.mime,
        startOffsetMs: f.startedOffsetMs, stopOffsetMs: f.stoppedOffsetMs,
        captureWindowMs: f.captureWindowMs,
        containerDurationMs: f.durationMs, bytes: f.bytes, chunks: f.chunks
      })),
      stopReason: reason,
      finalizationComplete: complete,
      interruption: state.interrupted || null,
      videoPersisted: false,
      durationNote: 'Container duration is unmeasured. MediaRecorder WebM output may have no duration header; some players show an unknown length.',
      note: 'Two independent audio files plus shared timing offsets. This is not a muxed two-track container.'
    }
    const partial = !!state.interrupted || !!writeFailure || files.length !== (wantsCall ? 2 : 1)
    try { await storage.update(sessionId, { status: partial || !complete ? 'partial' : 'saved', tracks: files.map(f => ({ key: f.key, label: f.label, source: f.source, filename: f.filename, mimeType: f.mime, bytes: f.bytes, chunks: f.chunks, startedOffsetMs: f.startedOffsetMs, stoppedOffsetMs: f.stoppedOffsetMs, captureWindowMs: f.captureWindowMs })), timing, interrupted: state.interrupted || null, stopReason: reason, sessionDurationMs: timing.sessionDurationMs }) }
    catch (e) { publish({ interrupted: 'storage-failure', error: `Final metadata write failed: ${e.message}. Reload to inspect whether committed chunks are still available.` }) }
    await storage.endSession?.(sessionId)
    const interruptedMessage = state.interrupted ? `${state.message} ` : ''
    const capNote = trimmed ? ' Capture stopped at the cap; the audio already recorded is kept. ' : ''
    publish({
      phase: files.length === 0 ? 'error' : (partial || state.interrupted) ? 'partial' : 'done',
      saving: false,
      mic: { ...state.mic, live: false, level: 0 },
      call: { ...state.call, live: false, level: 0 },
      files: files,
      sessionId,
      timing,
      totalBytes,
      elapsedMs: now() - sessionStartMs,
      message: files.length === 0
        ? 'Stopped with no audio data. Nothing to download.'
        : `${interruptedMessage}${files.length} separate audio file${files.length === 1 ? '' : 's'} ready below, plus timing metadata.${state.micOnly ? ' Microphone only.' : ''}${capNote} Nothing left this page.`,
      error: state.error || (files.length === 0 ? 'No audio chunks were committed.' : !complete ? 'Encoder finalization timed out. Committed audio may be incomplete.' : '')
    })
  }

  const stop = async (reason = 'user') => {
    if (stopping) return stopping
    if (state.phase !== 'recording') return false
    stopping = finish(reason).then(() => true).catch(async error => {
      await storage.endSession?.(sessionId)
      releaseAll(''); publish({ phase: 'partial', saving: false, sessionId, interrupted: 'storage-failure', error: `Local finalization failed: ${error.message}. Reload to inspect any remaining local data; recovery is not guaranteed.` }); return false
    })
    try { return await stopping }
    finally { stopping = null }
  }

  const interrupt = (kind, message) => {
    if (!['starting', 'recording'].includes(state.phase)) return
    publish({ interrupted: kind, message })
    if (state.phase === 'recording') void stop('interrupted')
    else releaseAll(message)
  }

  const reset = () => {
    if (capturePhases.includes(state.phase)) return false
    generation++
    for (const file of state.files) if (file.url) env.URL.revokeObjectURL(file.url)
    releaseAll('')
    publish({
      phase: 'idle', files: [], timing: null, error: '', interrupted: '', saving: false,
      elapsedMs: 0, remainingMs: limits.maxDurationMs, totalBytes: 0, micOnly: false,
      mic: { status: 'none', label: '', live: false, level: 0 },
      call: { status: 'none', label: '', live: false, hasAudio: false, hasVideo: false, videoTracks: 0, level: 0 },
      message: 'Grant the microphone, then optionally add call audio.'
    })
    return true
  }

  const teardown = () => {
    disposed = true
    // Cancel acquisition, not an existing recording's finalization. Late grants
    // must still release their tracks, while captured audio must still drain.
    if (state.phase === 'recording') return stop('disposal')
    if (stopping) return stopping
    if (state.phase === 'starting') { generation++; releaseAll(''); return Promise.resolve() }
    if (capturePhases.includes(state.phase)) return Promise.resolve()
    generation++
    releaseAll('')
    return Promise.resolve()
  }

  const setLevel = (key, level) => {
    if (state[key].level === level) return
    state[key].level = level
    emit()
  }

  return {
    get state() { return state },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    grantMic, grantCallAudio, chooseMicOnly, clearMicOnly, start, stop, reset, teardown,
    releaseForUnload: teardown,
    setLevel,
    streams: () => ({ micStream, callStream }),
    active: () => capturePhases.includes(state.phase)
  }
}
