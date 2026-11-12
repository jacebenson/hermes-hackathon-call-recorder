"""Core recording logic for the Hermes call recorder plugin.

No framework imports here: this module is unit-testable and is used by both the
dashboard API half (``dashboard/plugin_api.py``) and the tests.

Everything shells out to plain PipeWire / ffmpeg / systemd user units, so the
recorder works on any Linux box running PipeWire. ``Recorder(host="media-pc")``
runs the same commands over SSH instead of locally.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
import subprocess
import threading
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

class CaptureUnavailable(RuntimeError):
    """The machine cannot capture audio at all, as opposed to one bad source.

    A machine with no audio server is a dead end, not a transient failure: say
    so once, in words the user can act on, instead of surfacing a 500 and
    letting the watcher retry forever.
    """


def capture_unavailable(detail: str, machine: str) -> CaptureUnavailable:
    text = (detail or "").lower()
    if "127" in text or "not found" in text:
        return CaptureUnavailable(
            f"{machine} has no audio tools installed, so it cannot record. "
            "Install PipeWire and pulseaudio-utils there, or pick a machine with audio."
        )
    if "connection refused" in text or "failed to connect" in text or "no such file" in text:
        return CaptureUnavailable(
            f"{machine} has no running audio server, so it cannot record. "
            "A headless server usually has none. Pick a machine with a desktop session."
        )
    return CaptureUnavailable(f"{machine} cannot report its audio devices: {detail.strip()[:200]}")


class Recorder:
    """Record mic + system audio on a Linux host into one two-track mkv.

    ``host=None`` records locally; ``host="media-pc"`` runs every command there
    over SSH (no exposed port, no token — the caller's SSH key is the credential).
    """

    def __init__(self, host: str | None = None, recordings_dir: str = "~/recordings",
                 python: str | None = None, live_script: str | None = None,
                 whisper_model: str | None = None, state_file: str = "~/.callrec-state.json",
                 auto_start: bool = True, auto_stop_seconds: int = 180,
                 os_name: str = "linux"):
        self.host = (host or "").strip() or None
        self.recordings_dir = recordings_dir or "~/recordings"
        self.python = python or ""
        self.live_script = live_script or ""
        self.whisper_model = whisper_model or ""
        self.state_file = state_file
        self.auto_start = bool(auto_start)
        self.auto_stop_seconds = int(auto_stop_seconds)
        #: "linux" or "windows": which shell the commands below are written for.
        self.os_name = (os_name or "linux").lower()
        #: CUDA library path for the live transcriber (detected, not configured).
        self.ld_library_path = ""
        self._transcription: dict = {}

    def home_dir(self) -> str:
        """The target's $HOME, asked once."""
        cached = getattr(self, "_home_dir", None)
        if cached:
            return cached
        value = self.ok("echo $HOME").strip() or "~"
        self._home_dir = value
        return value

    def rec_dir(self) -> str:
        """recordings_dir with `~` expanded on the *target*.

        systemd-run does not expand `~`, so passing it literally created a directory
        actually named `~` next to the home directory.
        """
        directory = self.recordings_dir or "~/recordings"
        if directory.startswith("~"):
            return self.home_dir() + directory[1:]
        return directory

    # -- shell plumbing ----------------------------------------------------- #

    # A Hermes backend started as a service inherits almost no session environment,
    # and without these two variables pactl answers "Connection refused" while the
    # desktop it is recording sits there playing audio. Export both, defaulting to
    # this user's runtime directory.
    SESSION_ENV = ('export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"; '
                   'export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-'
                   'unix:path=$XDG_RUNTIME_DIR/bus}"; '
                   'export PULSE_SERVER="${PULSE_SERVER:-unix:$XDG_RUNTIME_DIR/pulse/native}"; ')

    def shell_argv(self, command: str) -> list[str]:
        """The argv that runs *command* on this machine, quoted for ssh when remote."""
        if self.os_name != "windows":
            command = self.SESSION_ENV + command
        if self.os_name == "windows":
            inner = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            inner = ["bash", "-lc", command]
        if self.host:
            # ssh joins its arguments with spaces and hands the result to the remote
            # shell. Linux gets shlex quoting; Windows OpenSSH lands in cmd.exe, where a
            # single-quoted string is literal text, so it gets double quotes instead.
            if self.os_name == "windows":
                escaped = command.replace('"', '\\"')
                remote = f'powershell -NoProfile -NonInteractive -Command "{escaped}"'
            else:
                remote = "bash -lc " + shlex.quote(command)
            return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", self.host, remote]
        return inner

    def run(self, command: str, timeout: int = 30) -> tuple[int, str]:
        argv = self.shell_argv(command)
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
        """All four answers in one SSH round trip: each one costs ~300ms otherwise."""
        command = ("echo __SI__; pactl list sink-inputs 2>/dev/null; "
                   "echo __SK__; pactl list short sinks 2>/dev/null; "
                   "echo __DS__; pactl get-default-sink 2>/dev/null; "
                   "echo __DC__; pactl get-default-source 2>/dev/null")
        try:
            raw = self.ok(command)
        except RuntimeError as exc:
            raise capture_unavailable(str(exc), self.host or "this machine") from exc
        sections = {"__SI__": "", "__SK__": "", "__DS__": "", "__DC__": ""}
        current = None
        for line in raw.splitlines():
            if line.strip() in sections:
                current = line.strip()
            elif current:
                sections[current] += line + "\n"
        sink_inputs = parse_sink_inputs(sections["__SI__"])
        sinks = parse_sinks(sections["__SK__"])
        default_sink = sections["__DS__"].strip()
        mic = sections["__DC__"].strip()
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
        if self.os_name == "windows":
            import capture_windows_wasapi as wasapi
            if wasapi.available(self):
                return wasapi.start(self, live)
            import capture_windows
            return capture_windows.start(self, live)
        state = self.state()
        if state.get("unit") and self.unit_active(state["unit"]):
            return {"ok": True, "already": True, **self.status()}

        src = self.sources()
        # UTC on purpose: the name is one clock for every platform, and the pane
        # converts it to the reader's timezone when it shows it.
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        unit = f"{UNIT_PREFIX}{stamp}"
        out = f"{self.rec_dir()}/call-{stamp}.mkv"
        self.ok(f"mkdir -p {shlex.quote(self.rec_dir())}")

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
        state = {"unit": unit, "file": out, "started": time.time(),
                 "monitor": src["monitor"], "mic": src["mic"], "live": bool(live)}
        self.save_state(state)
        if live:
            self.start_live(model=self.whisper_model, ld_library_path=self.ld_library_path)
        # Answer from what we already know: another status() here is only extra latency.
        return {"ok": True, "recording": True, "live": bool(live) and self.live_available(),
                "host": self.host or "local", "unit": unit, "file": out, "bytes": 0,
                "elapsed": 0, "monitor": src["monitor"], "mic": src["mic"],
                "auto_start": self.auto_start, "auto_stop_seconds": self.auto_stop_seconds,
                "pending_stop": 0}

    def stop(self) -> dict:
        if self.os_name == "windows":
            import capture_windows_wasapi as wasapi
            if self.state().get("os") == "windows-wasapi":
                return wasapi.stop(self)
            import capture_windows
            return capture_windows.stop(self)
        state = self.state()
        unit = state.get("unit")
        path = state.get("file")
        stops = [f"systemctl --user stop {LIVE_UNIT} 2>/dev/null"]
        if unit:
            stops.append(f"systemctl --user stop {shlex.quote(unit)} 2>/dev/null")
            stops.append(f"systemctl --user reset-failed {shlex.quote(unit)} 2>/dev/null")
        command = "; ".join(stops)
        if path:
            command += (f"; echo __SZ__; stat -c %s {shlex.quote(path)} 2>/dev/null"
                        f"; echo __DU__; ffprobe -v error -show_entries format=duration "
                        f"-of csv=p=0 {shlex.quote(path)} 2>/dev/null")
        _code, out = self.run(command, timeout=90)
        result = {"ok": True, "recording": False}
        if path:
            result["file"] = path
            size = duration = ""
            section = None
            for line in out.splitlines():
                if line.strip() == "__SZ__":
                    section = "size"
                elif line.strip() == "__DU__":
                    section = "duration"
                elif section == "size":
                    size += line.strip()
                elif section == "duration":
                    duration += line.strip()
            try:
                result["bytes"] = int(size.strip().splitlines()[0])
            except (ValueError, IndexError):
                result["bytes"] = 0
            result["duration"] = parse_duration(duration)
            if not result.get("bytes"):
                # Matroska writes its first cluster after ~10s; a shorter take saves
                # nothing, and silently returning an empty file looks like data loss.
                result["warning"] = ("stopped before anything was written - a recording "
                                     "needs about ten seconds to save its first audio")
        self.save_state({**state, "unit": None, "stopped": time.time()})
        return result

    def status(self) -> dict:
        if self.os_name == "windows":
            import capture_windows_wasapi as wasapi
            if self.state().get("os") == "windows-wasapi":
                return wasapi.status(self)
            import capture_windows
            return capture_windows.status(self)
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
            "mic_db": self.mic_level() if recording else None,
        }

    # -- levels -------------------------------------------------------------- #

    def mic_level(self, fresh: bool = False) -> float | None:
        """Microphone level in dB, sampled in the background, never on the caller.

        A recording can run perfectly while the microphone is muted, so this has to
        be measured - but a one-second capture inside /status turned every poll into
        a one-second wait. The sample runs on a thread and status returns the value
        it already has.
        """
        now = time.time()
        cached = getattr(self, "_mic_level", None)
        if cached and now - cached[1] < 10:
            return cached[0]
        if not getattr(self, "_mic_sampling", False):
            self._mic_sampling = True

            def _sample() -> None:
                try:
                    self._mic_level = (self._measure_mic(), time.time())
                finally:
                    self._mic_sampling = False

            threading.Thread(target=_sample, daemon=True).start()
        return cached[0] if cached else None

    def _measure_mic(self) -> float | None:
        """One second of the microphone, measured."""
        now = time.time()
        device = self.state().get("mic") or "easyeffects_source"
        if self.os_name == "windows":
            command = (f'& ffmpeg -hide_banner -nostdin -f dshow -i audio="{device}" '
                       "-t 0.7 -af volumedetect -f null - 2>&1 | Out-String")
        else:
            command = (f"ffmpeg -hide_banner -nostdin -f pulse -i {shlex.quote(device)} "
                       "-t 0.7 -af volumedetect -f null - 2>&1")
        code, out = self.run(command, timeout=15)
        level = None
        match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", out)
        return float(match.group(1)) if match else None

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
                return {**result, "action": "stopped", "reason": "call ended"}
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

    def start_live(self, model: str = "", ld_library_path: str = "") -> dict:
        if not self.live_available():
            return {"ok": False, "error": "live transcription is not configured"}
        state = self.state()
        source = state.get("file")
        output = str(Path(source).with_suffix(".live.txt")) if source else f"{self.rec_dir()}/live.txt"
        cuda = bool(ld_library_path)
        env = (f" -E LIVE_OUT={shlex.quote(output)}"
               f" -E WHISPER_DEVICE={'cuda' if cuda else 'cpu'}"
               f" -E WHISPER_COMPUTE={'float16' if cuda else 'int8'}"
               " -E XDG_RUNTIME_DIR -E DBUS_SESSION_BUS_ADDRESS -E PULSE_SERVER")
        for key, field in (("MON_SRC", "monitor"), ("MIC_SRC", "mic")):
            if state.get(field):
                env += f" -E {key}={shlex.quote(state[field])}"
        chosen_model = model or self.whisper_model
        if chosen_model:
            env += f" -E WHISPER_MODEL={shlex.quote(chosen_model)}"
        if ld_library_path:
            env += f" -E LD_LIBRARY_PATH={shlex.quote(ld_library_path)}"
        command = " ".join([
            "systemd-run --user", f"--unit={LIVE_UNIT}", "--collect", env.strip(),
            shlex.quote(self.python), shlex.quote(self.live_script),
        ])
        return {"ok": self.run(command)[0] == 0}

    def stop_live(self) -> None:
        self.run(f"systemctl --user stop {LIVE_UNIT}", timeout=30)
        self.run(f"systemctl --user reset-failed {LIVE_UNIT}", timeout=15)

    def transcript_tail(self, lines: int = 60) -> list[str]:
        path = f"{self.rec_dir()}/live.txt"
        state = self.state()
        if state.get("file") and self.unit_active(state.get("unit")):
            current = shlex.quote(str(Path(state["file"]).with_suffix(".live.txt")))
            command = (f"if [ -f {current} ]; then tail -n {int(lines)} {current}; "
                       f"else tail -n {int(lines)} {shlex.quote(path)}; fi 2>/dev/null")
        else:
            command = f"tail -n {int(lines)} {shlex.quote(path)} 2>/dev/null"
        out = self.run(command)[1]
        # The file carries "=== live started …" section markers; they are not speech.
        return [line for line in out.splitlines()
                if line.strip() and not line.strip().startswith("===")]

    # -- delete -------------------------------------------------------------- #

    def _mine(self, path: str, pattern: str) -> tuple[str, str | None]:
        """Confine a caller's path to one of our names in the configured directory."""
        import ntpath
        import posixpath

        if not isinstance(path, str):
            return "", "file is required"
        windows = self.os_name == "windows"
        paths = ntpath if windows else posixpath
        name = paths.basename(path)
        if not re.fullmatch(pattern, name):
            return "", "not a recording of mine"
        if windows:
            from capture_windows import resolved_dir
            directory = resolved_dir(self).rstrip("\\/")
        else:
            directory = self.rec_dir().rstrip("/")
        if paths.normcase(paths.dirname(path)) != paths.normcase(directory):
            return "", "outside the recordings directory"
        return paths.join(directory, name), None

    def validate_recording(self, path: str) -> dict:
        """Validate before any archive work, including runtime discovery."""
        target, error = self._mine(path, r"call-\d{8}-\d{6}\.mkv")
        return {"ok": False, "error": error} if error else {"ok": True, "file": target}

    def delete_recording(self, path: str) -> dict:
        """Remove only a recording in the configured directory and its transcript."""
        import ntpath
        import posixpath

        windows = self.os_name == "windows"
        paths = ntpath if windows else posixpath
        try:
            target, error = self._mine(path, r"call-\d{8}-\d{6}\.mkv")
            if error:
                return {"ok": False, "error": error}
            name = paths.basename(target)
            transcript = target[:-4] + ".transcript.md"
            live = target[:-4] + ".live.txt"
            if windows:
                from capture_windows_wasapi import _q
                command = (f"try {{ if (!(Test-Path -LiteralPath {_q(target)} -PathType Leaf)) "
                           "{ Write-Output 'Recording not found.'; exit 3 }; "
                           f"if (Test-Path -LiteralPath {_q(transcript)}) {{ "
                           f"Remove-Item -LiteralPath {_q(transcript)} -ErrorAction Stop }}; "
                           f"if (Test-Path -LiteralPath {_q(live)}) {{ "
                           f"Remove-Item -LiteralPath {_q(live)} -ErrorAction Stop }}; "
                           f"Remove-Item -LiteralPath {_q(target)} -ErrorAction Stop; "
                           "Write-Output 'deleted' } catch { Write-Output 'Permission denied or file in use.'; exit 1 }")
            else:
                command = (f"if [ ! -f {shlex.quote(target)} ]; then "
                           "printf 'Recording not found.'; exit 3; fi; "
                           f"rm -f -- {shlex.quote(transcript)} {shlex.quote(live)} && rm -- {shlex.quote(target)} && echo deleted")
            code, out = self.run(command, timeout=60)
        except (OSError, RuntimeError):
            return {"ok": False, "error": "Could not reach the recording machine to delete the recording."}
        if code in (124, 127, 255):
            return {"ok": False, "error": "Could not reach the recording machine to delete the recording."}
        if code == 3:
            return {"ok": False, "error": "Recording not found. It may already have been deleted."}
        if code != 0 or "deleted" not in out:
            error = "Permission denied or recording is in use." if "permission" in out.lower() else "Could not delete that recording on the recording machine."
            return {"ok": False, "error": error}
        return {"ok": True, "removed": [name, paths.basename(transcript), paths.basename(live)]}

    # -- scripts ------------------------------------------------------------ #

    def install_scripts(self, local_scripts: Path, dest: str = "") -> dict:
        """Copy live.py and archive.py to the recorded machine.

        The plugin ships them, so "where is live.py" never becomes a setting: they are
        written once into a directory the user owns, and reused from then on.
        """
        dest = dest or f"{self.home_dir()}/.local/share/call-recorder"
        try:
            self.ok(f"mkdir -p {shlex.quote(dest)}")
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        for name in ("live.py", "archive.py"):
            source = Path(local_scripts) / name
            if not source.is_file():
                return {"ok": False, "error": f"plugin is missing scripts/{name}"}
            payload = base64.b64encode(source.read_bytes()).decode()
            try:
                self.ok(f"printf %s {shlex.quote(payload)} | base64 -d > {dest}/{name}")
            except RuntimeError as exc:
                return {"ok": False, "error": str(exc)}
        return {"ok": True, "live_script": f"{dest}/live.py"}

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

    def set_title(self, path: str, title: str) -> dict:
        from recording_titles import request
        return request(self, "set", path, title)

    def get_title(self, path: str) -> dict:
        from recording_titles import request
        return request(self, "get", path)

    def recording_titles(self) -> dict:
        from recording_titles import request
        return request(self, "list")

    def recordings(self, limit: int = 20) -> list[dict]:
        listing = self.run(
            f"ls -1t {shlex.quote(self.rec_dir())}/call-*.mkv 2>/dev/null | head -{int(limit)}")[1]
        titles = self.recording_titles().get("titles", {})
        items = []
        for path in [line.strip() for line in listing.splitlines() if line.strip()]:
            transcript = re.sub(r"\.mkv$", ".transcript.md", path)
            has = self.run(f"test -f {shlex.quote(transcript)}")[0] == 0
            items.append({"file": path, "name": path.rsplit("/", 1)[-1],
                          "title": titles.get(path.rsplit("/", 1)[-1], ""),
                          "bytes": self.file_size(path), "duration": self.duration(path),
                          "transcript": transcript if has else None})
        return items

    def read_transcript(self, path: str, limit_lines: int = 4000) -> str:
        target, error = self._mine(path, r"call-\d{8}-\d{6}\.(transcript\.md|live\.txt)")
        if error:
            raise ValueError(error)
        if self.os_name == "windows":
            from capture_windows_wasapi import _q
            return self.run(f"Get-Content -LiteralPath {_q(target)} -TotalCount {int(limit_lines)}")[1]
        return self.run(f"head -n {int(limit_lines)} {shlex.quote(target)}")[1]



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

TRANSCRIPTION_PROBE = (
    # A python that can import faster_whisper, and a live.py with archive.py beside it.
    'for p in "$HOME/whisper/.venv/bin/python" "$HOME/.venv/bin/python3" '
    '"/usr/bin/python3" "$(command -v python3 2>/dev/null)"; do '
    '[ -n "$p" ] && [ -x "$p" ] && "$p" -c "import faster_whisper" >/dev/null 2>&1 '
    '&& { echo PY="$p"; break; }; done; '
    'for s in "$HOME/whisper/live.py" "$HOME/.local/share/call-recorder/live.py"; do '
    '[ -f "$s" ] && [ -f "$(dirname "$s")/archive.py" ] && { echo LIVE="$s"; break; }; done; '
    'for d in "$HOME/models-be-small" "$HOME/models"/* "$HOME/whisper/models"/*; do '
    '[ -f "$d/model.bin" ] && { echo MODEL="$d"; break; }; done; '
    'for v in "$HOME/whisper/.venv"; do '
    '[ -d "$v" ] && echo LD="$(ls -d "$v"/lib/python3*/site-packages/nvidia/*/lib 2>/dev/null | tr "\n" ":")"; '
    'done; '
    'true'
)


def detect_transcription(host: str | None = None, timeout: int = 20) -> dict:
    """Find a Whisper runtime on the recorded machine so nobody has to type paths.

    Both halves are required: an interpreter that can import faster_whisper, and a
    live.py with archive.py beside it. Missing either means "record only".
    """
    rec = Recorder(host=host, state_file="/nonexistent/callrec-probe.json")
    _code, out = rec.run(TRANSCRIPTION_PROBE, timeout=timeout)
    found: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.strip().partition("=")
        if key in ("PY", "LIVE", "MODEL", "LD") and value.strip():
            found[key] = value.strip()
    python, script = found.get("PY", ""), found.get("LIVE", "")
    libraries = found.get("LD", "").strip(":")
    cuda = bool(libraries)
    return {"ok": bool(python and script), "python": python, "live_script": script,
            "model": (found.get("MODEL") or "small.en") if cuda else "tiny.en",
            "device": "cuda" if cuda else "cpu",
            "ld_library_path": libraries,
            "target": host or "local"}


def choose_source(configured_host: str = "") -> dict:
    """An explicit remote host, otherwise the machine running Hermes."""
    host = (configured_host or "").strip()
    return {"host": host, "mode": "configured" if host else "local",
            "probe": {},
            "why": f"set to {host} in settings" if host else
                   "recording on the machine Hermes runs on"}
