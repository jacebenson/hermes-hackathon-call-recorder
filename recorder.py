"""Core recording logic for the Hermes call recorder plugin.

No framework imports here: this module is unit-testable and is used by both the
dashboard API half (``dashboard/plugin_api.py``) and the tests.

Everything shells out to plain PipeWire / ffmpeg / systemd user units, so the
recorder works on any Linux box running PipeWire. ``Recorder(host="media-pc")``
runs the same commands over SSH instead of locally.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from pathlib import Path

#: Apps whose audio stream means "a call is happening right now".
CALL_APPS = (
    "chromium", "chrome", "brave", "firefox", "vivaldi",
    "zoom", "teams", "slack", "discord", "webex", "signal", "telegram",
)

UNIT_PREFIX = "callrec-"
LIVE_UNIT = "callrec-live"


# --------------------------------------------------------------------------- #
# pure parsers (the parts worth testing)
# --------------------------------------------------------------------------- #

def parse_sink_inputs(text: str) -> list[dict]:
    """Parse ``pactl list sink-inputs`` into ``[{app, sink}]``."""
    out = []
    for block in text.split("Sink Input #")[1:]:
        app = re.search(r'application\.name = "([^"]+)"', block)
        sink = re.search(r"\n\tSink: (\d+)", block)
        if app and sink:
            out.append({"app": app.group(1), "sink": sink.group(1)})
    return out


def parse_source_outputs(text: str) -> list[str]:
    """Parse ``pactl list source-outputs`` into the list of capturing apps."""
    apps = []
    for block in text.split("Source Output #")[1:]:
        app = re.search(r'application\.name = "([^"]+)"', block)
        if app:
            apps.append(app.group(1))
    return apps


def app_key(name: str) -> str:
    """Normalise an app name so `Chromium` and `Chromium input` compare equal."""
    return (name or "").lower().replace(" input", "").strip()


def same_app(a: str, b: str) -> bool:
    ka, kb = app_key(a), app_key(b)
    return bool(ka and kb) and (ka == kb or ka in kb or kb in ka)


def parse_sinks(text: str) -> dict[str, str]:
    """Parse ``pactl list short sinks`` into ``{id: name}``."""
    sinks = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) > 1:
            sinks[parts[0]] = parts[1]
    return sinks


def is_call_app(app_name: str) -> bool:
    lowered = (app_name or "").lower()
    return any(a in lowered for a in CALL_APPS)


def pick_monitor(sink_inputs: list[dict], sinks: dict[str, str], default_sink: str = "") -> str:
    """The monitor source carrying the call: the call app's sink, else the default."""
    for item in sink_inputs:
        if is_call_app(item["app"]):
            name = sinks.get(item["sink"])
            if name:
                return f"{name}.monitor"
    if default_sink:
        return f"{default_sink}.monitor"
    raise RuntimeError("no sink to record from (no call app and no default sink)")


def parse_duration(text: str) -> float | None:
    try:
        return float((text or "").strip())
    except (TypeError, ValueError):
        return None


def parse_transcript(line: str) -> dict | None:
    """``[12:34:56] you> text`` -> ``{time, speaker, text}``."""
    m = re.match(r"^\[(\d\d:\d\d:\d\d)\]\s+(you|them)>\s*(.*)$", line.strip())
    if not m:
        return None
    return {"time": m.group(1), "speaker": m.group(2), "text": m.group(3)}


def human_clock(seconds: float | int | None) -> str:
    total = int(seconds or 0)
    return f"{total // 60:02d}:{total % 60:02d}"


# --------------------------------------------------------------------------- #
# recorder
# --------------------------------------------------------------------------- #

class Recorder:
    """Record mic + system audio on a Linux host into one two-track mkv.

    ``host=None`` records locally; ``host="media-pc"`` runs every command there
    over SSH (no exposed port, no token — the caller's SSH key is the credential).
    """

    def __init__(self, host: str | None = None, recordings_dir: str = "~/recordings",
                 python: str | None = None, live_script: str | None = None,
                 whisper_model: str | None = None, state_file: str = "~/.callrec-state.json",
                 auto_start: bool = True, auto_stop_seconds: int = 180):
        self.host = (host or "").strip() or None
        self.recordings_dir = recordings_dir or "~/recordings"
        self.python = python or ""
        self.live_script = live_script or ""
        self.whisper_model = whisper_model or ""
        self.state_file = state_file
        self.auto_start = bool(auto_start)
        self.auto_stop_seconds = int(auto_stop_seconds)

    # -- shell plumbing ----------------------------------------------------- #

    def run(self, command: str, timeout: int = 30) -> tuple[int, str]:
        if self.host:
            # ssh joins its arguments with spaces and hands the result to the remote
            # shell, which would then word-split a multi-word command. Quote it once.
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                    self.host, "bash", "-lc", shlex.quote(command)]
        else:
            argv = ["bash", "-lc", command]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return done.returncode, (done.stdout or "") + (done.stderr or "")
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        except OSError as exc:
            return 127, str(exc)

    def ok(self, command: str, timeout: int = 30) -> str:
        code, out = self.run(command, timeout)
        if code != 0:
            raise RuntimeError(f"command failed ({code}): {out.strip()[:300]}")
        return out

    # -- state -------------------------------------------------------------- #

    def _state_path(self) -> Path:
        return Path(self.state_file).expanduser()

    def state(self) -> dict:
        try:
            return json.loads(self._state_path().read_text())
        except (OSError, ValueError):
            return {}

    def save_state(self, state: dict) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=1))

    def unit_active(self, unit: str | None) -> bool:
        if not unit:
            return False
        return self.run(f"systemctl --user is-active {shlex.quote(unit)}")[1].strip() == "active"

    # -- sources ------------------------------------------------------------ #

    def sources(self) -> dict:
        sink_inputs = parse_sink_inputs(self.ok("pactl list sink-inputs"))
        sinks = parse_sinks(self.ok("pactl list short sinks"))
        default_sink = self.ok("pactl get-default-sink").strip()
        mic = self.ok("pactl get-default-source").strip()
        return {"monitor": pick_monitor(sink_inputs, sinks, default_sink), "mic": mic,
                "call_apps": [i["app"] for i in sink_inputs if is_call_app(i["app"])]}

    def call_state(self) -> dict:
        """A call is an app that is both *playing* audio and *capturing* the mic.

        Playing audio alone is just as likely to be a video in a browser tab, which
        is the mistake this check exists to avoid.
        """
        playing = [i["app"] for i in parse_sink_inputs(self.ok("pactl list sink-inputs"))]
        capturing = parse_source_outputs(self.ok("pactl list source-outputs"))
        both = sorted({p for p in playing if is_call_app(p)
                       and any(same_app(p, c) for c in capturing)})
        return {"playing": playing, "capturing": capturing, "call_apps": both, "in_call": bool(both)}

    def call_active(self) -> bool:
        return self.call_state()["in_call"]

    # -- record ------------------------------------------------------------- #

    def start(self, live: bool = False) -> dict:
        state = self.state()
        if state.get("unit") and self.unit_active(state["unit"]):
            return {"ok": True, "already": True, **self.status()}

        src = self.sources()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        unit = f"{UNIT_PREFIX}{stamp}"
        out = f"{self.recordings_dir}/call-{stamp}.mkv"
        self.ok(f"mkdir -p {shlex.quote(self.recordings_dir)}")

        command = " ".join([
            "systemd-run --user", f"--unit={shlex.quote(unit)}", "--collect",
            "ffmpeg -nostdin -hide_banner -loglevel warning",
            "-thread_queue_size 1024", f"-f pulse -i {shlex.quote(src['monitor'])}",
            "-thread_queue_size 1024", f"-f pulse -i {shlex.quote(src['mic'])}",
            "-map 0:a -map 1:a -c:a libopus -b:a 96k",
            "-metadata:s:a:0 title=system -metadata:s:a:1 title=mic",
            shlex.quote(out),
        ])
        self.ok(command)
        self.save_state({"unit": unit, "file": out, "started": time.time(),
                         "monitor": src["monitor"], "mic": src["mic"], "live": bool(live)})
        if live:
            self.start_live()
        time.sleep(1.0)
        return {"ok": True, **self.status()}

    def stop(self) -> dict:
        state = self.state()
        self.stop_live()
        if state.get("unit"):
            self.run(f"systemctl --user stop {shlex.quote(state['unit'])}", timeout=30)
            self.run(f"systemctl --user reset-failed {shlex.quote(state['unit'])}", timeout=15)
        time.sleep(0.5)
        result = {"ok": True, "recording": False}
        path = state.get("file")
        if path:
            result["file"] = path
            result["bytes"] = self.file_size(path)
            result["duration"] = self.duration(path)
        self.save_state({**state, "unit": None, "stopped": time.time()})
        return result

    def status(self) -> dict:
        state = self.state()
        recording = self.unit_active(state.get("unit"))
        live = self.unit_active(LIVE_UNIT)
        grace = self.auto_stop_seconds
        pending = 0
        if recording and state.get("ended_at") and not state.get("keep") and grace > 0:
            pending = max(0, int(grace - (time.time() - state["ended_at"])))
        return {
            "recording": recording,
            "live": live,
            "host": self.host or "local",
            "unit": state.get("unit"),
            "file": state.get("file"),
            "elapsed": int(time.time() - state["started"]) if recording and state.get("started") else 0,
            "bytes": self.file_size(state.get("file")) if state.get("file") else 0,
            "monitor": state.get("monitor"),
            "mic": state.get("mic"),
            "auto_start": self.auto_start,
            "auto_stop_seconds": grace,
            "pending_stop": pending,
        }

    # -- automation ---------------------------------------------------------- #

    def tick(self) -> dict:
        """One automation decision: start on a call, stop after it has been gone a while.

        Stopping is deliberately slow (``auto_stop_seconds``, default 3 minutes) so a
        dropped call window, a screen share or a muted minute never truncates a
        recording. While the countdown runs the recording keeps going, and
        ``keep()`` cancels it for the current session.
        """
        state = self.state()
        recording = self.unit_active(state.get("unit"))
        in_call = self.call_active()

        if not in_call:
            if not recording:
                return {"action": "idle"}
            grace = int(self.auto_stop_seconds)
            ended = state.get("ended_at")
            if not ended:
                state["ended_at"] = time.time()
                self.save_state(state)
                ended = state["ended_at"]
            waited = time.time() - ended
            if state.get("keep") or grace <= 0:
                return {"action": "kept", "waited": int(waited)}
            if waited >= grace:
                result = self.stop()
                return {"action": "stopped", "reason": "call ended",
                        "file": result.get("file"), "duration": result.get("duration")}
            return {"action": "pending_stop", "remaining": int(grace - waited)}

        if state.get("ended_at") or state.get("keep"):
            state.pop("ended_at", None)
            state.pop("keep", None)
            self.save_state(state)
        if recording:
            return {"action": "recording"}
        if not self.auto_start:
            return {"action": "call_detected"}
        return {"action": "started", **self.start(live=True), "apps": self.call_state()["call_apps"]}

    def keep(self) -> dict:
        """Cancel a pending auto-stop for this recording."""
        state = self.state()
        state["keep"] = True
        self.save_state(state)
        return {"ok": True, "keep": True}

    # -- live transcription -------------------------------------------------- #

    def live_available(self) -> bool:
        return bool(self.python and self.live_script)

    def start_live(self) -> dict:
        if not self.live_available():
            return {"ok": False, "error": "live transcription is not configured"}
        command = " ".join([
            "systemd-run --user", f"--unit={LIVE_UNIT}", "--collect",
            shlex.quote(self.python), shlex.quote(self.live_script),
        ])
        return {"ok": self.run(command)[0] == 0}

    def stop_live(self) -> None:
        self.run(f"systemctl --user stop {LIVE_UNIT}", timeout=30)
        self.run(f"systemctl --user reset-failed {LIVE_UNIT}", timeout=15)

    def transcript_tail(self, lines: int = 60) -> list[str]:
        path = f"{self.recordings_dir}/live.txt"
        out = self.run(f"tail -n {int(lines)} {shlex.quote(path)} 2>/dev/null")[1]
        return [line for line in out.splitlines() if line.strip()]

    # -- recordings --------------------------------------------------------- #

    def file_size(self, path: str | None) -> int:
        if not path:
            return 0
        code, out = self.run(f"stat -c %s {shlex.quote(path)} 2>/dev/null")
        try:
            return int(out.strip()) if code == 0 else 0
        except ValueError:
            return 0

    def duration(self, path: str | None) -> float | None:
        if not path:
            return None
        code, out = self.run(
            f"ffprobe -v error -show_entries format=duration -of csv=p=0 {shlex.quote(path)}",
            timeout=60)
        return parse_duration(out) if code == 0 else None

    def recordings(self, limit: int = 20) -> list[dict]:
        listing = self.run(
            f"ls -1t {shlex.quote(self.recordings_dir)}/call-*.mkv 2>/dev/null | head -{int(limit)}")[1]
        items = []
        for path in [line.strip() for line in listing.splitlines() if line.strip()]:
            transcript = re.sub(r"\.mkv$", ".transcript.md", path)
            has = self.run(f"test -f {shlex.quote(transcript)}")[0] == 0
            items.append({"file": path, "name": path.rsplit("/", 1)[-1],
                          "bytes": self.file_size(path), "duration": self.duration(path),
                          "transcript": transcript if has else None})
        return items

    def read_transcript(self, path: str, limit_lines: int = 4000) -> str:
        return self.run(f"head -n {int(limit_lines)} {shlex.quote(path)}")[1]

    def transcribe(self, path: str) -> dict:
        """Archive pass: one Whisper run per track, merged speaker-labelled markdown."""
        if not self.live_available():
            return {"ok": False, "error": "transcription is not configured"}
        target = re.sub(r"\.mkv$", ".transcript.md", path)
        command = " ".join([
            shlex.quote(self.python), shlex.quote(self.live_script.replace("live.py", "archive.py")),
            shlex.quote(path),
        ])
        code, out = self.run(command, timeout=3600)
        if code != 0:
            return {"ok": False, "error": out.strip()[:300]}
        return {"ok": True, "transcript": target}


def from_settings(settings: dict) -> Recorder:
    """Build a Recorder from Hermes plugin settings (see README)."""
    return Recorder(
        host=settings.get("host"),
        recordings_dir=settings.get("recordings_dir") or "~/recordings",
        python=settings.get("python"),
        live_script=settings.get("live_script"),
        whisper_model=settings.get("whisper_model"),
    )


# --------------------------------------------------------------------------- #
# which machine to record
# --------------------------------------------------------------------------- #

# Only known call apps count as a call in progress. Counting every stream would be
# wrong twice over: it would treat a music player as a call, and this plugin's own
# ffmpeg capture (application.name "Lavf…") as one, so a recording would look like a
# call that never ends and auto-stop would never fire.
_CALL_APP_PATTERN = "|".join(CALL_APPS)

PROBE_SCRIPT = (
    'echo HOST=$(uname -n 2>/dev/null || hostname 2>/dev/null); '
    'echo OS=$(uname -s 2>/dev/null || echo unknown); '
    'command -v pactl >/dev/null 2>&1 && echo PIPEWIRE=1 || echo PIPEWIRE=0; '
    'command -v ffmpeg >/dev/null 2>&1 && echo FFMPEG=1 || echo FFMPEG=0; '
    'systemctl --user is-system-running 2>/dev/null | sed "s/^/SYSTEMD=/"; '
    'pactl get-default-sink 2>/dev/null | sed "s/^/SINK=/"; '
    'pactl get-default-source 2>/dev/null | sed "s/^/SOURCE=/"; '
    'pactl list sink-inputs 2>/dev/null '
    f'| grep -ciE \'application.name = "({_CALL_APP_PATTERN})\' | sed "s/^/PLAYING=/"; '
    'pactl list source-outputs 2>/dev/null '
    f'| grep -ciE \'application.name = "({_CALL_APP_PATTERN})\' | sed "s/^/CAPTURING=/"; '
    'true'
)


def _count(value: str | None) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def probe(host: str | None = None, timeout: int = 8) -> dict:
    """What a candidate machine can actually do: PipeWire, ffmpeg, a user session, devices.

    The question this answers is "does audio live here?" — because a gateway host
    usually has no microphone and would record silence without complaining.
    """
    rec = Recorder(host=host, state_file="/nonexistent/callrec-probe.json")
    code, out = rec.run(PROBE_SCRIPT, timeout=timeout)
    found: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.strip().partition("=")
        if key and key.isupper() and value:
            found[key] = value.strip()
    reachable = bool(found.get("HOST"))
    return {
        "target": host or "local",
        "reachable": reachable,
        "hostname": found.get("HOST"),
        "os": found.get("OS") or "unknown",
        "pipewire": found.get("PIPEWIRE") == "1",
        "ffmpeg": found.get("FFMPEG") == "1",
        "systemd": found.get("SYSTEMD") or "",
        "sink": found.get("SINK") or None,
        "source": found.get("SOURCE") or None,
        "can_record": reachable and found.get("PIPEWIRE") == "1" and found.get("FFMPEG") == "1",
        "has_audio": bool(found.get("SINK")),
        # Both directions in use is the closest thing to "someone is on a call here".
        "in_call": _count(found.get("PLAYING")) > 0 and _count(found.get("CAPTURING")) > 0,
    }


def ssh_aliases(config: str = "~/.ssh/config") -> list[str]:
    """Concrete host aliases (no wildcards) declared in the user's SSH config."""
    path = Path(config).expanduser()
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    aliases: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2 or parts[0].lower() != "host":
            continue
        for alias in parts[1:]:
            if any(ch in alias for ch in "*?!") or alias.startswith("-"):
                continue
            if alias not in aliases:
                aliases.append(alias)
    return aliases


def detect_candidates(limit: int = 12, timeout: int = 5) -> list[dict]:
    """Probe the local machine and each SSH alias, and say which ones can record."""
    candidates = [probe(None, timeout=timeout)]
    for alias in ssh_aliases()[:limit]:
        candidates.append(probe(alias, timeout=timeout))
    return candidates


def _missing(local: dict) -> dict:
    """Why no machine was chosen - the OS case deserves its own sentence."""
    local_os = (local.get("os") or "").lower()
    if local_os and local_os != "linux":
        return {"host": "", "mode": "unsupported", "probe": local,
                "why": f"recording needs Linux with PipeWire; this host is {local.get('os')}"
                       " - name a Linux machine in settings"}
    return {"host": "", "mode": "none", "probe": local,
            "why": "no machine with a microphone was found - set one in settings"}


def choose_source(configured_host: str = "", candidates: list[dict] | None = None) -> dict:
    """Decide which machine to record, and say why.

    * a machine named in settings always wins;
    * otherwise this machine, when audio actually lives on it;
    * otherwise the first detected machine that has a mic and a sink.

    Returning the reason matters as much as the choice: "recording beast-1 because
    the gateway host has no microphone" is a sentence the UI can show.
    """
    configured = (configured_host or "").strip()
    if configured:
        info = probe(configured)
        return {"host": configured, "mode": "configured", "probe": info,
                "why": f"set to {configured} in settings"}

    if candidates is not None:
        local = candidates[0] if candidates else {}
        for candidate in candidates:
            if candidate.get("in_call") and candidate.get("can_record"):
                return {"host": "" if candidate is local else candidate["target"],
                        "mode": "local" if candidate is local else "auto",
                        "probe": candidate,
                        "why": "a call is in progress on this machine"
                        if candidate is local else f"a call is in progress on {candidate['target']}"}
        if local.get("has_audio") and local.get("can_record"):
            return {"host": "", "mode": "local", "probe": local,
                    "why": "this machine has a microphone and audio output"}
        for candidate in candidates[1:]:
            if candidate.get("can_record") and candidate.get("has_audio"):
                local_name = local.get("hostname") or "this machine"
                return {"host": candidate["target"], "mode": "auto", "probe": candidate,
                        "why": f"{local_name} has no audio devices; {candidate['target']} does"}
        return _missing(local)

    # Lazy on purpose: probing every SSH alias costs seconds.
    #
    # Order matters. A call in progress beats mere audio hardware, because with two
    # desks the question is not "which box has a microphone" but "which box am I
    # sitting at" - and the one where a call app is holding the mic is the answer.
    local = probe(None)
    if local.get("in_call") and local.get("can_record"):
        return {"host": "", "mode": "local", "probe": local,
                "why": "a call is in progress on this machine"}

    seen: list[dict] = []
    for alias in ssh_aliases()[:12]:
        candidate = probe(alias, timeout=4)
        if candidate.get("in_call") and candidate.get("can_record"):
            return {"host": alias, "mode": "auto", "probe": candidate,
                    "why": f"a call is in progress on {alias}"}
        seen.append(candidate)

    if local.get("has_audio") and local.get("can_record"):
        return {"host": "", "mode": "local", "probe": local,
                "why": "this machine has a microphone and audio output"}
    for candidate in seen:
        if candidate.get("can_record") and candidate.get("has_audio"):
            local_name = local.get("hostname") or "this machine"
            return {"host": candidate["target"], "mode": "auto", "probe": candidate,
                    "why": f"{local_name} has no audio devices; {candidate['target']} does"}
    return _missing(local)


