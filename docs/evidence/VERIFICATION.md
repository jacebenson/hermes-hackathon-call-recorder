# Verification — client-local v2

Base: `00ce57a6cd411fcdda2249d9b273bcbb19852981` (`origin/main`, fetched and independently checked with `git ls-remote`). Branch: `feat/client-local-recorder`.

## Executed results

| Command | Result | Exit |
|---|---|---|
| `node scripts/build-desktop.mjs` | single generated ESM artifact; SDK/React remain host imports | 0 |
| `sh scripts/install-desktop.sh <disposable-home>` | copied only desktop artifact; `cmp` equals source | 0 |
| `node --check desktop/plugin.js` | parses | 0 |
| `node tests/desktop.test.mjs` | packaged identity, privacy/import/registration invariants | 0 |
| `node tests/browser-capture.test.mjs` | 15 core + 12 library checks | 0 |
| `python -m unittest discover -s tests` | 143 tests, including clean installed module paths and inert registration/import | 0 |
| `hermes plugins validate . --json` | safe; no warnings; SDK surface; isolated register | 0 |
| `node tests/plugin.browser.mjs` | 38 real-browser checks | 0 |
| `git diff --check` | clean | 0 |

[Python output](python.log), [core/library output](core-library.log), [browser output](browser.log), [validator](validator.json), [individual browser checks](checks.json), [actual loader inventory/registration](runtime-loader.json), [Blob hashes](blob-hashes.json).

The final browser run served **the copied desktop-only artifact** from a newly created scratch Hermes home, not a real installation. Its SHA was compared byte-for-byte with `desktop/plugin.js`. No Python backend was installed there. The unified-package test separately copied all retained runtime Python assets to another clean scratch home, imported via the dashboard's file-path mode in a fresh process, asserted dependency paths remain inside that package, and rejected any thread/process launch while importing/registering.

The browser fixture uses the installed `loadRuntimePlugin` unchanged (allowlist, SDK shim Blob imports, validation and real scoped registration), genuine SDK components/React, ThemeProvider, and compiled production CSS. The unrelated catalog artwork Vite glob is an empty fixture, not a loader substitute. The proof records `call-recorder` as `loaded`, with namespaced `plugin:call-recorder` pane provenance.

The SDK's real connection/profile atoms start at fixture remote A, switch to remote B during capture, and return the expected SDK identity without recorder API requests or rebinding local audio. The pane uses neither identity as a storage/backend input.

## What the browser run proves

- Real microphone capture through Chromium's fake capture device and real MediaRecorder encoders for both files.
- Explicitly **instrumented** getDisplayMedia: 300 Hz WebAudio plus a canvas video track. Two non-silent distinct audio files, ffprobe reports one audio stream each and no video. ffmpeg exits 0 and produces nonzero PCM energy; it emits an Opus packet-header diagnostic for each fixture, retained in the log. This is not a claim of diagnostic-free decoding.
- Both Blob hashes match after full reload; downloaded bytes match those stored Blobs; both actual audio players advance beyond 0.25 s.
- Rename and metadata export, scoped two-step delete/cancel, survivor bytes unchanged.
- No-audio share releases video and requires explicit mic-only selection; permission denial saves nothing.
- Forced QuotaExceededError leaves no partial record, preserves immediate exports and succeeds on explicit Retry save.
- Active two-input pane unmount and pagehide release all acquired tracks. Host `setPluginEnabled(false)` during a late real-microphone permission return releases the late stream. Saved calls survive all teardown paths.
- Actual nonzero source levels; no recorder backend requests; narrow/large keyboard focus and no horizontal overflow.

## Screenshots inspected

All four are the genuine SDK/CSS fixture, not a mockup:

- [Dark narrow, 390px](dark-390.png)
- [Light narrow, 390px](light-390.png)
- [Dark large, 1440px](dark-1440.png)
- [Light large, 1440px](light-1440.png)

Controls and text wrap without horizontal clipping. Saved tracks below the viewport are reached by the pane's internal scroll; the screenshots are viewport captures, not full-library images. Disabled controls intentionally have reduced contrast. Enabled SDK controls use the secondary variant; buttons in the pane request the SDK's touch size. Keyboard interaction and focus were exercised separately from screenshot inspection.

## Not verified / not authorized

Native Electron runtime, a real OS screen/tab share picker, Firefox/Safari/Windows/mobile, and long-term browser eviction behavior remain unverified. The inspected Electron source has microphone/camera permission hooks but no display-media request handler; no host-core patch or universal native shared-audio support is claimed. The headless fixture does not reproduce the user's prior manual beast-1 share.

No deployment, live installation, restart, recording-directory change, catalog update, original browser-profile access or prototype localhost:8817 storage migration occurred. Export those existing prototype recordings before changing origin. Holly's independent post-delivery review is still required.
