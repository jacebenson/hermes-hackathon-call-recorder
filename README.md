# hermes-call-recorder

Records both sides of a call as one two-track file and transcribes it locally with
speaker labels. **Linux (PipeWire)** and **Windows (WASAPI loopback)**; no virtual
cable, no driver, no admin rights.

![The composer with the live transcript strip and the recorder pane](docs/screenshots/composer-and-live-strip.png)

## Install

```bash
hermes plugins install jacebenson/hermes-hackathon-call-recorder
hermes plugins enable call-recorder
```

## What it needs

The Hermes host needs an `ssh` client. On the machine being recorded:

| | |
| --- | --- |
| Linux | PipeWire with `pactl` and a `systemd --user` session, plus `ffmpeg` |
| Windows | Python 3, `python -m pip install pyaudiowpatch`, `ffmpeg` on PATH, and someone logged in |
| Transcription (optional) | `faster-whisper` on that same machine, otherwise recordings stay untranscribed |

Windows gives a background session no audio, so capture runs in the logged-in desktop:
the plugin launches it there itself and refuses when nobody is in. macOS capture is not
implemented.

```bash
# one-time Whisper runtime, Linux
uv venv ~/whisper/.venv && ~/whisper/.venv/bin/python -m pip install faster-whisper
```

## Using it

Click `rec` by the composer. The chip shows a red dot and a timer, and turns **amber**
after thirty seconds of mic silence. Every stop path, including the pane and auto-stop,
queues archive transcription when enabled. The UI reports transcription progress and
shows success only after the archive pass succeeds. Failures appear in a toast.

Auto-start is **off by default**. Turn it on and a call is detected as *a known call app
playing audio and capturing the mic at the same time*; playback alone is not a call. With
auto-stop on, the recording ends three minutes after the call disappears, so a screen
share or a dropped window does not truncate it.

Files stay on the machine that recorded them: `call-YYYYMMDD-HHMMSS.mkv` with a `system`
and a `mic` track, plus `<file>.transcript.md`. Two tracks, not a mix, which is what makes
speaker labels possible without diarisation. The pane lists them by title or date, opens a
transcript, puts one in the composer, or deletes the pair. Expand a recording and choose
`Rename`. Enter saves, Escape cancels, and an empty title restores the date. Titles are
trimmed, limited to 120 characters, and cannot contain control characters. They stay in
`.titles.json` beside the recordings, never in media filenames. Naming uses `python3`
on Linux or `python` on Windows, independent of Whisper. Click the live transcript
header to collapse or expand it. That choice lasts for the desktop session.

Four optional settings cover which machine, where to save, transcribe or not, and stop
when the call ends. An empty machine field picks a machine on a call, else one with audio.

## What it touches

* **Your SSH config** — `~/.ssh/config` supplies candidate host aliases when no host is
  pinned. Keys are used by `ssh` itself, never read by the plugin.
* **Shell commands over SSH** — `pactl`, `ffmpeg`, `systemctl --user` on Linux;
  `powershell`, `ffprobe` and a scheduled task on Windows.
* **Live transcript files**: the pane reads through the plugin API over SSH. The
  recording machine opens no HTTP viewer or listening port.
* **Helper scripts on the recording machine** — `~/.local/share/call-recorder/` (Linux)
  or `%USERPROFILE%\.call-recorder\` (Windows). Windows rewrites them on every start;
  Linux re-checks them when transcription is detected, cached for five minutes.
* **Background work** — an ffmpeg capture as a `systemd --user` unit, or a scheduled task
  and a Python process on Windows. A watcher thread polls for calls when auto-start or
  auto-stop is enabled.
* **No stored credentials**, no telemetry, no cloud transcription. Whisper fetches a model
  if the machine has none.
* **Plain files**: the mkv, its transcript, `.titles.json` and small state files, on the recording
  machine.

## Limits

- macOS capture is not implemented.
- English transcription by default; one recording at a time.
- A live-voice session in the same browser holds the mic and plays audio, so it looks
  like a call. Leave auto-start off if that matters.

## Development

```bash
python3 -m unittest discover -s tests                       # 66 tests
node --experimental-vm-modules --no-warnings tests/desktop.test.mjs
node --check desktop/plugin.js
hermes plugins validate . --install-deps
```

## License

MIT, see [LICENSE](LICENSE).
