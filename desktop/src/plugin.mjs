import { Button, Input, Badge, host } from '@hermes/plugin-sdk'
import { createElement as h, useEffect, useState } from 'react'
import { createCallRecorder } from './recorder-core.mjs'
import { metadataFor } from './library.mjs'
import { createChunkStore } from './chunk-store.mjs'

const box = { border: '1px solid var(--ui-stroke-secondary)', borderRadius: 8, padding: 12, minWidth: 0 }
const row = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }
const note = { color: 'var(--ui-text-secondary)', overflowWrap: 'anywhere' }
const fmt = ms => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`
const action = (text, onClick, props = {}) => h(Button, { type: 'button', variant: 'secondary', size: 'touch', style: { justifySelf: 'start' }, onClick, ...props }, text)

// One controller per mounted pane; neither backend identity nor chat profile is a
// storage or routing input. No host network methods are used anywhere here.
export function createController(env = window) {
  const listeners = new Set(), cleanups = new Set()
  const backend = createChunkStore({ indexedDBImpl: env.indexedDB })
  let disposed = false, disposal
  const ui = { calls: [], error: '', saveState: 'none', saveError: '', ready: false }
  const emit = () => { if (!disposed) for (const fn of listeners) fn() }
  const core = createCallRecorder({ env, storage: backend })
  const refresh = async () => {
    const rows = await backend.list()
    const visible = await Promise.all(rows.map(async r => {
      if (await backend.isActive(r.id)) return null
      return r.status === 'recording' ? { ...r, status: 'recovered', interrupted: 'Interrupted session. Only committed chunks recovered; encoder tail may be missing.' } : r
    }))
    ui.calls = visible.filter(Boolean)
    emit()
  }
  const unsubscribe = core.subscribe(state => {
    ui.saveState = state.phase === 'done' ? 'saved' : state.phase === 'partial' ? 'partial' : state.phase === 'recording' ? 'recording' : state.saving ? 'saving' : 'none'
    ui.saveError = state.error
    emit()
    if (['done', 'partial', 'error'].includes(state.phase)) void refresh().catch(e => { ui.error = e.message; emit() })
  })
  void refresh().catch(e => { ui.error = `Local library unavailable: ${e.message}` }).finally(() => { ui.ready = true; emit() })
  const dispose = () => {
    if (disposed) return disposal
    disposed = true
    const drain = core.teardown()
    for (const cleanup of [...cleanups]) cleanup()
    listeners.clear()
    disposal = Promise.resolve(drain).finally(() => { unsubscribe(); backend.close() })
    return disposal
  }
  return { core, ui, backend, refresh, dispose,
    onCleanup(fn) { let live = true; const cleanup = () => { if (!live) return; live = false; cleanups.delete(cleanup); fn() }; cleanups.add(cleanup); return cleanup },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    async rename(id, title) { try { await backend.update(id, { title: title.trim().slice(0, 120) || 'Untitled call' }); await refresh() } catch (e) { ui.error = e.message; emit() } },
    async remove(id) { try { await backend.remove(id); await refresh() } catch (e) { ui.error = e.message; emit() } }
  }
}

function useBlob(blob) {
  const [url, setUrl] = useState('')
  useEffect(() => { const next = URL.createObjectURL(blob); setUrl(next); return () => URL.revokeObjectURL(next) }, [blob])
  return url
}
function Track({ track, record, controller }) {
  const [blob, setBlob] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const load = async () => { setBusy(true); try { setBlob(await controller.backend.assemble(record.id, track.key)) } catch (e) { setError(e.message) } finally { setBusy(false) } }
  const exportFile = async () => {
    try { const handle = await window.showSaveFilePicker({ suggestedName: track.filename }); await controller.backend.exportTo(record.id, track.key, await handle.createWritable()) }
    catch (e) { if (e.name !== 'AbortError') setError(e.message) }
  }
  return h('section', { style: box, 'data-track-key': track.key },
    h('strong', null, track.label), h('p', { style: note }, `${track.source} · ${track.bytes} bytes · ${track.chunks} committed chunks · start +${track.startedOffsetMs.toFixed(0)} ms`),
    window.showSaveFilePicker && action('Export track to file', exportFile),
    !blob ? action(busy ? 'Loading…' : 'Load playback / download', load, { disabled: busy }) : h(LoadedTrack, { blob, track }),
    error && h('p', { role: 'alert' }, error))
}
function LoadedTrack({ blob, track }) {
  const url = useBlob(blob)
  return h('div', null, h('audio', { controls: true, src: url, style: { width: '100%' }, 'aria-label': `${track.label} playback` }), h('a', { href: url, download: track.filename, 'data-role': `download-${track.key}` }, `Download ${track.label}`))
}
function Metadata({ record }) {
  const [blob] = useState(() => new Blob([JSON.stringify(metadataFor(record), null, 2)], { type: 'application/json' }))
  const url = useBlob(blob)
  return h('a', { href: url, download: `metadata-${record.id}.json`, style: { color: 'var(--ui-accent)' }, 'data-role': 'metadata' }, 'Download metadata')
}
function Card({ record, controller }) {
  const [confirm, setConfirm] = useState(false), [busy, setBusy] = useState(false), [title, setTitle] = useState(record.title)
  return h('article', { style: { ...box, display: 'grid', gap: 12 }, 'data-call-id': record.id },
    h('small', { style: note }, new Date(record.capturedAtISO).toLocaleString()),
    h('label', null, 'Call title', h(Input, { value: title, maxLength: 120, 'aria-label': 'Call title', onChange: e => setTitle(e.target.value) })),
    action('Save title', () => controller.rename(record.id, title)),
    h(Badge, null, record.micOnly ? 'Microphone only' : 'Microphone + call audio'),
    h(Badge, null, record.status === 'saved' ? 'Saved' : record.status === 'recovered' ? 'Recovered partial' : 'Partial'),
    record.interrupted && h('p', null, `Interrupted: ${record.interrupted}`),
    ...record.tracks.map(track => h(Track, { key: track.key, track, record, controller })),
    h('small', { style: note }, 'Separate files with shared timing offsets. No mixing, muxing or drift correction.'),
    h(Metadata, { key: record.title, record }),
    !confirm ? action('Delete this call', () => setConfirm(true)) : h('div', { style: box },
      h('p', null, `Permanently delete “${record.title}” and its audio from this browser? Other calls are not touched.`),
      action(busy ? 'Deleting…' : 'Delete permanently', async () => { setBusy(true); await controller.remove(record.id); setBusy(false) }, { disabled: busy }),
      action('Cancel', () => setConfirm(false), { disabled: busy })))
}
function InputSource({ controller, source, label, grant }) {
  const state = controller.core.state[source]
  const [level, setLevel] = useState(-90)
  useEffect(() => {
    const stream = controller.core.streams()[source === 'mic' ? 'micStream' : 'callStream']
    if (!stream || !state.live) { setLevel(-90); return }
    let context, node, frame
    try {
      context = new AudioContext()
      node = context.createMediaStreamSource(new MediaStream(stream.getAudioTracks()))
      const analyser = context.createAnalyser(); analyser.fftSize = 1024; node.connect(analyser)
      const samples = new Float32Array(1024)
      const tick = () => { analyser.getFloatTimeDomainData(samples); const rms = Math.sqrt(samples.reduce((s, v) => s + v * v, 0) / samples.length); setLevel(Math.round(rms ? Math.max(-90, 20 * Math.log10(rms)) : -90)); frame = requestAnimationFrame(tick) }
      void context.resume(); tick()
    } catch { setLevel(-90) }
    return controller.onCleanup(() => { cancelAnimationFrame(frame); node?.disconnect(); if (context && context.state !== 'closed') void context.close() })
  }, [controller, state.live])
  const phase = controller.core.state.phase
  const locked = !['idle', 'error', 'done', 'partial'].includes(phase)
  return h('section', { style: box }, h('div', { style: row }, h('strong', null, label), h(Badge, null, state.status)),
    h('p', { style: note }, state.label || 'No input acquired on this device.'),
    h('meter', { min: -90, max: 0, value: level, 'aria-label': `${label} level`, style: { width: '100%' } }),
    h('small', null, `${level} dBFS · ${state.live ? 'live audio track' : 'not live'}`),
    action(`Grant ${label.toLowerCase()}`, grant, { disabled: locked || state.status === 'granted' }))
}
export function RecorderPane({ controller: c, onUnmount }) {
  const [, update] = useState(0)
  const [storage, setStorage] = useState('')
  useEffect(() => { const un = c.subscribe(() => update(n => n + 1)); return () => { un(); c.dispose(); onUnmount?.() } }, [c])
  const s = c.core.state, u = c.ui
  const recording = s.phase === 'recording'
  const busy = ['starting', 'stopping', 'finalizing', 'granting-mic', 'granting-call'].includes(s.phase) || u.saveState === 'saving'
  const blocked = busy || (u.saveState === 'failed')
  return h('div', { style: { padding: 16, display: 'grid', alignContent: 'start', gap: 12, overflow: 'auto', height: '100%', color: 'var(--ui-text-primary)', fontSize: 14 }, 'data-recorder-pane': true },
    h('h2', { style: { margin: 0 } }, 'Call recorder'),
    h('p', { style: note }, 'Capture on this client device. No backend, audio upload, transcription or model calls. Chunks commit locally throughout capture; no total duration cap.'),
    h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 12 } },
      h(InputSource, { controller: c, source: 'mic', label: 'Microphone', grant: () => c.core.grantMic() }),
      h(InputSource, { controller: c, source: 'call', label: 'Call audio', grant: () => c.core.grantCallAudio() })),
    h('small', { style: note }, 'Call audio comes only from the browser’s shared-audio track. Select Share audio in the picker. Screen video sustains sharing only; it is never recorded.'),
    action(s.micOnly ? 'Microphone only selected' : 'Use microphone only', () => c.core.chooseMicOnly(), { disabled: blocked || !s.mic.live || s.call.status === 'granted' || s.phase === 'recording' || s.phase === 'ready' }),
    h('div', { style: row }, action(recording ? 'Stop' : 'Record', () => recording ? c.core.stop() : c.core.start(), { disabled: recording ? false : blocked || s.phase !== 'ready' }),
      h('output', { 'aria-label': 'Recording timer', style: { fontVariantNumeric: 'tabular-nums' } }, fmt(s.elapsedMs)),
      action('Release / reset inputs', () => { c.core.reset(); u.saveState = 'none'; u.pending = null; update(n => n + 1) }, { disabled: busy || recording || u.saveState === 'failed' })),
    h('p', { role: s.error ? 'alert' : 'status', style: note }, s.error || s.message),
    u.saveState !== 'none' && h('p', { role: 'status', 'data-save-state': u.saveState }, u.saveState === 'saving' ? 'Committing final chunks…' : u.saveState === 'recording' ? 'Recording; chunks committing locally.' : u.saveState === 'saved' ? 'Saved in this browser.' : `Partial, not fully saved. ${u.saveError}. Reload to inspect any remaining committed chunks.`),
    h('section', { style: box }, h('strong', null, 'This browser’s saved calls'),
      h('p', { style: note }, 'Playback and fallback downloads assemble one selected track in memory. For long calls use Export track to file where supported, which writes one chunk at a time. Legacy v1 recordings remain untouched in their original database; export with the previous version. IndexedDB belongs to this origin and browser profile, not the connected machine or Hermes profile. It can be evicted or cleared. Download a backup. Calls from localhost:8817 do not move here: export there before switching.'),
      action('Estimate storage', async () => { try { const v = await navigator.storage.estimate(); setStorage(`Origin usage ${v.usage} / quota ${v.quota} bytes`) } catch (e) { setStorage(e.message) } }),
      action('Request persistent storage', async () => { try { setStorage(await navigator.storage.persist() ? 'Persistent storage granted; clearing site data still deletes calls.' : 'Not granted; calls remain evictable. Download a backup.') } catch (e) { setStorage(e.message) } }),
      h('p', { role: 'status' }, storage)),
    u.error && h('p', { role: 'alert' }, u.error),
    !u.ready && h('p', null, 'Opening local library…'),
    u.ready && !u.calls.length && h('p', null, 'No calls saved in this browser yet.'),
    ...u.calls.map(record => h(Card, { key: record.id, record, controller: c })))
}
export default {
  id: 'call-recorder', name: 'Call recorder', defaultEnabled: false,
  description: 'Client-local microphone and shared-call audio, saved in this browser. No upload.',
  register(ctx) {
    let controller = null
    const Pane = () => { const [c] = useState(() => controller ||= createController()); return h(RecorderPane, { controller: c, onUnmount: () => { if (controller === c) controller = null } }) }
    ctx.register({ id: 'call-recorder-pane', area: 'panes', title: 'Call recorder', data: { placement: 'right', width: '440px' }, render: () => h(Pane) })
    ctx.onDispose(() => { controller?.dispose(); controller = null })
    const unload = () => controller?.dispose()
    ctx.addEventListener(window, 'pagehide', unload)
    ctx.addEventListener(window, 'beforeunload', unload)
    ctx.register({ id: 'call-recorder-composer', area: 'composer.actions', render: () => action('Call recorder', () => host.revealPane('call-recorder:call-recorder-pane'), { 'aria-label': 'Open Call recorder pane', variant: 'ghost', size: 'sm' }) })
  }
}
