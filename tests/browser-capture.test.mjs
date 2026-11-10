// v1 upload-controller assertions remain in git history; this release has no uploads.
// v2 has no upload controller. Privacy/lifecycle paths are covered in the pure
// core suite plus actual packaged-plugin tests/plugin.browser.mjs.
await import('./local/core.test.mjs')
await import('./local/library.test.mjs')
