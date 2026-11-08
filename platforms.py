"""Which capture backend this machine has, and what it still needs.

The recorder can only work where a program is allowed to read both sides of the
audio. Linux gives that away with PipeWire monitors, and Windows does too through
WASAPI loopback, which is part of the OS and needs no driver. The one platform that
does still need a virtual driver is macOS, and the honest job here is to detect the
platform, detect what is missing, and say so before anything pretends to record.

States returned by :func:`readiness`:

* ``ready``      — capture can start now
* ``blocked``    — this platform can work, but something must be installed
* ``unsupported``— detected, but this plugin has no capture path yet
"""

from __future__ import annotations

import re

LINUX, WINDOWS, MACOS, UNKNOWN = "linux", "windows", "macos", "unknown"

#: Virtual devices that mean "system audio can be captured here", by platform.
#: Strong ones are dedicated cables; weak ones work but depend on another app running.
LOOPBACK_STRONG = {
    WINDOWS: ("cable output", "voicemeeter", "virtual audio cable"),
    MACOS: ("blackhole", "soundflower", "loopback audio", "existential audio"),
}
LOOPBACK_WEAK = {
    WINDOWS: ("steam streaming microphone",),
    MACOS: (),
}
LOOPBACK_HINTS = {**{k: v + LOOPBACK_WEAK.get(k, ()) for k, v in LOOPBACK_STRONG.items()},
                  **{k: v for k, v in LOOPBACK_WEAK.items() if k not in LOOPBACK_STRONG}}

FIXES = {
    WINDOWS: ("Run 'python -m pip install pyaudiowpatch' on the machine being recorded. "
              "Windows already has WASAPI loopback for every output device, so no virtual "
              "cable, driver or admin rights are needed - and someone has to be logged in, "
              "because a background session has no audio to capture."),
    MACOS: ("Install BlackHole 2ch and set the call's output to 'BlackHole 2ch'."),
}


def detect_os(recorder_cls, host: str | None = None) -> str:
    """Linux, Windows, macOS or unknown — asked of the machine itself."""
    posix = recorder_cls(host=host, os_name=LINUX)
    _code, out = posix.run("uname -s 2>/dev/null || echo unknown")
    lowered = out.lower()
    if "linux" in lowered:
        return LINUX
    if "darwin" in lowered:
        return MACOS
    windows = recorder_cls(host=host, os_name=WINDOWS)
    code, out = windows.run("Write-Output $env:OS")
    if code == 0 and "windows" in out.lower():
        return WINDOWS
    return UNKNOWN


def parse_dshow_devices(output: str) -> list[str]:
    """Audio device names from `ffmpeg -f dshow -list_devices true`."""
    names = []
    for line in output.splitlines():
        match = re.search(r'"([^"]+)"\s+\(audio\)', line)
        if match:
            names.append(match.group(1))
    return names


def parse_avfoundation_devices(output: str) -> list[str]:
    """Audio device names from `ffmpeg -f avfoundation -list_devices true`."""
    names = []
    for line in output.splitlines():
        match = re.search(r"\[(\d+)\]\s+(.+)$", line.strip())
        if match and "audio" in line.lower():
            names.append(match.group(2).strip())
    return names


def find_loopback(os_name: str, devices: list[str]) -> str | None:
    """The virtual cable among the capture devices, preferring a dedicated one."""
    for hints in (LOOPBACK_STRONG.get(os_name, ()), LOOPBACK_WEAK.get(os_name, ())):
        for device in devices:
            lowered = device.lower()
            if any(hint in lowered for hint in hints):
                return device
    return None


def is_weak_loopback(os_name: str, device: str | None) -> bool:
    """True for a cable that only exists while some other app is running (Steam)."""
    if not device:
        return False
    lowered = device.lower()
    return any(hint in lowered for hint in LOOPBACK_WEAK.get(os_name, ()))


def _microphone(devices: list[str], loopback: str | None) -> str | None:
    """A real microphone: anything that is not the cable and not a known virtual device."""
    for device in devices:
        lowered = device.lower()
        if loopback and device == loopback:
            continue
        if any(hint in lowered for hints in LOOPBACK_HINTS.values() for hint in hints):
            continue
        return device
    return None


def _wasapi_ready(rec) -> dict:
    """Windows needs no driver for this: python plus one pip package."""
    code, _out = rec.run('python -c "import pyaudiowpatch"', timeout=40)
    if code != 0:
        return {"ok": False,
                "fix": "python -m pip install pyaudiowpatch"}
    return {"ok": True}


def readiness(recorder_cls, host: str | None = None) -> dict:
    """Can this machine record right now, and if not, what is missing."""
    os_name = detect_os(recorder_cls, host)
    target = host or "local"

    if os_name == LINUX:
        info = recorder_cls(host=host, os_name=LINUX)
        code, out = info.run("pactl get-default-sink 2>/dev/null; "
                             "command -v ffmpeg >/dev/null && echo FFMPEG=1; "
                             "systemctl --user is-system-running 2>/dev/null | sed 's/^/SYSTEMD=/'")
        sink = out.strip().splitlines()[0].strip() if out.strip() else ""
        if "FFMPEG=1" in out and sink:
            return {"state": "ready", "os": os_name, "target": target, "backend": "pipewire",
                    "device": sink, "detail": "PipeWire monitor + default microphone"}
        return {"state": "blocked", "os": os_name, "target": target, "backend": "pipewire",
                "detail": "PipeWire with pactl, ffmpeg and a user session is required",
                "fix": "Start the graphical session on that machine, or record a different host."}

    if os_name == WINDOWS:
        win = recorder_cls(host=host, os_name=WINDOWS)
        # WASAPI loopback first: no driver, no admin, nothing to trust.
        wasapi = _wasapi_ready(win)
        if wasapi.get("ok"):
            return {"state": "ready", "os": os_name, "target": target, "backend": "wasapi",
                    "device": "default output (WASAPI loopback)",
                    "detail": "WASAPI loopback plus the default microphone, no driver needed"}
        _code, out = win.run('& ffmpeg -hide_banner -f dshow -list_devices true -i dummy 2>&1 '
                             '| Out-String', timeout=40)
        devices = parse_dshow_devices(out)
        loopback = find_loopback(WINDOWS, devices)
        mic = _microphone(devices, loopback)
        if loopback and mic and is_weak_loopback(os_name, loopback):
            return {"state": "blocked", "os": os_name, "target": target, "backend": "dshow",
                    "device": loopback, "microphone": mic, "devices": devices,
                    "detail": f"only {loopback} was found, which works only while Steam runs; WASAPI capture needs pyaudiowpatch",
                    "fix": FIXES[WINDOWS]}
        if loopback and mic:
            return {"state": "ready", "os": os_name, "target": target, "backend": "dshow",
                    "device": loopback, "microphone": mic, "devices": devices,
                    "detail": f"system audio via {loopback}"}
        if not loopback:
            return {"state": "blocked", "os": os_name, "target": target, "backend": "dshow",
                    "devices": devices, "fix": FIXES[WINDOWS],
                    "detail": "no loopback device and no WASAPI capture available"}
        return {"state": "blocked", "os": os_name, "target": target, "backend": "dshow",
                "devices": devices, "fix": "Plug in a microphone (the cable is not a mic).",
                "detail": "a cable was found but no real microphone"}

    if os_name == MACOS:
        return {"state": "unsupported", "os": os_name, "target": target, "backend": "avfoundation",
                "fix": FIXES[MACOS],
                "detail": "macOS is detected but its capture path is not implemented yet - "
                          "please report what you see on GitHub"}

    return {"state": "unsupported", "os": os_name, "target": target,
            "detail": "this machine did not answer as Linux, Windows or macOS"}


def diagnose(recorder_cls, host: str | None = None, settings: dict | None = None) -> dict:
    """Fresh, ordered checks. Failures are data, including transport exceptions."""
    host = (host or "").strip() or None
    cfg = settings or {}
    steps = []
    os_name = UNKNOWN

    def check(key, action):
        try:
            ok, message = action()
        except Exception as exc:
            ok, message = False, f"Could not check {key}: {str(exc)[:160]}"
        steps.append({"id": key, "status": "ok" if ok else "failed", "message": message})
        return ok

    def reachable():
        # This command works in both POSIX shells and the Windows SSH default shell.
        # Do not require bash or PowerShell before we know the target OS.
        import subprocess
        import platform
        if host:
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                    "echo callrec-connected"]
            done = subprocess.run(argv, capture_output=True, text=True, timeout=12)
            ok = done.returncode == 0 and "callrec-connected" in done.stdout
            return ok, (f"SSH reaches {host} without a password." if ok else
                        f"SSH could not reach {host} without a password. Check the host and SSH key.")
        rec = recorder_cls(os_name=WINDOWS if platform.system() == "Windows" else LINUX)
        code, out = rec.run("echo callrec-connected", timeout=12)
        ok = code == 0 and "callrec-connected" in out
        return ok, ("Local commands work on the machine Hermes runs on. SSH is not needed."
                    if ok else "Could not run commands on the local machine.")

    connected = check("ssh", reachable)

    rec = None

    def operating_system():
        nonlocal os_name, rec
        os_name = detect_os(recorder_cls, host)
        rec = recorder_cls(host=host, os_name=os_name)
        return os_name != UNKNOWN, (f"Detected {os_name}." if os_name != UNKNOWN else
                                    "Could not detect the operating system.")

    if connected:
        check("os", operating_system)
    else:
        check("os", lambda: (False, "Cannot detect the OS until the connection works."))

    def audio():
        if os_name == LINUX:
            code, sink = rec.run("pactl get-default-sink 2>/dev/null")
            mic_code, mic = rec.run("pactl get-default-source 2>/dev/null")
            sources_code, sources = rec.run("pactl list short sources 2>/dev/null")
            names = [line.split()[1] for line in sources.splitlines() if len(line.split()) > 1]
            monitor = sink.strip() + ".monitor"
            ok = (code == mic_code == sources_code == 0 and monitor in names
                  and mic.strip() in names and not mic.strip().endswith(".monitor"))
            return ok, (f"PipeWire sink monitor {monitor} and microphone {mic.strip()} are present."
                        if ok else "PipeWire sink monitor or microphone is missing. Check the audio session and default devices.")
        if os_name == WINDOWS:
            from capture_windows_wasapi import interactive_session
            session = interactive_session(rec)
            if not session.get("ok"):
                return False, "Nobody is logged in with an active session. Log in on the recording machine to expose WASAPI audio."
            from diagnose_windows import audio_devices
            return audio_devices(rec)
        return False, f"Audio capture is not supported on {os_name}."

    def ffmpeg():
        command = "(Get-Command ffmpeg -ErrorAction Stop).Source" if os_name == WINDOWS else "command -v ffmpeg"
        code, out = rec.run(command)
        ok = code == 0 and bool(out.strip())
        return ok, f"ffmpeg is at {out.strip()}." if ok else "ffmpeg is missing on the recording machine."

    def whisper():
        import shlex
        if os_name == WINDOWS:
            from capture_windows_wasapi import _q
            python = cfg.get("python") or "python"
            code, out = rec.run(f"& {_q(python)} -c \"import faster_whisper; print(faster_whisper.__file__)\"", timeout=30)
        elif cfg.get("python"):
            code, out = rec.run(f"{shlex.quote(cfg['python'])} -c 'import faster_whisper; print(faster_whisper.__file__)'", timeout=30)
        else:
            from recorder import TRANSCRIPTION_PROBE
            code, raw = rec.run(TRANSCRIPTION_PROBE, timeout=30)
            out = next((line[3:] for line in raw.splitlines() if line.startswith("PY=")), "")
        ok = code == 0 and bool(out.strip())
        return ok, f"Whisper is at {out.strip()}." if ok else "Whisper is missing on the recording machine. Recording can work without transcription."

    for key, action in [("audio", audio), ("ffmpeg", ffmpeg), ("whisper", whisper)]:
        if connected and os_name != UNKNOWN:
            check(key, action)
        else:
            check(key, lambda: (False, "Cannot check this until the connection and OS checks pass."))
    return {"ok": all(s["status"] == "ok" for s in steps), "target": host or "local",
            "os": os_name, "steps": steps}
