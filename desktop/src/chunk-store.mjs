// Incremental, origin-local storage. No library-wide media reads.
export function createChunkStore({ indexedDBImpl = globalThis.indexedDB, dbName = 'call-recorder-chunks-v2' } = {}) {
  let connection
  const open = () => connection ||= new Promise((resolve, reject) => {
    const r = indexedDBImpl.open(dbName, 1)
    r.onupgradeneeded = () => {
      r.result.createObjectStore('sessions', { keyPath: 'id' })
      r.result.createObjectStore('chunks', { keyPath: ['id', 'track', 'sequence'] })
    }
    r.onsuccess = () => resolve(r.result)
    r.onerror = () => reject(r.error)
    r.onblocked = () => reject(new Error('Close other copies before opening storage.'))
  })
  const tx = async (names, mode, run) => {
    const db = await open()
    return new Promise((resolve, reject) => {
      const t = db.transaction(names, mode)
      let value, failure
      const fail = e => { failure = e; try { t.abort() } catch {} }
      t.oncomplete = () => resolve(value)
      t.onabort = t.onerror = () => reject(failure || t.error || new Error('Local storage transaction failed'))
      try { run(t, v => { value = v }, fail) } catch (e) { fail(e) }
    })
  }
  const get = id => tx(['sessions'], 'readonly', (t, set) => {
    const r = t.objectStore('sessions').get(id); r.onsuccess = () => set(r.result)
  })
  const create = session => tx(['sessions'], 'readwrite', t => t.objectStore('sessions').add(session))
  const update = (id, patch) => tx(['sessions'], 'readwrite', (t, set, fail) => {
    const s = t.objectStore('sessions'), r = s.get(id)
    r.onsuccess = () => { try { if (!r.result) throw new Error('Session is missing'); s.put({ ...r.result, ...patch }) } catch (e) { fail(e) } }
  })
  const append = (id, track, sequence, blob, offsetMs, startedOffsetMs = 0) => tx(['sessions', 'chunks'], 'readwrite', (t, set, fail) => {
    const s = t.objectStore('sessions'), r = s.get(id)
    r.onsuccess = () => {
      try {
      const session = r.result
      if (!session || session.status !== 'recording') { t.abort(); return }
      const tracks = session.tracks.map(v => v.key === track ? { ...v, bytes: v.bytes + blob.size, chunks: v.chunks + 1, startedOffsetMs, stoppedOffsetMs: offsetMs, captureWindowMs: offsetMs - startedOffsetMs } : v)
      t.objectStore('chunks').add({ id, track, sequence, blob, offsetMs })
      s.put({ ...session, tracks, sessionDurationMs: offsetMs })
      } catch (e) { fail(e) }
    }
  })
  const list = () => tx(['sessions'], 'readonly', (t, set) => {
    const result = [], r = t.objectStore('sessions').openCursor()
    r.onsuccess = () => { const c = r.result; if (!c) { set(result.reverse()); return }; result.push(c.value); c.continue() }
  })
  // One indexed key at a time. File System Access export never assembles a track.
  async function* chunks(id, track) {
    for (let sequence = 0; ; sequence++) {
      const value = await tx(['chunks'], 'readonly', (t, set) => {
        const r = t.objectStore('chunks').get([id, track, sequence]); r.onsuccess = () => set(r.result)
      })
      if (!value) return
      yield value.blob
    }
  }
  const assemble = async (id, track) => {
    const session = await get(id), info = session.tracks.find(t => t.key === track)
    const parts = []; for await (const blob of chunks(id, track)) parts.push(blob)
    return new Blob(parts, { type: info.mimeType })
  }
  const exportTo = async (id, track, writable) => {
    try { for await (const blob of chunks(id, track)) await writable.write(blob); await writable.close() }
    catch (e) { await writable.abort?.(e); throw e }
  }
  const remove = id => tx(['sessions', 'chunks'], 'readwrite', t => {
    t.objectStore('sessions').delete(id)
    const r = t.objectStore('chunks').openCursor(IDBKeyRange.bound([id], [id, []]))
    r.onsuccess = () => { const c = r.result; if (c) { c.delete(); c.continue() } }
  })
  return { create, append, get, update, list, chunks, assemble, exportTo, remove, close: () => { connection?.then(db => db.close()); connection = null } }
}

// Serial writes release each Blob after commit. Hard pending bounds stop capture
// rather than silently dropping media or claiming unlimited buffering.
export function createChunkWriter({ store, id, maxBytes = 8 * 1024 * 1024, maxChunks = 16, onFailure }) {
  let tail = Promise.resolve(), pendingBytes = 0, pendingChunks = 0, failure = null
  const sequences = new Map()
  const fail = error => { if (!failure) { failure = error; onFailure?.(error) } }
  return {
    append(track, blob, offsetMs, startedOffsetMs = 0) {
      if (failure) return false
      if (pendingBytes + blob.size > maxBytes || pendingChunks >= maxChunks) {
        fail(new Error('Pending local writes exceeded the safety buffer. Committed chunks are preserved; the remaining tail may be missing.'))
        return false
      }
      const sequence = sequences.get(track) || 0; sequences.set(track, sequence + 1)
      pendingBytes += blob.size; pendingChunks++
      tail = tail.then(async () => {
        if (!failure) { try { await store.append(id, track, sequence, blob, offsetMs, startedOffsetMs) } catch (e) { fail(e) } }
        pendingBytes -= blob.size; pendingChunks--
      })
      return true
    },
    async drain() { await tail; return failure },
    get pendingBytes() { return pendingBytes }, get pendingChunks() { return pendingChunks }, get error() { return failure }
  }
}
