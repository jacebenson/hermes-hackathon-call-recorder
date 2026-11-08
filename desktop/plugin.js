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

const { Badge, Button, StatusDot, Switch, cn, host, useQuery, useQueryClient } = sdk

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

function ComposerRecordButton({ ctx }) {
  const qc = useQueryClient()
  // The click flips the button immediately; the request catches up. Starting a
  // remote recording takes a second or two, and a button that ignores you for
  // that long reads as broken.
  const [pending, setPending] = useState(null)
  const status = useQuery(statusQuery(ctx))
  const st = status.data || {}
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
        const res = await ctx.rest('/stop', { method: 'POST', body: {} })
        const file = res && res.file
        host.notify({
          kind: res && res.warning ? 'error' : 'info',
          message: res && res.warning ? 'call-recorder: ' + res.warning : 'call-recorder: stopped'
        })
        if (file) {
          host.notify({ kind: 'info', message: 'call-recorder: transcribing…' })
          ctx
            .rest('/transcribe', { method: 'POST', body: { file }, timeoutMs: 3600000 })
            .then(() => {
              host.notify({ kind: 'info', message: 'call-recorder: transcript ready' })
              qc.invalidateQueries({ queryKey: [ID] })
            })
            .catch(() =>
              host.notify({
                kind: 'error',
                message: 'call-recorder: recording saved, transcription failed'
              })
            )
        }
      } else {
        await ctx.rest('/start', { method: 'POST', body: { live: true } })
        host.notify({ kind: 'info', message: 'call-recorder: recording' })
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

function RecorderPane({ ctx }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState('')
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

  const lines = (live.data && live.data.lines) || []
  useEffect(() => {
    if (tailRef.current) tailRef.current.scrollTop = tailRef.current.scrollHeight
  }, [lines.length])

  const call = useCallback(
    async (path, body) => {
      setBusy(true)
      try {
        await ctx.rest(path, { method: 'POST', body: body || {} })
        await qc.invalidateQueries({ queryKey: [ID] })
      } catch (e) {
        host.notify({ kind: 'error', message: 'call-recorder: ' + (e && e.message ? e.message : e) })
      } finally {
        setBusy(false)
      }
    },
    [ctx, qc]
  )

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
      cfg.why
        ? jsx('div', {
            className: 'text-[0.6875rem] leading-snug text-(--ui-text-quaternary)',
            children: cfg.host_mode === 'auto' ? 'auto: ' + cfg.why : cfg.why
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
      jsx('div', { className: 'h-px bg-(--ui-stroke-secondary)' }),
      jsx('div', {
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
                        jsx('span', { className: 'truncate', children: r.name.replace(/^call-|\.mkv$/g, '') }),
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
                            r.transcript
                              ? jsx(TranscriptView, { ctx, path: r.transcript })
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
          : jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: 'none yet' })
      })
    ]
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
