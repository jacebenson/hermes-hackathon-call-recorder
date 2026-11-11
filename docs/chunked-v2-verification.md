# Chunked v2 verification and live acceptance

## Scope

Implementation is stacked on PR #8 at `8dc459e247b36d72b6b557ce45a67aa660de8aa1`. Neither that PR nor unrelated PR #6 is merged or replaced. This is a draft feature artifact, not an installation or final catalog release. Holly must independently inspect the diff and rerun checks before revising catalog PR NousResearch/hermes-agent#151506. Keep that catalog PR draft until its live acceptance gates are resolved.

## Automated verification

The maintained deterministic suites are:

```sh
node --test tests/local/chunked.test.mjs
node tests/local/core.test.mjs
node tests/local/library.test.mjs
python3 -m unittest discover -s tests -p test_distribution.py
python3 -m compileall -q .
node --check desktop/plugin.js
hermes plugins validate packages/call-recorder --json
```

The existing real SDK/React/ThemeProvider/production-CSS fixture loads the exact generated artifact through Hermes's installed `loadRuntimePlugin`, not an element-shaped React stub:

```sh
# Point these at an existing SDK workspace and built production CSS as needed.
export HERMES_DESKTOP=/path/to/hermes-workspace/apps/desktop
export HERMES_CSS_DIR=/path/to/hermes/apps/desktop/dist-webapp/assets
export PROOF_OUT="$TMPDIR/call-recorder-sdk-proof"
node tests/serve-sdk.mjs
# Separate terminal, same checkout:
PROOF_CHROME=/path/to/chromium CHUNKED_OUT="$TMPDIR/call-recorder-v2-proof" node tests/chunked.browser.mjs
```

The default browser suite takes over three minutes because its `long` case actually waits 183 seconds with two real MediaRecorders running. It does not advance a fake clock. Short repeat runs can set `FINALIZATION_MODES=stop-disable,stop-unmount,active-unmount,error-mic,error-dual,repeated-stop`.

The fixture uses Chromium's fake microphone and injected 300 Hz WebAudio/canvas display sharing. It checks terminal emitted/persisted track SHA-256 equality, stop plus disable/unmount, active unmount, repeated stop, actual inactive-state `InvalidModificationError`, persisted hashes across a real reload, recovery of committed chunks after a real reload with graceful-unload handlers suppressed, injected IndexedDB quota errors, playback advancement, and no backend API requests. Streaming export uses the real chunk retrieval/writer with a test writable sink, not a native Save File dialog. The interrupted-reload fixture deliberately suppresses pagehide/beforeunload cleanup so a fast graceful save cannot turn the interrupted test into Saved. It does not prove process-crash or power-loss durability. These prove renderer/recorder/storage behavior, not OS capture permission, a real share picker, actual desktop audio, native file dialog or installed Electron behavior.

Historical `tests/plugin.browser.mjs` and `tests/finalization.browser.mjs` target the previous whole-record Blob schema. They remain as historical source, not v2 acceptance suites. `tests/local/core.test.mjs` has been adapted to an incremental storage dependency while retaining acquisition, cancellation, delayed encoder, error and teardown checks. `tests/local/library.test.mjs` still tests the preserved legacy data facade.

The complete Python suite is also run, not silently omitted. The legacy backend has pre-existing CPU config/library route expectation failures on the PR8 base. The host initially lacks PyYAML, causing additional legacy settings failures. Verification installs PyYAML only into a task-owned test-deps directory, never a live profile. Exact baseline/current logs, isolated HOME commands and outcomes are in the private handoff reports. Those legacy failures are not presented as green; the desktop-only distribution contains no Python code or dependency on PyYAML. Legacy Python source is preserved unchanged.

Observed automated results: 36 full browser checks and 31 short repeat checks passed. The two-track capture ran 183112.7 real elapsed milliseconds. Deterministic checks passed 6/6 chunk writer/lifecycle additions, 17/17 adapted core lifecycle, 12/12 preserved legacy library and 2/2 distribution. Validator, JavaScript syntax, Python compilation and diff checks passed. Full Python ran 145 tests with the same three failures and two errors reproduced on the 143-test PR8 base. [Sanitized hash-bound evidence](evidence/chunked-v2.json) records the generated artifact and media hashes. No live-client acceptance is included in those totals.

## Distribution contract

Current Hermes `hermes_cli.plugins_cmd_inspect.detect_components` treats a YAML manifest with a sibling `__init__.py` or `dashboard/manifest.json` as an agent payload. An inert registration function does not remove that payload. Root installation would therefore still ship/register legacy code.

`packages/call-recorder` contains only `plugin.yaml` and `desktop/plugin.js`. This satisfies the validator's desktop loadable layout and probes as agent=false, desktop=true. The catalog supports `subdir` and resolves it as `repo#subdir`; use `packages/call-recorder`. The installer publishes only that desktop tree. The repository root probes as agent=true and must not be used as the v2 install target. No existing plugin, recording, config or service is deleted/disabled by this change. Both source and generated package artifacts must be reviewed at the same exact SHA.

## Storage boundaries

- Each append atomically commits its chunk and corresponding metadata counters in one transaction. A whole call is not crash-atomic across transactions.
- A session is created before capture. Saved appears only after terminal encoder events, queued writes and final session metadata commit. An interrupted, failed or timed-out session is Partial. Reload labels unfinished `recording` rows Recovered partial without writing a false successful finalization.
- Pending writes have an 8 MiB/16-chunk bound across both tracks. An overlarge browser chunk stops capture; it is not silently accepted beyond the bound. Already committed media survives, but a failure can discard queued/uncommitted data. Stop and export the committed prefix. Do not claim lossless quota recovery.
- Listing/recovery uses metadata-only cursors in a new database. It never `getAll`s the chunk store or scans old libraries. One selected track is assembled only for playback/fallback download. That fallback can consume substantial memory for a long call. File System Access export reads one exact indexed chunk at a time and closes the destination only after all writes.
- Hard unload cannot await encoder events or storage. Recovery guarantees neither the pending queue nor the terminal container tail. Partial media may not decode everywhere. Calls from other origins/profiles and the old database do not appear automatically.

## Required live acceptance on beast-1

**Blocked, not passed.** The operator's current SSH attempt fails `Host key verification failed`. Do not bypass host verification, alter known_hosts without coordinated identity verification, drive the desktop, install/restart Hermes or acquire its microphone without operator access and explicit coordination.

Once the operator restores trusted access, execute separately in both the actual browser Hermes client and the locally installed native Electron client:

1. Record the client app/version, host/version, exact origin, secure-context state, plugin commit and source subdirectory. Ensure no duplicate legacy `callrec`/`call-recorder` UI is enabled unintentionally. Preserve existing recordings and the old installation.
2. With participant consent, enable this reviewed artifact using the normal client installer. Do not infer enablement or mounting from files alone. Confirm the visible pane and no runtime import errors.
3. Grant the real microphone and inspect the real OS/browser share picker. Share a playing source with Share audio enabled. Verify actual source labels and audible independent mic/call output. If Electron cannot offer audio, record its rejection as a blocker, not a passed microphone-only substitute.
4. Record speech plus a controlled shared-audio clip for more than three real minutes. Confirm elapsed capture continues, chunk counters grow in client IndexedDB and stop finalizes once. Download/export both tracks and metadata, hash and decode them, measure non-silence in each track, and listen to the source separation.
5. Repeat Stop plus plugin-disable, Stop plus pane-unmount and active unmount. Ensure all acquired audio/video tracks and level-analysis contexts release immediately and terminal bytes still persist. Re-enable/reopen without deleting site data; the call must remain.
6. Reload during an explicitly disposable test recording. Confirm only committed data returns as Recovered partial with honest missing-tail disclosure. Export the recovered tracks and inspect actual decoding. Do not demand a missing encoder tail after process destruction.
7. Verify title save, scoped delete cancel/confirm, selected playback advancement, actual native Save File streaming export, fallback download byte equality, persistent-storage grant/denial messaging, dark/light themes, narrow view and keyboard focus. Never clear the entire origin to test one call.
8. Compare browser and Electron origin/profile boundaries. They must not imply a shared library. Preserve all user recordings. Report each client independently and attach exact evidence before changing catalog ready state.
