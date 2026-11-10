// Real IndexedDB backend for the local call library.
//
// One record per call. put() writes the entire record (both track Blobs, timing
// metadata, provenance, ID) inside a single readwrite transaction, so a call is
// either fully present or not present at all. Rename is a single read-modify-write
// transaction. Delete removes exactly one key.
//
// Scope is the page origin and this browser profile. No backend, no network.

export const DB_NAME = 'call-recorder-local-library'
export const DB_VERSION = 1
export const STORE = 'calls'

export function createIdbBackend({ indexedDBImpl = globalThis.indexedDB, dbName = DB_NAME, version = DB_VERSION } = {}) {
  if (!indexedDBImpl) throw new Error('IndexedDB is not available in this browser.')

  let dbPromise = null
  const openDb = () => {
    if (dbPromise) return dbPromise
    dbPromise = new Promise((resolve, reject) => {
      let request
      try {
        request = indexedDBImpl.open(dbName, version)
      } catch (error) {
        reject(error)
        return
      }
      request.onupgradeneeded = () => {
        const db = request.result
        if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'id' })
      }
      request.onsuccess = () => {
        const db = request.result
        db.onversionchange = () => { try { db.close() } catch { /* ignore */ } dbPromise = null }
        resolve(db)
      }
      request.onerror = () => reject(request.error || new Error('Opening IndexedDB failed.'))
      request.onblocked = () => reject(new Error('IndexedDB is blocked by another open copy of this page.'))
    }).catch((error) => { dbPromise = null; throw error })
    return dbPromise
  }

  // Runs fn(store) inside one transaction. Resolves with the value captured by
  // the operation's onsuccess, only after the transaction commits.
  const withStore = async (mode, fn) => {
    const db = await openDb()
    return new Promise((resolve, reject) => {
      let transaction
      try {
        transaction = db.transaction(STORE, mode)
      } catch (error) {
        reject(error)
        return
      }
      const store = transaction.objectStore(STORE)
      let value
      let failure = null
      transaction.oncomplete = () => (failure ? reject(failure) : resolve(value))
      transaction.onerror = () => reject(failure || transaction.error || new Error('IndexedDB transaction failed.'))
      transaction.onabort = () => reject(failure || transaction.error || new Error('IndexedDB transaction was aborted.'))
      try {
        value = fn(store, (v) => { value = v })
      } catch (error) {
        failure = error
        try { transaction.abort() } catch { /* already aborting */ }
      }
    })
  }

  const put = async (record) => {
    await withStore('readwrite', (store) => { store.put(record) })
    return record
  }

  const getAll = () => withStore('readonly', (store, set) => {
    const request = store.getAll()
    request.onsuccess = () => set(request.result || [])
  })

  const get = (id) => withStore('readonly', (store, set) => {
    const request = store.get(id)
    request.onsuccess = () => set(request.result || null)
  })

  const deleteOne = (id) => withStore('readwrite', (store) => { store.delete(id) })

  const updateTitle = (id, title) => withStore('readwrite', (store, set) => {
    const request = store.get(id)
    request.onsuccess = () => {
      const record = request.result
      if (!record) return
      store.put({ ...record, title, titleUpdatedAtISO: new Date().toISOString() })
      set(title)
    }
  })

  return { put, getAll, get, delete: deleteOne, updateTitle, close: () => { try { dbPromise?.then((db) => db.close()) } catch { /* ignore */ } dbPromise = null } }
}
