// v2 replaces backend capture-target selection with client-local inputs.
// Keep the existing browser-test entrypoint, exercising the packaged UI instead.
// Run node tests/serve-sdk.mjs first (see README).
await import('./plugin.browser.mjs')
