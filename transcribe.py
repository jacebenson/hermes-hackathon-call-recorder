"""One transcription path, two engines.

The recording is sliced into fixed chunks and each chunk is transcribed by whichever
engine the machine can actually use:

* ``gpu``   — faster-whisper on the recording machine (fast, local, keeps timing);
* ``hermes``— Hermes's own speech-to-text, the same one push-to-talk uses, so a
  machine with no Whisper install still produces a transcript.

Both write the same markdown with one line per speaker and a ``[mm:ss]`` stamp, so
downstream code and people do not care which engine ran.
"""

from __future__ import annotations

import base64
import pathlib
import tempfile

#: Seconds per chunk. The GPU engine picks its own finer timing; this is what keeps
#: the Hermes engine under the 25 MB upload limit and still gives usable stamps.
CHUNK_SECONDS = 120

ENGINE_GPU, ENGINE_HERMES = "gpu", "hermes"


def detect_engine(rec, gpu_available: bool, hermes_available: bool) -> str | None:
    """Fastest first: the recording machine's GPU, else Hermes's own STT."""
    if gpu_available:
        return ENGINE_GPU
    if hermes_available:
        return ENGINE_HERMES
    return None


def hermes_stt_configured() -> bool:
    """True when this Hermes profile has speech-to-text switched on."""
    import os

    try:
        import yaml
    except Exception:  # noqa: BLE001 - no PyYAML (running outside Hermes)
        return False

    home = os.environ.get("HERMES_HOME") or str(pathlib.Path.home() / ".hermes")
    try:
        data = yaml.safe_load((pathlib.Path(home) / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return False
    stt = data.get("stt") or {}
    return bool(stt.get("enabled")) and bool(stt.get("provider"))


def _hermes_transcribe_file(path: str) -> str:
    """Transcribe one audio file with the profile's configured provider."""
    from tools.voice_mode import transcribe_recording  # type: ignore

    result = transcribe_recording(path)
    if not result.get("success"):
        error = str(result.get("error") or "")
        if "empty transcript" in error.lower():
            return ""
        raise RuntimeError(error or "transcription failed")
    return str(result.get("transcript") or "").strip()


def _extract_chunk(rec, source: str, track: int, offset: int, seconds: int, dest: str) -> bool:
    """Pull one slice of one track to a local file on this machine."""
    remote = f"/tmp/callrec-chunk-{offset}-{track}.wav"
    code, out = rec.run(
        f"ffmpeg -y -v error -ss {offset} -t {seconds} -i {source} -map 0:a:{track} "
        f"-ac 1 -ar 16000 -c:a pcm_s16le {remote}", timeout=180)
    if code != 0:
        raise RuntimeError(f"could not extract chunk at {offset}s: {out.strip()[:200]}")
    code, b64 = rec.run(f"base64 -w0 {remote}", timeout=180)
    rec.run(f"rm -f {remote}")
    if code != 0 or not b64.strip():
        raise RuntimeError("could not read the extracted chunk")
    payload = base64.b64decode(b64.strip())
    with open(dest, "wb") as fh:
        fh.write(payload)
    return True


def transcribe_with_hermes(rec, source: str, duration: float, out_path: pathlib.Path) -> dict:
    """Chunk both tracks and send each slice through Hermes's speech-to-text."""
    rows: list[tuple[int, str, str]] = []
    tracks = ((0, "them"), (1, "you"))
    offset = 0
    while offset < max(duration, 1):
        seconds = int(min(CHUNK_SECONDS, duration - offset)) or CHUNK_SECONDS
        for track, who in tracks:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                try:
                    _extract_chunk(rec, source, track, offset, seconds, tmp.name)
                    text = _hermes_transcribe_file(tmp.name)
                except Exception as exc:  # noqa: BLE001 - one bad chunk is not the end
                    text = f"[transcription error: {exc}]"
            if text:
                rows.append((offset, who, text))
        offset += seconds

    rows.sort(key=lambda row: row[0])
    with out_path.open("w") as fh:
        fh.write(f"# {pathlib.Path(source).name}\n\n"
                 f"{duration / 60:.1f} minutes, engine hermes, {len(rows)} chunks\n\n")
        for start, who, text in rows:
            fh.write(f"**[{int(start // 60):02d}:{int(start % 60):02d}] {who}:** {text}\n\n")
    return {"ok": True, "engine": ENGINE_HERMES, "path": str(out_path), "segments": len(rows)}


def transcribe_with_gpu(rec, source: str, out_path: pathlib.Path, python: str,
                        script: str) -> dict:
    """The recording machine's own Whisper run (per-line timing, fastest)."""
    code, out = rec.run(f"{python} {script} {source}", timeout=7200)
    if code != 0:
        return {"ok": False, "engine": ENGINE_GPU, "error": out.strip()[:300]}
    segments = 0
    try:
        with out_path.open() as fh:
            segments = sum(1 for line in fh if line.startswith("**["))
    except OSError:
        pass
    return {"ok": True, "engine": ENGINE_GPU, "path": str(out_path), "segments": segments}


def transcribe(rec, source: str, duration: float, *, gpu_python: str = "",
               gpu_script: str = "", prefer_gpu: bool = True) -> dict:
    """Run whichever engine is available and return the result dict."""
    source_path = pathlib.Path(source)
    out_path = source_path.with_suffix(".transcript.md")
    hermes = hermes_stt_configured()
    gpu = bool(prefer_gpu and gpu_python and gpu_script)
    engine = detect_engine(rec, gpu, hermes)
    if engine is None:
        return {"ok": False, "error": "no transcription engine available "
                                      "(no Whisper on the recording machine and Hermes "
                                      "speech-to-text is not configured)"}
    if engine == ENGINE_GPU:
        result = transcribe_with_gpu(rec, source, out_path, gpu_python, gpu_script)
        if result.get("ok"):
            return result
        if not hermes:
            return result
        # GPU path failed mid-way: fall through to the engine that needs no install.
    return transcribe_with_hermes(rec, source, duration, out_path)
