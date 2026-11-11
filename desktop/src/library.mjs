// Saved-call record shape and library facade.
//
// Pure enough to test under Node: storage is a small backend interface, the
// real one lives in idb-backend.mjs. A record holds both track Blobs, the
// shared timing/provenance document and one generated call ID. The caller
// writes the whole record in one backend.put(), which the IndexedDB backend
// performs inside a single readwrite transaction.
//
// No mixing, concatenation or muxing happens here. The two tracks stay separate
// byte-for-byte, and timing metadata records their shared session offsets.

export const RECORD_SCHEMA = 'call-library-record/1'
export const MAX_TITLE = 120

const ID_RE = /^call-\d{8}-\d{6}-[a-z0-9]{6}$/

const pad = (n, w = 2) => String(n).padStart(w, '0')

export function newCallId(date = new Date(), random = () => crypto.getRandomValues(new Uint8Array(3))) {
  const stamp = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  const bytes = Array.from(random())
  return `call-${stamp}-${bytes.map((b) => b.toString(36).padStart(2, '0')).join('').slice(0, 6)}`
}

export function isCallId(id) {
  return typeof id === 'string' && ID_RE.test(id)
}

export function normalizeTitle(title) {
  const value = typeof title === 'string' ? title.trim().replace(/\s+/g, ' ') : ''
  if (!value) return 'Untitled call'
  return value.slice(0, MAX_TITLE)
}

function cloneTiming(timing) {
  // Structured copy of the metadata only. Blobs never enter this document.
  return JSON.parse(JSON.stringify(timing ?? null))
}

export function buildCallRecord(result, { id, title = '', origin = '', createdAtISO = new Date().toISOString() } = {}) {
  if (!result || !Array.isArray(result.files)) throw new Error('No finished recording to save.')
  if (!isCallId(id)) throw new Error(`Invalid call ID: ${String(id)}`)
  const audioFiles = result.files.filter((file) => file.key !== 'timing' && file.blob instanceof Blob && file.blob.size > 0)
  if (audioFiles.length === 0) throw new Error('nothing-to-save')
  if (!audioFiles.some((file) => file.key === 'mic')) throw new Error('The recording has no microphone track.')
  const timing = cloneTiming(result.timing)
  if (!timing) throw new Error('The recording has no timing metadata.')

  const tracks = audioFiles.map((file) => ({
    key: file.key,
    label: file.label,
    source: file.source,
    filename: file.filename,
    mimeType: file.mime,
    bytes: file.bytes,
    blob: file.blob,
    startedOffsetMs: file.startedOffsetMs,
    stoppedOffsetMs: file.stoppedOffsetMs,
    captureWindowMs: file.captureWindowMs,
    containerDurationMs: file.durationMs ?? null,
    chunks: file.chunks ?? null
  }))
  const micOnly = timing.capture?.callAudio?.source === 'not captured'
  if (micOnly && tracks.length !== 1) throw new Error('Microphone-only timing must own exactly one track.')
  if (!micOnly && tracks.length !== 2) throw new Error('A two-input call must own exactly two tracks.')

  return {
    id,
    schema: RECORD_SCHEMA,
    title: normalizeTitle(title),
    createdAtISO,
    capturedAtISO: result.startedAtISO || timing.sessionStartedISO || createdAtISO,
    sessionDurationMs: timing.sessionDurationMs ?? null,
    stopReason: timing.stopReason || 'user',
    interrupted: timing.interruption || null,
    micOnly,
    tracks,
    timing,
    provenance: {
      capture: 'two independent browser MediaRecorder instances',
      storage: 'IndexedDB, this origin and this browser profile',
      backend: 'none',
      mixed: false,
      synchronizedContainer: false,
      videoPersisted: false,
      origin
    }
  }
}

// Metadata export built from a stored record, not from live capture state.
export function metadataFor(record) {
  return {
    schema: record.schema || RECORD_SCHEMA,
    id: record.id,
    title: record.title,
    createdAtISO: record.createdAtISO,
    capturedAtISO: record.capturedAtISO,
    sessionDurationMs: record.sessionDurationMs,
    stopReason: record.stopReason,
    interrupted: record.interrupted,
    micOnly: record.micOnly,
    provenance: record.provenance,
    tracks: record.tracks.map(({ blob, ...rest }) => rest),
    timing: record.timing,
    note: 'Two separate audio files plus shared timing offsets. Not a muxed or synchronized two-track container.'
  }
}

export function createLibrary({ backend, origin = '', now = () => new Date(), makeId = () => newCallId(now()) } = {}) {
  if (!backend) throw new Error('createLibrary needs a storage backend.')

  const save = async (result, { title = '' } = {}) => {
    const record = buildCallRecord(result, { id: makeId(), title, origin, createdAtISO: now().toISOString() })
    // One call to the backend = one record = one transaction. Nothing is
    // written before this point, so a failed or absent stop saves nothing.
    await backend.put(record)
    return record
  }

  const list = async () => {
    const records = await backend.getAll()
    return [...records].sort((a, b) => String(b.capturedAtISO).localeCompare(String(a.capturedAtISO)))
  }

  const rename = async (id, title) => {
    if (!isCallId(id)) throw new Error(`Invalid call ID: ${String(id)}`)
    const next = normalizeTitle(title)
    const saved = await backend.updateTitle(id, next)
    if (!saved) throw new Error('That saved call no longer exists.')
    return next
  }

  const remove = async (id) => {
    if (!isCallId(id)) throw new Error(`Invalid call ID: ${String(id)}`)
    const record = await backend.get(id)
    if (!record) throw new Error('That saved call no longer exists.')
    await backend.delete(id)
    return id
  }

  return { save, list, rename, remove }
}
