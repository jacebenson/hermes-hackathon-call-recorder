#!/bin/sh
# Desktop-only install: no Python, helper setup, backend or configuration changes.
set -eu
if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  printf '%s\n' 'Usage: sh scripts/install-desktop.sh /explicit/hermes-home' >&2
  exit 2
fi
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$1/desktop-plugins/call-recorder"
cp "$root/packages/call-recorder/desktop/plugin.js" "$1/desktop-plugins/call-recorder/plugin.js"
cmp "$root/packages/call-recorder/desktop/plugin.js" "$1/desktop-plugins/call-recorder/plugin.js"
printf '%s\n' 'Copied desktop artifact only. Enable Call recorder in Hermes plugin settings.'
