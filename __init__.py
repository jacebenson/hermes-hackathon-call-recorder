"""call-recorder — records both sides of a call and transcribes it.

The work lives in two halves:

* ``dashboard/plugin_api.py`` — the routes the UI calls (start, stop, status,
  transcript, recordings, transcribe).
* ``desktop/plugin.js`` — the composer button, the status chip and the pane.

This module registers no agent tools: recording is a recording, not an action
the model should take on its own. Ask for it in the UI, or drive the routes.
"""

from __future__ import annotations

import logging

_log = logging.getLogger("hermes.plugins.call-recorder")


def register(ctx) -> None:
    _log.info("call-recorder: dashboard API + desktop half loaded")
