import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'

// Run the actual plugin with a small React/SDK host at its public contributions.
const notes = [], requests = [], invalidations = []
let values = [], cursor = 0, effects = [], data = {}, response = { ok: true }
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
const sdk = {
  Button: 'Button', Input: 'Input', Badge: 'Badge', StatusDot: 'StatusDot', Switch: 'Switch', cn: (...v) => v.filter(Boolean).join(' '),
  host: { notify: n => notes.push(n), composer: { insertText: async () => true } },
  useQuery: q => ({ data: data[q.queryKey[1]] || {}, dataUpdatedAt: Date.now() }),
  useQueryClient: () => ({ invalidateQueries: v => invalidations.push(v), setQueryData: (key, fn) => { data[key[1]] = fn(data[key[1]]) } })
}
const context = vm.createContext({ console, Date, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {} })
const module = new vm.SourceTextModule(readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8'), { context })
await module.link(async spec => {
  const exports = spec === 'react' ? react : spec === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : sdk
  return new vm.SyntheticModule(Object.keys(exports), function () {
    for (const [k, v] of Object.entries(exports)) this.setExport(k, v)
  }, { context })
})
await module.evaluate()
const contributions = []
const ctx = { register: c => contributions.push(c), storage: { get: () => true, set() {} },
  rest: async (path, options) => { requests.push({ path, options }); return response } }
module.namespace.default.register(ctx)
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
const reset = () => { values = []; notes.length = 0; requests.length = 0; invalidations.length = 0 }

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
tree = render('call-recorder-pane')
let disclosure = nodes(tree).find(n => n.props['aria-label'] === 'Toggle live transcript')
assert.equal(disclosure.props['aria-expanded'], true)
disclosure.props.onClick()
tree = render('call-recorder-pane')
assert.equal(nodes(tree).find(n => n.props['aria-label'] === 'Toggle live transcript').props['aria-expanded'], false)
assert(nodes(tree).some(n => String(n.props.children).includes('current words')))
assert(!nodes(tree).some(n => n.props.ref))
assert(button(tree, 'Stop'))
assert(nodes(tree).some(n => n.props.children === 'recordings'))
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
console.log('Desktop behavior checks passed.')
