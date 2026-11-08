#!/usr/bin/env python3
"""Live transcription of two PipeWire sources -> live.txt.

One chunked faster-whisper pass per source, so the two speakers stay separated:

    [19:47:09] them> the call audio, from the sink monitor
    [19:47:10] you>  your mic

Environment:
  MON_SRC, MIC_SRC   pactl source names (default: auto-detected as "what the call
                     app plays into" and "the default source")
  LIVE_OUT           transcript path (default ~/recordings/live.txt)
  WHISPER_MODEL      model name on the Hub, or a local ct2 directory (default small.en)
  WHISPER_DEVICE     cuda | cpu (default cuda)
  WHISPER_COMPUTE    float16 | int8 (default float16)
  CHUNK_SEC          seconds per chunk (default 6)
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time

import numpy as np
from faster_whisper import WhisperModel

RATE = 16000
OUT = os.path.expanduser(os.environ.get("LIVE_OUT", "~/recordings/live.txt"))
MODEL_ID = os.environ.get("WHISPER_MODEL", "small.en")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
COMPUTE = os.environ.get("WHISPER_COMPUTE", "float16")
CPU_THREADS = int(os.environ.get("WHISPER_THREADS", "8"))
CHUNK = float(os.environ.get("CHUNK_SEC", "6"))
CALL_APPS = ("chromium", "chrome", "brave", "firefox", "vivaldi", "zoom",
             "teams", "slack", "discord", "webex", "signal", "telegram")

lock = threading.Lock()
queue_in = queue.Queue()


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_sources() -> tuple[str, str]:
    """The sink the call app plays into, and the default mic."""
    sinks = {}
    for line in _run(["pactl", "list", "short", "sinks"]).splitlines():
        parts = line.split("\t")
        if len(parts) > 1:
            sinks[parts[0]] = parts[1]
    monitor = ""
    for block in _run(["pactl", "list", "sink-inputs"]).split("Sink Input #")[1:]:
        app = re.search(r'application\.name = "([^"]+)"', block)
        sink = re.search(r"\n\tSink: (\d+)", block)
        if app and sink and any(a in app.group(1).lower() for a in CALL_APPS):
            monitor = f"{sinks.get(sink.group(1), '')}.monitor"
            break
    if not monitor:
        monitor = _run(["pactl", "get-default-sink"]).strip() + ".monitor"
    mic = _run(["pactl", "get-default-source"]).strip()
    return monitor, mic


def reader(name: str, source: str) -> None:
    """Collect fixed-size chunks; `parec` hands back partial reads, so accumulate."""
    cmd = ["parec", f"--device={source}", "--format=s16le", f"--rate={RATE}",
           "--channels=1", "--raw", "--latency-msec=100"]
    need = int(RATE * 2 * CHUNK)
    while True:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            stream = proc.stdout
            if stream is None:
                break
            read = getattr(stream, "read1", None) or stream.read
            buf = b""
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= need:
                    take, buf = buf[:need], buf[need:]
                    queue_in.put((name, np.frombuffer(take, dtype=np.int16).astype(np.float32) / 32768.0))
            print(f"[{name}] capture ended, restarting", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep the other source alive
            print(f"[{name}] capture error: {exc}", flush=True)
        time.sleep(1)


def writer(model: WhisperModel) -> None:
    while True:
        name, audio = queue_in.get()
        try:
            segments, _ = model.transcribe(audio, language=os.environ.get("WHISPER_LANG", "en"),
                                           vad_filter=True, beam_size=1,
                                           condition_on_previous_text=False)
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:  # noqa: BLE001
            text = f"[error {exc}]"
        if not text:
            continue
        line = f"[{time.strftime('%H:%M:%S')}] {name}> {text}"
        with lock:
            with open(OUT, "a", buffering=1) as fh:
                fh.write(line + "\n")
        print(line, flush=True)


def main() -> None:
    mon = os.environ.get("MON_SRC")
    mic = os.environ.get("MIC_SRC")
    if not (mon and mic):
        detected_mon, detected_mic = detect_sources()
        mon = mon or detected_mon
        mic = mic or detected_mic
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    print(f"loading {MODEL_ID} on {DEVICE}…", flush=True)
    model = WhisperModel(MODEL_ID, device=DEVICE, compute_type=COMPUTE, cpu_threads=CPU_THREADS)
    print(f"model ready: them={mon} you={mic} -> {OUT}", flush=True)
    with open(OUT, "a", buffering=1) as fh:
        fh.write(f"\n=== live started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    threading.Thread(target=writer, args=(model,), daemon=True).start()
    threading.Thread(target=reader, args=("them", mon), daemon=True).start()
    threading.Thread(target=reader, args=("you", mic), daemon=True).start()

    # Keep the capture and transcription daemon threads alive.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
