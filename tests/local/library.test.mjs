// Library record and storage-facade checks under Node with an in-memory
// backend. These prove record shape, atomicity at the backend boundary, failure
// handling and scoped delete. They prove nothing about a real IndexedDB; the
// browser suite (tests/browser-library.mjs) covers that separately.
import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import { buildCallRecord, createLibrary, metadataFor, newCallId, normalizeTitle } from '../../desktop/src/library.mjs'

const results = []
const check = (name, fn) => {
  try { fn(); results.push({ name, ok: true }) }
  catch (error) { results.push({ name, ok: false, detail: error?.message || String(error) }) }
}
const checkAsync = async (name, fn) => {
  try { await fn(); results.push({ name, ok: true }) }
  catch (error) { results.push({ name, ok: false, detail: error?.message || String(error) }) }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const memoryBackend = () => {
  const records = new Map()
  const stats = { puts: 0, transactions: 0, deletes: 0, updates: 0, failPut: null }
  return {
    stats,
    records,
    async put(record) {
      stats.puts++
      stats.transactions++
      if (stats.failPut) throw stats.failPut
      records.set(record.id, structuredClone(record))
      return record
    },
    async getAll() { stats.transactions++; return [...records.values()].map((r) => structuredClone(r)) },
    async get(id) { stats.transactions++; const r = records.get(id); return r ? structuredClone(r) : null },
    async delete(id) { stats.deletes++; stats.transactions++; records.delete(id) },
    async updateTitle(id, title) {
      stats.updates++
      stats.transactions++
      const record = records.get(id)
      if (!record) return null
      record.title = title
      return title
    }
  }
}

const blobOf = (text) => new Blob([text], { type: 'audio/webm;codecs=opus' })

const resultFor = ({ micOnly = false, startedAtISO = '2026-11-10T20:04:53.000Z' } = {}) => {
  const micBlob = blobOf(`MIC-${micOnly ? 'ONLY' : 'TWO'}-${'a'.repeat(64)}`)
  const callBlob = blobOf(`CALL-${'b'.repeat(64)}`)
  const tracks = [{
    key: 'mic', label: 'Microphone', source: 'getUserMedia audio track', filename: 'mic-20261110-140453.webm',
    mimeType: 'audio/webm;codecs=opus', startOffsetMs: 1, stopOffsetMs: 901, captureWindowMs: 900, containerDurationMs: 900, bytes: micBlob.size, chunks: 1
  }]
  if (!micOnly) {
    tracks.push({
      key: 'call-audio', label: 'Call audio', source: 'getDisplayMedia shared audio track', filename: 'call-audio-20261110-140453.webm',
      mimeType: 'audio/webm;codecs=opus', startOffsetMs: 4, stopOffsetMs: 904, captureWindowMs: 900, containerDurationMs: 900, bytes: callBlob.size, chunks: 1
    })
  }
  const timing = {
    schema: 'call-recorder-timing/1',
    sessionStartedISO: startedAtISO,
    sessionDurationMs: 905,
    mimeType: 'audio/webm;codecs=opus',
    capture: {
      microphone: { source: 'getUserMedia({audio:true})', provenance: 'this device microphone', separateRecorder: true },
      callAudio: micOnly
        ? { source: 'not captured', provenance: 'user chose microphone only', separateRecorder: false }
        : { source: 'getDisplayMedia({audio:true,video:true})', provenance: 'shared tab/window audio track returned by the browser', separateRecorder: true, videoPersisted: false }
    },
    tracks,
    stopReason: 'user',
    interruption: null,
    videoPersisted: false
  }
  const files = [{
    key: 'mic', label: 'Microphone', source: 'getUserMedia audio track', filename: 'mic-20261110-140453.webm',
    mime: 'audio/webm;codecs=opus', bytes: micBlob.size, blob: micBlob, url: 'blob:mic',
    startedOffsetMs: 1, stoppedOffsetMs: 901, captureWindowMs: 900, durationMs: 900, chunks: 1
  }]
  if (!micOnly) {
    files.push({
      key: 'call-audio', label: 'Call audio', source: 'getDisplayMedia shared audio track', filename: 'call-audio-20261110-140453.webm',
      mime: 'audio/webm;codecs=opus', bytes: callBlob.size, blob: callBlob, url: 'blob:call',
      startedOffsetMs: 4, stoppedOffsetMs: 904, captureWindowMs: 900, durationMs: 900, chunks: 1
    })
  }
  files.push({ key: 'timing', label: 'Timing metadata', source: 'generated in this page', filename: 'timing-20261110-140453.json', mime: 'application/json', bytes: 10, blob: blobOf('{}'), url: 'blob:timing' })
  return { files, timing, startedAtISO, interrupted: '' }
}

const run = async () => {
  check('generated call IDs are stable-format and unique', () => {
    const ids = new Set()
    for (let i = 0; i < 200; i++) ids.add(newCallId(new Date('2026-11-10T20:04:53Z')))
    assert.equal(ids.size, 200)
    for (const id of ids) assert.match(id, /^call-20261110-\d{6}-[a-z0-9]{6}$/)
    assert.match(newCallId(new Date('2026-11-10T20:04:53Z')), /^call-\d{8}-\d{6}-[a-z0-9]{6}$/)
  })

  check('titles are trimmed, length-capped and defaulted', () => {
    assert.equal(normalizeTitle('   '), 'Untitled call')
    assert.equal(normalizeTitle('  Quarterly   planning  '), 'Quarterly planning')
    assert.equal(normalizeTitle('x'.repeat(400)).length, 120)
  })

  await checkAsync('one save writes one record holding both track Blobs and shared timing', async () => {
    const backend = memoryBackend()
    const lib = createLibrary({ backend, origin: 'http://127.0.0.1:8080', makeId: () => 'call-20261110-140453-abc123' })
    const result = resultFor()
    const record = await lib.save(result)
    assert.equal(backend.stats.puts, 1, 'exactly one backend write')
    assert.equal(backend.stats.transactions, 1, 'exactly one transaction')
    assert.equal(backend.records.size, 1)
    assert.equal(record.id, 'call-20261110-140453-abc123')
    assert.equal(record.tracks.length, 2)
    assert.deepEqual(record.tracks.map((t) => t.key), ['mic', 'call-audio'])
    assert.equal(record.tracks[0].blob.size, result.files[0].bytes)
    assert.equal(await record.tracks[0].blob.text(), 'MIC-TWO-' + 'a'.repeat(64))
    assert.equal(await record.tracks[1].blob.text(), 'CALL-' + 'b'.repeat(64))
    assert.equal(record.timing.sessionStartedISO, result.startedAtISO)
    assert.equal(record.timing.capture.callAudio.source.startsWith('getDisplayMedia'), true)
    assert.equal(record.micOnly, false)
    assert.equal(record.provenance.backend, 'none')
    assert.equal(record.provenance.mixed, false)
    assert.equal(record.provenance.synchronizedContainer, false)
    assert.equal(record.schema, 'call-library-record/1')
  })

  await checkAsync('stored timing is a copy, not a live reference to capture state', async () => {
    const backend = memoryBackend()
    const lib = createLibrary({ backend, makeId: () => 'call-20261110-140453-abc124' })
    const result = resultFor()
    const record = await lib.save(result)
    result.timing.stopReason = 'mutated-after-save'
    const stored = await backend.get(record.id)
    assert.equal(stored.timing.stopReason, 'user')
  })

  await checkAsync('microphone-only capture is clearly marked and owns one track', async () => {
    const backend = memoryBackend()
    const lib = createLibrary({ backend, makeId: () => 'call-20261110-140453-abc125' })
    const record = await lib.save(resultFor({ micOnly: true }))
    assert.equal(record.micOnly, true)
    assert.equal(record.tracks.length, 1)
    assert.equal(record.timing.capture.callAudio.source, 'not captured')
  })

  await checkAsync('stopping with no audio saves nothing and writes nothing', async () => {
    const backend = memoryBackend()
    const lib = createLibrary({ backend, makeId: () => 'call-20261110-140453-abc126' })
    await assert.rejects(() => lib.save({ files: [], timing: null, startedAtISO: '' }), /nothing-to-save/)
    await assert.rejects(() => lib.save(null), /No finished recording to save/)
    assert.equal(backend.stats.puts, 0)
    assert.equal(backend.records.size, 0)
  })

  await checkAsync('an inconsistent record (mic-only timing with two tracks) is refused', async () => {
    const backend = memoryBackend()
    const lib = createLibrary({ backend, makeId: () => 'call-20261110-140453-abc127' })
    const result = resultFor()
    result.timing.capture.callAudio = { source: 'not captured' }
    await assert.rejects(() => lib.save(result), /exactly one track/)
    assert.equal(backend.stats.puts, 0)
  })

  await checkAsync('a quota failure rejects the save and leaves zero records', async () => {
    const backend = memoryBackend()
    backend.stats.failPut = new DOMException('quota exceeded', 'QuotaExceededError')
    const lib = createLibrary({ backend, makeId: () => 'call-20261110-140453-abc128' })
    const result = resultFor()
    await assert.rejects(() => lib.save(result), /quota exceeded/)
    assert.equal(backend.records.size, 0)
    // The caller still holds the captured Blobs and their URLs for export.
    assert.equal(result.files.filter((f) => f.blob).length, 3)
    assert.equal(result.files[0].url, 'blob:mic')
  })

  await checkAsync('listing returns newest capture first', async () => {
    const backend = memoryBackend()
    let n = 0
    const lib = createLibrary({ backend, makeId: () => `call-20261110-14045${n}-abc12${n++}` })
    await lib.save(resultFor({ startedAtISO: '2026-11-10T10:00:00.000Z' }))
    await lib.save(resultFor({ startedAtISO: '2026-11-10T12:00:00.000Z' }))
    await lib.save(resultFor({ startedAtISO: '2026-11-10T11:00:00.000Z' }))
    const calls = await lib.list()
    assert.deepEqual(calls.map((c) => c.capturedAtISO), [
      '2026-11-10T12:00:00.000Z', '2026-11-10T11:00:00.000Z', '2026-11-10T10:00:00.000Z'
    ])
  })

  await checkAsync('rename changes only the title and keeps both track Blobs', async () => {
    const backend = memoryBackend()
    const lib = createLibrary({ backend, makeId: () => 'call-20261110-140453-abc130' })
    const record = await lib.save(resultFor())
    const before = await backend.get(record.id)
    assert.equal(await lib.rename(record.id, '  Quarterly planning  '), 'Quarterly planning')
    const after = await backend.get(record.id)
    assert.equal(after.title, 'Quarterly planning')
    assert.equal(after.tracks.length, 2)
    assert.equal(after.tracks[0].blob.size, before.tracks[0].blob.size)
    assert.equal(await after.tracks[1].blob.text(), 'CALL-' + 'b'.repeat(64))
    await assert.rejects(() => lib.rename('not-a-call-id', 'x'), /Invalid call ID/)
  })

  await checkAsync('delete removes only the chosen record and leaves the other intact', async () => {
    const backend = memoryBackend()
    let n = 0
    const lib = createLibrary({ backend, makeId: () => `call-20261110-14045${n}-abc13${n++}` })
    const a = await lib.save(resultFor())
    const b = await lib.save(resultFor({ micOnly: true }))
    assert.equal(backend.records.size, 2)
    await lib.remove(a.id)
    assert.equal(backend.records.size, 1)
    assert.equal(backend.records.has(a.id), false)
    const kept = await backend.get(b.id)
    assert.equal(kept.tracks.length, 1)
    assert.equal(kept.tracks[0].blob.size > 0, true)
    await assert.rejects(() => lib.remove(a.id), /no longer exists/, 'second delete of the same id is refused')
    assert.equal(backend.records.size, 1)
  })

  await checkAsync('metadata export comes from the stored record and contains no Blob', async () => {
    const backend = memoryBackend()
    const lib = createLibrary({ backend, makeId: () => 'call-20261110-140453-abc140' })
    const record = await lib.save(resultFor())
    const meta = metadataFor(record)
    const json = JSON.parse(JSON.stringify(meta))
    assert.equal(json.id, record.id)
    assert.equal(json.tracks.length, 2)
    assert.equal('blob' in json.tracks[0], false)
    assert.equal(json.timing.capture.callAudio.source.startsWith('getDisplayMedia'), true)
    assert.match(json.note, /not a muxed/i)
    assert.equal(record.tracks[0].blob instanceof Blob, true)
  })

  const failed = results.filter((r) => !r.ok)
  const summary = { suite: 'library records (memory backend)', total: results.length, passed: results.length - failed.length, failed: failed.length, results }
  writeFileSync(new URL('./checks-library.json', import.meta.url), JSON.stringify(summary, null, 2))
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.ok ? '' : ' :: ' + r.detail}`)
  console.log(`\n${summary.passed}/${summary.total} library checks passed`)
  process.exitCode = failed.length ? 1 : 0
}

await run()
await sleep(1)
