import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'

// Run the actual plugin with a small React/SDK host at its public contributions.
const notes = [], requests = [], invalidations = [], queries = []
let values = [], cursor = 0, effects = [], data = {}, response = { ok: true }, sourceQuery = null
let sourceRetries = 0
const jsx = (type, props, key) => ({ type, props: props || {}, key })
const react = {
  useState(init) {
    const i = cursor++
    if (!(i in values)) values[i] = typeof init === 'function' ? init() : init
    return [values[i], v => { values[i] = typeof v === 'function' ? v(values[i]) : v }]
  },
  useRef: v => ({ current: v }), useCallback: fn => fn,
  useEffect: fn => { effects.push(fn) }
}
const atom = initial => {
 let value=initial; const listeners=new Set()
 return {get:()=>value,listen:fn=>{listeners.add(fn);return()=>listeners.delete(fn)},
   set:v=>{value=v;for(const fn of listeners)fn(v)}}
}
const identity={connectionId:atom('original'),profile:atom('default'),gateway:atom('open')}
const sdk = {
  Button: 'Button', Input: 'Input', Badge: 'Badge', StatusDot: 'StatusDot', Switch: 'Switch',
  Select: 'Select', SelectTrigger: 'SelectTrigger', SelectValue: 'SelectValue', SelectContent: 'SelectContent', SelectItem: 'SelectItem',
  cn: (...v) => v.filter(Boolean).join(' '),
  host: { state: identity, notify: n => notes.push(n), composer: { insertText: async () => true } },
  useQuery: q => { queries.push(q); if (q.queryKey[1] === 'capture-host' && sourceQuery) return sourceQuery; return { data: q.queryKey[1] in data ? data[q.queryKey[1]] : {}, dataUpdatedAt: Date.now() } },
  useQueryClient: () => ({ invalidateQueries: v => invalidations.push(v), setQueryData: (key, fn) => { data[key[1]] = fn(data[key[1]]) } })
}
const layoutKey = 'call-recorder.layout.v1'
const storage = new Map()
const localStorage = {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value)
}
const context = vm.createContext({ console, Date, localStorage, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {} })
const contributions = [], disposers = []
const ctx = { onDispose: fn => disposers.push(fn), register: c => contributions.push(c), storage: { get: () => true, set() {} },
  rest: async (path, options) => { requests.push({ path, options }); return typeof response === 'function' ? response(path, options) : response } }
const reloadPlugin = async () => {
  for (const dispose of disposers.splice(0)) dispose()
  values = []
  contributions.length = 0
  const module = new vm.SourceTextModule(readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8'), { context })
  await module.link(async spec => {
    const exports = spec === 'react' ? react : spec === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : sdk
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [k, v] of Object.entries(exports)) this.setExport(k, v)
    }, { context })
  })
  await module.evaluate()
  module.namespace.default.register(ctx)
}
await reloadPlugin()
const render = id => {
  cursor = 0; effects = []
  const root = contributions.find(c => c.id === id).render()
  const tree = root.type(root.props)
  for (const effect of effects) effect()
  return tree
}
const nodes = tree => {
  if (!tree || typeof tree !== 'object') return []
  return [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)]
}
const button = (tree, label) => nodes(tree).find(n => n.props.children === label)
const reset = () => { values = []; storage.clear(); notes.length = 0; requests.length = 0; invalidations.length = 0 }
const rowButton = (tree, title) => nodes(tree).find(n => n.type === 'button' && nodes(n).some(c => c.props.children === title))
const liveButton = tree => nodes(tree).find(n => n.props['aria-label'] === 'Toggle live transcript')

// A failed source query must stop loading, retain the backend error and offer retry.
reset()
sourceQuery = { isError: true, error: new Error('404: Not Found'), data: undefined,
  refetch: () => { sourceRetries++ } }
const failedSource = render('call-recorder-pane')
assert(!nodes(failedSource).some(n => n.props.children === 'Loading audio source…'))
assert(nodes(failedSource).some(n => String(n.props.children).includes('404: Not Found')))
await button(failedSource, 'Retry').props.onClick()
assert.equal(sourceRetries, 1)
for (const q of queries.filter(q => q.queryKey[1] === 'capture-host')) {
  requests.length = 0
  await q.queryFn()
  assert.equal(requests[0].options.timeoutMs, 15000)
}
sourceQuery = null

// A saved set can restore several rows, without conflating identical paths on machines.
reset()
data = { status: {}, recordings: { items: [
  { file: '/same.mkv', machine: 'alpha', title: 'Alpha' },
  { file: '/same.mkv', machine: 'beta', title: 'Beta' },
  { file: '/other.mkv', machine: 'alpha', title: 'Other' }
] } }
storage.set(layoutKey, JSON.stringify({ expanded: ['alpha:/same.mkv', 'alpha:/other.mkv'], liveCollapsed: true }))
let restored = render('call-recorder-pane')
assert.equal(rowButton(restored, 'Alpha').props['aria-expanded'], true)
assert.equal(rowButton(restored, 'Beta').props['aria-expanded'], false)
assert.equal(rowButton(restored, 'Other').props['aria-expanded'], true)
assert.equal(nodes(restored).filter(n => n.props.children === 'Rename').length, 2)
assert.equal(liveButton(restored).props['aria-expanded'], false)
rowButton(restored, 'Beta').props.onClick()
restored = render('call-recorder-pane')
assert.deepEqual(JSON.parse(storage.get(layoutKey)), {
  expanded: ['alpha:/same.mkv', 'alpha:/other.mkv', 'beta:/same.mkv'], liveCollapsed: true
})
rowButton(restored, 'Alpha').props.onClick()
restored = render('call-recorder-pane')
assert.deepEqual(JSON.parse(storage.get(layoutKey)), {
  expanded: ['alpha:/other.mkv', 'beta:/same.mkv'], liveCollapsed: true
})
liveButton(restored).props.onClick()
restored = render('call-recorder-pane')
assert.deepEqual(JSON.parse(storage.get(layoutKey)), {
  expanded: ['alpha:/other.mkv', 'beta:/same.mkv'], liveCollapsed: false
})
await reloadPlugin() // New module as well as new React state, not just a remount.
restored = render('call-recorder-pane')
assert.equal(rowButton(restored, 'Alpha').props['aria-expanded'], false)
assert.equal(rowButton(restored, 'Beta').props['aria-expanded'], true)
assert.equal(rowButton(restored, 'Other').props['aria-expanded'], true)
assert.equal(liveButton(restored).props['aria-expanded'], true)
// Switching capture machine keeps all remembered rows, including absent ones.
const savedLayout = storage.get(layoutKey)
response = { ok: true, capture_mode: 'computer', host: '', host_label: 'local' }
await nodes(restored).find(n => n.type === 'Select').props.onValueChange('host:')
restored = render('call-recorder-pane')
assert.equal(storage.get(layoutKey), savedLayout)
assert.equal(rowButton(restored, 'Beta').props['aria-expanded'], true)
const allItems = data.recordings.items
data.recordings.items = [allItems[2]]
render('call-recorder-pane')
assert.equal(storage.get(layoutKey), savedLayout)
data.recordings.items = allItems
// Failed deletion preserves disclosure; successful deletion removes only its key.
const betaContainer = () => nodes(render('call-recorder-pane')).find(n => n.key === 'beta:/same.mkv')
response = { ok: false, error: 'denied' }
button(betaContainer(), 'delete').props.onClick()
await button(betaContainer(), 'confirm').props.onClick()
render('call-recorder-pane')
assert.equal(storage.get(layoutKey), savedLayout)
response = { ok: true }
button(betaContainer(), 'delete').props.onClick()
await button(betaContainer(), 'confirm').props.onClick()
render('call-recorder-pane')
assert.deepEqual(JSON.parse(storage.get(layoutKey)), { expanded: ['alpha:/other.mkv'], liveCollapsed: false })
await reloadPlugin()
assert.equal(rowButton(render('call-recorder-pane'), 'Other').props['aria-expanded'], true)
// Missing, corrupt and obsolete shapes fail closed rather than breaking render.
for (const saved of [null, '{broken', 'null', '[]', '{"open":"alpha:/other.mkv"}',
  '{"expanded":[42],"liveCollapsed":false}', '{"expanded":[],"liveCollapsed":"false"}']) {
  reset()
  if (saved !== null) storage.set(layoutKey, saved)
  await reloadPlugin()
  const fallback = render('call-recorder-pane')
  assert.equal(liveButton(fallback).props['aria-expanded'], false)
  assert(!nodes(fallback).some(n => n.props.children === 'Rename'))
}
reset()
const getItem = localStorage.getItem, setItem = localStorage.setItem
localStorage.getItem = () => { throw new Error('storage denied') }
localStorage.setItem = () => { throw new Error('quota exceeded') }
await reloadPlugin()
restored = render('call-recorder-pane')
assert.equal(liveButton(restored).props['aria-expanded'], false)
rowButton(restored, 'Other').props.onClick()
assert.equal(rowButton(render('call-recorder-pane'), 'Other').props['aria-expanded'], true)
localStorage.getItem = getItem
localStorage.setItem = setItem

reset(); data = { status: { recording: true } }; response = { ok: false, error: 'stop denied', file: 'x' }
await render('call-recorder-composer').props.onClick()
assert(notes.some(n => n.kind === 'error' && n.message.includes('stop denied')))
assert(!requests.some(r => r.path === '/transcribe'))
reset(); data = { status: { recording: false, transcriptions: [{ file: 'x', state: 'failed', ok: false, error: 'Whisper failed' }] } }
render('call-recorder-composer')
assert(notes.some(n => n.kind === 'error' && n.message.includes('Whisper failed')))
reset(); data = { status: {}, recordings: { items: [{ file: 'f', name: 'call-20260101-120000.mkv', title: 'Planning' }] } }
let tree = render('call-recorder-pane')
assert(nodes(tree).some(n => n.props.children === 'Planning'))
await nodes(tree).find(n => n.type === 'button' && nodes(n).some(child => child.props.children === 'Planning')).props.onClick()
tree = render('call-recorder-pane')
button(tree, 'Rename').props.onClick()
tree = render('call-recorder-pane')
let input = nodes(tree).find(n => n.props['aria-label'] === 'Recording title')
assert.equal(input.props.value, 'Planning')
input.props.onChange({ target: { value: 'New name' } })
tree = render('call-recorder-pane')
input = nodes(tree).find(n => n.props['aria-label'] === 'Recording title')
input.props.onKeyDown({ key: 'Escape', preventDefault() {} })
assert(!nodes(render('call-recorder-pane')).some(n => n.props['aria-label'] === 'Recording title'))
assert.equal(requests.length, 0)
button(render('call-recorder-pane'), 'Rename').props.onClick()
input = nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'Recording title')
input.props.onChange({ target: { value: 'Saved name' } })
input = nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'Recording title')
response = { ok: true, title: 'Saved name' }
await input.props.onKeyDown({ key: 'Enter', preventDefault() {} })
assert.equal(requests[0].path, '/title')
assert.equal(requests[0].options.body.title, 'Saved name')
reset(); data = { status: { recording: true, live: true }, live: { lines: ['[12:00:00] you> current words'] }, recordings: { items: [] } }
storage.set(layoutKey, JSON.stringify({ expanded: [], liveCollapsed: false }))
tree = render('call-recorder-pane')
let disclosure = nodes(tree).find(n => n.props['aria-label'] === 'Toggle live transcript')
assert.equal(disclosure.props['aria-expanded'], true)
disclosure.props.onClick()
tree = render('call-recorder-pane')
assert.equal(nodes(tree).find(n => n.props['aria-label'] === 'Toggle live transcript').props['aria-expanded'], false)
assert(nodes(tree).some(n => String(n.props.children).includes('current words')))
assert(!nodes(tree).some(n => n.props.ref))
assert(button(tree, 'Stop'))
assert(nodes(tree).some(n => n.props.children === 'Recordings'))
values = [] // Remount within the same module session.
assert.equal(nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'Toggle live transcript').props['aria-expanded'], false)
reset(); data = { status: {}, recordings: { items: [{ file: 'delete-me', name: 'call-20260101-120000.mkv', title: 'Delete target' }] } }
tree = render('call-recorder-pane')
nodes(tree).find(n => n.type === 'button' && nodes(n).some(c => c.props.children === 'Delete target')).props.onClick()
button(render('call-recorder-pane'), 'delete').props.onClick()
assert.equal(requests.length, 0)
response = { ok: false, error: 'Permission denied.' }
await button(render('call-recorder-pane'), 'confirm').props.onClick()
assert(notes.some(n => n.kind === 'error' && n.message.includes('Permission denied.')))
assert(nodes(render('call-recorder-pane')).some(n => n.props.children === 'Delete target'))
assert.equal(invalidations.length, 0)
response = { ok: true }
button(render('call-recorder-pane'), 'delete').props.onClick()
await button(render('call-recorder-pane'), 'confirm').props.onClick()
assert(!nodes(render('call-recorder-pane')).some(n => n.props.children === 'Delete target'))
reset(); data = { status: { recording: true }, recordings: {} }; response = { ok: true, file: 'archive-2', transcription: { file: 'archive-2', state: 'queued', ok: null } }
await render('call-recorder-composer').props.onClick()
assert(notes.some(n => n.message.includes('transcribing')))
assert(!notes.some(n => n.message.includes('transcript ready')))
assert(!requests.some(r => r.path === '/transcribe'))
notes.length = 0
data.status = { transcriptions: [{ file: 'archive-2', state: 'ready', ok: true }] }
render('call-recorder-composer')
assert(notes.some(n => n.message.includes('transcript ready')))
notes.length = 0
render('call-recorder-composer')
assert.equal(notes.length, 0)
reset(); data = { status: { recording: true, live: true }, strip: { lines: ['[12:00:00] them> strip words'] } }
tree = render('call-recorder-strip')
nodes(tree).find(n => n.props['aria-label'] === 'Toggle live transcript').props.onClick()
values = []
tree = render('call-recorder-strip')
assert.equal(nodes(tree).find(n => n.props['aria-label'] === 'Toggle live transcript').props['aria-expanded'], false)
assert(nodes(tree).some(n => String(n.props.children).includes('strip words')))
assert(!nodes(tree).some(n => n.props.children === 'them'))
for (const [engine, wording] of [['live', 'live pass'], ['gpu', 'Whisper re-pass']]) {
  reset(); data = { status: { transcriptions: [{ file: 'engine-' + engine, state: 'ready', ok: true, engine }] } }
  render('call-recorder-pane')
  assert(notes.some(n => n.message.includes('transcript ready from ' + wording)))
}
reset(); data = { status: { transcriptions: [{ file: 'both-failed', state: 'failed', ok: false, both_failed: true, error: 'disk full' }] } }
render('call-recorder-pane')
assert(notes.some(n => n.kind === 'error' && n.message.includes('live pass and Whisper re-pass both failed') && n.message.includes('disk full')))
assert(!notes.some(n => n.message.includes('transcript ready')))
reset(); data = { status: { transcriptions: [{ file: 'same-file', state: 'ready', ok: true, engine: 'live' }] } }
render('call-recorder-pane')
notes.length = 0
data.status.transcriptions[0].engine = 'gpu'
render('call-recorder-pane')
assert(notes.some(n => n.message.includes('Whisper re-pass')))
reset(); data = { status: {}, recordings: { items: [{ file: 'quality', name: 'call-20260101-120000.mkv', title: 'Quality target', transcript: 'quality.transcript.md' }] } }
tree = render('call-recorder-pane')
nodes(tree).find(n => n.type === 'button' && nodes(n).some(c => c.props.children === 'Quality target')).props.onClick()
response = { ok: true, engine: 'gpu', segments: 5, path: 'quality.transcript.md' }
await button(render('call-recorder-pane'), 'Transcribe').props.onClick()
assert.equal(requests[0].path, '/transcribe')
assert.equal(requests[0].options.body.file, 'quality')
assert(notes.some(n => n.message.includes('transcript ready from Whisper re-pass')))
reset(); data = { status: {}, recordings: {} }
response = { ok: false, target: 'beast-1', steps: [
  { id: 'ssh', status: 'ok', message: 'SSH reaches beast-1.' },
  { id: 'audio', status: 'failed', message: 'Microphone missing.' }
] }
await button(render('call-recorder-pane'), 'Test').props.onClick()
assert.equal(requests[0].path, '/diagnose')
tree = render('call-recorder-pane')
const checklist = nodes(tree).filter(n => n.props['data-check'])
assert.equal(checklist[0].props['data-check'], 'audio')
assert.equal(checklist[1].props['data-check'], 'ssh')
assert(nodes(tree).some(n => n.props.children === 'failed: Microphone missing.'))
assert.equal(notes.length, 0)
assert(button(tree, 'Check again'))
for (const host of ['local (hermes-host)', 'audio-laptop']) {
  reset(); data = { status: { host: 'local' }, 'capture-host': { host: host === 'audio-laptop' ? host : '', backend_host: 'hermes-host', capture_mode: 'computer' }, config: { host: host === 'audio-laptop' ? host : 'local', host_label: host, transcription: true, transcription_detail: 'tiny.en on cpu' } }
  tree = render('call-recorder-pane')
  assert(nodes(tree).some(n => n.props.children === 'Microphone and system audio, saved on ' + (host === 'audio-laptop' ? host : 'hermes-host') + '.'))
  assert(!nodes(tree).some(n => n.props.children === 'local'))
  assert(nodes(tree).some(n => String(n.props.children).includes('tiny.en on cpu')))
}
// Backend library rows retain their own machine even when capture settings change.
for (const stored of [true, false]) {
  reset()
  const file = stored ? '/library/original/call-20260101-120000.mkv' : '/calls/call-20260101-120000.mkv'
  const transcript = file.replace('.mkv', '.transcript.md')
  data = { status: {}, config: { host_label: 'new-capture-host' }, recordings: { items: [{
    file, transcript, name: 'call-20260101-120000.mkv', title: 'Library item',
    machine: 'original-capture-host', stored, upload_error: stored ? '' : 'Copy failed: disk full'
  }] } }
  tree = render('call-recorder-pane')
  assert(nodes(tree).some(n => n.props.children === 'Recordings'))
  const row = nodes(tree).find(n => n.type === 'button' && nodes(n).some(c => c.props.children === 'Library item'))
  const rowText = nodes(row).flatMap(n => [n.props.children].flat(Infinity)).filter(v => typeof v === 'string').join('')
  assert(rowText.includes('original-capture-host'))
  assert(nodes(row).some(n => n.props.children === (stored ? 'remote on original-capture-host' : 'local on original-capture-host')))
  assert(!rowText.includes('new-capture-host'))
  if (!stored) {
    assert(!rowText.includes('remote on original-capture-host'))
    assert(nodes(tree).some(n => n.props.children === 'Copy failed: disk full'))
  }
  row.props.onClick()
  tree = render('call-recorder-pane')
  const transcriptView = nodes(tree).find(n => typeof n.type === 'function' && n.props.path === transcript)
  assert.equal(transcriptView.props.machine, 'original-capture-host')
  response = { ok: true, markdown: '# full transcript' }
  await button(tree, '→ chat').props.onClick()
  assert.equal(requests[0].path, '/transcript/read?path=' + encodeURIComponent(transcript) + '&machine=original-capture-host')
  requests.length = 0
  response = { ok: true }
  await button(tree, 'Transcribe').props.onClick()
  assert.equal(requests[0].options.body.file, file)
  assert.equal(requests[0].options.body.machine, 'original-capture-host')
  requests.length = 0
  button(render('call-recorder-pane'), 'Rename').props.onClick()
  let titleInput = nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'Recording title')
  titleInput.props.onChange({ target: { value: 'Renamed stored item' } })
  titleInput = nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'Recording title')
  response = { ok: true, title: 'Renamed stored item' }
  await titleInput.props.onKeyDown({ key: 'Enter', preventDefault() {} })
  assert.equal(requests[0].options.body.machine, 'original-capture-host')
  assert.equal(requests[0].options.body.file, file)
  requests.length = 0
  button(render('call-recorder-pane'), 'delete').props.onClick()
  await button(render('call-recorder-pane'), 'confirm').props.onClick()
  assert.equal(requests[0].options.body.file, file)
  assert.equal(requests[0].options.body.machine, 'original-capture-host')
}
reset()
data = { status: {}, recordings: { items: ['backend', 'remote'].map(machine => ({
  file: '/same/call-20260101-120000.mkv', name: 'call-20260101-120000.mkv', title: machine + ' item', machine,
  stored: machine === 'backend'
})) } }
tree = render('call-recorder-pane')
nodes(tree).find(n => n.type === 'button' && nodes(n).some(c => c.props.children === 'remote item')).props.onClick()
tree = render('call-recorder-pane')
assert.equal(nodes(tree).filter(n => n.props.children === 'Rename').length, 1)
button(tree, 'delete').props.onClick()
response = { ok: true }
await button(render('call-recorder-pane'), 'confirm').props.onClick()
assert.deepEqual(data.recordings.items.map(r => r.machine), ['backend'])
// One themed capture target combines microphone and machine selection.
reset()
await reloadPlugin()
const sourceSelect = () => nodes(render('call-recorder-pane')).find(n => n.type === 'Select')
const optionPairs = () => nodes(render('call-recorder-pane')).filter(n => n.type === 'SelectItem').map(n => [n.props.value, n.props.children])
data = {status: {}, 'capture-host': {capture_mode: 'browser', host: 'framework', backend_host: 'hermes-host', recent_hosts: ['hermes-host', 'framework', 'audio-laptop', 'HERMES-HOST']}, config: {}, recordings: {items: []}}
tree = render('call-recorder-pane')
assert.equal(nodes(tree).filter(n => n.props['aria-label'] === 'Audio source').length, 1)
assert(!nodes(tree).some(n => n.props['aria-label'] === 'Capture machine'))
assert.equal(nodes(tree).filter(n => n.type === 'Select').length, 1)
const sourceTrigger = nodes(tree).find(n => n.props['aria-label'] === 'Audio source')
assert.equal(sourceTrigger.type, 'SelectTrigger')
assert(nodes(sourceTrigger).some(n => n.type === 'SelectValue'))
assert(!nodes(tree).some(n => n.type === 'select'))
assert.deepEqual(optionPairs(), [
  ['browser', 'This device’s microphone'],
  ['host:', 'System audio + mic on hermes-host (this backend)'],
  ['host:framework', 'System audio + mic on framework'],
  ['host:audio-laptop', 'System audio + mic on audio-laptop']
])
assert.equal(sourceSelect().props.value, 'browser')
assert.equal(sourceSelect().props.disabled, false)
assert(nodes(tree).some(n => n.props['data-capture-source'] && n.props.children === 'One mixed microphone recording that uploads to the Hermes backend when stopped. No system audio. No live transcription.'))
for (const mode of ['computer', 'browser']) {
  requests.length = 0
  response = { ...data['capture-host'], ok: true, capture_mode: mode, host: 'audio-laptop' }
  const write = sourceSelect().props.onValueChange(mode === 'browser' ? 'browser' : 'host:audio-laptop')
  assert.equal(sourceSelect().props.disabled, true, 'pending setting write locks the source')
  await write
  assert.deepEqual(JSON.parse(JSON.stringify(requests.map(r => [r.path, r.options.method, r.options.body]))), mode === 'browser'
    ? [['/capture-mode', 'POST', {capture_mode: 'browser'}]]
    : [['/capture-host', 'POST', {host: 'audio-laptop'}]])
  assert.equal(sourceSelect().props.value, mode === 'browser' ? 'browser' : 'host:audio-laptop')
  assert.equal(sourceSelect().props.disabled, false)
}
// Only a confirmed host response still outside computer mode needs the mode call.
requests.length = 0
response = path => {
  return {...data['capture-host'], ok: true, host: 'framework', capture_mode: path === '/capture-host' ? 'browser' : 'computer'}
}
await sourceSelect().props.onValueChange('host:framework')
assert.deepEqual(JSON.parse(JSON.stringify(requests.map(r => [r.path, r.options.body]))), [
  ['/capture-host', {host: 'framework'}], ['/capture-mode', {capture_mode: 'computer'}]
])
assert.equal(sourceSelect().props.value, 'host:framework')
assert(nodes(render('call-recorder-pane')).some(n => n.props['data-capture-source'] && n.props.children === 'Microphone and system audio, saved on framework.'))
assert(invalidations.some(v => v.queryKey[1] === 'recordings' && v.refetchType === 'all'))
response = {...data['capture-host'], ok: true, capture_mode: 'computer', host: ''}
await sourceSelect().props.onValueChange('host:')
assert.equal(requests.at(-1).options.body.host, '')
assert.equal(sourceSelect().props.value, 'host:')
assert(nodes(render('call-recorder-pane')).some(n => n.props['data-capture-source'] && n.props.children === 'Microphone and system audio, saved on hermes-host.'))
// Free-text machine entry remains in advanced settings, without another picker.
let hostInput = nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'New capture machine')
hostInput.props.onChange({target: {value: 'new-host'}})
response = {ok: false, error: 'disk full'}
invalidations.length = 0
await nodes(render('call-recorder-pane')).find(n => n.type === 'form').props.onSubmit({preventDefault() {}})
assert.equal(requests.at(-1).options.body.host, 'new-host')
assert.equal(sourceSelect().props.value, 'host:')
assert(notes.some(n => n.message.includes('disk full')))
assert.equal(invalidations.length, 0)
response = {...data['capture-host'], ok: true, host: 'new-host', recent_hosts: ['new-host']}
await nodes(render('call-recorder-pane')).find(n => n.type === 'form').props.onSubmit({preventDefault() {}})
assert.equal(sourceSelect().props.value, 'host:new-host')
assert.equal(nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'New capture machine').props.value, '')
// Missing backend identity has an explicit fallback and never invents a host.
data['capture-host'] = {capture_mode: 'browser'}
assert.deepEqual(optionPairs(), [['browser', 'This device’s microphone'], ['host:', 'System audio + mic on the backend computer (this backend)']])
data.status.recording = true
assert.equal(sourceSelect().props.disabled, true, 'computer recording locks the source')
assert.equal(nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'Audio source').props.title, 'Stop recording before switching machines')
// The surviving free-text input cannot bypass the recording lock.
const lockedHost = {capture_mode: 'computer', host: 'framework', backend_host: 'hermes-host'}
const previousSource = data['capture-host']
data['capture-host'] = lockedHost
const lockedInput = nodes(render('call-recorder-pane')).find(n => n.props['aria-label'] === 'New capture machine')
assert.equal(lockedInput.props.disabled, true)
lockedInput.props.onChange({target: {value: 'forbidden'}})
const beforeLockedSubmit = requests.length
await nodes(render('call-recorder-pane')).find(n => n.type === 'form').props.onSubmit({preventDefault() {}})
assert.equal(requests.length, beforeLockedSubmit)
data['capture-host'] = previousSource
data.status.recording = false
const confirmedSource = data['capture-host']
data['capture-host'] = undefined
assert.equal(sourceSelect().props.disabled, true, 'unloaded source locks the control')
data['capture-host'] = confirmedSource
const paneCapture = contributions.find(c => c.id === 'call-recorder-pane').render().props.ctx.browserCapture
const originalGet = paneCapture.get, originalActive = paneCapture.active
for (const phase of ['pending', 'recording', 'stopping', 'uploading']) {
  paneCapture.get = () => ({ phase, started: Date.now() })
  paneCapture.active = () => true
  assert.equal(sourceSelect().props.disabled, true, 'browser ' + phase + ' locks the source')
}
paneCapture.get = originalGet
paneCapture.active = originalActive
response = { ok: false, error: 'source save denied' }
requests.length = 0
await sourceSelect().props.onValueChange('host:framework')
assert.deepEqual(requests.map(r => r.path), ['/capture-host'], 'failed host selection must not change mode')
assert.equal(sourceSelect().props.value, 'browser', 'failed writes preserve the confirmed selection')
assert(notes.some(n => n.kind === 'error' && n.message.includes('source save denied')))
tree = render('call-recorder-pane')
assert(!nodes(tree).some(n => n.props['aria-label'] === 'Capture machine'))
assert(!button(tree, 'Test'))
assert.equal(queries.filter(q=>q.queryKey[1]==='live').at(-1).enabled,false, 'browser mode must not fetch native live transcript')
assert(nodes(tree).some(n => String(n.props.children).includes('One mixed microphone recording')))
await button(tree, 'Record').props.onClick()
tree = render('call-recorder-pane')
assert(nodes(tree).some(n => n.props.role === 'alert' && n.props.children.includes('HTTPS')))
assert(!requests.some(r => r.path === '/start'), 'browser failures never start computer capture')
// Composer-only errors must be observable without opening the pane.
reset(); await reloadPlugin()
data = {'capture-host':{capture_mode:'browser'},status:{}}
await render('call-recorder-composer').props.onClick()
assert(notes.some(n=>n.kind==='error' && n.message.includes('HTTPS')), 'composer failure must toast')
const errorNotices=notes.length
render('call-recorder-composer'); render('call-recorder-composer')
assert.equal(notes.length,errorNotices, 'rerenders must not repeat error toasts')
// Socket reconnect does not change backend identity or discard buffered audio.
let trackStops=0
context.isSecureContext=true
context.Blob=Blob
context.navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){trackStops++},addEventListener(){}}]})}}
context.MediaRecorder=class {
 static isTypeSupported(){return true}
 constructor(){this.state='inactive';this.mimeType='audio/webm'}
 start(){this.state='recording'}
 stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['tone'])});this.onstop?.()}
}
reset(); await reloadPlugin()
await render('call-recorder-composer').props.onClick()
identity.gateway.set('closed'); identity.gateway.set('open'); identity.connectionId.set('original')
assert.equal(trackStops,0,'same connection reconnect must retain microphone and chunks')
identity.profile.set('other')
assert.equal(trackStops,1,'real profile change must release microphone')
// Permission denial and upload failure remain visible with only composer mounted.
for (const failure of ['permission', 'upload']) {
 reset(); await reloadPlugin()
 if(failure==='permission') context.navigator.mediaDevices.getUserMedia=async()=>{throw {name:'NotAllowedError'}}
 else context.navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop(){trackStops++},addEventListener(){}}]})
 let complete
 const oldRest=ctx.rest
 if(failure==='upload') {
   ctx.rest=async()=>new Promise(r=>{complete=r})
   await reloadPlugin()
 }
 const start=render('call-recorder-composer').props.onClick()
 if(failure==='permission') {
   await start
   assert(notes.some(n=>n.kind==='error' && n.message.includes('permission denied')))
 } else {
   // Pending is visible before the microphone promise settles.
   assert(notes.some(n=>n.message.includes('Waiting for microphone permission')))
   await start
   const stop=render('call-recorder-composer').props.onClick()
   await new Promise(r=>setTimeout(r,0))
   const uploading=render('call-recorder-composer')
   assert.equal(uploading.props.disabled,true)
   assert(uploading.props.title.includes('Saving microphone'))
   complete({ok:false,error:'test upload failure'}); await stop
   assert(notes.some(n=>n.kind==='error' && n.message.includes('test upload failure')))
 }
 const count=notes.length
 render('call-recorder-composer'); render('call-recorder-composer')
 assert.equal(notes.length,count)
 ctx.rest=oldRest
}
// Deferred outcomes survive actual registered source/profile/dispose callbacks.
for (const outcome of ['success','failure']) {
 reset()
 const original=ctx.rest; let complete, fail, calls=0
 ctx.rest=async()=>{calls++;return new Promise((r,j)=>{complete=r;fail=j})}
 await reloadPlugin()
 const contribution=contributions.find(c=>c.id==='call-recorder-composer').render()
 const capture=contribution.props.ctx.browserCapture
 await capture.start(); const done=capture.stop()
 await new Promise(r=>setTimeout(r,0))
 identity.connectionId.set('backend-'+outcome); identity.profile.set('profile-'+outcome)
 for(const dispose of disposers.splice(0)) dispose()
 assert.equal(capture.get().phase,'uploading')
 assert.equal(notes.filter(n=>n.message.includes('Upload continues to the original backend')).length,1)
 await capture.start();assert.equal(calls,1)
 if(outcome==='success')complete({ok:true,file:'original-backend-receipt.mkv'})
 else fail(Error('original backend offline'))
 await done
 assert.equal(capture.get().phase,outcome==='success'?'saved':'error')
 if(outcome==='success')assert.equal(capture.get().result.file,'original-backend-receipt.mkv')
 else assert(capture.get().error.includes('original backend offline'))
 assert(notes.some(n=>outcome==='success'?n.message.includes('original-backend-receipt.mkv'):n.kind==='error'&&n.message.includes('original backend offline')))
 ctx.rest=original
}
console.log('Desktop behavior checks passed.')
