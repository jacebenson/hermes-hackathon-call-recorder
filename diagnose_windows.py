"""Bounded Windows audio probe in the same session used by capture."""
import json
from pathlib import Path
import uuid

from capture_windows_wasapi import _home, _q, _write_bytes


def audio_devices(rec) -> tuple[bool, str]:
    # SSH owns session 0. An Interactive task sees the user's actual endpoints.
    token = uuid.uuid4().hex
    directory = _home(rec) + '\\.call-recorder'
    script = directory + '\\diagnose-' + token + '.py'
    result = directory + '\\diagnose-' + token + '.json'
    task = 'call-recorder-diagnose-' + token
    try:
        if 'True' not in _write_bytes(rec, script, (Path(__file__).parent / 'scripts' / 'diagnose_windows.py').read_bytes()):
            return False, 'Could not write the audio test helper on the recording machine.'
        command = (
            "$ErrorActionPreference='Stop'; "
            "$python=(Get-Command python).Source; "
            f"$args={_q(chr(34) + script + chr(34) + ' ' + chr(34) + result + chr(34))}; "
            "$action=New-ScheduledTaskAction -Execute $python -Argument $args; "
            "$me=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name; "
            "$principal=New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited; "
            "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-ExecutionTimeLimit (New-TimeSpan -Seconds 30); "
            f"Register-ScheduledTask -TaskName {_q(task)} -Action $action -Principal $principal -Settings $settings | Out-Null; "
            f"Start-ScheduledTask -TaskName {_q(task)}; "
            f"for ($i=0; $i -lt 20 -and !(Test-Path -LiteralPath {_q(result)}); $i++) {{ Start-Sleep -Seconds 1 }}; "
            f"Get-Content -LiteralPath {_q(result)} -Raw"
        )
        code, out = rec.run(command, timeout=40)
        if code != 0:
            return False, 'Could not inspect audio in the logged-in session. Check Python, pyaudiowpatch and the SSH user.'
        found = json.loads(out.strip())
        return bool(found['ok']), str(found['message'])
    finally:
        rec.run(f"Stop-ScheduledTask -TaskName {_q(task)} -ErrorAction SilentlyContinue; "
                f"Unregister-ScheduledTask -TaskName {_q(task)} -Confirm:$false -ErrorAction SilentlyContinue; "
                f"Remove-Item -LiteralPath {_q(script)},{_q(result)} -Force -ErrorAction SilentlyContinue", timeout=15)
