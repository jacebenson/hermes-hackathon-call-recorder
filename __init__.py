"""Call recorder v2: client-local Desktop UI; legacy API retained.

The Desktop artifact requires no Python backend. Registration here is inert:
no watcher, helper installation, SSH polling, model calls or recording starts.
Existing legacy recordings and explicit dashboard routes remain available to
operators who intentionally use those older APIs.
"""
from __future__ import annotations
import logging
_log = logging.getLogger("hermes.plugins.call-recorder")


def register(ctx) -> None:
    _log.info("call-recorder: client-local desktop package loaded; legacy automation inactive")
