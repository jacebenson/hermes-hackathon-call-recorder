#!/bin/sh
# Desktop-only install: no Python, helper setup, backend or configuration changes.
set -eu
if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  printf '%s\n' 'Usage: sh scripts/install-desktop.sh /explicit/hermes-home' >&2
  exit 2
fi
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$1/desktop-plugins/call-recorder"
cp "$root/desktop/plugin.js" "$1/desktop-plugins/call-recorder/plugin.js"
cmp "$root/desktop/plugin.js" "$1/desktop-plugins/call-recorder/plugin.js"
printf '%s\n' 'Copied desktop UI only. Native recording also requires the unified call-recorder backend plugin enabled in this Hermes home.'
