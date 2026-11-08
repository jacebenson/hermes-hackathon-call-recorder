# hermes-call-recorder

A Hermes plugin that records both sides of a call — the audio you hear *and* your
microphone — as one two-track file, then transcribes it locally with speaker labels.
Records on **Linux (PipeWire)** and **Windows (WASAPI loopback)**; neither needs a
virtual cable, a driver or admin rights.

![The composer with the live transcript strip and the recorder pane](docs/screenshots/composer-and-live-strip.png)

## Install

```bash
hermes plugins install jacebenson/hermes-hackathon-call-recorder
hermes plugins enable call-recorder
```

## What it needs

On the machine being recorded:

| | |
| --- | --- |
| Linux | PipeWire with `pactl` and a `systemd --user` session, plus `ffmpeg` |
| Windows | Python 3, `python -m pip install pyaudiowpatch`, `ffmpeg` on PATH — and someone logged in |
| Transcription (optional) | `faster-whisper` on that same machine; without it the plugin records and says so |

Windows exposes no audio to a background session, so recording has to happen in a
logged-in desktop — the plugin launches it there itself and refuses when nobody is in.
macOS is not supported yet. The Hermes host only needs an `ssh` client.

## Using it

Click `rec` in the composer to start; the status chip shows a red dot and a timer. The
dot turns **amber** when the mic has been silent for thirty seconds — a muted call and
a working one look identical otherwise. Stop, and the pane shows `transcribing…` then
`transcript ready`.

It also starts by itself when a call is detected — *a known call app that is both
playing audio and capturing the mic* — and stops three minutes after that call
disappears, long enough that a screen share or a dropped window never truncates a
recording. Playing audio alone is not a call; that is just a YouTube tab.

Recordings land on the machine that recorded them as `call-YYYYMMDD-HHMMSS.mkv` with a
`system` and a `mic` track, and `<file>.transcript.md` beside it. Two tracks, not a mix
— that is what makes speaker labels possible without diarisation. The pane lists them
with readable dates, opens a transcript, drops one into the composer, or deletes the
pair.

## Settings

Four fields, all optional: the machine to record (chosen for you when empty), where to
save, transcribe on/off, and stop-when-the-call-ends. An untouched settings page
records on the right machine and stops on its own.

## Transcription

Whisper runs on the recording machine, so the audio never leaves it and the GPU already
there does the work. There is no cloud fallback: with no Whisper runtime the plugin
records and reports `transcription: unavailable` instead of half-transcribing.

```bash
uv venv ~/whisper/.venv && ~/whisper/.venv/bin/python -m pip install faster-whisper
```

## What it touches

* **Your SSH config** — `~/.ssh/config` supplies candidate machines when no host is
  pinned. Host aliases only; keys are never read.
* **Shell commands over SSH** — `pactl`, `ffmpeg`, `systemctl --user` on Linux;
  `powershell`, `ffprobe` and a scheduled task on Windows. Nothing new listens on the
  recorded machine; the SSH key is the credential.
* **Helper scripts on the recording machine** — `~/.local/share/call-recorder/` (Linux)
  or `%USERPROFILE%\.call-recorder\` (Windows), rewritten on every start so a stale copy
  cannot drift.
* **Background work** — an ffmpeg capture as a `systemd --user` unit, or a scheduled task
  plus a Python process on Windows. Both stop when you stop the recording.
* **No stored credentials**, no telemetry, no cloud transcription.
* **Plain files** — the mkv and its transcript, on the machine that recorded them.

## Limits

- macOS capture is not implemented.
- English transcription by default.
- One recording at a time.
- A live-voice session in the same browser holds the mic and plays audio, so it looks
  like a call. Turn auto-start off if that matters more than the convenience.

## Development

```bash
python3 -m unittest discover -s tests        # 51 tests
hermes plugins validate . --install-deps
```

## License

MIT — see `LICENSE`.
