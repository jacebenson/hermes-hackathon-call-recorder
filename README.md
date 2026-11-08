# hermes-call-recorder

A Hermes plugin that records both sides of a call — the audio you hear *and* your
mic — then transcribes it live while you talk and properly afterwards.

Built because "record the call so I can review it later" should not mean opening
OBS, hunting for the right PulseAudio source, and losing the half of the
conversation that came out of your headphones.

## What you get

- **A `rec` button in the composer.** Click to start, click to stop. It counts up
  while it records.
- **A status chip** — dim `rec` when idle, red `REC 12:34` while rolling.
- **A pane** with the live transcript, the finished recordings, and their
  transcripts readable in place.
- **Automation.** A call is detected as *a known call app that is both playing
  audio and capturing the mic*. When one appears, recording starts by itself.
  When it disappears, the recorder waits `auto_stop_seconds` (default 3 minutes)
  before stopping — long enough that a dropped window, a screen share or a muted
  minute never truncates anything. Press **Keep recording** to cancel the countdown.

Playing audio alone is deliberately *not* a call: that is a YouTube tab, and this
check exists because a YouTube tab is exactly what got recorded by accident the
first time this was built.

## Requirements

On the machine whose audio you want to record (the *target host*):

- Linux with PipeWire (`pactl`, `systemd --user`)
- `ffmpeg`
- Optional, for transcription: `faster-whisper`, plus `live.py` and `archive.py`
  from the companion scripts. Without them the plugin still records; it just
  cannot transcribe.

The machine running the Hermes gateway needs `ssh` access to the target host when
they are not the same box.

## Install

```bash
hermes plugins install jacebenson/hermes-hackathon-call-recorder
```

Then add `call-recorder` to `plugins.enabled` in `~/.hermes/config.yaml` and
restart the gateway so the dashboard routes and the desktop half load.

## Configure

Settings live where you would look for them: **Settings → Plugins → Call Recorder**,
generated from the `config_schema` in `plugin.yaml`. Nothing to hand-edit.

| field | meaning |
| --- | --- |
| Record this machine | SSH alias of the machine whose audio to record. Empty records the gateway host itself. |
| Recordings folder | Where the two-track mkv files land, on the recorded machine. |
| Transcription interpreter | Python on the recorded machine that can import `faster_whisper`. Empty disables transcription; recording still works. |
| Live transcription script | Path to `live.py` on the recorded machine; `archive.py` must sit beside it. |
| Start recording automatically | Starts when a call app is both playing audio and capturing the mic. |
| Stop delay (seconds) | How long to keep recording after the call disappears. `0` never stops automatically. |
| Check interval (seconds) | How often the recorder looks for a call. |

Resolution order, highest first: `CALLREC_*` environment variables, then the saved
settings (`plugins.entries.call-recorder.settings` in `config.yaml`), then an optional
`settings.json` beside `plugin.yaml`, then the schema defaults. So the UI always wins,
and an environment variable can pin a machine-specific value without editing config.


## How it works

```
composer button / pane / chip        (desktop/plugin.js)
            |
            v
  /api/plugins/call-recorder/*       (dashboard/plugin_api.py)
            |
            v
       recorder.py  --- ssh --->  target host
                                    pactl          detect call + pick sources
                                    systemd-run    ffmpeg -> call-<ts>.mkv
                                    faster-whisper live.txt + transcript.md
```

One ffmpeg process captures two streams into one matroska file: the monitor of
the sink the call app plays into (track `system`) and the default source
(track `mic`). Two tracks, not a mix — that is what makes speaker labelling
possible later without diarisation.

Recording runs as a transient `systemd --user` unit, so it survives the dashboard
restarting, the SSH connection dropping, and the browser tab closing.

## API

| route | purpose |
| --- | --- |
| `GET /config` | settings actually in effect, and whether the target answers |
| `GET /status` | `{recording, live, elapsed, bytes, monitor, mic, pending_stop}` |
| `POST /start` | `{"live": true}` — begin recording |
| `POST /stop` | stop and report the file + duration |
| `POST /keep` | cancel a pending auto-stop |
| `GET /call` | who is playing and who has the mic right now |
| `GET /transcript?lines=60` | tail of the live transcript |
| `GET /recordings` | recent recordings, sizes, durations, transcript presence |
| `GET /transcript/read?path=…` | markdown of a finished transcript |
| `POST /transcribe` | `{"file": "…"}` — run the archive pass |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests cover the parts that actually break: picking the right monitor, telling
a call apart from a browser tab, and the start/delay/stop decisions.

## Limits

- Linux + PipeWire only. No macOS or Windows capture path.
- English transcription (change `language` in the scripts for anything else).
- One recording at a time.
- Voice sessions in the same browser (e.g. a live-voice plugin) also hold the mic
  and play audio, so they look like a call. Set `auto_start: false` if that
  matters more than the convenience.

## License

MIT — see `LICENSE`.
