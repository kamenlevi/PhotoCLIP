#!/bin/bash
# Install PhotoCLIP Python dependencies into a dedicated venv so they
# don't pollute the system Python and survive OS upgrades.
set -e

VENV="/opt/photoclip/venv"
REQS="/usr/lib/photoclip/sidecar/requirements.txt"

# Try to find requirements.txt in any of the possible resource locations
for candidate in \
    "/usr/lib/photoclip/sidecar/requirements.txt" \
    "/usr/share/photoclip/sidecar/requirements.txt" \
    "/usr/lib/com.photoclip.app/sidecar/requirements.txt" \
    "/usr/share/com.photoclip.app/sidecar/requirements.txt"; do
    if [ -f "$candidate" ]; then
        REQS="$candidate"
        break
    fi
done

mkdir -p /opt/photoclip
python3 -m venv "$VENV" 2>/dev/null || true

if [ -f "$REQS" ]; then
    "$VENV/bin/pip" install --quiet -r "$REQS" 2>&1 | tail -5 || true
fi
