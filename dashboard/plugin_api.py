"""call-recorder — dashboard half: routes the desktop UI (and the dashboard) call.

Mounted by Hermes at ``/api/plugins/call-recorder/``:

  GET  /config            → what this install is pointed at, and whether it answers
  GET  /status            → {recording, live, elapsed, bytes, monitor, mic, file}
  POST /start {live}      → begin recording mic + system audio on the target host
  POST /stop              → stop, and report the finished file + duration
  GET  /transcript?lines  → tail of the live transcript
  GET  /recordings        → recent recordings on the target host
  GET  /transcript/read?path=…  → markdown of a finished transcript
  POST /transcribe {file} → run the archive pass for one recording

Native control shells out locally, or over existing SSH when ``host`` is set.
Browser microphone uploads use Hermes's authenticated plugin API and backend storage;
they never launch computer capture or require an SSH credential.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import socket
import tempfile
import unicodedata
import threading
import time
from pathlib import Path

from fastapi import APIRouter
from starlette.requests import Request

_log = logging.getLogger("hermes.plugins.call-recorder")

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_FILE = _PLUGIN_ROOT / "settings.json"

import sys

if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from recording_library import RecordingLibrary  # noqa: E402
from platforms import readiness as platform_readiness, diagnose  # noqa: E402
from transcribe import transcribe as run_transcription, transcribe_from_live  # noqa: E402
from recorder import (  # noqa: E402
    Recorder,
    choose_source,
    detect_transcription,
)

router = APIRouter()

#: Advanced keys stay here (env / settings.json can pin them) but are NOT in the
#: manifest schema: the settings page asks four questions, not seven.
DEFAULTS = {
    "host": "",                    # "" = the machine Hermes runs on
    "capture_mode": "browser",
    "recordings_dir": "~/recordings",
    "transcribe": True,            # the one transcription switch
    "stop_when_call_ends": True,   # three minutes of grace, then stop and transcribe
    "python": "",                  # advanced: pin an interpreter
    "live_script": "",             # advanced: pin a script pair
    "whisper_model": "",
    "auto_start": False,           # manual start only, by default
    "auto_stop_seconds": 180,
    "watch_seconds": 10,
}

#: Seconds of grace after the call disappears before recording stops.
STOP_GRACE_SECONDS = 180

PLUGIN_ID = "call-recorder"


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def manifest_settings() -> dict:
    """Values the application wrote for this plugin's ``config_schema``.

    They live at ``plugins.entries.call-recorder.settings`` in config.yaml, which is
    what the Settings → Plugins form writes, so the UI is the source of truth.
    """
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    try:
        import yaml
    except ImportError:
        return {}
    try:
        data = yaml.safe_load((Path(home) / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a corrupt config must not take the routes down
        return {}
    if not isinstance(data, dict):
        return {}
    entry = ((data.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
    values = entry.get("settings") if isinstance(entry, dict) else None
    return dict(values) if isinstance(values, dict) else {}


def settings() -> dict:
    """Env > pane host choice > Settings page > settings.json > defaults.

    The pane choice applies only while the page's host value is unchanged.
    """
    merged = dict(DEFAULTS)
    saved = {}
    try:
        saved = json.loads(_SETTINGS_FILE.read_text())
        if not isinstance(saved, dict):
            saved = {}
        merged.update(saved)
    except (OSError, ValueError):
        pass
    manifest = manifest_settings()
    merged.update({k: v for k, v in manifest.items() if k in DEFAULTS})
    if 'capture_mode' not in saved and 'capture_mode' not in manifest:
        # Explicit legacy local capture is as intentional as an SSH host.
        merged['capture_mode'] = 'computer' if ('host' in saved or 'host' in manifest or os.environ.get('CALLREC_HOST')) else 'browser'
    # A pane choice wins over the page value it replaced, but a later explicit
    # change in Settings wins again. Keep all other settings' precedence intact.
    if "_capture_host_manifest" in saved and saved["_capture_host_manifest"] == manifest.get("host"):
        merged["host"] = saved.get("host", "")
    for key in DEFAULTS:
        env = os.environ.get(f"CALLREC_{key.upper()}")
        if env:
            merged[key] = env
    merged["transcribe"] = _as_bool(merged["transcribe"], True)
    merged["stop_when_call_ends"] = _as_bool(merged["stop_when_call_ends"], True)
    merged["auto_start"] = _as_bool(merged["auto_start"], False)
    merged["auto_stop_seconds"] = _as_int(merged["auto_stop_seconds"],
                                          STOP_GRACE_SECONDS if merged["stop_when_call_ends"] else 0)
    merged["watch_seconds"] = _as_int(merged["watch_seconds"], 10)
    if not merged["stop_when_call_ends"]:
        merged["auto_stop_seconds"] = 0
    if not merged["transcribe"]:
        merged["python"] = ""
        merged["live_script"] = ""
    return merged


def recorder() -> Recorder:
    cfg = settings()
    src = resolved_source()
    transc = resolved_transcription()
    live = Recorder(
        host=src["host"],
        recordings_dir=cfg["recordings_dir"],
        python=cfg["python"] or transc.get("python", ""),
        live_script=cfg["live_script"] or transc.get("live_script", ""),
        whisper_model=cfg["whisper_model"] or transc.get("model", ""),
        auto_start=cfg["auto_start"],
        auto_stop_seconds=cfg["auto_stop_seconds"],
    )
    live.ld_library_path = transc.get("ld_library_path", "")
    live._transcription = dict(transc)
    return live


def resolved_source(force: bool = False) -> dict:
    """The setting is the target. Empty always means local."""
    return choose_source(settings()["host"])


_READINESS_CACHE: dict = {"at": 0.0, "host": None, "value": None}


def resolved_readiness() -> dict:
    """Can the target capture audio at all, and what is missing if not."""
    host = resolved_source()["host"]
    now = time.time()
    cached = _READINESS_CACHE.get("value")
    if cached is not None and _READINESS_CACHE.get("host") == host \
            and now - float(_READINESS_CACHE.get("at") or 0) < 60:
        return cached
    try:
        value = platform_readiness(Recorder, host or None)
    except Exception as exc:  # noqa: BLE001 - never break the routes over a probe
        _log.warning("call-recorder: readiness failed: %s", exc)
        value = {"state": "unknown", "detail": str(exc)[:200]}
    _READINESS_CACHE.update({"at": now, "host": host, "value": value})
    return value


_TRANSCRIPTION_CACHE: dict = {"at": 0.0, "host": None, "value": None}


def resolved_transcription() -> dict:
    """Where Whisper lives, detected once per host instead of typed into a field.

    Both settings empty means "find it yourself"; setting either one pins the pair.
    """
    cfg = settings()
    if cfg["python"] and cfg["live_script"]:
        found = detect_transcription(resolved_source()["host"] or None)
        return {**found, "ok": True, "mode": "configured", "python": cfg["python"],
                "live_script": cfg["live_script"], "target": resolved_source()["host"] or "local"}
    host = resolved_source()["host"]
    now = time.time()
    cached = _TRANSCRIPTION_CACHE.get("value")
    if cached is not None and _TRANSCRIPTION_CACHE.get("host") == host \
            and now - float(_TRANSCRIPTION_CACHE.get("at") or 0) < 300:
        return cached
    try:
        found = detect_transcription(host or None)
    except Exception as exc:  # noqa: BLE001
        _log.warning("call-recorder: transcription detection failed: %s", exc)
        found = {"ok": False, "python": "", "live_script": "", "target": host or "local"}
    if found.get("python"):
        # Always run the scripts that ship with this plugin, not whatever copy happens
        # to sit on the machine: a stale archive.py there is invisible otherwise.
        installed = Recorder(host=host or None).install_scripts(_PLUGIN_ROOT / "scripts")
        if installed.get("ok"):
            found = {**found, "ok": True, "live_script": installed["live_script"],
                     "installed": True,
                     "existing_script": found.get("live_script") or ""}
    result = {**found, "mode": "auto" if found.get("ok") else "unavailable"}
    _TRANSCRIPTION_CACHE.update({"at": now, "host": host, "value": result})
    return result


def action_recorder(path: str, machine: str = ""):
    target = RecordingLibrary().target(path, machine)
    if target:
        return target[0]
    rec = recorder()
    if machine and machine != (rec.host or socket.gethostname()):
        raise ValueError("Capture machine changed. Refresh the recordings list.")
    return rec


def _store(rec, path: str) -> dict:
    result = RecordingLibrary().store(rec, path)
    if not result.get("ok"):
        _log.warning("call-recorder upload: %s", result.get("error"))
    return result


def browser_transcription() -> dict:
    # The browser archive script ships here; native live helpers are not required.
    runtime = detect_transcription(None)
    return {**runtime, "ok": bool(runtime.get("python"))}


def _transcribe(rec, path: str) -> dict:
    target = RecordingLibrary().target(path)
    if target and target[2].get("capture_mode") == "browser":
        from browser_audio import transcribe_microphone
        return transcribe_microphone(path, browser_transcription())
    # Stored actions use the backend runtime, never the currently selected host.
    if not rec.host and RecordingLibrary().target(path):
        transc = {**detect_transcription(None),
                  "live_script": str(_PLUGIN_ROOT / "scripts" / "live.py")}
    else:
        transc = rec.__dict__.get("_transcription") or resolved_transcription()
    script = transc.get("live_script") or ""
    archive = script.replace("live.py", "archive.py") if script.endswith("live.py") else ""
    # A machine with no CUDA libraries is CPU only: say so instead of asking Whisper
    # for a device it does not have, which fails the whole run.
    cuda = bool(transc.get("ld_library_path"))
    env = {"LD_LIBRARY_PATH": transc.get("ld_library_path") or "",
           "WHISPER_MODEL": rec.whisper_model or transc.get("model") or "",
           "WHISPER_DEVICE": "cuda" if cuda else "cpu",
           "WHISPER_COMPUTE": "float16" if cuda else "int8"}
    return run_transcription(rec, path,
                             gpu_python=transc.get("python") or "",
                             gpu_script=archive, gpu_env=env,
                             prefer_gpu=bool(archive))


_TRANSCRIPTION_JOBS: dict = {}
_TRANSCRIPTION_LOCK = threading.Lock()
_TRANSCRIPTION_FILE_LOCKS: dict = {}


def _launch_archive(worker) -> None:
    threading.Thread(target=worker, name="call-recorder-archive", daemon=True).start()


def _after_stop(rec, result: dict) -> dict:
    """Publish live speech first, or queue one archive fallback per recording."""
    path = result.get("file")
    if not result.get("ok") or not path:
        return result
    if not settings()["transcribe"]:
        return {**result, "upload": _store(rec, path)}
    key = (rec.host or "local", path)
    with _TRANSCRIPTION_LOCK:
        if key in _TRANSCRIPTION_JOBS:
            return {**result, "transcription": dict(_TRANSCRIPTION_JOBS[key])}
        job = {"file": path, "state": "queued", "ok": None}
        _TRANSCRIPTION_JOBS[key] = job
        file_lock = _TRANSCRIPTION_FILE_LOCKS.setdefault(key, threading.Lock())

    with file_lock:
        with _TRANSCRIPTION_LOCK:
            if _TRANSCRIPTION_JOBS.get(key) is not job:
                return {**result, "transcription": dict(_TRANSCRIPTION_JOBS[key])}
        try:
            live = transcribe_from_live(rec, path)
        except Exception as exc:
            _log.warning("call-recorder live transcript: %s", exc)
            live = {"ok": False, "error": "Could not read or save the live transcript."}
        with _TRANSCRIPTION_LOCK:
            if live.get("ok"):
                job.update(live, state="ready")
        if live.get("ok"):
            upload = _store(rec, path)
            with _TRANSCRIPTION_LOCK:
                job["upload"] = upload
            return {**result, "transcription": dict(job)}
        with _TRANSCRIPTION_LOCK:
            job["live_error"] = live.get("error", "No usable live transcript.")

    def worker():
        with file_lock:
            with _TRANSCRIPTION_LOCK:
                # A forced re-pass supersedes an automatic job that has not run.
                if _TRANSCRIPTION_JOBS.get(key) is not job:
                    return
                job.update(state="running")
            try:
                outcome = _transcribe(rec, path)
            except Exception as exc:
                _log.warning("call-recorder archive: %s", exc)
                outcome = {"ok": False, "error": "Could not transcribe on the recording machine."}
            with _TRANSCRIPTION_LOCK:
                job.update(outcome)
                job["state"] = "ready" if outcome.get("ok") else "failed"
                job["both_failed"] = not bool(outcome.get("ok"))
            # Even a failed transcript pass must not strand the finished audio.
            upload = _store(rec, path)
            with _TRANSCRIPTION_LOCK:
                job["upload"] = upload

    try:
        _launch_archive(worker)
    except Exception:
        with _TRANSCRIPTION_LOCK:
            job.update(state="failed", ok=False, both_failed=True, error="Could not start transcription.")
    return {**result, "transcription": dict(job)}


def _native_status_recorder():
    # Status alone must not detect Whisper or install native helpers.
    cfg = settings()
    return Recorder(host=cfg['host'] or None, recordings_dir=cfg['recordings_dir'],
                    auto_start=False, auto_stop_seconds=cfg['auto_stop_seconds'])


def _status_payload() -> dict:
    with _SETTINGS_LOCK:
        rec = _native_status_recorder() if settings().get('capture_mode') == 'browser' else recorder()
        status = rec.status()
    # Never hide an already-running native capture behind a stale browser choice.
    with _TRANSCRIPTION_LOCK:
        jobs = [dict(job) for (host, _), job in _TRANSCRIPTION_JOBS.items()
                if host == (rec.host or "local")]
    return {"ok": True, **status, "transcriptions": jobs}


_WATCHER_STARTED = False


def _watch() -> None:
    """Poll the target host and let the recorder decide: start, wait, or stop."""
    interval = max(3, int(settings().get("watch_seconds") or 10))
    while True:
        try:
            with _SETTINGS_LOCK:
                if settings().get("capture_mode") == "browser":
                    rec = _native_status_recorder()
                    decision = rec.tick() if rec.status().get('recording') else {'action': 'idle'}
                else:
                    rec = recorder()
                    decision = rec.tick()
            if decision.get("action") == "stopped":
                decision = _after_stop(rec, decision)
            if decision.get("action") not in ("idle", "recording"):
                _log.info("call-recorder: %s", decision)
        except Exception as exc:  # a dead SSH target must not kill the watcher
            _log.warning("call-recorder watcher: %s", exc)
        time.sleep(interval)


def ensure_watcher() -> None:
    """Start the automation thread once per process, only when there is work to do."""
    global _WATCHER_STARTED
    cfg = settings()
    if cfg.get("capture_mode") == "browser" or _WATCHER_STARTED or not (cfg["auto_start"] or int(cfg["auto_stop_seconds"] or 0) > 0):
        return
    _WATCHER_STARTED = True
    threading.Thread(target=_watch, name="call-recorder-watch", daemon=True).start()
    _log.info("call-recorder: watcher running (%ss)", cfg["watch_seconds"])


def _config_payload() -> dict:
    cfg = settings()
    if cfg.get("capture_mode") == "browser":
        transc = browser_transcription()
        return {"ok": True, "capture_mode": "browser", "host": cfg["host"] or "local",
                "host_label": cfg["host"] or f"backend ({socket.gethostname()})",
                "transcription": bool(transc.get("ok")), "live": False,
                "transcription_detail": "Backend Whisper available" if transc.get("ok") else "Backend Whisper unavailable"}
    src = resolved_source()
    transc = resolved_transcription()
    rec = recorder()
    code, out = rec.run("echo ok")
    return {
        "ok": code == 0,
        "host": src["host"] or "local",
        "backend_machine": {"hostname": socket.gethostname(), "os": platform.system().lower()},
        "host_label": src["host"] or f"local ({socket.gethostname()})",
        "host_mode": src["mode"],
        "why": src["why"],
        "probe": src.get("probe") or {},
        "recordings_dir": cfg["recordings_dir"],
        "readiness": resolved_readiness(),
        "transcription": bool(transc.get("ok")),
        "transcription_engine": "gpu" if transc.get("ok") else "unavailable",
        "transcription_mode": transc.get("mode"),
        "transcription_detail": (
            f"{cfg['whisper_model'] or transc.get('model') or 'small.en'} on "
            f"{transc.get('device') or ('cuda' if transc.get('ld_library_path') else 'cpu')}"
            if transc.get("ok") else "not found on that machine"),
        "auto_start": cfg["auto_start"],
        "auto_stop_seconds": cfg["auto_stop_seconds"],
        "reachable": code == 0,
        "detail": out.strip()[:200],
    }


RECENT_HOST_LIMIT = 5
_SETTINGS_LOCK = threading.RLock()


def validate_capture_host(value) -> str:
    if not isinstance(value, str):
        raise ValueError("host must be a string; use an empty string for this machine")
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("host must not contain control characters")
    value = value.strip()
    if not value or value.lower() == "local":
        return ""
    if len(value) > 253 or not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError("host must be a hostname or SSH machine alias, or local")
    return value


def _recent_hosts(saved: dict, *hosts) -> list[str]:
    recent = saved.get("recent_hosts", [])
    result = []
    for value in [*hosts, *(recent if isinstance(recent, list) else [])]:
        try:
            host = validate_capture_host(value)
        except ValueError:
            continue
        if host and host.lower() not in [h.lower() for h in result]:
            result.append(host)
    return result[:RECENT_HOST_LIMIT]


def _capture_host_payload(saved: dict | None = None) -> dict:
    if saved is None:
        try:
            saved = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
    if not isinstance(saved, dict):
        saved = {}
    host = settings()["host"]
    return {"ok": True, "capture_mode": settings()["capture_mode"], "host": host,
            "host_label": host or f"local ({socket.gethostname()})",
            "recent_hosts": _recent_hosts(saved, host)}


@router.get("/capture-host")
async def get_capture_host() -> dict:
    return await asyncio.to_thread(_capture_host_payload)


@router.post("/capture-host")
async def post_capture_host(body: dict) -> dict:
    try:
        host = validate_capture_host(body.get("host"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    def _save():
        with _SETTINGS_LOCK:
            if host != settings()['host'] and _native_status_recorder().status().get('recording'):
                return {'ok': False, 'error': 'Stop computer recording before switching audio source or machine.'}
            if os.environ.get("CALLREC_HOST"):
                return {"ok": False, "error": "Capture machine is pinned by CALLREC_HOST. Remove it before switching machines."}
            temporary = None
            try:
                try:
                    saved = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    saved = {}
                if not isinstance(saved, dict):
                    raise ValueError("settings.json must contain an object")
                previous = settings()["host"]
                saved.update(host=host, capture_mode="computer", recent_hosts=_recent_hosts(saved, host, previous),
                             _capture_host_manifest=manifest_settings().get("host"))
                # Replace one file containing both selection and history, never
                # truncate the previous settings when a write fails midway.
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                                 dir=_SETTINGS_FILE.parent,
                                                 prefix=".capture-host-", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(saved, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, _SETTINGS_FILE)
            except (OSError, ValueError) as exc:
                return {"ok": False, "error": f"Could not save capture machine: {exc}"}
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            _READINESS_CACHE.update(at=0.0, host=None, value=None)
            _TRANSCRIPTION_CACHE.update(at=0.0, host=None, value=None)
            return _capture_host_payload()
    return await asyncio.to_thread(_save)


@router.post("/capture-mode")
async def post_capture_mode(body: dict) -> dict:
    mode = body.get('capture_mode')
    if mode not in ('browser', 'computer'):
        return {'ok': False, 'error': 'Choose This browser or A computer.'}
    def save():
        with _SETTINGS_LOCK:
            if mode != settings()['capture_mode'] and _native_status_recorder().status().get('recording'):
                return {'ok': False, 'error': 'Stop computer recording before switching audio source or machine.'}
            if os.environ.get('CALLREC_CAPTURE_MODE'):
                return {'ok': False, 'error': 'Capture choice is pinned by CALLREC_CAPTURE_MODE.'}
            temporary = None
            try:
                saved = json.loads(_SETTINGS_FILE.read_text()) if _SETTINGS_FILE.exists() else {}
                if not isinstance(saved, dict):
                    raise ValueError('Settings must be an object.')
                saved['capture_mode'] = mode
                with tempfile.NamedTemporaryFile(mode='w', dir=_SETTINGS_FILE.parent,
                                                 prefix='.capture-mode-', delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(saved, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, _SETTINGS_FILE)
                return _capture_host_payload()
            except (OSError, ValueError):
                return {'ok': False, 'error': 'Could not save capture choice. Previous choice retained.'}
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
    return await asyncio.to_thread(save)


@router.post('/browser-audio')
async def post_browser_audio(request: Request) -> dict:
    """Hermes authenticates the plugin namespace before dispatching this route.

    SDK upload is multipart `file`. Bound the entire body before multipart parsing;
    ignore client filename and any extra fields. Never accept a destination path.
    """
    from recording_library import MAX_BROWSER_BYTES
    limit = MAX_BROWSER_BYTES + 65536
    try:
        length = request.headers.get('content-length')
        if length and (int(length) < 0 or int(length) > limit):
            return {'ok': False, 'error': 'Upload exceeds 32 MiB.'}
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > limit:
                return {'ok': False, 'error': 'Upload exceeds 32 MiB.'}
            content.extend(chunk)
        request._body = bytes(content)
        async with request.form(max_files=1, max_fields=0) as form:
            audio = form.get('file')
            if audio is None or not hasattr(audio, 'read'):
                return {'ok': False, 'error': 'A microphone audio file is required.'}
            data = await audio.read(MAX_BROWSER_BYTES + 1)
            result = await asyncio.to_thread(RecordingLibrary().store_browser, data, audio.content_type or '')
        if result.get('ok'):
            result['transcription'] = {'state': 'available' if (await asyncio.to_thread(browser_transcription)).get('ok') else 'unavailable',
                                       'detail': 'Use Transcribe for the saved single voice mix. Live transcription unavailable.'}
        return result
    except Exception:
        return {'ok': False, 'error': 'Invalid microphone upload. Use one supported audio file, maximum 32 MiB.'}


@router.get("/config")
async def get_config() -> dict:
    return await asyncio.to_thread(_config_payload)


@router.get("/status")
async def get_status() -> dict:
    return await asyncio.to_thread(_status_payload)


@router.post("/start")
async def post_start(body: dict | None = None) -> dict:
    def start():
        with _SETTINGS_LOCK:
            if settings().get('capture_mode') == 'browser':
                return {'ok': False, 'error': 'This browser is selected. Press Record in the browser to allow microphone access; computer capture will not be started.'}
            ensure_watcher()
            return recorder().start(live=bool((body or {}).get('live')))
    return await asyncio.to_thread(start)


@router.post("/stop")
async def post_stop() -> dict:
    def _stop():
        rec = recorder()
        return _after_stop(rec, rec.stop())
    return await asyncio.to_thread(_stop)


@router.post("/keep")
async def post_keep() -> dict:
    """Cancel the auto-stop countdown: keep recording until told otherwise."""
    return await asyncio.to_thread(lambda: recorder().keep())


@router.get("/title")
async def get_title(file: str, machine: str = "") -> dict:
    return await asyncio.to_thread(lambda: action_recorder(file, machine).get_title(file))


@router.post("/title")
async def post_title(body: dict) -> dict:
    from recording_titles import validate_title
    try:
        title = validate_title(body.get("title"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not isinstance(body.get("file"), str) or not body["file"]:
        return {"ok": False, "error": "file is required"}
    return await asyncio.to_thread(lambda: action_recorder(body["file"], body.get("machine", "")).set_title(body["file"], title))


@router.post("/delete")
async def post_delete(body: dict) -> dict:
    """Remove one recording and its transcript. Destructive: the UI asks first."""
    path = (body or {}).get("file", "")
    if not path:
        return {"ok": False, "error": "file is required"}
    def _delete():
        try:
            library = RecordingLibrary()
            with_library = library.delete(path, body.get("machine", ""))
            if with_library is not None:
                return with_library
            return action_recorder(path, body.get("machine", "")).delete_recording(path)
        except Exception as exc:
            _log.warning("call-recorder delete: %s", exc)
            return {"ok": False, "error": "Could not reach the recording machine to delete the recording."}
    return await asyncio.to_thread(_delete)


@router.post("/recheck")
async def post_recheck() -> dict:
    """Forget every cached detection and look again (used after installing a driver)."""
    def _recheck() -> dict:
        _TRANSCRIPTION_CACHE.update({"at": 0.0, "host": None, "value": None})
        _READINESS_CACHE.update({"at": 0.0, "host": None, "value": None})
        return _config_payload()
    return await asyncio.to_thread(_recheck)


@router.get("/call")
async def get_call() -> dict:
    """What the target host currently looks like: who is playing, who has the mic."""
    return await asyncio.to_thread(lambda: {"ok": True, **recorder().call_state()})


@router.post("/diagnose")
async def post_diagnose() -> dict:
    """Run fresh connection checks without starting capture."""
    cfg = settings()
    return await asyncio.to_thread(diagnose, Recorder, cfg["host"] or None, cfg)


@router.get("/transcript")
async def get_transcript(lines: int = 60) -> dict:
    def _tail() -> dict:
        with _SETTINGS_LOCK:
            if settings().get('capture_mode') == 'browser':
                return {'ok': True, 'lines': [], 'live': False}
            rows = recorder().transcript_tail(lines)
        return {"ok": True, "lines": rows}
    return await asyncio.to_thread(_tail)


@router.get("/recordings")
async def get_recordings(limit: int = 20) -> dict:
    def _list() -> dict:
        library = RecordingLibrary()
        cfg = settings()
        selected = Recorder(host=cfg["host"] or None, recordings_dir=cfg["recordings_dir"]) if cfg.get("capture_mode") == "browser" else recorder()
        items = library.recordings(selected, limit)
        library.schedule_retry()
        return {"ok": True, "items": items}
    return await asyncio.to_thread(_list)


@router.get("/transcript/read")
async def read_transcript(path: str, machine: str = "") -> dict:
    def _read() -> dict:
        return {"ok": True, "markdown": action_recorder(path, machine).read_transcript(path)}
    return await asyncio.to_thread(_read)


@router.post("/transcribe")
async def post_transcribe(body: dict) -> dict:
    """Force a quality Whisper re-pass, replacing any live-derived transcript."""
    path = (body or {}).get("file", "")
    if not path:
        return {"ok": False, "error": "file is required"}

    def _force():
        rec = action_recorder(path, body.get("machine", ""))
        key = (rec.host or "local", path)
        with _TRANSCRIPTION_LOCK:
            job = {"file": path, "state": "running", "ok": None}
            _TRANSCRIPTION_JOBS[key] = job
            file_lock = _TRANSCRIPTION_FILE_LOCKS.setdefault(key, threading.Lock())
        with file_lock:
            try:
                outcome = _transcribe(rec, path)
            except Exception as exc:
                _log.warning("call-recorder archive: %s", exc)
                outcome = {"ok": False, "error": "Could not transcribe on the recording machine."}
            with _TRANSCRIPTION_LOCK:
                job.update(outcome, state="ready" if outcome.get("ok") else "failed")
            if outcome.get("ok") and (rec.host or not RecordingLibrary().target(path)):
                job["upload"] = _store(rec, path)
        return outcome
    return await asyncio.to_thread(_force)


# The plugin is imported by the dashboard process once, at startup: this is where
# the automation thread belongs. It is a no-op when auto start/stop is disabled.
try:
    ensure_watcher()
except Exception:  # never let automation break loading the plugin
    _log.exception("call-recorder: watcher failed to start")

