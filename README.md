# hermes-call-recorder

A Hermes plugin that records both sides of a call — the audio you hear *and* your
mic — then transcribes it live while you talk and properly afterwards.

Built because "record the call so I can review it later" should not mean opening
OBS, hunting for the right PulseAudio source, and losing the half of the
conversation that came out of your headphones.

Records on **Linux (PipeWire)** and **Windows (WASAPI loopback)**. Neither needs a
virtual cable: on Windows the loopback is part of the OS, so there is no driver to
install, no admin rights and no certificate to trust.

## What it looks like

![The composer, live transcript strip and record control](docs/screenshots/composer-and-live-strip.png)

While a call is recording, the last few transcribed lines sit above the composer,
labelled `LIVE` and split by speaker. The `rec` control is the dot in the composer;
the elapsed time lives in the status bar.

![The recorder pane](docs/screenshots/recorder-pane.png)

The pane carries the state (`Recording`, or the reason it cannot record), the
live transcript, the recordings with their durations and sizes, and a transcript
per recording.

![The recorder in context](docs/screenshots/in-context.png)

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

- **Linux:** PipeWire with `pactl` and a `systemd --user` session, plus `ffmpeg`.
- **Windows:** Python 3 and `python -m pip install pyaudiowpatch`, plus `ffmpeg` on
  PATH. No driver, no admin rights, no reboot — and someone logged in, because a
  background session has no audio to capture.
- **Optional, for transcription on either platform:** `faster-whisper` on the target.
  Without it the plugin still records; it just reports transcription as unavailable.

The machine running the Hermes gateway needs an `ssh` client to reach a remote target
(Windows 10+ ships `ssh.exe`). Recording the gateway's own machine assumes a POSIX
shell; point `host` at a machine over SSH on a Windows gateway.

## Install

```bash
hermes plugins install jacebenson/hermes-hackathon-call-recorder
```

Then add `call-recorder` to `plugins.enabled` in `~/.hermes/config.yaml` and
restart the gateway so the dashboard routes and the desktop half load.

## Configure

Four questions, in the order they matter. This is the whole settings page.

**1. Machine that records.** The computer with the microphone and the headset. Leave
it empty and it is chosen for you: the machine where a call is in progress, else the
first machine in your SSH config that has audio devices. Fill it in only to pin one.

**2. Save recordings to.** A folder on that machine. Recording happens where the
audio is, so the mkv and its transcript stay there — the file is already on disk and
survives the connection dropping mid-call.

**3. Transcribe recordings.** On by default. It needs a Python on the recording
machine that can import `faster-whisper`; the recorder looks for one, copies its own
`live.py` and `archive.py` there (into `~/.local/share/call-recorder`), and says so
in the pane when it cannot find a runtime. Off means record-only.

**4. Stop when the call ends.** Three minutes of grace after the call disappears,
then it stops and transcribes. Off means record until you press stop.

Nothing here has to be filled in for a working install: an untouched settings page
records on the right machine, transcribes, and stops on its own. Every field exists
only to pin something specific.

Advanced keys (`python`, `live_script`, `auto_start`, `auto_stop_seconds`,
`watch_seconds`, `whisper_model`) are still honoured through `CALLREC_*` environment
variables or a `settings.json` beside `plugin.yaml`, but they are deliberately not on
the page.

## While recording

The composer button is the whole interface:

- idle shows `rec`;
- recording shows a red dot and a timer that ticks every second;
- if the microphone has been at digital silence for thirty seconds the dot turns
  **amber** and the tooltip says the mic is silent — a muted call and a working one
  look identical otherwise;
- stopping shows `transcribing…` and then `transcript ready`, with the recording
  waiting in the pane.

Live transcription runs during the call when the recording machine has a Whisper
runtime; the pane says `live transcription: on` while it does. Without a runtime
there is no live view — the upload route Hermes exposes is one-shot, not streaming.

## Transcription

One engine: faster-whisper on the recording machine. The recording is cut into
slices and transcribed there, which keeps the audio local, uses the GPU the machine
already has, and gives per-line timing.

There is no cloud fallback on purpose. Reaching into Hermes's internal speech module
would break on any core release — a private import is exactly what the plugin catalog
asks authors not to ship — so when the recording machine has no Whisper runtime the
plugin says `transcription: unavailable` and records only. Install it once:

```bash
uv venv ~/whisper/.venv && ~/whisper/.venv/bin/python -m pip install faster-whisper
```

The plugin finds that interpreter, ships its own transcription scripts to the machine,
and reads back the segment count so the pane can show what actually happened.

## Platform support

| | today |
| --- | --- |
| Machine being recorded | **Linux with PipeWire** (`pactl`, `systemd --user`, `ffmpeg`) or **Windows** (Python 3, `pyaudiowpatch`, `ffmpeg`). Both are proven end to end. |
| Machine running Hermes | Any OS with an `ssh` client. A remote target is reached over SSH; recording the gateway's own machine assumes a POSIX shell. |
| Windows as the recorded machine | No install beyond one pip package, no admin, no certificate. Proven end to end: 25 s of a YouTube video and a voice, system at −34 dB and mic at −31 dB. |
| macOS as the recorded machine | Not supported yet. Needs BlackHole and an avfoundation capture path that has not been tested on real hardware. |
| Transport | SSH only. No HTTP agent, so nothing new listens on the recorded machine and the SSH key is the credential. |

## Windows: nothing to install but a pip package

Every output endpoint on Windows already has a loopback twin. That is what OBS uses,
it is part of the OS, and it needs no virtual cable:

```powershell
python -m pip install pyaudiowpatch
```

That is the whole setup. No driver, no certificate to trust, no reboot.

Two things are worth knowing, because both produce silence rather than an error:

* **Someone has to be logged in.** Windows gives a background service session no audio
  endpoint at all, so a recorder started over SSH records pure silence even though the
  file looks perfect. The plugin therefore starts the capture as a scheduled task with an
  interactive principal, which runs in the logged-in desktop, and refuses to start when
  nobody is logged in. If you launch it by hand, run it from your own desktop.
* **Loopback only delivers audio while sound is playing.** The helper writes silence for
  those gaps so the two tracks stay aligned; without that, every timestamp after the
  first quiet moment would drift.

A virtual cable (VB-CABLE, Voicemeeter) still works if you already have one: it shows up
as an ordinary recording device and the dshow path is used instead. It is no longer
required, and the plugin never asks anyone to trust a driver.

SSH is the *client* side on the Hermes host, which Windows 10+ ships as `ssh.exe`,
so driving a Linux desktop from a Windows gateway is mostly a matter of replacing
the `bash -lc` local runner. An HTTP agent would work too but it means opening a
port and inventing an auth story — the reason it is not the default.

## What it touches (disclosure)

Catalog rule 13 asks a plugin to say what a user would want to know before installing.

* **It reads your SSH config.** `~/.ssh/config` supplies the candidate machines when no
  host is pinned; the plugin reads host aliases only and never touches keys.
* **It runs shell commands over SSH.** `pactl`, `ffmpeg`, `systemctl --user` on Linux; on
  Windows `powershell`, `ffprobe` and a scheduled task. Nothing new listens on the
  recorded machine — SSH is both the transport and the credential.
* **It installs its own helper scripts on the recording machine**, in
  `~/.local/share/call-recorder/` (Linux) or `%USERPROFILE%\.call-recorder\` (Windows).
  They are rewritten from the plugin on every start so a stale copy cannot drift.
* **It starts long-running background work:** an ffmpeg capture as a `systemd --user`
  unit (Linux) or a scheduled task plus a Python process (Windows). Both stop when you
  stop the recording, and the pane shows what is running.
* **Windows needs one pip package**, `pyaudiowpatch`, and a logged-in desktop session.
  No driver, no admin rights, no certificate to trust.
* **Transcription is local.** Whisper runs on the recording machine; no audio leaves it.
  If there is no Whisper there, the plugin records and says transcription is unavailable.
* **No credentials are stored.** Nothing is written to `auth.json`, `.env` files or any
  other key store, and the plugin never refreshes or holds a token.
* **Recordings and transcripts are plain files** on the recording machine, named
  `call-YYYYMMDD-HHMMSS.mkv` with a `system` and a `mic` track, plus
  `<file>.transcript.md` beside it.

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

- Windows and Linux capture. No macOS path yet.
- English transcription (change `language` in the scripts for anything else).
- One recording at a time.
- Voice sessions in the same browser (e.g. a live-voice plugin) also hold the mic
  and play audio, so they look like a call. Set `auto_start: false` if that
  matters more than the convenience.

## License

MIT — see `LICENSE`.
