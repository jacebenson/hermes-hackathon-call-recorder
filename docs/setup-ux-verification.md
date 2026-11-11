# Recorder setup UX verification

## Change

Setup/settings has an explicit keyboard-accessible expand/collapse button with `aria-expanded` and `aria-controls`. The small `call-recorder.ui.v1` preference stores only `{version:1,setupExpanded:boolean}` in the plugin's localStorage namespace. Malformed, unknown-version or inaccessible storage falls back safely. It does not change audio grants or recording state.

Readiness collapses setup once when entering ready without an error. Users can reopen it. Source identity/status, grant controls, the explicit microphone-only choice, errors, save status and the primary Record/Stop/timer stay outside the disclosure. The primary controls are sticky in the pane's scrolling container. Meters and setup guidance collapse; storage/export guidance has a separate native disclosure. Collapsing does not unmount input components or reset the controller.

`NotSupportedError` from the real display-media request now renders an unsupported source badge, not a permission-denied badge. Its explanation distinguishes missing Electron host sharing support from microphone permission, offers a supported browser share path only if offered, and retains an explicit microphone-only action. No automatic downgrade, browser/OS inference or backend capture was added.

## Native capability boundary

The inspected Hermes Electron `installMediaPermissions` in `apps/desktop/electron/main.ts` registers media permission request/check handlers. It does not register `setDisplayMediaRequestHandler`. A search of the installed Electron source found `desktopCapturer` only in the screenshot command module, not a display-media sharing handler. Screenshot support is not an audio-source picker.

The installed plugin SDK exposes host state, gateway status and contribution APIs, but no display-media audio capability declaration or sharing handler registration. `host.status()` is a gateway system snapshot, not proof of this renderer's capture capability. Consequently this change does not guess host support from user-agent strings, a backend OS, or the mere presence of `getDisplayMedia`. Only an explicit share request and its returned audio track establish a usable source. A method can exist in Electron and still reject with NotSupportedError.

The reported native client's microphone grant plus call-audio Not supported is consistent with that missing host implementation. A recorder-only UI patch cannot provide it. Native call-audio capture remains blocked pending separately reviewed core support and an actual device test. Browser capture requires a Hermes browser client that loads this plugin, a supported sharing implementation, and an explicitly audio-enabled source. It is not global desktop loopback. The older Python dashboard is not made into a client for this plugin by this patch.

No core files, installed plugin, gateway, microphone, desktop session, profile, configuration or credentials were modified. No model, cloud audio, SSH or backend capture calls were added or used.

### Local ffmpeg is a separate possible implementation

The parent verified `ffmpeg` and `pactl` are installed on beast-1. That makes a native Linux capture implementation plausible, not verified. Available user-session microphone and sink-monitor devices, the call application's actual playback sink, permissions, separate-track output and graceful stop still need explicit device-level proof. No capture probe was run here.

A client-local native implementation could use a separately reviewed Electron main/preload capture bridge or a separately installed client-local helper. Such a bridge should expose bounded start/status/stop/source enumeration rather than an arbitrary shell, spawn ffmpeg with validated argument arrays, run in the logged-in user's audio session, name the capture machine, require explicit consent and own process/file lifecycle. It must preserve microphone/call-track separation and disclose native file storage rather than pretending ffmpeg output is already in this browser's IndexedDB. The inspected SDK/preload has no dedicated recorder/ffmpeg bridge; generic terminal or gateway methods are not a safe substitute to quietly introduce one.

A Python recorder API reached through `ctx.rest` runs on the selected Hermes backend. When that backend is remote it captures the remote machine, not this renderer's machine. A deliberately selected and verified same-machine backend could support an explicitly labelled native capture mode, but would remain backend-owned capture, not a renderer-local helper. Neither path is implemented in this UI correction. Native capture is not fundamentally impossible; the current browser display-share path lacks the needed native host integration. Arbitrary SSH discovery/capture has not been restored.

## Executed checks

- `node scripts/build-desktop.mjs`, generated entry and packaged entry byte equality.
- `node --test tests/local/*.test.mjs`, lifecycle, library, incremental-write/quota/finalization checks.
- `python3 -m unittest discover -s tests -p test_distribution.py`, desktop-only package checks.
- `SETUP_OUT=<scratch>/setup-ux node tests/setup-ux.browser.mjs`, packaged artifact through the installed real runtime loader, React, ThemeProvider, SDK and production CSS. Chromium fake mic and instrumented WebAudio/canvas display stream. Narrow 390px and wide 1000px views, native Enter key activation, persistence across reload, malformed/unknown version fallback, unsupported error without downgrade, scroll-bottom sticky control hit-test, active microphone-only and two-input collapse, Stop/save and track release. Screenshots inspected.
- `PROOF_URL=http://127.0.0.1:4198/ PROOF_CHROME=/usr/sbin/chromium FINALIZATION_MODES=stop-disable,stop-unmount,active-unmount,error-mic,error-dual,repeated-stop node tests/chunked.browser.mjs`, focused lifecycle regression. Includes actual MediaRecorder inactive-before-error, terminal emitted/stored SHA-256 equality, reload hashes, interruption recovery prefix, quota failure, playback, export hashes and no backend API requests. The prior 183-second duration test was not repeated for this presentation/error-message change.

These tests prove renderer behavior with synthetic inputs, not real native sharing, an OS audio picker or live deployment. The PR stays draft. Parent/operator review and any later installation remain separate gates. Original broad-task native-acceptance scope is still blocked; no readiness bypass is claimed.
