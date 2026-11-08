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

Nothing here talks HTTP to the recorder: it shells out locally, or over SSH when
``host`` is set. The caller's SSH key is the credential, so there is no open port
and no token to leak.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter

_log = logging.getLogger("hermes.plugins.call-recorder")

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_FILE = _PLUGIN_ROOT / "settings.json"

import sys

if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from recorder import (  # noqa: E402
    Recorder,
    choose_source,
    detect_candidates,
    detect_transcription,
    probe,
    ssh_aliases,
)

router = APIRouter()

#: Advanced keys stay here (env / settings.json can pin them) but are NOT in the
#: manifest schema: the settings page asks four questions, not seven.
DEFAULTS = {
    "host": "",                    # "" = the machine that has the microphone
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
    """Effective settings: env > Settings → Plugins (config.yaml) > settings.json > defaults."""
    merged = dict(DEFAULTS)
    try:
        merged.update(json.loads(_SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        pass
    merged.update({k: v for k, v in manifest_settings().items() if k in DEFAULTS})
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
    return Recorder(
        host=src["host"],
        recordings_dir=cfg["recordings_dir"],
        python=cfg["python"] or transc.get("python", ""),
        live_script=cfg["live_script"] or transc.get("live_script", ""),
        whisper_model=cfg["whisper_model"],
        auto_start=cfg["auto_start"],
        auto_stop_seconds=cfg["auto_stop_seconds"],
    )


_SOURCE_CACHE: dict = {"at": 0.0, "value": None}


def resolved_source(force: bool = False) -> dict:
    """Which machine will be recorded, cached for a minute so routes stay fast."""
    now = time.time()
    cached = _SOURCE_CACHE.get("value")
    if not force and cached and now - float(_SOURCE_CACHE.get("at") or 0) < 60:
        return cached
    try:
        choice = choose_source(settings()["host"])
    except Exception as exc:  # noqa: BLE001 - never break a route over detection
        _log.warning("call-recorder: source detection failed: %s", exc)
        choice = {"host": settings()["host"], "mode": "unknown", "probe": {},
                  "why": f"detection failed: {exc}"}
    _SOURCE_CACHE.update({"at": now, "value": choice})
    return choice


_TRANSCRIPTION_CACHE: dict = {"at": 0.0, "host": None, "value": None}


def resolved_transcription() -> dict:
    """Where Whisper lives, detected once per host instead of typed into a field.

    Both settings empty means "find it yourself"; setting either one pins the pair.
    """
    cfg = settings()
    if cfg["python"] and cfg["live_script"]:
        return {"ok": True, "mode": "configured", "python": cfg["python"],
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
    if found.get("python") and not found.get("live_script"):
        # The machine can transcribe; it just has no scripts yet. Ship ours.
        installed = Recorder(host=host or None).install_scripts(_PLUGIN_ROOT / "scripts")
        if installed.get("ok"):
            found = {**found, "ok": True, "live_script": installed["live_script"],
                     "installed": True}
    result = {**found, "mode": "auto" if found.get("ok") else "unavailable"}
    _TRANSCRIPTION_CACHE.update({"at": now, "host": host, "value": result})
    return result


_WATCHER_STARTED = False


def _watch() -> None:
    """Poll the target host and let the recorder decide: start, wait, or stop."""
    interval = max(3, int(settings().get("watch_seconds") or 10))
    while True:
        try:
            decision = recorder().tick()
            if decision.get("action") not in ("idle", "recording"):
                _log.info("call-recorder: %s", decision)
        except Exception as exc:  # a dead SSH target must not kill the watcher
            _log.warning("call-recorder watcher: %s", exc)
        time.sleep(interval)


def ensure_watcher() -> None:
    """Start the automation thread once per process, only when there is work to do."""
    global _WATCHER_STARTED
    cfg = settings()
    if _WATCHER_STARTED or not (cfg["auto_start"] or int(cfg["auto_stop_seconds"] or 0) > 0):
        return
    _WATCHER_STARTED = True
    threading.Thread(target=_watch, name="call-recorder-watch", daemon=True).start()
    _log.info("call-recorder: watcher running (%ss)", cfg["watch_seconds"])


def _config_payload() -> dict:
    cfg = settings()
    src = resolved_source()
    transc = resolved_transcription()
    rec = recorder()
    code, out = rec.run("echo ok")
    return {
        "ok": code == 0,
        "host": src["host"] or "local",
        "host_mode": src["mode"],
        "why": src["why"],
        "probe": src.get("probe") or {},
        "recordings_dir": cfg["recordings_dir"],
        "transcription": bool(transc.get("ok")),
        "transcription_mode": transc.get("mode"),
        "transcription_detail": transc.get("python") or "not found on that machine",
        "auto_start": cfg["auto_start"],
        "auto_stop_seconds": cfg["auto_stop_seconds"],
        "reachable": code == 0,
        "detail": out.strip()[:200],
    }


@router.get("/config")
async def get_config() -> dict:
    return await asyncio.to_thread(_config_payload)


@router.get("/status")
async def get_status() -> dict:
    return await asyncio.to_thread(lambda: {"ok": True, **recorder().status()})


@router.post("/start")
async def post_start(body: dict | None = None) -> dict:
    live = bool((body or {}).get("live"))
    return await asyncio.to_thread(lambda: recorder().start(live=live))


@router.post("/stop")
async def post_stop() -> dict:
    return await asyncio.to_thread(lambda: recorder().stop())


@router.post("/keep")
async def post_keep() -> dict:
    """Cancel the auto-stop countdown: keep recording until told otherwise."""
    return await asyncio.to_thread(lambda: recorder().keep())


@router.get("/call")
async def get_call() -> dict:
    """What the target host currently looks like: who is playing, who has the mic."""
    return await asyncio.to_thread(lambda: {"ok": True, **recorder().call_state()})


@router.get("/detect")
async def get_detect() -> dict:
    """Every machine we could record, and which ones actually have audio."""
    def _detect() -> dict:
        candidates = detect_candidates()
        usable = [c for c in candidates if c.get("can_record") and c.get("has_audio")]
        return {"ok": True, "chosen": resolved_source(force=True), "usable": usable,
                "candidates": candidates, "ssh_hosts": ssh_aliases()[:12]}
    return await asyncio.to_thread(_detect)


@router.get("/transcript")
async def get_transcript(lines: int = 60) -> dict:
    def _tail() -> dict:
        rows = recorder().transcript_tail(lines)
        return {"ok": True, "lines": rows}
    return await asyncio.to_thread(_tail)


@router.get("/recordings")
async def get_recordings(limit: int = 20) -> dict:
    def _list() -> dict:
        return {"ok": True, "items": recorder().recordings(limit)}
    return await asyncio.to_thread(_list)


@router.get("/transcript/read")
async def read_transcript(path: str) -> dict:
    def _read() -> dict:
        return {"ok": True, "markdown": recorder().read_transcript(path)}
    return await asyncio.to_thread(_read)


@router.post("/transcribe")
async def post_transcribe(body: dict) -> dict:
    path = (body or {}).get("file", "")
    if not path:
        return {"ok": False, "error": "file is required"}
    return await asyncio.to_thread(lambda: recorder().transcribe(path))


# The plugin is imported by the dashboard process once, at startup: this is where
# the automation thread belongs. It is a no-op when auto start/stop is disabled.
try:
    ensure_watcher()
except Exception:  # never let automation break loading the plugin
    _log.exception("call-recorder: watcher failed to start")

