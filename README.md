# Hermes Call recorder v2

Record this client's microphone and explicitly shared call audio as separate local tracks. No backend request, upload, transcription or model call.

## Install

This branch is a draft, not a deployed release. The v2 distribution is `packages/call-recorder`, not the repository root. In Hermes's plugin installer use the reviewed commit and this source:

```
jacebenson/hermes-hackathon-call-recorder#packages/call-recorder
```

A catalog revision must set `subdir: packages/call-recorder` as well as its reviewed SHA. The root intentionally retains the legacy Python backend and is still detected as a mixed package. Do not install the root for v2. Alternatively, from a reviewed checkout:

```sh
sh scripts/install-desktop.sh /explicit/path/to/your/hermes-home
```

This copies only `packages/call-recorder/desktop/plugin.js` into `desktop-plugins/call-recorder/plugin.js`. No Python dependencies, backend routes, settings or helpers ship in the distribution. It does not remove an existing legacy installation. Resolve duplicate plugin identities before enabling a new copy. The plugin is opt-in.

## Record

Obtain participant consent first.

1. Open Call recorder. Grant microphone, then Grant call audio. Select a playing tab or window and enable Share audio in the picker.
2. A share without audio is released. Share again or explicitly choose Use microphone only. Video sustains sharing where needed but is never recorded.
3. Record, then Stop. Wait for Saved before leaving.
4. Rename a call, load a selected track for playback/download, export metadata or confirm deletion of one call.

There is no three-minute or total 24 MiB cap. One-second MediaRecorder timeslices commit incrementally to IndexedDB by call, track and sequence. At most 8 MiB or 16 chunks can await writes. A stalled writer, quota failure or oversized browser timeslice stops capture visibly. Committed chunks remain available, but uncommitted audio and the encoder tail may be missing. Browser encoders can internally buffer beyond our pending-write bound.

Stop awaits terminal recorder events and every queued write, not a fixed sleep. Broken encoders have a five-second deadline and yield Partial, never Saved. After reload, unfinished sessions appear as Recovered partial with their committed chunks. This does not resume recording or recover a missing tail.

## Storage and privacy

- Calls belong to this origin and browser profile, not the connected backend, Hermes profile or account. Native Electron and browser libraries are separate. No SSH, uploads, backend requests, transcription or model calls occur in v2.
- IndexedDB can be evicted or cleared. Download portable backups. Storage estimates and persistence requests require a click; denial is shown. Persistent storage cannot prevent explicit deletion or hardware failure.
- Listing/recovery uses metadata cursors, never every audio Blob. Stop does not assemble full tracks. **Playback and fallback downloads assemble one selected track in memory on demand.** For long calls use Export track to file where File System Access is available. That writes one indexed chunk at a time. The actual native file dialog remains a live acceptance item.
- Hard reload or process destruction cannot await encoder/IDB completion. Only committed chunks are recoverable. Partial exports may not decode in every player. Stop and wait for Saved before leaving.
- Timing records source labels and shared offsets, not sample synchronization, mixing, muxing or drift correction. Container duration is unmeasured and WebM can show unknown length. Recovered duration reflects the last committed chunk, not a fabricated final duration.
- The previous `call-recorder-local-library` database and legacy filesystem recordings remain untouched. Export v1 calls with the previous version before switching. No automatic scan/import occurs. Prototype `localhost:8817` recordings stay at that origin too.

## Compatibility

Browser Hermes requires HTTPS or loopback, MediaRecorder, IndexedDB, microphone permission and an audio-capable share. Audio availability depends on browser, OS and source. Firefox, Safari, Windows and mobile remain unverified.

Native Electron shared audio is **not verified**. A previously inspected host had microphone permission handling but no `setDisplayMediaRequestHandler`. The plugin cannot add a native share picker through the SDK. Microphone-only is an explicit fallback, not proof of shared audio. Both actual browser Hermes and locally installed Electron on beast-1 require the [live acceptance recipe](docs/chunked-v2-verification.md). Operator SSH is blocked by host-key verification. No bypass, live installation, restart or microphone manipulation was performed.

## Build and test

Use an existing Hermes SDK workspace with esbuild. No dependency installation is needed by the distribution.

```sh
HERMES_DESKTOP=/path/to/hermes/apps/desktop node scripts/build-desktop.mjs
node --test tests/local/chunked.test.mjs
node tests/local/core.test.mjs
node tests/local/library.test.mjs
hermes plugins validate packages/call-recorder
```

`desktop/src` is maintained source. Both generated artifacts are identical self-contained ESM with only SDK/React host imports. See [verification and remaining gates](docs/chunked-v2-verification.md) for the isolated production-loader browser fixture, legacy Python results and evidence boundaries. Historical evidence documents describe older revisions, not this release or beast-1 deployment. Independent Holly review is required before catalog pin/artwork/ready changes.

MIT. See [LICENSE](LICENSE).
