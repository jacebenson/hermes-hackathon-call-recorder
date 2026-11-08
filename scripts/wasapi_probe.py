"""WASAPI loopback capture: system audio with no driver and no admin rights.

Windows has no monitor source in the ffmpeg sense, but it does have WASAPI
loopback, which every audio app may use. That is the same API OBS uses, it is
part of the OS, and it needs no virtual cable.
"""
import sys
import wave

import pyaudiowpatch as pyaudio


def loopback_device(pa):
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    default = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if default.get("isLoopbackDevice"):
        return default
    for candidate in pa.get_loopback_device_info_generator():
        if default["name"] in candidate["name"]:
            return candidate
    raise RuntimeError("no loopback device for " + default["name"])


def record(seconds, out_path, with_mic=False):
    pa = pyaudio.PyAudio()
    try:
        loop = loopback_device(pa)
        rate = int(loop["defaultSampleRate"])
        channels = int(loop["maxInputChannels"])
        stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                         frames_per_buffer=1024, input=True,
                         input_device_index=loop["index"])
        frames = []
        for _ in range(int(rate / 1024 * seconds)):
            frames.append(stream.read(1024, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
        with wave.open(out_path, "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(b"".join(frames))
        print(f"loopback {loop['name']} -> {out_path} ({seconds}s, {rate}Hz, {channels}ch)")
    finally:
        pa.terminate()


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    out = sys.argv[2] if len(sys.argv) > 2 else "loopback.wav"
    record(seconds, out)
