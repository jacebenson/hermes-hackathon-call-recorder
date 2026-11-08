"""Which capture backend this machine has, and what it still needs.

The recorder can only work where a program is allowed to read both sides of the
audio. Linux gives that away (PipeWire monitors). Windows and macOS do not: they
need a virtual audio driver, which no plugin can install for you, so the honest
job here is to detect the platform, detect the driver, and say precisely what is
missing before anything pretends to record.

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
    WINDOWS: ("Install a loopback driver: 'choco install vb-cable -y' in an administrator "
              "PowerShell (or the installer from vb-audio.com), then set the call's speaker to "
              "'CABLE Input' and turn on Listen on 'CABLE Output' so you can still hear it."),
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
        _code, out = win.run('& ffmpeg -hide_banner -f dshow -list_devices true -i dummy 2>&1 '
                             '| Out-String', timeout=40)
        devices = parse_dshow_devices(out)
        loopback = find_loopback(WINDOWS, devices)
        mic = _microphone(devices, loopback)
        if loopback and mic and is_weak_loopback(os_name, loopback):
            return {"state": "blocked", "os": os_name, "target": target, "backend": "dshow",
                    "device": loopback, "microphone": mic, "devices": devices,
                    "detail": f"only {loopback} was found, which works only while Steam runs",
                    "fix": FIXES[WINDOWS]}
        if loopback and mic:
            return {"state": "ready", "os": os_name, "target": target, "backend": "dshow",
                    "device": loopback, "microphone": mic, "devices": devices,
                    "detail": f"system audio via {loopback}"}
        if not loopback:
            return {"state": "blocked", "os": os_name, "target": target, "backend": "dshow",
                    "devices": devices, "fix": FIXES[WINDOWS],
                    "detail": "no virtual audio cable found, so system audio cannot be captured"}
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
