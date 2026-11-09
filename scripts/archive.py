#!/usr/bin/env python3
"""Full-quality pass over a two-track recording -> speaker-labelled markdown.

Each track is transcribed on its own (that is the whole point of recording them
separately), then the segments are merged by timestamp:

    **[12:34] them:** what they said
    **[12:37] you:** what you said

Usage: archive.py <recording.mkv>   ->  <recording>.transcript.md

Environment: WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE, WHISPER_LANG,
ARCHIVE_CHUNK_SEC (default 300).
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

import numpy as np
from faster_whisper import WhisperModel

MODEL_ID = os.environ.get("WHISPER_MODEL", "small.en")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
COMPUTE = os.environ.get("WHISPER_COMPUTE", "float16")
LANG = os.environ.get("WHISPER_LANG", "en")
CHUNK = float(os.environ.get("ARCHIVE_CHUNK_SEC", "300"))


def track_to_raw(src: pathlib.Path, index: int, dest: pathlib.Path) -> pathlib.Path:
    """16k mono s16le — numpy chunks, so no decoder surprises in faster-whisper."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-map", f"0:a:{index}",
                    "-ac", "1", "-ar", "16000", "-f", "s16le", str(dest)], check=True)
    return dest


def write_transcript(src: pathlib.Path, rows: list) -> None:
    """Publish a complete document, never a header awaiting its segment lines."""
    minutes = rows[-1][0] / 60 if rows else 0
    markdown = f"# {src.name}\n\n{minutes:.1f} minutes, {len(rows)} segments\n\n"
    for start, who, text in rows:
        markdown += f"**[{int(start // 60):02d}:{int(start % 60):02d}] {who}:** {text}\n\n"
    out = src.with_suffix(".transcript.md")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out.parent,
                                         prefix=out.name + ".", delete=False) as fh:
            temporary = pathlib.Path(fh.name)
            fh.write(markdown)
        os.replace(temporary, out)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = pathlib.Path(sys.argv[1]).expanduser()
    out = src.with_suffix(".transcript.md")
    work = src.parent / ".work"
    work.mkdir(exist_ok=True)

    model = WhisperModel(MODEL_ID, device=DEVICE, compute_type=COMPUTE)
    rows: list[tuple[float, str, str]] = []

    tracks = ((0, "voice mix"),) if os.environ.get('CALLREC_SINGLE_TRACK') == '1' else ((0, "them"), (1, "you"))
    for index, who in tracks:
        raw = track_to_raw(src, index, work / f"track{index}.s16")
        data = np.memmap(raw, dtype=np.int16, mode="r")
        step = int(CHUNK * 16000)
        offset = 0.0
        counted = 0
        for start in range(0, len(data), step):
            audio = np.asarray(data[start:start + step], dtype=np.float32) / 32768.0
            try:
                segments, _ = model.transcribe(audio, language=LANG, vad_filter=True,
                                               beam_size=5, condition_on_previous_text=False)
            except Exception as exc:  # noqa: BLE001 - one bad chunk must not lose the rest
                print(f"{who}: chunk at {offset:.0f}s failed: {exc}", flush=True)
                offset += len(audio) / 16000
                continue
            for segment in segments:
                text = segment.text.strip()
                if text:
                    rows.append((offset + segment.start, who, text))
                    counted += 1
            offset += len(audio) / 16000
        print(f"{who}: {counted} segments", flush=True)

    rows.sort(key=lambda row: row[0])
    write_transcript(src, rows)
    print(f"WROTE {out} ({len(rows)} segments)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
