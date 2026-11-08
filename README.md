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
| Audio source machine | Leave empty. See below — it finds the machine with the microphone. |
| Recordings folder | Where the two-track mkv files land, on the recorded machine. |
| Transcription interpreter | Python on the recorded machine that can import `faster_whisper`. Empty disables transcription; recording still works. |
| Live transcription script | Path to `live.py` on the recorded machine; `archive.py` must sit beside it. |
| Start recording automatically | Starts when a call app is both playing audio and capturing the mic. |
| Stop delay (seconds) | How long to keep recording after the call disappears. `0` never stops automatically. |
| Check interval (seconds) | How often the recorder looks for a call. |

### Which machine gets recorded

The machine running Hermes is usually not the machine with the microphone — a
server has no headset. So the recorder works it out instead of asking:

1. A machine named in **Audio source machine** always wins.
2. Otherwise this machine, if it actually has an audio sink and source.
3. Otherwise it walks the concrete `Host` aliases in `~/.ssh/config`, probing each
   for `pactl`, `ffmpeg` and a systemd user session, and takes the first that has
   audio devices.
4. Nothing found: it says so in the pane rather than recording silence.

The pane shows the choice and the reason — *"pro-1 has no audio devices; beast-1
does"* — so there is nothing to guess at. `GET /config` returns the same fields
(`host`, `host_mode`, `why`, `probe`) and `GET /detect` lists every candidate it
found.

Recording happens over SSH when the machine is remote: no port opened on it, no
token to distribute, the SSH key is the credential.


Resolution order, highest first: `CALLREC_*` environment variables, then the saved
settings (`plugins.entries.call-recorder.settings` in `config.yaml`), then an optional
`settings.json` beside `plugin.yaml`, then the schema defaults. So the UI always wins,
and an environment variable can pin a machine-specific value without editing config.


## Platform support

| | today |
| --- | --- |
| Machine being recorded | **Linux with PipeWire.** It needs `pactl`, `ffmpeg` and a `systemd --user` session. |
| Machine running Hermes | Linux. Remote targets are reached over SSH; local execution assumes a POSIX shell. |
| Windows or macOS as the recorded machine | Not supported. That is a different capture path (WASAPI loopback / CoreAudio), not a different transport. |
| Transport | SSH only. No HTTP agent, so nothing new listens on the recorded machine and the SSH key is the credential. |

SSH is the *client* side on the Hermes host, which Windows 10+ ships as `ssh.exe`,
so driving a Linux desktop from a Windows gateway is mostly a matter of replacing
the `bash -lc` local runner. An HTTP agent would work too but it means opening a
port and inventing an auth story — the reason it is not the default.

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
