"""Windows capture: two DirectShow devices into one two-track mkv.

Only used when the machine has a loopback recording device (a virtual cable) and
WASAPI capture is unavailable; capture_windows_wasapi.py is the default path.

Two things are different from Linux and both matter:

* there is no systemd, so a recording is a hidden PowerShell host process;
* Windows buffers matroska writes, so killing ffmpeg loses the whole file. The
  host therefore owns ffmpeg's stdin and sends it `q` on request, which finishes
  the file properly. (Killing it after 20 seconds produced a 0-byte file; a
  graceful quit produced a valid 12-second one.)
"""

from __future__ import annotations

import base64
import time

HOST_SCRIPT = r"""param([string]$Out,[string]$Cable,[string]$Mic,[string]$StopFile)
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = 'ffmpeg'
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardInput = $true
$psi.RedirectStandardError = $true
$psi.Arguments = '-y -hide_banner -loglevel warning -f dshow -i ' + $Cable +
                 ' -f dshow -i ' + $Mic +
                 ' -map 0:a -map 1:a -c:a libopus -b:a 96k "' + $Out + '"'
$p = [System.Diagnostics.Process]::Start($psi)
Set-Content -Path ($Out + '.pid') -Value $p.Id
while (-not (Test-Path $StopFile)) {
  Start-Sleep -Milliseconds 500
  if ($p.HasExited) { break }
}
if (-not $p.HasExited) {
  $p.StandardInput.WriteLine('q')
  if (-not $p.WaitForExit(20000)) { $p.Kill() }
}
Remove-Item -Force $StopFile -ErrorAction SilentlyContinue
"""


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def resolved_dir(rec) -> str:
    """The literal path for `~/recordings`, resolved on the target machine."""
    directory = (rec.recordings_dir or "~/recordings").replace("/", "\\")
    if not directory.startswith("~"):
        return directory
    _code, out = rec.run("Write-Output $env:USERPROFILE")
    home = out.strip().splitlines()[0].strip() if out.strip() else ""
    rest = directory[1:].lstrip("\\")
    return (home + "\\" + rest).rstrip("\\") if rest else home


def write_host_script(rec) -> str:
    """Install the ffmpeg supervisor once per machine and return its path."""
    _code, out = rec.run("Write-Output $env:USERPROFILE")
    home = out.strip().splitlines()[0].strip() if out.strip() else ""
    directory = home + "\\.call-recorder"
    path = directory + "\\host.ps1"
    payload = base64.b64encode(HOST_SCRIPT.encode("utf-16-le")).decode()
    rec.run(f"New-Item -ItemType Directory -Force -Path {_q(directory)} | Out-Null")
    rec.run(f"$b=[Convert]::FromBase64String({_q(payload)}); "
            f"[IO.File]::WriteAllBytes({_q(path)}, $b)")
    return path


def _ready(rec) -> dict:
    from platforms import readiness

    return readiness(type(rec), rec.host)


def start(rec, live: bool = False) -> dict:
    """Record the virtual cable's microphone side plus a real microphone."""
    info = _ready(rec)
    if info.get("state") != "ready":
        return {"ok": False, "ready": False,
                "error": info.get("detail", "this machine cannot capture audio"),
                "fix": info.get("fix"), "readiness": info}

    cable, microphone = info["device"], info["microphone"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = resolved_dir(rec)
    out_file = f"{directory}\\call-{stamp}.mkv"
    stop_file = out_file + ".stop"
    host = write_host_script(rec)

    args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", host,
            "-Out", out_file, "-Cable", f'audio="{cable}"', "-Mic", f'audio="{microphone}"',
            "-StopFile", stop_file]
    quoted = ",".join(_q(arg) for arg in args)
    rec.run("New-Item -ItemType Directory -Force -Path "
            f"{_q(directory)} | Out-Null; Remove-Item -Force {_q(stop_file)} "
            "-ErrorAction SilentlyContinue")
    _code, launch = rec.run(f"Start-Process -FilePath 'powershell' -ArgumentList {quoted} "
                            "-WindowStyle Hidden")
    time.sleep(4)

    pid = ""
    _code, out = rec.run(f"Get-Content {_q(out_file + '.pid')} -ErrorAction SilentlyContinue")
    for line in out.splitlines():
        if line.strip().isdigit():
            pid = line.strip()
            break
    if not pid:
        return {"ok": False, "ready": True, "error": "ffmpeg did not start on that machine",
                "detail": launch.strip()[:300]}

    rec.save_state({"os": "windows", "pid": pid, "file": out_file, "stop_file": stop_file,
                    "host_script": host, "started": time.time(),
                    "device": cable, "microphone": microphone, "live": False, "unit": None})
    return {"ok": True, "ready": True, "recording": True, "file": out_file, "pid": pid,
            "device": cable, "microphone": microphone}


def status(rec) -> dict:
    state = rec.state()
    pid = state.get("pid")
    alive = False
    if pid:
        code, out = rec.run(f"Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue | "
                            "Select-Object -ExpandProperty Id")
        alive = code == 0 and str(pid) in out
    size = 0
    path = state.get("file")
    if path:
        # Directory metadata for an open file reads 0 on Windows; the file is fine.
        _code, out = rec.run(f"(Get-Item {_q(path)} -ErrorAction SilentlyContinue).Length")
        try:
            size = int(out.strip().splitlines()[0])
        except (ValueError, IndexError):
            size = 0
    return {"recording": alive, "live": False, "host": rec.host or "local", "os": "windows",
            "unit": None, "file": path, "bytes": size, "pid": pid,
            "elapsed": int(time.time() - state["started"]) if alive and state.get("started") else 0,
            "monitor": state.get("device"), "mic": state.get("microphone"),
            "auto_start": False, "auto_stop_seconds": 0, "pending_stop": 0}


def stop(rec) -> dict:
    state = rec.state()
    result = {"ok": True, "recording": False, "file": state.get("file")}
    stop_file = state.get("stop_file")
    if stop_file:
        rec.run(f"New-Item -ItemType File -Force -Path {_q(stop_file)} | Out-Null")
        # ffmpeg flushes and finalises on 'q'; give it time to write the index.
        for _ in range(30):
            time.sleep(1)
            code, out = rec.run(f"Get-Process -Id {int(state['pid'])} -ErrorAction SilentlyContinue "
                                "| Select-Object -ExpandProperty Id") if state.get("pid") else (1, "")
            if code != 0 or not out.strip():
                break
    path = state.get("file")
    if path:
        _code, duration = rec.run(
            '& ffprobe -v error -show_entries format=duration -of csv=p=0 ' + _q(path),
            timeout=120)
        from recorder import parse_duration
        result["duration"] = parse_duration(duration)
        _code, size = rec.run(f"(Get-Item {_q(path)} -ErrorAction SilentlyContinue).Length")
        try:
            result["bytes"] = int(size.strip().splitlines()[0])
        except (ValueError, IndexError):
            result["bytes"] = 0
    rec.save_state({**state, "pid": None, "stopped": time.time()})
    return result
