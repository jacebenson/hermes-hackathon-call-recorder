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
after thirty seconds of mic silence. Stopping from the composer gives you
`transcribing…` then `transcript ready`.

Auto-start is **off by default**. Turn it on and a call is detected as *a known call app
playing audio and capturing the mic at the same time*; playback alone is not a call. With
auto-stop on, the recording ends three minutes after the call disappears, so a screen
share or a dropped window does not truncate it.

Files stay on the machine that recorded them: `call-YYYYMMDD-HHMMSS.mkv` with a `system`
and a `mic` track, plus `<file>.transcript.md`. Two tracks, not a mix, which is what makes
speaker labels possible without diarisation. The pane lists them by date, opens a
transcript, puts one in the composer, or deletes the pair.

Four optional settings cover which machine, where to save, transcribe or not, and stop
when the call ends. An empty machine field picks a machine on a call, else one with audio.

## What it touches

* **Your SSH config** — `~/.ssh/config` supplies candidate host aliases when no host is
  pinned. Keys are used by `ssh` itself, never read by the plugin.
* **Shell commands over SSH** — `pactl`, `ffmpeg`, `systemctl --user` on Linux;
  `powershell`, `ffprobe` and a scheduled task on Windows.
* **A live transcript viewer** — while live transcription runs, `scripts/live.py` serves
  the transcript on `0.0.0.0:8799` with no authentication, on the recorded machine. The
  pane reads transcripts through the plugin API, not this port.
* **Helper scripts on the recording machine** — `~/.local/share/call-recorder/` (Linux)
  or `%USERPROFILE%\.call-recorder\` (Windows). Windows rewrites them on every start;
  Linux re-checks them when transcription is detected, cached for five minutes.
* **Background work** — an ffmpeg capture as a `systemd --user` unit, or a scheduled task
  and a Python process on Windows. A watcher thread polls for calls when auto-start or
  auto-stop is enabled.
* **No stored credentials**, no telemetry, no cloud transcription. Whisper fetches a model
  if the machine has none.
* **Plain files** — the mkv, its transcript and small state files, on the recording
  machine.

## Limits

- macOS capture is not implemented.
- English transcription by default; one recording at a time.
- A live-voice session in the same browser holds the mic and plays audio, so it looks
  like a call. Leave auto-start off if that matters.

## Development

```bash
python3 -m unittest discover -s tests        # 51 tests
hermes plugins validate . --install-deps
```

## License

MIT, see [LICENSE](LICENSE).
