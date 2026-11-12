# Finalization follow-up verification

Base `fb30dd503ee6df47240f4177aaee858bbbb1847e`, freshly fetched main.
Branch `fix/local-recording-finalization`, isolated scratch worktree. No shared
client-local checkout, existing review evidence or live desktop was changed.

## Reproduced failures and corrections

Both supplied reviewer scripts ran unchanged against `node tests/serve-sdk.mjs`
and the base packaged bundle, then against the rebuilt candidate. Stop followed
by actual host disable in the same task saved zero calls before, one after.
The native inactive-state InvalidModificationError emitted 5266 periodic bytes
plus 5847 final bytes. Before, only 5266 bytes persisted, losing 5847. After,
11113 bytes emitted and 11113 stored, zero lost. Both scripts exited 1 before
and 0 after. Logs: [before Stop](evidence/finalization/before-stop.log),
[after Stop](evidence/finalization/after-stop.log),
[before error](evidence/finalization/before-error.log),
[after error](evidence/finalization/after-error.log).

The committed regression also ran against the unchanged base bundle on :4198.
`FINALIZATION_MODES=stop-disable` failed with zero saved calls; `error-mic`
failed the emitted/stored SHA-256 comparison, 11113 versus 5266 bytes. Both exit 1.

Core terminal callbacks now exist before start and stay attached until every
recorder reports stop, or a 5-second encoder deadline expires. Inactive is not
used as a drain-completion signal. Tracks are released synchronously on stop.
Controller disposal waits for finalization and the resulting local save before
unsubscribing, revoking URLs and closing IndexedDB. Active unmount uses that same
path. Repeat Stop joins completion instead of launching a second finish.

## Real packaged runtime proof

[Machine-readable checks, event sequence and hashes](evidence/finalization/checks.json)
and [browser output](evidence/finalization/browser.log). Real Chromium
MediaRecorder and IndexedDB, actual installed SDK runtime loader and React.
The microphone is a Chromium fake device. Display sharing is explicitly
instrumented with 300 Hz WebAudio and canvas video, not a real share picker.
Each stored Blob's SHA-256 equals the concatenated native emitted chunks.

| Scenario | Emitted bytes per recorder | Stored Blob bytes | SHA-256 |
|---|---|---|---|
| stop-disable | 10953, 13785 | 10953, 13785 | equal |
| stop-unmount | 11585, 13785 | 11585, 13785 | equal |
| active-unmount | 11585, 13785 | 11585, 13785 | equal |
| error-mic | 11585 | 11585 | equal |
| error-dual | 11585, 13785 | 11585, 13785 | equal |
| repeated-stop | 11425, 13785 | 11425, 13785 | equal |

## Executed acceptance

| Command | Result | Exit |
|---|---|---|
| Both supplied reviewer browser scripts | fail before, pass after | 1 then 0 |
| `node tests/finalization.browser.mjs` | 25 checks, six lifecycle scenarios | 0 |
| `node tests/plugin.browser.mjs` | 38 checks, including late grant, atomic quota failure, real export hash and retry | 0 |
| `node tests/browser-capture.test.mjs` | 18 core and 12 library checks | 0 |
| `node tests/desktop.test.mjs` | packaged identity/privacy invariants | 0 |
| `python -m unittest discover -s tests` | 143 tests | 0 |
| `hermes plugins validate . --json` | safe, no warnings | 0 |
| `node --check` source, generated bundle and browser tests | parse | 0 |
| repeated `node scripts/build-desktop.mjs` and SHA-256 | deterministic bundle | 0 |
| `git diff --check` | clean | 0 |

Bundle SHA-256 `f8561bb25ab4732db68216b7cbfabfee315758388e47f73a27c69b42c424daed`.
[Existing browser log](evidence/finalization/existing-browser.log),
[Python log](evidence/finalization/python.log), [validator](evidence/finalization/validator.json).
The existing decode test still reports Opus packet-header diagnostics, with
ffmpeg exit 0 and nonzero PCM. No diagnostic-free decoding claim is made.

Run from the checkout with existing SDK/esbuild and Chromium, no installs:

```sh
node scripts/build-desktop.mjs
node tests/serve-sdk.mjs   # own loopback fixture, separate terminal
node tests/finalization.browser.mjs
PROOF_EVIDENCE="$TMPDIR/finalization-existing-browser" node tests/plugin.browser.mjs
node tests/browser-capture.test.mjs
```

The new regression accepts `PROOF_URL`, `PROOF_CHROME`, `FINALIZATION_MODES` and
`FINALIZATION_OUT`. To check sensitivity, serve the base bundle with
`PLUGIN_FILE` and `PORT=4198`, then run each failing mode with
`PROOF_URL=http://127.0.0.1:4198/`.

## Limits and review gate

No upload/backend/SSH/model path, source-selection behavior or capture cap changed.
Stop/disable/unmount preservation requires the document to remain alive for
encoder events and IndexedDB. Hard tab/window close, navigation, process crash
or browser destruction cannot await these asynchronous operations. Wait for
Saved before leaving. A synthetic pagehide test with a live document is not proof
of durability through real destruction. A missing encoder stop has a 5-second
deadline; received audio is kept and marked incomplete in the UI and timing.
Audio beyond existing memory caps is not retained. A save failure retains export
and Retry save in the mounted pane only; after unmount/disable there is no retry
UI and unsaved audio is not durable.

Native Electron, real OS screen audio, other browsers and crash durability remain
unverified. No package installs, plugin installs, deployment, restart, real
recording, real browser profile or live desktop access occurred. This is worker
execution evidence, not independent review certification. Holly must recheck the
final pushed SHA. Draft only, no merge.
