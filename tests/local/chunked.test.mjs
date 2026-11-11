import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createChunkWriter } from '../../desktop/src/chunk-store.mjs'
import { createCallRecorder } from '../../desktop/src/recorder-core.mjs'

test('serial commits release bounded pending bytes and preserve track sequences beyond former total cap', async () => {
  const seen = [], store = { append: async (id, track, sequence, blob) => { seen.push({ track, sequence, size: blob.size }) } }
  const writer = createChunkWriter({ store, id: 'a' })
  for (let i = 0; i < 30; i++) { writer.append('mic', new Blob([new Uint8Array(1024 * 1024)]), i); await writer.drain(); assert.equal(writer.pendingBytes, 0) }
  writer.append('call-audio', new Blob(['call']), 30); await writer.drain()
  assert.equal(seen.length, 31); assert.equal(seen[29].sequence, 29); assert.equal(seen[30].sequence, 0)
})
test('slow writer hits bounded pending limit once and never grows unbounded', async () => {
  let release, failures = 0
  const writer = createChunkWriter({ store: { append: () => new Promise(r => release = r) }, id: 'a', maxBytes: 4, maxChunks: 2, onFailure: () => failures++ })
  writer.append('mic', new Blob(['12']), 0); await Promise.resolve()
  writer.append('mic', new Blob(['34']), 1)
  assert.equal(writer.append('mic', new Blob(['5']), 2), false)
  assert.equal(writer.pendingBytes, 4); assert.equal(failures, 1)
  release(); await writer.drain(); assert.equal(writer.pendingBytes, 0)
})
test('quota and final chunk failures preserve committed prefix and reject Saved', async () => {
  const committed = []; let failures = 0
  const writer = createChunkWriter({ store: { append: async (id, track, n, b) => { if (n === 1) throw new DOMException('full', 'QuotaExceededError'); committed.push(await b.text()) } }, id: 'a', onFailure: () => failures++ })
  writer.append('mic', new Blob(['first']), 0); await writer.drain()
  writer.append('mic', new Blob(['terminal']), 1)
  const error = await writer.drain(); assert.equal(error.name, 'QuotaExceededError'); assert.deepEqual(committed, ['first']); assert.equal(failures, 1)
})

class Track { constructor() { this.kind = 'audio'; this.readyState = 'live'; this.label = 'fixture' } stop() { this.readyState = 'ended' } addEventListener() {} removeEventListener() {} }
class Stream { constructor(tracks = [new Track()]) { this.tracks = tracks } getTracks() { return this.tracks } getAudioTracks() { return this.tracks } getVideoTracks() { return [] } }
class Recorder {
  static isTypeSupported() { return true }
  static all = []
  constructor(stream, opts) { this.stream = stream; this.mimeType = opts.mimeType; this.state = 'inactive'; Recorder.all.push(this) }
  start() { this.state = 'recording' }
  stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['terminal']) }); this.onstop?.() }) }
}
function fixture({ failFinal = false } = {}) {
  Recorder.all = []; let session, committed = [], clock = 0
  const storage = { create: async r => { session = structuredClone(r) }, get: async () => session, append: async (id, track, sequence, blob) => { if (failFinal) throw Error('final-write'); committed.push(await blob.text()); const t = session.tracks.find(t => t.key === track); t.bytes += blob.size; t.chunks++ }, update: async (id, patch) => Object.assign(session, patch) }
  const env = { isSecureContext: true, performance, location: { origin: 'http://localhost' }, navigator: { mediaDevices: { getUserMedia: async () => new Stream(), getDisplayMedia: async () => new Stream() } }, MediaRecorder: Recorder, MediaStream: Stream, Blob, URL, setTimeout, clearTimeout, setInterval, clearInterval }
  const core = createCallRecorder({ env, storage, now: () => clock })
  return { core, storage, get session() { return session }, committed, advance: ms => clock += ms }
}
test('stop+teardown retains terminal data, waits writes and labels Saved only after commit', async () => {
  const f = fixture(); await f.core.grantMic(); f.core.chooseMicOnly(); await f.core.start(); f.advance(181000)
  const p = f.core.stop(); const teardown = f.core.teardown(); await Promise.all([p, teardown])
  assert.deepEqual(f.committed, ['terminal']); assert.equal(f.session.status, 'saved'); assert.equal(f.core.state.phase, 'done'); assert.equal(f.session.sessionDurationMs, 181000)
})
test('final write failure stops visibly, never Saved', async () => {
  const f = fixture({ failFinal: true }); await f.core.grantMic(); f.core.chooseMicOnly(); await f.core.start(); await f.core.stop()
  assert.equal(f.session.status, 'partial'); assert.notEqual(f.core.state.phase, 'done'); assert.match(f.core.state.error, /final-write/)
})
test('native inactive-before-error still drains terminal data for both inputs', async () => {
  const f = fixture(); await f.core.grantMic(); await f.core.grantCallAudio(); await f.core.start()
  const r = Recorder.all[0]; r.state = 'inactive'; r.onerror({ error: Error('encoder') }); queueMicrotask(() => { r.ondataavailable({ data: new Blob(['error-tail']) }); r.onstop() })
  await f.core.teardown(); assert.equal(f.session.status, 'partial'); assert.deepEqual(f.committed.sort(), ['error-tail', 'terminal'])
})
test('ownership remains held through terminal writes and metadata, then releases', async () => {
  const f = fixture(); const events = []
  const append = f.storage.append, update = f.storage.update
  f.storage.append = async (...args) => { events.push('append'); await append(...args) }
  f.storage.update = async (...args) => { events.push('metadata'); await update(...args) }
  f.storage.endSession = async () => events.push('release')
  await f.core.grantMic(); await f.core.grantCallAudio(); await f.core.start(); await f.core.stop()
  assert.deepEqual(events, ['append', 'append', 'metadata', 'release'])
  assert.equal(f.core.state.phase, 'done')
})
test('missing session releases ownership and does not promise recovery', async () => {
  const f = fixture(); let released = false
  await f.core.grantMic(); f.core.chooseMicOnly(); await f.core.start()
  f.storage.get = async () => undefined
  f.storage.endSession = async () => { released = true }
  assert.equal(await f.core.stop(), false)
  assert.equal(released, true)
  assert.notEqual(f.core.state.phase, 'done')
  assert.match(f.core.state.error, /Session is missing/)
  assert.doesNotMatch(f.core.state.error, /remain recoverable|reading 'tracks'/)
})
