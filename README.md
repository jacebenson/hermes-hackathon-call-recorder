# Hermes Call recorder — client-local v2

A native Hermes SDK pane for separate **Microphone** and **Call audio** recordings.
Capture, playback and the saved-call library run on the **client device**. There is
no recorder backend request, automatic upload, transcription or model call.

## Install

From this checkout, copy the complete, already-built Desktop artifact:

```sh
sh scripts/install-desktop.sh /explicit/path/to/your/hermes-home
```

This copies only `desktop/plugin.js` to
`desktop-plugins/call-recorder/plugin.js`. No Python backend, package installation,
machine/source settings or helper scripts are required. Enable **Call recorder**
in Hermes's plugin settings (the plugin is opt-in). Do not install both a disk copy
and a unified-package copy with the same identity.

The unified package also works through `hermes plugins install
jacebenson/hermes-hackathon-call-recorder` **after this release is merged**. It ships
the same generated `desktop/plugin.js`; the retained Python half is not needed by
the new UI. This branch is a draft, not a deployed release.

## Record

Obtain consent from participants first.

1. Open the **Call recorder** pane. The compact composer launcher only opens it;
   it never starts or stops capture.
2. **Grant microphone**, then **Grant call audio**. Choose the playing tab/window
   in the browser picker and select **Share audio**. Actual input labels and dBFS
   meters show the returned sources, not inferred remote-machine devices.
3. If the share contains no audio, all its tracks are released. Share again or
   explicitly choose **Use microphone only**. There is no silent downgrade.
4. Use the single **Record / Stop** control and timer. Screen video is requested
   only to sustain sharing and is **never attached to a recorder or persisted**.
5. Stop saves both separate audio Blobs and shared timing/provenance as **one call
   record in one IndexedDB transaction**. The UI explicitly shows Saving, Saved,
   or Not saved. Rename, play/download each track, export metadata, or delete one
   call using a named confirmation followed by **Delete permanently**.

Capture retains the proven **3-minute / 24 MiB in-memory caps**. No muxing, mixing,
concatenation, drift correction or synchronized-container claims. Metadata labels
capture windows and recorder start/stop offsets; decoded duration is unmeasured
unless actually available. WebM players may show unknown/zero length initially.

## Local storage and privacy

- IndexedDB is specific to the **Hermes origin and browser profile**, not its
  connected machine, chat profile, or account. Switching backend/profile does not
  upload, transfer or rebind recordings. The library is not partitioned by backend.
- Calls may be evicted or cleared with site data. Download backups. Storage
  estimates and persistent-storage requests run only when explicitly clicked;
  persistent storage is a browser decision, not a durability guarantee.
- Quota/write failure leaves no partial call record and keeps immediate audio and
  timing downloads in the mounted pane, with Retry save. Download before closing;
  unsaved in-memory audio is not durable.
- Unmounting the pane or disabling the plugin releases microphone, shared
  audio/video and level-analysis contexts immediately. Late permission responses
  are released too. An active recording is stopped and its received audio is
  finalized into the local library. An already requested Stop still drains and
  saves after the pane disappears. Object URLs are released after finalization
  and the save attempt. Already saved calls are not deleted.
- Finalization waits for each recorder's actual terminal stop event, including
  final data emitted after an inactive-state encoder error. A broken encoder has
  a 5-second drain deadline. Received chunks remain exportable, with an incomplete
  error and `timing.finalizationComplete: false`; missing audio cannot be recovered.
- Hard tab/window closure, navigation or browser/process destruction cannot wait
  for asynchronous encoder events or IndexedDB. Unload releases tracks and makes
  a best-effort save only while the page remains alive. Stop and wait for Saved
  before leaving. If storage fails after unmount/disable, no mounted retry/export
  UI remains and unsaved audio is not durable. Export before leaving on failure.
- **Prototype recordings at localhost:8817 do not follow into Hermes's origin.**
  Export both audio files and metadata there **before switching**. No migration,
  auto-copy, browser-profile access or legacy filesystem import is performed.

## Compatibility gate and limits

The production runtime loader accepts a **single plain ESM file**, with only
`@hermes/plugin-sdk`, `react` and `react/jsx-runtime` imports. `desktop/src/` contains
maintainable pure modules and the native React/SDK UI. The shipped artifact bundles
only our modules; the host provides genuine Button/Input/Badge components and
React. No iframe, private import, preview DOM access or parent-window workaround.

**Browser Hermes webapp:** HTTPS or loopback secure context, MediaRecorder,
getUserMedia, getDisplayMedia with an audio-capable share, IndexedDB and browser
permission are required. A browser may offer tab audio but not system/window audio.
A share with no audio is rejected; use the explicit microphone-only path if needed.

**Native Electron is not universally supported or verified.** The inspected host
`electron/main.ts:6741` installs microphone/camera permission handlers but has no
`setDisplayMediaRequestHandler`. Its media predicate permits audio/video capture,
not unknown display-capture permissions. The plugin cannot install a native screen
picker through the public SDK. A host without the display-media handler may reject
Grant call audio even when the method exists. No host-core patch is included.
Microphone-only is an explicit fallback, not proof of Electron shared audio.
Actual Electron execution and real screen/tab audio remain unverified in this PR.
The prior beast-1 manual prototype check is not evidence for the packaged plugin's
native Electron compatibility. Firefox, Safari, Windows and mobile are unverified.

## Legacy behavior change

All Python capture/transcription/API files and existing recording paths remain.
The new UI does not call any of them. Importing `dashboard/plugin_api.py` no longer
starts `ensure_watcher()`: updating/loading the package must not initiate hidden
legacy capture, helper installation or SSH polling. `__init__.register()` is inert.
An operator deliberately calling the old `/start` API can still start legacy
capture and its watcher, with its old settings/side effects. This release removes
the old mandatory backend settings form and old transcript/notes/remote controls
from the Desktop UI. It neither deletes nor migrates legacy recordings.

## Build and verification (no installs)

Use an existing Hermes workspace containing esbuild and its dependencies:

```sh
export HERMES_DESKTOP=/path/to/hermes-workspace/apps/desktop
export HERMES_CSS_DIR=/path/to/built/desktop/dist-webapp/assets
node scripts/build-desktop.mjs
node --check desktop/plugin.js
node tests/desktop.test.mjs
node tests/browser-capture.test.mjs
python -m unittest discover -s tests
hermes plugins validate . --json
node tests/serve-sdk.mjs  # loopback :4197, separate terminal
node tests/plugin.browser.mjs
node tests/finalization.browser.mjs
# PROOF_CHROME can select an existing Chromium binary
# TMPDIR must be writable; tests create/discard isolated browser profiles there.
git diff --check
```

`tests/serve-sdk.mjs` uses the actual installed production loader's import rewrite,
blob-module evaluation, registration/context, SDK React components, ThemeProvider
and production CSS. The unrelated catalog artwork glob is an empty fixture; there
is no backend. It does not load a real Hermes profile or install the plugin live.
`tests/plugin.browser.mjs` uses real MediaRecorder and IndexedDB, a Chromium fake
microphone, and **instrumented** 300 Hz WebAudio + canvas for display-share
provenance. It proves handling of shared audio/video, not a real OS share picker.
Browser evidence and screenshots: [docs/evidence](docs/evidence). The follow-up
[finalization report](docs/finalization-verification.md) records before/after
failures, byte-exact terminal-chunk checks and hard-unload limits.

The replaced v1 UI assertions concerned upload, remote-machine selection and live
transcripts that are intentionally absent now. Backend security/path-confinement
suites remain. The v2 core and real packaged-UI suites cover refusal/no silent
fallback, late grants, scoped delete, atomic quota failure, teardown, caps and no
backend requests instead.

Independent review by Holly is required after delivery; do not merge or deploy
based only on these automated checks.

MIT. See [LICENSE](LICENSE).
