"""Record system audio and a microphone on Windows with no driver installed.

WASAPI loopback is part of the OS: every output endpoint already has a loopback
twin, and no virtual cable, admin right or certificate is involved. This runs on
the machine being recorded, writes the two streams as wav, and lets ffmpeg mux
them into one two-track mkv (system, mic).

Two details decide whether the result is usable:

* loopback only delivers data while something is actually playing, so silence is
  written for the gaps. Without that the two tracks drift apart and every
  timestamp after the first quiet moment is wrong.
* the supervisor polls a stop file instead of being killed, so both wavs are
  closed cleanly.

usage: python wasapi_capture.py --out-dir DIR --stop-file FILE [--seconds N]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import queue
import subprocess
import sys
import threading
import time
import wave

import pyaudiowpatch as pyaudio

FORMAT = pyaudio.paInt16
CHUNK = 1024
SILENCE = b"\x00\x00"


class Track:
    """One capture stream plus the padding that keeps it on the wall clock."""

    def __init__(self, pa, index: int, rate: int, channels: int, label: str):
        self.label = label
        self.rate = rate
        self.channels = channels
        self.chunks: queue.Queue[bytes] = queue.Queue()
        self.chunks_buf: list[bytes] = []
        self.frames = 0
        self.stream = pa.open(
            format=FORMAT, channels=channels, rate=rate, input=True,
            input_device_index=index, frames_per_buffer=CHUNK,
            stream_callback=self._callback)

    def _callback(self, in_data, frame_count, time_info, status):
        self.chunks.put(in_data)
        return (None, pyaudio.paContinue)

    def drain(self, target_frames: int) -> None:
        """Append whatever arrived, then silence up to the expected position."""
        while True:
            try:
                data = self.chunks.get_nowait()
            except queue.Empty:
                break
            self.chunks_buf.append(data)
            self.frames += len(data) // (2 * self.channels)
        if target_frames > self.frames:
            missing = target_frames - self.frames
            filler = SILENCE * (missing * self.channels)
            self.chunks_buf.append(filler)
            self.frames += missing

    def close(self) -> None:
        try:
            self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass


def device_index(pa, kind: str) -> tuple[int, int, int, str]:
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    if kind == "loopback":
        default = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        for device in pa.get_loopback_device_info_generator():
            if default["name"] in device["name"]:
                return (device["index"], int(device["defaultSampleRate"]),
                        int(device["maxInputChannels"]), device["name"])
        raise RuntimeError("no loopback twin for " + default["name"])
    index = wasapi["defaultInputDevice"]
    device = pa.get_device_info_by_index(index)
    return (device["index"], int(device["defaultSampleRate"]),
            int(device["maxInputChannels"]), device["name"])


def write_wav(path: pathlib.Path, track: Track) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(track.channels)
        wav.setsampwidth(2)
        wav.setframerate(track.rate)
        wav.writeframes(b"".join(track.chunks_buf))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--status-file", default="")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    system_wav = out_dir / f"call-{stamp}.system.wav"
    mic_wav = out_dir / f"call-{stamp}.mic.wav"
    final = out_dir / f"call-{stamp}.mkv"
    status_path = pathlib.Path(args.status_file) if args.status_file else None

    pa = pyaudio.PyAudio()
    tracks = []
    try:
        try:
            loop_index, loop_rate, loop_ch, loop_name = device_index(pa, "loopback")
            mic_index, mic_rate, mic_ch, mic_name = device_index(pa, "mic")
            tracks.append(Track(pa, loop_index, loop_rate, loop_ch, "system"))
            tracks.append(Track(pa, mic_index, mic_rate, mic_ch, "mic"))
        except Exception as exc:
            print(f"cannot open the audio devices: {exc}", flush=True)
            return 2

        if status_path:
            status_path.write_text(json.dumps({
                "started": time.time(), "name": f"call-{stamp}",
                "system": loop_name, "mic": mic_name,
                "rates": [t.rate for t in tracks]}, indent=1))

        started = time.time()
        while True:
            if pathlib.Path(args.stop_file).exists():
                break
            elapsed = time.time() - started
            if args.seconds and elapsed >= args.seconds:
                break
            time.sleep(0.5)
            for track in tracks:
                track.drain(int(elapsed * track.rate))

        for track in tracks:
            track.drain(int((time.time() - started) * track.rate))
    finally:
        for track in tracks:
            track.close()
        pa.terminate()

    write_wav(system_wav, tracks[0])
    write_wav(mic_wav, tracks[1])
    mux = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(system_wav), "-i", str(mic_wav),
        "-map", "0:a", "-map", "1:a", "-c:a", "libopus", "-b:a", "96k",
        "-metadata:s:a:0", "title=system", "-metadata:s:a:1", "title=mic",
        str(final)]
    done = subprocess.run(mux, capture_output=True, text=True)
    if done.returncode != 0:
        print("mux failed: " + (done.stderr or "").strip()[:300], flush=True)
        return 3
    system_wav.unlink(missing_ok=True)
    mic_wav.unlink(missing_ok=True)
    pathlib.Path(args.stop_file).unlink(missing_ok=True)
    print(json.dumps({"file": str(final), "seconds": round(time.time() - started, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
