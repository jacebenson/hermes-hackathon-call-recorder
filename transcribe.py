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

import base64
import pathlib
import re
import shlex

from capture_windows_wasapi import _q as powershell_quote
from recorder import parse_transcript

ENGINE_GPU = "gpu"
ENGINE_NONE = "none"
ENGINE_LIVE = "live"


def live_to_markdown(source: str, text: str) -> tuple[str, int]:
    """Render live wall clocks relative to the recording filename's start time.

    Live clocks describe when each chunk finished, not precise word timings.
    A clock after midnight belongs to the next day. Stable sorting preserves
    emission order when two speakers have the same clock.
    """
    name = source.replace("\\", "/").rsplit("/", 1)[-1]
    match = re.fullmatch(r"call-\d{8}-(\d{2})(\d{2})(\d{2})\.mkv", name)
    origin = None
    if match:
        hour, minute, second = map(int, match.groups())
        origin = hour * 3600 + minute * 60 + second
    rows = []
    for line in text.splitlines():
        speech = parse_transcript(line)
        if not speech or not speech["text"].strip() or speech["text"].startswith("[error "):
            continue
        hour, minute, second = map(int, speech["time"].split(":"))
        if hour > 23 or minute > 59 or second > 59:
            continue
        clock = hour * 3600 + minute * 60 + second
        if origin is None:
            origin = clock
        rows.append(((clock - origin) % 86400, speech["speaker"], speech["text"].strip()))
    rows.sort(key=lambda row: row[0])
    minutes = rows[-1][0] / 60 if rows else 0
    markdown = f"# {name}\n\n{minutes:.1f} minutes, {len(rows)} segments\n\n"
    for start, who, words in rows:
        markdown += f"**[{start // 60:02d}:{start % 60:02d}] {who}:** {words}\n\n"
    return markdown, len(rows)


def transcribe_from_live(rec, source: str) -> dict:
    """Read and atomically publish on the recording host, then verify the file."""
    live = str(pathlib.Path(source).with_suffix(".live.txt"))
    output = str(pathlib.Path(source).with_suffix(".transcript.md"))
    windows = getattr(rec, "os_name", "linux") == "windows"
    if windows:
        read = lambda path: f"Get-Content -LiteralPath {powershell_quote(path)} -Raw -Encoding UTF8 -ErrorAction Stop"
    else:
        read = lambda path: f"cat -- {shlex.quote(path)}"
    code, text = rec.run(read(live))
    if code != 0:
        return {"ok": False, "engine": ENGINE_LIVE, "error": "No readable live transcript."}
    markdown, count = live_to_markdown(source, text)
    if not count:
        return {"ok": False, "engine": ENGINE_LIVE, "error": "No speech in the live transcript."}
    payload = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
    if windows:
        command = (f"$tmp = {powershell_quote(output)} + '.' + [guid]::NewGuid().ToString() + '.tmp'; "
                   f"try {{ [IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String('{payload}')); "
                   f"Move-Item -LiteralPath $tmp -Destination {powershell_quote(output)} -Force -ErrorAction Stop "
                   "} catch { Write-Output $_; exit 1 } finally { "
                   "Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue }")
    else:
        command = (f"tmp=$(mktemp {shlex.quote(output + '.XXXXXX')}) || exit 1; "
                   "trap 'rm -f -- \"$tmp\"' EXIT; "
                   f"printf %s {shlex.quote(payload)} | base64 -d > \"$tmp\" && "
                   f"mv -f -- \"$tmp\" {shlex.quote(output)}")
    code, _ = rec.run(command)
    if code != 0:
        return {"ok": False, "engine": ENGINE_LIVE, "error": "Could not write the live transcript."}
    code, written = rec.run(read(output))
    if code != 0 or written.rstrip("\r\n") != markdown.rstrip("\r\n"):
        return {"ok": False, "engine": ENGINE_LIVE, "error": "Could not verify the live transcript."}
    return {"ok": True, "engine": ENGINE_LIVE, "path": output, "segments": count}


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
    command = (f"{prefix + ' ' if prefix else ''}{shlex.quote(python)} "
               f"{shlex.quote(script)} {shlex.quote(source)}")
    code, out = rec.run(command, timeout=7200)
    if code != 0:
        return {"ok": False, "engine": ENGINE_GPU, "error": out.strip()[:300]}
    # Verify the exact remote target before reporting completion.
    code, _ = rec.run(f"test -f {shlex.quote(str(out_path))}")
    if code != 0:
        return {"ok": False, "engine": ENGINE_GPU, "error": "Whisper did not write a transcript."}
    code, counted = rec.run(r"grep -c '^\*\*\[' " + shlex.quote(str(out_path)))
    try:
        segments = int(counted.strip())
        if code not in (0, 1) or segments < 0:
            raise ValueError("invalid segment count")
    except (ValueError, TypeError):
        return {"ok": False, "engine": ENGINE_GPU, "error": "Could not verify the Whisper transcript."}
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
