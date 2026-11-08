"""Turn a two-track recording into a speaker-labelled transcript.

Only the recording machine's own Whisper is used. That is deliberate: a plugin that
reaches into Hermes's internal voice modules breaks on any core release, and the
catalog treats a private import as a design problem rather than a bug. Whisper on the
machine that already has the audio (and usually the GPU) is faster anyway, and when it
is missing the plugin says so instead of half-transcribing.

The engine contract: the recording machine runs ``archive.py <file>``, which writes
``<file>.transcript.md`` with a ``**[mm:ss] you:**`` / ``them:`` line per segment.
"""

from __future__ import annotations

import pathlib
import shlex

ENGINE_GPU = "gpu"
ENGINE_NONE = "none"


def detect_engine(gpu_available: bool) -> str:
    """One engine: Whisper where the audio already is."""
    return ENGINE_GPU if gpu_available else ENGINE_NONE


def transcribe_with_gpu(rec, source: str, out_path: pathlib.Path, python: str,
                        script: str, env: dict | None = None) -> dict:
    """The recording machine's own Whisper run (per-line timing, fastest).

    The environment travels with the command: without WHISPER_MODEL it downloads a
    model, and without LD_LIBRARY_PATH it cannot reach the GPU at all. A login
    shell's environment does not come through ssh, so pass it explicitly.
    """
    prefix = " ".join(f"{key}={shlex.quote(str(value))}"
                      for key, value in (env or {}).items() if value)
    command = f"{prefix + ' ' if prefix else ''}{python} {script} {source}"
    code, out = rec.run(command, timeout=7200)
    if code != 0:
        return {"ok": False, "engine": ENGINE_GPU, "error": out.strip()[:300]}
    # The transcript lives on the recording machine, so count it there.
    _code, counted = rec.run(f"grep -c '^\\\\*\\\\*\\\\[' {shlex.quote(str(out_path))} || true")
    try:
        segments = int(counted.strip().splitlines()[0])
    except (ValueError, IndexError):
        segments = 0
    return {"ok": True, "engine": ENGINE_GPU, "path": str(out_path), "segments": segments}


def transcribe(rec, source: str, *, gpu_python: str = "",
               gpu_script: str = "", gpu_env: dict | None = None,
               prefer_gpu: bool = True) -> dict:
    """Run Whisper on the recording machine, or explain why it cannot."""
    out_path = pathlib.Path(source).with_suffix(".transcript.md")
    engine = detect_engine(bool(prefer_gpu and gpu_python and gpu_script))
    if engine == ENGINE_NONE:
        return {"ok": False, "engine": ENGINE_NONE,
                "error": "no Whisper on the recording machine: install faster-whisper there "
                         "and let the plugin find its python"}
    return transcribe_with_gpu(rec, source, out_path, gpu_python, gpu_script, gpu_env)
