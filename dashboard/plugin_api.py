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

from recorder import Recorder  # noqa: E402

router = APIRouter()

DEFAULTS = {
    "host": "",                    # "" = record the machine running this plugin
    "recordings_dir": "~/recordings",
    "python": "",                  # e.g. ~/whisper/.venv/bin/python (needed for transcription)
    "live_script": "",             # e.g. ~/whisper/live.py (archive.py sits beside it)
    "whisper_model": "",
    "auto_start": True,            # start on its own when a call is detected
    "auto_stop_seconds": 180,      # keep recording this long after the call ends (0 = never stop)
    "watch_seconds": 10,           # how often the watcher looks
}


def settings() -> dict:
    """settings.json in the plugin dir, overridable per key by CALLREC_* env vars."""
    merged = dict(DEFAULTS)
    try:
        merged.update(json.loads(_SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        pass
    for key in DEFAULTS:
        env = os.environ.get(f"CALLREC_{key.upper()}")
        if env:
            merged[key] = env
    return merged


def recorder() -> Recorder:
    cfg = settings()
    return Recorder(
        host=cfg["host"],
        recordings_dir=cfg["recordings_dir"],
        python=cfg["python"],
        live_script=cfg["live_script"],
        whisper_model=cfg["whisper_model"],
        auto_start=cfg["auto_start"],
        auto_stop_seconds=cfg["auto_stop_seconds"],
    )


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
    rec = recorder()
    code, out = rec.run("echo ok")
    return {
        "ok": code == 0,
        "host": cfg["host"] or "local",
        "recordings_dir": cfg["recordings_dir"],
        "transcription": bool(cfg["python"] and cfg["live_script"]),
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

