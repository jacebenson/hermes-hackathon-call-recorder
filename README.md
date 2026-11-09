# Hermes call recorder

Record the microphone in this browser, or microphone and system audio on a configured computer. Finished recordings and transcripts appear in the Call Recorder pane.

## Quick start

```bash
hermes plugins install jacebenson/hermes-hackathon-call-recorder
hermes plugins enable call-recorder
```

Open Call Recorder, check the audio-source sentence, and press **Record**. Press **Stop** to save. New installs select **This browser**. Existing explicit computer settings stay selected. Switching audio source preserves configured machines, titles and library layout.

## Two audio locations

- **This browser** says "Microphone on this device only". It requests microphone permission only when you press Record. It records one voice mix, not speaker-separated audio, and does not record system audio. Live transcription is unavailable. HTTPS or localhost, microphone permission, and a browser supporting `getUserMedia` and MediaRecorder with WebM, Ogg or MP4 audio are required. The backend needs `ffmpeg` and `ffprobe`. No SSH setup is needed for browser capture.
- **A computer** says "Microphone and system audio on <host>". An empty computer host means the Hermes backend, not necessarily the device displaying the browser. Advanced settings and diagnostics contains the explicit host override, recent machines, connection test and settings link. Remote capture needs existing passwordless SSH from the backend. Linux needs PipeWire, `pactl`, `ffmpeg` and a `systemd --user` session. Windows needs an interactive logged-in desktop, Python, `pyaudiowpatch` and `ffmpeg`. Native macOS computer capture is not implemented. Existing computer live transcription and two-track capture remain available.

Browser display/tab audio capture is not implemented here. Browser support for display audio varies; microphone capture alone is not OS-audio capture. The plugin does not identify which physical device you use from a remote backend's hostname.

## Storage and transcription

Browser audio uploads through Hermes's authenticated plugin API after Stop. Uploads are limited to 32 MiB and two hours, validated and converted to a one-track `.mkv` in `$HERMES_HOME/plugin-data/call-recorder/browser-<id>/`. Permission, device or upload failures show an error. There is no silent device fallback or automatic upload retry. Cancel, closing a recorder contribution, changing backend connection/profile, or disabling the plugin releases the microphone and discards audio that has not been dispatched. Reconnecting the same backend preserves the recording. Once dispatched, an upload cannot be cancelled and continues to its original backend; its eventual receipt or failure is reported even if the view closes or the profile changes. A lost response can leave persistence unconfirmed. Do not close the page before saving finishes.

Computer recordings remain on their capture machine. Remote files and transcript sidecars are copied over existing SSH to the backend library; source files are retained. Local computer files are indexed without moving them. Backend history remains listed when the selected capture machine is offline. Deleting a copied computer recording also deletes its source; an unreachable source prevents deletion. Browser deletion affects only its backend copy.

Expand a recording to rename, delete, read a transcript, insert a transcript into the composer, or choose **Transcribe**. Browser transcription uses backend Whisper if available, labels only a single voice mix, and otherwise reports unavailable while keeping audio saved. Computer transcription uses the existing live/final pass. Install `faster-whisper` on the relevant machine for transcription. Whisper may download its selected model if it is not cached. No cloud transcription provider or browser live ASR is used.

## Privacy and side effects

Obtain consent before recording. Audio and transcripts are sensitive and are stored on the configured machines and backend, not sent to a transcription service. They are not written to application logs. Browser permission is not acquired in the background. Native capture writes helper scripts and recording state, uses Linux user services or Windows interactive scheduled tasks, and may poll call activity when native auto-start/auto-stop is enabled. Auto-start is off by default. Titles live in `.titles.json`, not media filenames. No SSH keys or credentials are read by the plugin; SSH uses your existing configuration.

## Development

```bash
python -m unittest discover -s tests
node --experimental-vm-modules --no-warnings tests/desktop.test.mjs
node tests/browser-capture.test.mjs
node --check desktop/plugin.js
hermes plugins validate . --install-deps
git diff --check
```

MIT. See [LICENSE](LICENSE).
