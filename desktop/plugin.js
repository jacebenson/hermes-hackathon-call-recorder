// call-recorder — record both sides of a call, watch it live, read it after.
//
// Three surface contributions:
//   * a `rec` button left of the composer (start/stop, counts up while live)
//   * a statusbar chip (dim when idle, red REC hh:mm while recording)
//   * a pane: state, live transcript tail, and the finished recordings with
//     their transcripts readable in place
//
// All data comes from the plugin's own dashboard routes (ctx.rest), so there is
// no host or credential baked into this file.
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState, useEffect, useRef, useCallback } from 'react'
import * as sdk from '@hermes/plugin-sdk'

const { Badge, Button, Input, StatusDot, Switch, cn, host, useQuery, useQueryClient } = sdk

const ID = 'call-recorder'

const clock = (s) =>
  String(Math.floor((s || 0) / 60)).padStart(2, '0') + ':' + String((s || 0) % 60).padStart(2, '0')

// The server reports elapsed once per poll; between polls the number is stale, so
// derive it from the local clock. Otherwise the timer jumps 2 to 6 seconds at a time.
const useTicker = (active) => {
  const [, force] = useState(0)
  useEffect(() => {
    if (!active) return undefined
    const id = setInterval(() => force((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [active])
}

const elapsedNow = (status) => {
  const st = (status && status.data) || {}
  if (!st.recording) return 0
  const base = Number(st.elapsed) || 0
  const at = Number(status.dataUpdatedAt) || 0
  if (!at) return base
  return base + Math.max(0, Math.floor((Date.now() - at) / 1000))
}

const mb = (n) => ((n || 0) / 1048576).toFixed(1) + ' MB'

// Recordings are named call-YYYYMMDD-HHMMSS.mkv: sortable, unreadable. Show the date.
const friendlyName = (name) => {
  const m = /^call-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})\.mkv$/.exec(String(name || ''))
  if (!m) return String(name || '')
  // The name is UTC, one clock for every platform. Parse it as UTC and let the
  // browser render it in this user's timezone.
  const when = new Date(Date.UTC(
    Number(m[1]), Number(m[2]) - 1, Number(m[3]),
    Number(m[4]), Number(m[5]), Number(m[6])
  ))
  if (isNaN(when.getTime())) return name
  const now = new Date()
  const time = when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (when.toDateString() === now.toDateString()) return 'Today · ' + time
  const sameYear = when.getFullYear() === now.getFullYear()
  const day = when.toLocaleDateString([], sameYear
    ? { month: 'short', day: 'numeric' }
    : { year: 'numeric', month: 'short', day: 'numeric' })
  return day + ' · ' + time
}

const parseLive = (line) => {
  const m = /^\[(\d\d:\d\d:\d\d)\]\s+(you|them)>\s*(.*)$/.exec(line)
  return m ? { t: m[1], who: m[2], text: m[3] } : { t: '', who: '', text: line }
}

const statusQuery = (ctx) => ({
  queryKey: [ID, 'status'],
  queryFn: () => ctx.rest('/status', { timeoutMs: 15000 }),
  refetchInterval: 2000,
  retry: false
})

const checked = (res) => {
  if (!res || res.ok !== true) throw new Error((res && res.error) || 'request failed')
  return res
}

const announcedJobs = new Map()
const transcriptionPhase = (job) => ['queued', 'running'].includes(job.state)
  ? 'running' : [job.state, job.engine || '', job.both_failed || false].join(':')
const notifyTranscription = (job, force = false) => {
  if (!job) return
  const phase = transcriptionPhase(job)
  if (!force && announcedJobs.get(job.file) === phase) return
  announcedJobs.set(job.file, phase)
  host.notify({
    kind: job.ok === false ? 'error' : 'info',
    message: 'call-recorder: ' + (job.ok === false
      ? (job.both_failed ? 'live pass and Whisper re-pass both failed: ' : '') + (job.error || 'transcription failed')
      : job.state === 'ready' && job.ok === true
        ? 'transcript ready' + (job.engine === 'live' ? ' from live pass' : job.engine === 'gpu' ? ' from Whisper re-pass' : '')
        : 'transcribing…')
  })
}
const useTranscriptionNotices = (st, qc) => {
  useEffect(() => {
    for (const job of st.transcriptions || []) {
      const previous = announcedJobs.get(job.file)
      notifyTranscription(job)
      if (previous !== transcriptionPhase(job) && ['ready', 'failed'].includes(job.state)) {
        qc.invalidateQueries({ queryKey: [ID, 'recordings'] })
      }
    }
  }, [st.transcriptions, qc])
}

function ComposerRecordButton({ ctx }) {
  const qc = useQueryClient()
  // The click flips the button immediately; the request catches up. Starting a
  // remote recording takes a second or two, and a button that ignores you for
  // that long reads as broken.
  const [pending, setPending] = useState(null)
  const status = useQuery(statusQuery(ctx))
  const st = status.data || {}
  useTranscriptionNotices(st, qc)
  const recording = !!st.recording
  const shownRecording = pending === 'start' ? true : pending === 'stop' ? false : recording
  useTicker(shownRecording && !pending)
  const elapsed = elapsedNow(status)

  // A muted microphone looks exactly like a working one. Track how long the level
  // has been at digital silence and say so.
  const silentSince = useRef(null)
  const db = typeof st.mic_db === 'number' ? st.mic_db : null
  if (!shownRecording) silentSince.current = null
  else if (db !== null && db > -55) silentSince.current = null
  else if (db !== null && silentSince.current === null) silentSince.current = Date.now()
  const silentFor = silentSince.current ? Math.floor((Date.now() - silentSince.current) / 1000) : 0
  const micSilent = shownRecording && silentFor >= 30

  // A request that never answers must not leave the button saying '…' forever.
  useEffect(() => {
    if (!pending) return undefined
    const id = setTimeout(() => setPending(null), 8000)
    return () => clearTimeout(id)
  }, [pending])

  const toggle = async () => {
    if (pending) return
    const stopping = recording
    setPending(stopping ? 'stop' : 'start')
    try {
      if (stopping) {
        const res = checked(await ctx.rest('/stop', { method: 'POST', body: {}, timeoutMs: 120000 }))
        host.notify({ kind: res.warning ? 'error' : 'info', message: 'call-recorder: ' + (res.warning || 'stopped') })
        notifyTranscription(res.transcription)
      } else {
        checked(await ctx.rest('/start', { method: 'POST', body: { live: true } }))
        host.notify({ kind: 'info', message: 'call-recorder: recording' })
        // First recording ever: say where things go, while it matters.
        let seen = true
        try {
          seen = Boolean(ctx.storage.get('hintShown.v2'))
        } catch {
          seen = false
        }
        if (!seen) {
          try {
            ctx.storage.set('hintShown.v2', true)
          } catch {
            /* storage unavailable: still worth saying once per session */
          }
          host.notify({
            kind: 'info',
            message:
              'transcript appears above the composer while you talk; the recording and its transcript are saved in the Call Recorder pane'
          })
        }
      }
      qc.invalidateQueries({ queryKey: [ID] })  // no await: never hold the button
    } catch (e) {
      host.notify({ kind: 'error', message: 'call-recorder: ' + (e && e.message ? e.message : e) })
    } finally {
      setPending(null)
    }
  }

  return jsxs('button', {
    type: 'button',
    onClick: toggle,
    title: !shownRecording
      ? 'Record this call (system audio + mic)'
      : micSilent
        ? 'Microphone is silent (' + silentFor + 's) - are you muted? Click to stop.'
        : 'Stop recording (' + clock(elapsed) + ')',
    className: cn(
      // Stacked on purpose: the dot above the label costs half the horizontal room.
      'inline-flex h-8 w-7 flex-col items-center justify-center gap-[2px] rounded-md',
      'text-[0.5625rem] leading-none transition-colors',
      shownRecording
        ? micSilent
          ? 'bg-amber-500/10 text-amber-500'
          : 'bg-red-500/10 text-red-500'
        : 'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
      pending ? 'opacity-70' : ''
    ),
    children: [
      jsx('span', {
        className: cn(
          'block h-1.5 w-1.5 rounded-full',
          micSilent
            ? 'bg-amber-500'
            : shownRecording
              ? 'bg-red-500 animate-pulse'
              : 'bg-current opacity-50'
        )
      }),
      // The timer lives in the status bar; here only the state matters.
      shownRecording ? '' : 'rec'
    ]
  })
}

function RecordChip({ ctx }) {
  const status = useQuery(statusQuery(ctx))
  const st = status.data || {}
  useTicker(!!st.recording)
  const elapsed = elapsedNow(status)
  if (!st.recording)
    return jsxs('div', {
      className: 'flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-quaternary)',
      children: [jsx(StatusDot, { className: 'text-(--ui-text-quaternary)' }), 'rec']
    })
  return jsxs('div', {
    className: 'flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-red-500',
    children: [jsx(StatusDot, { className: 'text-red-500' }), 'REC ', clock(elapsed)]
  })
}

function TranscriptView({ ctx, path }) {
  const doc = useQuery({
    queryKey: [ID, 'transcript', path],
    queryFn: () => ctx.rest('/transcript/read?path=' + encodeURIComponent(path), { timeoutMs: 30000 }),
    retry: false,
    staleTime: 60000
  })
  if (doc.isLoading)
    return jsx('div', { className: 'p-2 text-[0.6875rem] text-(--ui-text-quaternary)', children: 'loading…' })
  if (doc.isError)
    return jsx('div', { className: 'p-2 text-[0.6875rem] text-red-500', children: String(doc.error) })
  const lines = String((doc.data && doc.data.markdown) || '').split('\n').slice(0, 800)
  return jsx('div', {
    className: 'max-h-72 overflow-y-auto p-2 font-mono text-[0.6875rem] leading-relaxed',
    children: lines.map((line, i) => {
      const m = /^\*\*\[(\d+:\d+)\]\s+(you|them):\*\*\s*(.*)$/.exec(line)
      if (!m) return jsx('div', { className: 'text-(--ui-text-quaternary)', children: line }, i)
      return jsxs(
        'div',
        {
          className: 'flex gap-1.5',
          children: [
            jsx('span', { className: 'shrink-0 text-(--ui-text-quaternary)', children: m[1] }),
            jsx('span', {
              className: cn('shrink-0 font-semibold', m[2] === 'you' ? 'text-green-500' : 'text-blue-400'),
              children: m[2]
            }),
            jsx('span', { className: 'min-w-0', children: m[3] })
          ]
        },
        i
      )
    })
  })
}

// Module memory lasts for this desktop session, including pane remounts.
const liveCollapsed = { pane: false, strip: false }
const useLiveDisclosure = (surface) => {
  const [collapsed, setCollapsed] = useState(() => liveCollapsed[surface])
  const toggle = () => setCollapsed((old) => { liveCollapsed[surface] = !old; return !old })
  return [collapsed, toggle]
}
const liveSummary = (lines, on) => lines.length
  ? parseLive(lines[lines.length - 1]).text
  : on ? 'waiting for speech…' : 'no live transcript'

function RecorderPane({ ctx }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState('')
  const [confirmDelete, setConfirmDelete] = useState('')
  const [editingTitle, setEditingTitle] = useState('')
  const [titleDraft, setTitleDraft] = useState('')
  const [diagnosis, setDiagnosis] = useState(null)
  const [collapsed, toggleLive] = useLiveDisclosure('pane')
  const tailRef = useRef(null)

  const status = useQuery(statusQuery(ctx))
  const config = useQuery({
    queryKey: [ID, 'config'],
    queryFn: () => ctx.rest('/config', { timeoutMs: 30000 }),
    staleTime: 60000,
    retry: false
  })
  const st = status.data || {}
  const cfg = config.data || {}
  useTranscriptionNotices(st, qc)
  const recording = !!st.recording
  const liveOn = recording && !!st.live

  const live = useQuery({
    queryKey: [ID, 'live'],
    queryFn: () => ctx.rest('/transcript?lines=40', { timeoutMs: 15000 }),
    refetchInterval: liveOn ? 2500 : false,
    retry: false
  })
  const recs = useQuery({
    queryKey: [ID, 'recordings'],
    queryFn: () => ctx.rest('/recordings', { timeoutMs: 60000 }),
    refetchInterval: 30000,
    retry: false
  })

  const lines = ((live.data && live.data.lines) || []).filter(
    (line) => !String(line).trim().startsWith('===')
  )
  useEffect(() => {
    if (tailRef.current) tailRef.current.scrollTop = tailRef.current.scrollHeight
  }, [lines.length])

  const call = useCallback(
    async (path, body) => {
      setBusy(true)
      try {
        const res = checked(await ctx.rest(path, { method: 'POST', body: body || {}, timeoutMs: path === '/transcribe' ? 3600000 : 120000 }))
        if (path === '/delete') {
          qc.setQueryData([ID, 'recordings'], (old) => old
            ? { ...old, items: (old.items || []).filter((r) => r.file !== body.file) } : old)
          setOpen('')
        }
        if (path === '/title') {
          qc.setQueryData([ID, 'recordings'], (old) => old
            ? { ...old, items: (old.items || []).map((r) => r.file === body.file ? { ...r, title: res.title } : r) } : old)
        }
        if (path === '/stop') notifyTranscription(res.transcription)
        if (path === '/transcribe') {
          notifyTranscription({ ...res, file: body.file, state: 'ready' }, true)
          qc.invalidateQueries({ queryKey: [ID, 'transcript', res.path] })
        }
        qc.invalidateQueries({ queryKey: [ID] })
        return res  // never hold the button on a refetch
      } catch (e) {
        host.notify({ kind: 'error', message: 'call-recorder: ' + (e && e.message ? e.message : e) })
      } finally {
        setBusy(false)
      }
    },
    [ctx, qc]
  )

  // The full transcript belongs in the chat when you want to talk about it; the
  // SDK's composer bus is the supported way to put text in front of the user.
  const putInComposer = async (path) => {
    try {
      const doc = await ctx.rest('/transcript/read?path=' + encodeURIComponent(path), { timeoutMs: 30000 })
      const text = String((doc && doc.markdown) || '').trim()
      if (!text) {
        host.notify({ kind: 'error', message: 'call-recorder: that transcript is empty' })
        return
      }
      const ok = await host.composer.insertText(null, text, { mode: 'block' })
      host.notify({
        kind: ok ? 'info' : 'error',
        message: ok
          ? 'call-recorder: transcript added to the composer'
          : 'call-recorder: no composer to put it in'
      })
    } catch (e) {
      host.notify({ kind: 'error', message: 'call-recorder: ' + (e && e.message ? e.message : e) })
    }
  }

  const items = (recs.data && recs.data.items) || []

  return jsxs('div', {
    className: 'flex h-full flex-col gap-2 p-3 text-sm',
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx(StatusDot, { className: recording ? 'text-red-500' : 'text-(--ui-text-quaternary)' }),
              jsx('span', {
                className: 'font-medium',
                children: recording
                  ? 'Recording'
                  : status.isError
                    ? 'Recorder unreachable'
                    : 'Idle'
              }),
              recording ? jsx(Badge, { children: clock(st.elapsed) }) : null
            ]
          }),
          jsx('span', {
            className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
            children: recording ? mb(st.bytes) : st.host || cfg.host || ''
          })
        ]
      }),
      status.isError
        ? jsx('div', {
            className: 'text-[0.6875rem] leading-snug text-(--ui-text-quaternary)',
            children: 'This window is talking to a Hermes backend that is not answering for this plugin. If the plugin is installed somewhere else, open Settings and check where it is enabled.'
          })
        : null,
      cfg.why
        ? jsx('div', {
            className: 'text-[0.6875rem] leading-snug text-(--ui-text-quaternary)',
            children: cfg.why
          })
        : null,
      jsx('div', {
        className: cn(
          'text-[0.6875rem] leading-snug',
          recording && st.live
            ? 'text-red-500'
            : cfg.transcription
              ? 'text-(--ui-text-quaternary)'
              : 'text-amber-500'
        ),
        children: recording && st.live
          ? 'live transcription: on'
          : cfg.transcription
          ? 'whisper: ' +
            (cfg.transcription_mode === 'auto' ? 'found ' : '') +
            (cfg.transcription_detail || 'available')
          : 'whisper: not found on ' +
            (cfg.host || 'that machine') +
            ' - recording only'
      }),
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Button, {
            size: 'sm',
            disabled: busy || recording,
            onClick: () => call('/start', { live: true }),
            children: 'Start'
          }),
          jsx(Button, {
            size: 'sm',
            variant: 'secondary',
            disabled: busy || !recording,
            onClick: () => call('/stop'),
            children: 'Stop'
          }),
          jsx('span', {
            className: 'ml-auto truncate text-[0.6875rem] text-(--ui-text-quaternary)',
            children: st.mic || (liveOn ? 'live on' : 'live off')
          })
        ]
      }),
      cfg.readiness && cfg.readiness.state && cfg.readiness.state !== 'ready'
        ? jsxs('div', {
            className:
              'flex items-start justify-between gap-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-[0.6875rem] leading-snug text-amber-500',
            children: [
              jsxs('div', {
                className: 'min-w-0',
                children: [
                  jsx('div', { children: cfg.readiness.detail || 'this machine cannot capture audio' }),
                  cfg.readiness.fix ? jsx('div', { className: 'mt-0.5 opacity-80', children: cfg.readiness.fix }) : null
                ]
              })
            ]
          })
        : null,
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Button, {
            size: 'sm', variant: 'secondary', disabled: busy,
            onClick: () => call('/recheck'), children: 'Check again'
          }),
          jsx(Button, {
            size: 'sm', variant: 'secondary', disabled: busy,
            onClick: async () => {
              setBusy(true)
              setDiagnosis(null)
              try {
                // A failed checklist is a result, not a rejected recording action.
                setDiagnosis(await ctx.rest('/diagnose', { method: 'POST', body: {}, timeoutMs: 180000 }))
              } catch (e) {
                setDiagnosis({ steps: [{ id: 'connection', status: 'failed', message: 'Could not run the connection test. ' + (e?.message || e) }] })
              } finally {
                setBusy(false)
              }
            },
            children: 'Test'
          }),
          jsx(Button, {
            size: 'sm', variant: 'secondary', disabled: busy,
            title: 'Where this plugin is installed and enabled',
            // The usual reason the pane cannot reach its backend is that the plugin
            // is not enabled on the backend this window is talking to.
            onClick: () => host.navigate('/capabilities?tab=plugins'),
            children: 'Settings'
          })
        ]
      }),
      diagnosis ? jsxs('div', {
        role: 'status',
        className: 'text-[0.6875rem] leading-snug',
        children: [
          jsx('div', { children: 'Connection test: ' + (diagnosis.target || cfg.host || 'local') }),
          ...(diagnosis.steps || []).slice().sort((a, b) => Number(a.status !== 'failed') - Number(b.status !== 'failed')).map((step) => jsx('div', {
            'data-check': step.id,
            className: step.status === 'failed' ? 'font-semibold text-(--ui-text-primary)' : 'text-(--ui-text-secondary)',
            children: step.status + ': ' + step.message
          }, step.id))
        ]
      }) : null,
      jsx('div', { className: 'h-px bg-(--ui-stroke-secondary)' }),
      jsx('button', {
        type: 'button',
        'aria-label': 'Toggle live transcript',
        'aria-expanded': !collapsed,
        onClick: toggleLive,
        className: 'truncate text-left text-[0.6875rem] text-(--ui-text-quaternary)',
        children: (collapsed ? '▸ Live · ' : '▾ Live') + (collapsed ? liveSummary(lines, liveOn) : '')
      }),
      collapsed ? null : jsx('div', {
        ref: tailRef,
        className:
          'min-h-0 flex-1 overflow-y-auto rounded border border-(--ui-stroke-secondary) p-2 font-mono text-[0.6875rem] leading-relaxed',
        children: lines.length
          ? lines.map((line, i) => {
              const p = parseLive(line)
              return jsxs(
                'div',
                {
                  className: 'flex gap-1.5',
                  children: [
                    jsx('span', { className: 'shrink-0 text-(--ui-text-quaternary)', children: p.t }),
                    jsx('span', {
                      className: cn('shrink-0 font-semibold', p.who === 'you' ? 'text-green-500' : 'text-blue-400'),
                      children: p.who
                    }),
                    jsx('span', { className: 'min-w-0', children: p.text })
                  ]
                },
                i
              )
            })
          : jsx('div', {
              className: 'text-(--ui-text-quaternary)',
              children: recording ? 'waiting for speech…' : 'no live transcript'
            })
      }),
      jsx('div', {
        className: 'text-[0.6875rem] uppercase tracking-wide text-(--ui-text-quaternary)',
        children: 'recordings'
      }),
      jsx('div', {
        className: 'flex flex-col gap-1 overflow-y-auto',
        children: items.length
          ? items.map((r) =>
              jsxs(
                'div',
                {
                  className: 'rounded border border-(--ui-stroke-secondary)',
                  children: [
                    jsxs('button', {
                      type: 'button',
                      onClick: () => setOpen(open === r.file ? '' : r.file),
                      className:
                        'flex w-full items-center justify-between gap-2 px-2 py-1 text-left text-[0.6875rem] hover:bg-(--chrome-action-hover)',
                      children: [
                        jsx('span', { className: 'truncate', children: r.title || friendlyName(r.name) }),
                        jsxs('span', {
                          className: 'shrink-0 text-(--ui-text-quaternary)',
                          children: [
                            r.duration ? clock(Math.round(r.duration)) : '—',
                            ' · ',
                            mb(r.bytes),
                            r.transcript ? ' · txt' : ''
                          ]
                        })
                      ]
                    }),
                    open === r.file
                      ? jsxs('div', {
                          className: 'border-t border-(--ui-stroke-secondary)',
                          children: [
                            jsxs('div', {
                              className: 'flex items-center gap-2 p-2',
                              children: [
                                editingTitle === r.file
                                  ? jsx(Input, {
                                      autoFocus: true,
                                      'aria-label': 'Recording title',
                                      maxLength: 120,
                                      value: titleDraft,
                                      disabled: busy,
                                      placeholder: friendlyName(r.name),
                                      onChange: (e) => setTitleDraft(e.target.value),
                                      onKeyDown: async (e) => {
                                        if (e.key === 'Escape') {
                                          e.preventDefault()
                                          setEditingTitle('')
                                        } else if (e.key === 'Enter' && !busy) {
                                          e.preventDefault()
                                          const res = await call('/title', { file: r.file, title: titleDraft })
                                          if (res) setEditingTitle('')
                                        }
                                      }
                                    })
                                  : jsx(Button, {
                                      size: 'sm', variant: 'ghost', disabled: busy,
                                      onClick: () => { setEditingTitle(r.file); setTitleDraft(r.title || '') },
                                      children: 'Rename'
                                    }),
                                jsx(Button, {
                                  size: 'sm',
                                  variant: 'ghost',
                                  disabled: busy,
                                  title: 'Delete this recording and its transcript',
                                  // two steps: audio is not recoverable
                                  onClick: () => {
                                    if (confirmDelete !== r.file) {
                                      setConfirmDelete(r.file)
                                      setTimeout(
                                        () => setConfirmDelete(''),
                                        5000
                                      )
                                      return
                                    }
                                    setConfirmDelete('')
                                    return call('/delete', { file: r.file })
                                  },
                                  children:
                                    confirmDelete === r.file ? 'confirm' : 'delete'
                                })
                              ]
                            }),
                            r.transcript
                              ? jsxs('div', {
                                  children: [
                                    jsxs('div', {
                                      className:
                                        'flex items-center justify-between gap-2 border-b border-(--ui-stroke-secondary) px-2 py-1',
                                      children: [
                                        jsx('span', {
                                          className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
                                          children: 'transcript'
                                        }),
                                        jsxs('div', {
                                          className: 'flex items-center gap-1',
                                          children: [
                                            jsx(Button, {
                                              size: 'sm',
                                              variant: 'secondary',
                                              disabled: busy,
                                              title: 'Replace this transcript with a cleaner Whisper re-pass',
                                              onClick: () => call('/transcribe', { file: r.file }),
                                              children: 'Transcribe'
                                            }),
                                            jsx(Button, {
                                              size: 'sm',
                                              variant: 'secondary',
                                              disabled: busy,
                                              title: 'Put the whole transcript in the composer',
                                              onClick: () => putInComposer(r.transcript),
                                              children: '→ chat'
                                            })
                                          ]
                                        })
                                      ]
                                    }),
                                    jsx(TranscriptView, { ctx, path: r.transcript })
                                  ]
                                })
                              : jsxs('div', {
                                  className: 'flex items-center justify-between gap-2 p-2',
                                  children: [
                                    jsx('span', {
                                      className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
                                      children: 'no transcript yet'
                                    }),
                                    jsx(Button, {
                                      size: 'sm',
                                      disabled: busy,
                                      onClick: () => call('/transcribe', { file: r.file }),
                                      children: 'Transcribe'
                                    })
                                  ]
                                })
                          ]
                        })
                      : null
                  ]
                },
                r.file
              )
            )
          : jsx('div', {
              className: 'text-[0.6875rem] leading-snug text-(--ui-text-quaternary)',
              children: 'nothing recorded yet - click the rec button next to the composer while you are on a call'
            })
      })
    ]
  })
}


function LiveStrip({ ctx }) {
  const [collapsed, toggleLive] = useLiveDisclosure('strip')
  const status = useQuery(statusQuery(ctx))
  const st = status.data || {}
  const on = !!st.recording && !!st.live
  const live = useQuery({
    queryKey: [ID, 'strip'],
    queryFn: () => ctx.rest('/transcript?lines=12', { timeoutMs: 15000 }),
    refetchInterval: on ? 2500 : false,
    enabled: on,
    retry: false
  })
  if (!on) return null
  const lines = ((live.data && live.data.lines) || [])
    .filter((line) => !String(line).trim().startsWith('==='))
    .slice(-3)
  return jsxs('div', {
    className: cn(
      // Full width so the timestamp sits at the left edge, not inset with the composer.
      'mb-1 w-full rounded-md border-l-2 border-red-500/70',
      'bg-(--card) py-1 pl-1.5 pr-2 font-mono text-[0.6875rem] leading-snug'
    ),
    children: [jsx('button', {
      type: 'button',
      'aria-label': 'Toggle live transcript',
      'aria-expanded': !collapsed,
      onClick: toggleLive,
      className: 'block w-full truncate text-left text-(--ui-text-quaternary)',
      children: (collapsed ? '▸ Live · ' : '▾ Live') + (collapsed ? liveSummary(lines, on) : '')
    }), collapsed ? null : lines.length
      ? lines.map((line, i) => {
          const p = parseLive(line)
          return jsxs(
            'div',
            {
              className: 'flex gap-1.5',
              children: [
                jsx('span', {
                  className: 'w-8 shrink-0 font-sans text-[0.5625rem] uppercase tracking-wide text-red-500',
                  children: i === 0 ? 'live' : ''
                }),
                jsx('span', { className: 'w-14 shrink-0 text-(--ui-text-quaternary)', children: p.t }),
                jsx('span', {
                  className: cn('w-9 shrink-0 font-semibold', p.who === 'you' ? 'text-green-500' : 'text-blue-400'),
                  children: p.who
                }),
                jsx('span', { className: 'min-w-0 text-(--ui-text-secondary)', children: p.text })
              ]
            },
            i
          )
        })
      : jsx('div', { className: 'text-(--ui-text-quaternary)', children: 'listening…' })]
  })
}

export default {
  id: ID,
  name: 'Call Recorder',
  register(ctx) {
    ctx.register({
      id: 'call-recorder-composer',
      area: 'composer.leading',
      render: () => jsx(ComposerRecordButton, { ctx })
    })
    ctx.register({
      id: 'call-recorder-strip',
      area: 'composer.top',
      render: () => jsx(LiveStrip, { ctx })
    })
    ctx.register({
      id: 'call-recorder-chip',
      area: 'statusBar.right',
      order: 120,
      render: () => jsx(RecordChip, { ctx })
    })
    ctx.register({
      id: 'call-recorder-pane',
      area: 'panes',
      title: 'call recorder',
      data: { placement: 'right', width: '360px' },
      render: () => jsx(RecorderPane, { ctx })
    })
  }
}
