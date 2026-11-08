"""Windows capture that installs nothing: WASAPI loopback plus the microphone.

The ffmpeg/dshow route needs a virtual cable the user has to trust and install.
Windows already exposes every output endpoint as a loopback device through WASAPI,
so this records the system audio and the microphone in one Python process with no
driver, no admin rights and no certificate prompt.

The helper runs on the machine being recorded; this module ships it there, starts
it hidden, watches its status file, and stops it with a file rather than a signal
so both streams finish cleanly.

Prerequisite on the target: `pip install pyaudiowpatch` (pure pip, no admin).
"""

from __future__ import annotations

import base64
import json
import pathlib
import time

SCRIPT_NAME = "wasapi_capture.py"


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _home(rec) -> str:
    _code, out = rec.run("Write-Output $env:USERPROFILE")
    return out.strip().splitlines()[0].strip() if out.strip() else ""


def script_path(rec) -> str:
    return _home(rec) + "\\.call-recorder\\" + SCRIPT_NAME


def install(rec) -> dict:
    """Copy the helper to the target machine, byte for byte."""
    local = pathlib.Path(__file__).parent / "scripts" / SCRIPT_NAME
    if not local.exists():
        return {"ok": False, "error": f"{SCRIPT_NAME} is missing from this install"}
    dest = script_path(rec)
    directory = _home(rec) + "\\.call-recorder"
    staging = directory + "\\" + SCRIPT_NAME + ".b64"
    payload = base64.b64encode(local.read_bytes()).decode()
    rec.run(f"New-Item -ItemType Directory -Force -Path {_q(directory)} | Out-Null; "
            f"Remove-Item -Force {_q(staging)} -ErrorAction SilentlyContinue")
    # A cmd.exe command line stops at 8191 characters, so ship the payload in pieces.
    # One big write truncates silently, which looks exactly like a missing file later.
    for offset in range(0, len(payload), 1500):
        piece = _q(payload[offset:offset + 1500])
        rec.run(f"Add-Content -Path {_q(staging)} -Value {piece} -NoNewline")
    rec.run(f"$b=[Convert]::FromBase64String((Get-Content {_q(staging)} -Raw)); "
            f"[IO.File]::WriteAllBytes({_q(dest)}, $b); Remove-Item -Force {_q(staging)}")
    _code, check = rec.run(f"Test-Path {_q(dest)}")
    if "True" not in check:
        return {"ok": False, "error": "the capture helper could not be written to that machine"}
    return {"ok": True, "script": dest}


def python_ready(rec) -> dict:
    """Python plus pyaudiowpatch: two checks, both cheap, both honest."""
    code, out = rec.run("python -c \"import sys; print(sys.version_info[0])\"")
    if code != 0:
        return {"ok": False, "error": "no python on that machine",
                "fix": "Install Python 3 from python.org (no admin needed) and run it once."}
    code, out = rec.run("python -c \"import pyaudiowpatch; print('ok')\"")
    if code != 0 or "ok" not in out:
        return {"ok": False, "error": "pyaudiowpatch is not installed",
                "fix": "python -m pip install pyaudiowpatch"}
    return {"ok": True}


def available(rec) -> bool:
    return bool(python_ready(rec).get("ok"))


def _dirs(rec) -> tuple[str, str, str, str]:
    from capture_windows import resolved_dir

    out_dir = resolved_dir(rec)
    return (out_dir, out_dir + "\\call-recorder.stop", out_dir + "\\call-recorder.status.json",
            out_dir + "\\call-recorder.log")


def interactive_session(rec) -> dict:
    """Is anyone logged in with a desktop? Windows has no audio without one."""
    code, out = rec.run("quser")
    text = (out or "").lower()
    if "active" in text:
        return {"ok": True}
    return {"ok": False,
            "error": "nobody is logged in on that machine",
            "fix": "Log in on that PC (the console or a remote desktop session); "
                   "Windows exposes no audio to capture without a live session."}


TASK_NAME = "call-recorder-capture"

LAUNCH_PS1 = r"""param([string]$Script,[string]$OutDir,[string]$StopFile,[string]$StatusFile)
$python = (Get-Command python).Source
$argline = '-u "' + $Script + '" --out-dir "' + $OutDir + '" --stop-file "' + $StopFile + '" --status-file "' + $StatusFile + '"'
$action = New-ScheduledTaskAction -Execute $python -Argument $argline
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'call-recorder-capture' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'call-recorder-capture'
$state = (Get-ScheduledTask -TaskName 'call-recorder-capture').State
Write-Output ("scheduled=" + $state)
"""


def _write_text(rec, dest: str, text: str) -> dict:
    """Write a text file on the target in pieces the command line can carry."""
    directory = dest.rsplit("\\", 1)[0]
    staging = dest + ".b64"
    payload = base64.b64encode(text.encode("utf-8-sig")).decode()
    rec.run(f"New-Item -ItemType Directory -Force -Path {_q(directory)} | Out-Null; "
            f"Remove-Item -Force {_q(staging)} -ErrorAction SilentlyContinue")
    for offset in range(0, len(payload), 1500):
        rec.run(f"Add-Content -Path {_q(staging)} -Value {_q(payload[offset:offset + 1500])} -NoNewline")
    rec.run(f"$b=[Convert]::FromBase64String((Get-Content {_q(staging)} -Raw)); "
            f"[IO.File]::WriteAllBytes({_q(dest)}, $b); Remove-Item -Force {_q(staging)}")
    _code, check = rec.run(f"Test-Path {_q(dest)}")
    return {"ok": "True" in check, "path": dest}


def _launch_in_session(rec, script: str, out_dir: str, stop_file: str,
                       status_file: str, log_file: str) -> dict:
    """Start the recorder inside the logged-in desktop, not in the SSH session.

    An SSH child runs in session 0, which owns no audio endpoint: the capture opens
    fine and records pure silence. A scheduled task with an Interactive principal
    runs in the user's own session, where the speakers and microphone live.
    """
    session = interactive_session(rec)
    if not session.get("ok"):
        return session

    helper = _home(rec) + "\\.call-recorder\\start-capture.ps1"
    written = _write_text(rec, helper, LAUNCH_PS1)
    if not written.get("ok"):
        return {"ok": False, "error": "could not write the launcher to that machine"}

    command = (f"powershell -NoProfile -ExecutionPolicy Bypass -File {_q(helper)} "
               f"-Script {_q(script)} -OutDir {_q(out_dir)} -StopFile {_q(stop_file)} "
               f"-StatusFile {_q(status_file)}")
    code, out = rec.run(command, timeout=120)
    text = (out or "").strip()
    if "scheduled=" not in text:
        return {"ok": False, "error": "the scheduled task did not start",
                "detail": text[:300]}
    return {"ok": True, "task": TASK_NAME, "state": text.split("scheduled=")[-1].strip()}


def start(rec, live: bool = False) -> dict:
    from platforms import readiness

    info = readiness(type(rec), rec.host)
    if info.get("state") == "blocked" and info.get("backend") != "wasapi":
        # dshow-style blocked (a cable problem). WASAPI may still be fine.
        check = python_ready(rec)
        if not check.get("ok"):
            return {"ok": False, "ready": False, "error": check["error"],
                    "fix": check.get("fix"), "readiness": info}
    session = interactive_session(rec)
    if not session.get("ok"):
        # Nothing is installed yet: Windows cannot give this machine audio, so say why.
        return {"ok": False, "ready": True, "error": session["error"], "fix": session.get("fix")}
    installed = install(rec)
    if not installed.get("ok"):
        return {"ok": False, "error": installed["error"]}

    out_dir, stop_file, status_file, log_file = _dirs(rec)
    rec.run(f"New-Item -ItemType Directory -Force -Path {_q(out_dir)} | Out-Null; "
            f"Remove-Item -Force {_q(stop_file)},{_q(status_file)} -ErrorAction SilentlyContinue")
    launched = _launch_in_session(rec, installed["script"], out_dir, stop_file, status_file, log_file)
    if not launched.get("ok"):
        return {"ok": False, "ready": True, "error": launched.get("error", "could not start recording"),
                "detail": launched.get("detail", ""), "fix": launched.get("fix")}

    for _ in range(24):
        time.sleep(0.5)
        _code, raw = rec.run(f"Get-Content {_q(status_file)} -Raw -ErrorAction SilentlyContinue")
        # ssh prints its own warnings on stdout, so find the json rather than assume line 1.
        body = "\n".join(line for line in (raw or "").splitlines() if line.strip().startswith("{"))
        if body:
            try:
                info = json.loads(body[body.index("{"):])
            except ValueError:
                continue
            state = {"os": "windows-wasapi", "out_dir": out_dir, "stop_file": stop_file,
                     "status_file": status_file, "name": info.get("name") or "",
                     "system": info.get("system"), "mic": info.get("mic"),
                     "started": info.get("started") or time.time(), "live": False, "unit": None}
            state["file"] = out_dir + "\\" + (state["name"] or "") + ".mkv"
            rec.save_state(state)
            return {"ok": True, "ready": True, "recording": True, "file": state["file"],
                    "device": state["system"], "microphone": state["mic"],
                    "backend": "wasapi"}

    _code, log = rec.run(f"Get-Content {_q(log_file + '.err')} -Tail 5 -ErrorAction SilentlyContinue")
    return {"ok": False, "ready": True,
            "error": "the capture helper did not report ready",
            "detail": (log or "").strip()[:300]}


def status(rec) -> dict:
    state = rec.state()
    if state.get("os") != "windows-wasapi":
        return {"recording": False, "live": False, "host": rec.host or "local",
                "os": "windows", "unit": None, "file": None, "bytes": 0, "elapsed": 0,
                "monitor": None, "mic": None, "auto_start": False,
                "auto_stop_seconds": 0, "pending_stop": 0}
    stop_file = state.get("stop_file")
    _code, present = rec.run(f"Test-Path {_q(stop_file)}")
    recording = "False" in present or "false" in present
    elapsed = int(time.time() - state["started"]) if recording and state.get("started") else 0
    size = 0
    path = state.get("file")
    if path:
        _code, out = rec.run(f"(Get-Item {_q(path)} -ErrorAction SilentlyContinue).Length")
        try:
            size = int(out.strip().splitlines()[0])
        except (ValueError, IndexError):
            size = 0
    return {"recording": recording, "live": False, "host": rec.host or "local",
            "os": "windows", "unit": None, "file": path, "bytes": size,
            "elapsed": elapsed, "monitor": state.get("system"), "mic": state.get("mic"),
            "auto_start": False, "auto_stop_seconds": 0, "pending_stop": 0}


def stop(rec) -> dict:
    state = rec.state()
    path = state.get("file")
    result = {"ok": True, "recording": False, "file": path}
    if state.get("os") == "windows-wasapi" and state.get("stop_file"):
        rec.run(f"New-Item -ItemType File -Force -Path {_q(state['stop_file'])} | Out-Null")
        for _ in range(60):
            time.sleep(1)
            _code, out = rec.run(f"(Get-Item {_q(path)} -ErrorAction SilentlyContinue).Length")
            if out.strip() and out.strip().splitlines()[0].isdigit():
                break
    if path:
        import subprocess  # noqa: F401 - kept for symmetry with capture_windows

        _code, duration = rec.run(
            '& ffprobe -v error -show_entries format=duration -of csv=p=0 ' + _q(path), timeout=120)
        from recorder import parse_duration
        result["duration"] = parse_duration(duration)
        _code, size = rec.run(f"(Get-Item {_q(path)} -ErrorAction SilentlyContinue).Length")
        try:
            result["bytes"] = int(size.strip().splitlines()[0])
        except (ValueError, IndexError):
            result["bytes"] = 0
    rec.save_state({**state, "stopped": time.time()})
    rec.run(f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false "
            "-ErrorAction SilentlyContinue")
    return result
